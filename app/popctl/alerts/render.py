"""Pure rendering of an Alert into notification arguments and a sound choice.

No side effects — the daemon's notifier executes what these return, which keeps
the formatting/urgency/sound-selection logic unit-testable without a desktop.
"""

from datetime import datetime

from popctl.alerts.config import AlertsConfig
from popctl.alerts.protocol import Alert, PlainAlert

_ICON = {"preeve": "🗓", "pre": "⏰", "warning": "🔴"}
_LEAD = {"preeve": "Tomorrow", "pre": "Soon", "warning": "Now"}


def _hhmm(start_time: str) -> str:
    try:
        return datetime.fromisoformat(start_time).strftime("%H:%M")
    except ValueError:
        return start_time


def _summary(alert: Alert) -> str:
    return f"{_ICON.get(alert.kind, '📅')} {_LEAD.get(alert.kind, '')}: {alert.title}"


def _body(alert: Alert) -> str:
    lines = [f"🕘 {_hhmm(alert.start_time)}"]
    if alert.location:
        lines.append(f"📍 {alert.location}")
    if alert.meeting_link:
        lines.append(f"🔗 {alert.meeting_link}")
    return "\n".join(lines)


def build_notify_args(alert: Alert, config: AlertsConfig) -> list[str]:
    urgency = config.urgency_by_kind.get(alert.kind, "critical")
    return [
        "notify-send",
        "--app-name=popctl",
        f"--urgency={urgency}",
        # Config resolution supplies a desktop-aware expiry; 0 persists until dismissed.
        f"--expire-time={config.expire_ms}",
        "--",  # end-of-options: summary/body are positional, never parsed as flags
        _summary(alert),
        _body(alert),
    ]


def build_plain_notify_args(plain: PlainAlert, config: AlertsConfig) -> list[str]:
    """notify-send args for a non-calendar (plain text) reminder.

    First line becomes the summary, the rest the body; urgency stays critical like
    calendar alerts."""
    parts = plain.text.strip().split("\n", 1)
    summary = f"🔔 {parts[0][:120]}" if parts[0] else "🔔 Reminder"
    body = parts[1][:2000] if len(parts) > 1 else ""  # cap a verbose agent reminder
    return [
        "notify-send",
        "--app-name=popctl",
        "--urgency=critical",
        f"--expire-time={config.expire_ms}",
        "--",
        summary,
        body,
    ]


def select_sound(alert: Alert, config: AlertsConfig) -> str | None:
    return config.sound_by_kind.get(alert.kind) or config.default_sound or None
