from pathlib import Path

import pytest
from popctl.alerts.config import load_alerts_config


def test_defaults_apply_with_minimal_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("XDG_CURRENT_DESKTOP", raising=False)
    monkeypatch.delenv("XDG_SESSION_DESKTOP", raising=False)
    p = tmp_path / "alerts.toml"
    p.write_text('ws_url = "ws://alert-host.test:8765/"\n', encoding="utf-8")
    cfg = load_alerts_config(p)
    assert cfg.ws_url == "ws://alert-host.test:8765/"
    assert cfg.chat_id == "desktop-alerts"
    assert cfg.client_id == "desktop-alerts"
    assert cfg.sound_players == [
        "pw-play",
        "paplay",
        "canberra-gtk-play",
        "ffplay",
        "mpv",
        "aplay",
    ]
    assert cfg.fallback_sounds  # audible-by-default: a non-empty system-sound fallback list
    assert cfg.urgency_by_kind["warning"] == "critical"
    assert cfg.expire_ms == 0


@pytest.mark.parametrize(
    ("current_desktop", "session_desktop", "expected_expire_ms"),
    [
        (None, None, 0),
        ("UNKNOWN", None, 0),
        ("COSMIC", None, 0),
        ("pop:COSMIC", None, 0),
        ("ubuntu:GNOME", None, 30_000),
        ("KDE", None, 30_000),
        (None, "GNOME", 30_000),
    ],
)
def test_expire_default_is_resolved_from_desktop_signals(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    current_desktop: str | None,
    session_desktop: str | None,
    expected_expire_ms: int,
):
    monkeypatch.delenv("XDG_CURRENT_DESKTOP", raising=False)
    monkeypatch.delenv("XDG_SESSION_DESKTOP", raising=False)
    if current_desktop is not None:
        monkeypatch.setenv("XDG_CURRENT_DESKTOP", current_desktop)
    if session_desktop is not None:
        monkeypatch.setenv("XDG_SESSION_DESKTOP", session_desktop)
    p = tmp_path / "alerts.toml"
    p.write_text('ws_url = "ws://alert-host.test:8765/"\n', encoding="utf-8")

    assert load_alerts_config(p).expire_ms == expected_expire_ms


def test_expire_setting_overrides_desktop_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "COSMIC")
    p = tmp_path / "alerts.toml"
    p.write_text(
        'ws_url = "ws://alert-host.test:8765/"\nexpire_ms = 15000\n', encoding="utf-8"
    )

    assert load_alerts_config(p).expire_ms == 15_000


def test_explicit_zero_expiry_overrides_non_cosmic_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "ubuntu:GNOME")
    p = tmp_path / "alerts.toml"
    p.write_text(
        'ws_url = "ws://alert-host.test:8765/"\nexpire_ms = 0\n', encoding="utf-8"
    )

    assert load_alerts_config(p).expire_ms == 0


def test_missing_file_raises_helpful_error(tmp_path: Path):
    with pytest.raises(FileNotFoundError, match="ws_url"):
        load_alerts_config(tmp_path / "nope.toml")


def test_overrides_parse(tmp_path: Path):
    p = tmp_path / "alerts.toml"
    p.write_text(
        'ws_url = "wss://alert-host.test:8765/ws"\n'
        'token = "secret"\n'
        'default_sound = "/usr/share/sounds/x.oga"\n'
        "[sound_by_kind]\n"
        'warning = "/w.oga"\n'
        "[urgency_by_kind]\n"
        'preeve = "normal"\n',
        encoding="utf-8",
    )
    cfg = load_alerts_config(p)
    assert cfg.token == "secret"
    assert cfg.default_sound == "/usr/share/sounds/x.oga"
    assert cfg.sound_by_kind == {"warning": "/w.oga"}
    # A provided [urgency_by_kind] table replaces the default wholesale (render
    # backfills the missing kinds to critical — see test_render).
    assert cfg.urgency_by_kind == {"preeve": "normal"}
