"""WebSocket client daemon for a compatible WebSocket alert sink (e.g. a nanobot instance).

A blocking connect → handshake → receive loop delivers alerts with exponential reconnect backoff.
The daemon re-sends ``attach`` on every (re)connect.
"""

import json
import logging
import time
from collections.abc import Callable
from urllib.parse import urlencode

from websocket import WebSocketException, create_connection  # type: ignore  # untyped lib

from popctl.alerts import notifier
from popctl.alerts.config import AlertsConfig
from popctl.alerts.protocol import Alert, parse_frame

logger = logging.getLogger("popctl.alerts")

_CONNECT_TIMEOUT_S = 15.0


def build_url(config: AlertsConfig) -> str:
    params = {"client_id": config.client_id}
    if config.token:
        params["token"] = config.token
    sep = "&" if "?" in config.ws_url else "?"
    return f"{config.ws_url}{sep}{urlencode(params)}"


def _run_session(config: AlertsConfig, on_attached: Callable[[], None]) -> None:
    ws = create_connection(build_url(config), timeout=_CONNECT_TIMEOUT_S)
    try:
        ws.recv()  # consume the initial `ready` frame; we attach explicitly regardless
        ws.send(json.dumps({"type": "attach", "chat_id": config.chat_id}))
        logger.info("attached to chat_id=%s", config.chat_id)
        on_attached()  # connection is healthy → let the caller reset reconnect backoff
        # Use a recv timeout longer than the server's ping interval so an idle but
        # healthy connection is not torn down, while a genuinely dead socket is still
        # detected within the window (rather than blocking forever after suspend).
        ws.settimeout(config.recv_timeout_s)
        while True:
            raw = ws.recv()
            if not raw:
                raise ConnectionError("server closed the connection")
            text = raw if isinstance(raw, str) else raw.decode("utf-8", "replace")
            alert = parse_frame(text)
            if alert is None:
                continue  # non-alert frame (ready/attached/delta/...) — ignore
            desc = (
                f"{alert.kind} — {alert.title}"
                if isinstance(alert, Alert)
                else alert.text.strip().split("\n", 1)[0][:80]
            )
            logger.info("alert received: %s", desc)
            # Isolate delivery: one bad alert must never drop the connection or kill
            # the daemon (a notify/sound OSError here is unrelated to the WebSocket).
            try:
                notifier.deliver(alert, config)
            except Exception:
                logger.exception("alert delivery failed (%s); continuing", desc)
    finally:
        ws.close()


def run(config: AlertsConfig) -> None:
    backoff = config.reconnect_min_s

    def _reset_backoff() -> None:
        nonlocal backoff
        backoff = config.reconnect_min_s

    while True:
        try:
            logger.info("connecting to %s", config.ws_url)
            _run_session(config, on_attached=_reset_backoff)
        except (WebSocketException, OSError) as exc:
            logger.warning("connection lost (%s); reconnecting in %.0fs", exc, backoff)
            time.sleep(backoff)
            backoff = min(backoff * 2, config.reconnect_max_s)
