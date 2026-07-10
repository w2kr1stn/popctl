import json

from popctl.alerts.protocol import Alert, PlainAlert, parse_frame


def _frame(payload: str) -> str:
    return json.dumps({"event": "message", "chat_id": "desktop-alerts", "text": payload})


def _alert_frame(alert: dict) -> str:
    return _frame(json.dumps(alert))


def _alert(**over) -> dict:
    a = {
        "kind": "warning",
        "event_id": "e1",
        "title": "Standup",
        "start_time": "2026-06-10T09:00:00+02:00",
        "online": True,
    }
    a.update(over)
    return a


def test_parses_valid_structured_alert():
    alert = parse_frame(_alert_frame(_alert(location="Room 1", meeting_link="https://x")))
    assert isinstance(alert, Alert)
    assert alert.kind == "warning"
    assert alert.event_id == "e1"
    assert alert.title == "Standup"
    assert alert.online is True
    assert alert.location == "Room 1"
    assert alert.meeting_link == "https://x"


def test_optional_fields_default_when_absent():
    alert = parse_frame(_alert_frame(_alert()))
    assert isinstance(alert, Alert)
    assert alert.location is None
    assert alert.meeting_link is None


def test_non_message_events_return_none():
    for ev in ("ready", "attached", "delta", "stream_end", "reasoning_delta"):
        assert parse_frame(json.dumps({"event": ev, "chat_id": "x"})) is None


def test_malformed_outer_frame_returns_none():
    assert parse_frame("not json") is None
    assert parse_frame(json.dumps([1, 2, 3])) is None


def test_message_without_text_returns_none():
    assert parse_frame(json.dumps({"event": "message", "chat_id": "x"})) is None
    assert parse_frame(json.dumps({"event": "message", "text": ""})) is None


def test_plain_text_payload_becomes_plain_alert(caplog):
    # A non-JSON reminder (standing/ad-hoc) is rendered, not dropped — and is NOT drift,
    # so it must not log a warning (those are expected, common payloads).
    import logging

    with caplog.at_level(logging.WARNING, logger="popctl.alerts"):
        a = parse_frame(_frame("🌱 Water the plants!"))
    assert isinstance(a, PlainAlert)
    assert a.text == "🌱 Water the plants!"
    assert not caplog.records, "plain text is not schema drift; must not warn"


def test_alert_shaped_drift_renders_but_warns(caplog):
    # Valid JSON shaped like an alert but failing validation = probable wire-format drift.
    # Still rendered (never lost), but it MUST surface a warning, not mask the drift.
    import logging

    with caplog.at_level(logging.WARNING, logger="popctl.alerts"):
        a = parse_frame(_alert_frame(_alert(kind="bogus")))
    assert isinstance(a, PlainAlert)
    assert any("schema drift" in r.message for r in caplog.records), "drift must be logged"


def test_missing_required_field_drift_warns(caplog):
    import logging

    bad = _alert()
    del bad["title"]
    with caplog.at_level(logging.WARNING, logger="popctl.alerts"):
        assert isinstance(parse_frame(_alert_frame(bad)), PlainAlert)
    assert any("schema drift" in r.message for r in caplog.records)
