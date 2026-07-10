from popctl.alerts.config import AlertsConfig
from popctl.alerts.protocol import Alert, PlainAlert
from popctl.alerts.render import build_notify_args, build_plain_notify_args, select_sound


def _cfg(**over) -> AlertsConfig:
    base = {"ws_url": "ws://alert-host.test:8765/"}
    base.update(over)
    return AlertsConfig(**base)


def _alert(**over) -> Alert:
    base = {
        "kind": "warning",
        "event_id": "e1",
        "title": "Standup",
        "start_time": "2026-06-10T09:00:00+02:00",
    }
    base.update(over)
    return Alert(**base)


def test_notify_args_use_critical_urgency_by_default():
    args = build_notify_args(_alert(kind="warning"), _cfg())
    assert args[0] == "notify-send"
    assert "--urgency=critical" in args
    assert "--app-name=popctl" in args


def test_notify_args_persist_by_default_when_desktop_is_unknown(monkeypatch):
    monkeypatch.delenv("XDG_CURRENT_DESKTOP", raising=False)
    monkeypatch.delenv("XDG_SESSION_DESKTOP", raising=False)
    assert "--expire-time=0" in build_notify_args(_alert(), _cfg())


def test_expire_time_configurable():
    assert "--expire-time=15000" in build_notify_args(_alert(), _cfg(expire_ms=15000))


def test_urgency_overridable_per_kind():
    cfg = _cfg(urgency_by_kind={"preeve": "normal", "pre": "critical", "warning": "critical"})
    assert "--urgency=normal" in build_notify_args(_alert(kind="preeve"), cfg)


def test_urgency_falls_back_to_critical_for_unmapped_kind():
    # A partial urgency_by_kind in config replaces the whole dict; render must still
    # default the missing kinds to critical (persist).
    cfg = _cfg(urgency_by_kind={"preeve": "normal"})
    assert "--urgency=critical" in build_notify_args(_alert(kind="warning"), cfg)


def test_summary_and_body_contain_title_time_location():
    args = build_notify_args(
        _alert(title="1:1", start_time="2026-06-10T09:05:00+02:00", location="Cafe"), _cfg()
    )
    summary, body = args[-2], args[-1]
    assert "1:1" in summary
    assert "09:05" in body
    assert "Cafe" in body


def test_summary_uses_english_lead_word_for_each_wire_kind():
    for kind, lead in (("preeve", "Tomorrow"), ("pre", "Soon"), ("warning", "Now")):
        args = build_notify_args(_alert(kind=kind), _cfg())
        assert f" {lead}: Standup" in args[-2]


def test_meeting_link_in_body_when_present():
    args = build_notify_args(_alert(meeting_link="https://meet/x"), _cfg())
    assert "https://meet/x" in args[-1]


def test_bad_start_time_falls_back_to_raw():
    args = build_notify_args(_alert(start_time="garbage"), _cfg())
    assert "garbage" in args[-1]


def test_plain_notify_args_render_text_and_use_default_expiry(monkeypatch):
    monkeypatch.delenv("XDG_CURRENT_DESKTOP", raising=False)
    monkeypatch.delenv("XDG_SESSION_DESKTOP", raising=False)
    args = build_plain_notify_args(PlainAlert(text="Take out the trash\nRecycling bin"), _cfg())
    assert args[0] == "notify-send"
    assert "--urgency=critical" in args
    assert "--expire-time=0" in args
    summary, body = args[-2], args[-1]
    assert "Take out the trash" in summary
    assert "Recycling bin" in body


def test_plain_notify_args_single_line():
    args = build_plain_notify_args(PlainAlert(text="Water the plants"), _cfg())
    assert "Water the plants" in args[-2]  # summary
    assert args[-1] == ""  # empty body


def test_plain_notify_args_empty_text_uses_neutral_fallback_summary():
    args = build_plain_notify_args(PlainAlert(text=""), _cfg())
    assert args[-2] == "🔔 Reminder"
    assert args[-1] == ""


def test_select_sound_prefers_per_kind_then_default_then_none():
    full = _cfg(sound_by_kind={"warning": "/w.ogg"}, default_sound="/d.ogg")
    assert select_sound(_alert(kind="warning"), full) == "/w.ogg"
    assert select_sound(_alert(kind="pre"), full) == "/d.ogg"
    assert select_sound(_alert(kind="pre"), _cfg()) is None
