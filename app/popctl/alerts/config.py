"""Configuration for the desktop alerts daemon (``~/.config/popctl/alerts.toml``)."""

import os
import tomllib
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from popctl.core.paths import get_config_dir

_DEFAULT_URGENCY = {"preeve": "critical", "pre": "critical", "warning": "critical"}
_DEFAULT_EXPIRE_MS = 30_000
_UNKNOWN_DESKTOP_SIGNALS = {"", "UNKNOWN"}


def _default_expire_ms() -> int:
    desktop_signals = (
        os.environ.get("XDG_CURRENT_DESKTOP", "").strip().upper(),
        os.environ.get("XDG_SESSION_DESKTOP", "").strip().upper(),
    )
    if any("COSMIC" in signal for signal in desktop_signals):
        return 0
    if any(signal not in _UNKNOWN_DESKTOP_SIGNALS for signal in desktop_signals):
        return _DEFAULT_EXPIRE_MS
    return 0


class AlertsConfig(BaseModel):
    ws_url: str  # Required; e.g. ws://alert-host.example:8765/
    token: str = ""  # static WS token (?token=); empty for trusted/no-auth networks
    chat_id: str = "desktop-alerts"  # generic example channel identifier; configure for the sink
    client_id: str = "desktop-alerts"  # allowFrom identifier
    reconnect_min_s: float = 1.0
    reconnect_max_s: float = 30.0
    # recv timeout for an established connection; must exceed the server's WS ping
    # interval so an idle-but-healthy link isn't torn down.
    recv_timeout_s: float = 60.0
    # Sound players tried in order; the first one present on PATH is used.
    # Keep aplay last: it cannot play Ogg/Vorbis, including the bundled alert tone.
    sound_players: list[str] = Field(
        default_factory=lambda: [
            "pw-play",
            "paplay",
            "canberra-gtk-play",
            "ffplay",
            "mpv",
            "aplay",
        ]
    )
    default_sound: str = ""  # path to a sound file; empty = use fallback_sounds
    sound_by_kind: dict[str, str] = Field(default_factory=dict)  # kind -> sound path
    # System sounds tried (first existing wins) when no per-kind/default sound resolves.
    # Audible-by-default is the whole point; set this to [] together with an empty
    # default_sound to opt into silent (visual-only) alerts.
    fallback_sounds: list[str] = Field(
        default_factory=lambda: [
            "/usr/share/sounds/freedesktop/stereo/complete.oga",
            "/usr/share/sounds/freedesktop/stereo/message.oga",
            "/usr/share/sounds/alsa/Front_Center.wav",
        ]
    )
    # urgency=critical is set per kind, but expiry controls how long notifications stay visible.
    urgency_by_kind: dict[str, str] = Field(default_factory=lambda: dict(_DEFAULT_URGENCY))
    # notify-send --expire-time in ms. Omitted values persist when COSMIC is detected or
    # desktop detection is unknown; only a detected non-COSMIC desktop expires after 30 seconds.
    # An explicit alerts.toml value always takes precedence.
    expire_ms: int = Field(default_factory=_default_expire_ms)


def get_alerts_config_path() -> Path:
    return get_config_dir() / "alerts.toml"


def load_alerts_config(path: Path | None = None) -> AlertsConfig:
    cfg_path = path or get_alerts_config_path()
    if not cfg_path.exists():
        msg = (
            f"Alerts config not found at {cfg_path}. "
            "Create it with at least a 'ws_url' entry (see 'popctl alerts --help')."
        )
        raise FileNotFoundError(msg)
    try:
        data = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
        return AlertsConfig.model_validate(data)
    except tomllib.TOMLDecodeError as exc:
        raise ValueError(f"Malformed TOML in {cfg_path}: {exc}") from exc
    except ValidationError as exc:
        raise ValueError(f"Invalid alerts config in {cfg_path}:\n{exc}") from exc
