"""Parse alert frames from a compatible WebSocket alert sink (e.g. a nanobot instance).

Wire format is double-encoded: the sink sends a message frame
``{"event": "message", "chat_id": ..., "text": "<payload>"}``. The payload is
either a structured alert (JSON) or plain reminder text. ``parse_frame`` returns
``None`` for non-alert frames (``ready``/``attached``/``delta``/... or an
unrecognizable envelope), an ``Alert`` for valid structured JSON, and a
``PlainAlert`` for everything else — so a non-JSON reminder is rendered as a plain
notification rather than dropped.
"""

import json
import logging
from typing import Literal

from pydantic import BaseModel, ValidationError

logger = logging.getLogger("popctl.alerts")

AlertKind = Literal["preeve", "pre", "warning"]


class Alert(BaseModel):
    kind: AlertKind
    event_id: str
    title: str
    start_time: str
    online: bool = False
    location: str | None = None
    meeting_link: str | None = None


class PlainAlert(BaseModel):
    """A message-frame payload that is not structured alert JSON — rendered as-is."""

    text: str


class _Frame(BaseModel):
    """The outer WebSocket message envelope; ``text`` carries the alert payload."""

    event: str
    text: str | None = None


def parse_frame(raw: str) -> Alert | PlainAlert | None:
    try:
        frame = _Frame.model_validate(json.loads(raw))
    except (json.JSONDecodeError, TypeError, ValidationError):
        return None
    if frame.event != "message" or not frame.text:
        return None
    try:
        return Alert.model_validate(json.loads(frame.text))
    except json.JSONDecodeError:
        # Not JSON at all → a genuine plain-text reminder; render the raw text.
        return PlainAlert(text=frame.text)
    except ValidationError as exc:
        # Parsed as JSON but not the Alert schema → still render (never drop), but this is
        # the shape calendar-alert wire-format drift takes, so surface it instead of masking.
        logger.warning(
            "alert-shaped payload failed Alert validation (probable schema drift): %s",
            str(exc)[:200],
        )
        return PlainAlert(text=frame.text)
