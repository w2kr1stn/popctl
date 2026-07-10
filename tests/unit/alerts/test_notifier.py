import pytest
from popctl.alerts import notifier
from popctl.alerts.config import AlertsConfig
from popctl.alerts.protocol import Alert
from popctl.utils.shell import CommandResult


def _ok(*_args, **_kwargs) -> CommandResult:
    return CommandResult(stdout="", stderr="", returncode=0)


def _alert(kind: str = "warning") -> Alert:
    return Alert.model_validate(
        {"kind": kind, "event_id": "e", "title": "T", "start_time": "2026-06-10T09:00:00+02:00"}
    )


def test_deliver_calls_notify_send_and_configured_sound(mocker):
    calls: list[list[str]] = []
    mocker.patch("popctl.alerts.notifier.command_exists", return_value=True)
    mocker.patch("popctl.alerts.notifier.os.path.isfile", return_value=True)
    mocker.patch(
        "popctl.alerts.notifier.run_command",
        side_effect=lambda args, **_k: (calls.append(args), _ok())[1],
    )
    notifier.deliver(_alert(), AlertsConfig(ws_url="ws://x", default_sound="/s.ogg"))
    assert calls[0][0] == "notify-send"
    assert any(c[0] in ("pw-play", "paplay", "aplay") and c[-1] == "/s.ogg" for c in calls)


def test_deliver_falls_back_to_system_sound_when_none_configured(mocker):
    """The audible-by-default fix: no configured sound must still play a fallback."""
    calls: list[list[str]] = []
    mocker.patch("popctl.alerts.notifier.command_exists", return_value=True)
    # No configured sound; only the first fallback exists on disk.
    fallbacks = AlertsConfig(ws_url="ws://x").fallback_sounds
    mocker.patch("popctl.alerts.notifier.os.path.isfile", side_effect=lambda p: p == fallbacks[0])
    mocker.patch(
        "popctl.alerts.notifier.run_command",
        side_effect=lambda args, **_k: (calls.append(args), _ok())[1],
    )
    notifier.deliver(_alert(), AlertsConfig(ws_url="ws://x"))
    assert any(c[-1] == fallbacks[0] for c in calls), "must play the first existing fallback"


def test_deliver_uses_bundled_sound_when_nothing_configured(mocker):
    """With no configured sound, the bundled tone (a real shipped file) is played."""
    calls: list[list[str]] = []
    mocker.patch("popctl.alerts.notifier.command_exists", return_value=True)
    # isfile NOT mocked → the real bundled ogg on disk resolves; fallbacks unneeded.
    mocker.patch(
        "popctl.alerts.notifier.run_command",
        side_effect=lambda args, **_k: (calls.append(args), _ok())[1],
    )
    notifier.deliver(_alert(), AlertsConfig(ws_url="ws://x", fallback_sounds=[]))
    assert any(c[-1].endswith("alert.ogg") for c in calls), "must play bundled tone"


def test_deliver_plain_alert_renders_text_and_plays_sound(mocker):
    from popctl.alerts.protocol import PlainAlert

    calls: list[list[str]] = []
    mocker.patch("popctl.alerts.notifier.command_exists", return_value=True)
    mocker.patch("popctl.alerts.notifier.os.path.isfile", return_value=True)
    mocker.patch(
        "popctl.alerts.notifier.run_command",
        side_effect=lambda args, **_k: (calls.append(args), _ok())[1],
    )
    notifier.deliver(
        PlainAlert(text="Water the plants"), AlertsConfig(ws_url="ws://x", default_sound="/s.ogg")
    )
    assert calls[0][0] == "notify-send"
    assert any("Water the plants" in a for a in calls[0]), "plain text must reach notify-send"
    assert any(c[0] in ("pw-play", "paplay", "aplay") and c[-1] == "/s.ogg" for c in calls)


def test_bundled_sound_file_exists():
    import os

    assert os.path.isfile(notifier._BUNDLED_SOUND), "the bundled alert tone must ship in-package"


def test_deliver_silent_only_when_no_sound_anywhere(mocker):
    mocker.patch("popctl.alerts.notifier.command_exists", return_value=True)
    mocker.patch("popctl.alerts.notifier.os.path.isfile", return_value=False)  # nothing on disk
    run = mocker.patch("popctl.alerts.notifier.run_command", return_value=_ok())
    notifier.deliver(_alert(), AlertsConfig(ws_url="ws://x", fallback_sounds=[]))
    assert run.call_count == 1  # notify-send only; no sound resolvable


def test_deliver_no_subprocess_when_tools_missing(mocker):
    mocker.patch("popctl.alerts.notifier.command_exists", return_value=False)
    mocker.patch("popctl.alerts.notifier.os.path.isfile", return_value=True)
    run = mocker.patch("popctl.alerts.notifier.run_command", return_value=_ok())
    notifier.deliver(_alert(), AlertsConfig(ws_url="ws://x"))
    assert run.call_count == 0  # notify-send missing + no player → nothing executed


@pytest.mark.parametrize(
    ("first_available", "expected_args"),
    [
        ("pw-play", ["pw-play", "/s.ogg"]),
        ("paplay", ["paplay", "/s.ogg"]),
        ("canberra-gtk-play", ["canberra-gtk-play", "--file", "/s.ogg"]),
        ("ffplay", ["ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet", "/s.ogg"]),
        (
            "mpv",
            ["mpv", "--no-video", "--really-quiet", "--terminal=no", "--keep-open=no", "/s.ogg"],
        ),
        ("aplay", ["aplay", "/usr/share/sounds/alsa/Front_Center.wav"]),
    ],
)
def test_default_sound_players_use_the_first_available_command(
    mocker, first_available: str, expected_args: list[str]
):
    config = AlertsConfig(ws_url="ws://x")
    mocker.patch(
        "popctl.alerts.notifier.command_exists",
        side_effect=lambda command: command in {first_available, "aplay"},
    )
    mocker.patch("popctl.alerts.notifier.os.path.isfile", return_value=True)
    run = mocker.patch("popctl.alerts.notifier.run_command", return_value=_ok())

    assert notifier._play_sound("/s.ogg", config)
    run.assert_called_once_with(expected_args, timeout=30.0)


def test_aplay_only_uses_wav_fallback_when_sound_is_unconfigured(mocker):
    config = AlertsConfig(ws_url="ws://x", sound_players=["aplay"])
    mocker.patch("popctl.alerts.notifier.command_exists", return_value=True)
    mocker.patch("popctl.alerts.notifier.os.path.isfile", return_value=True)
    run = mocker.patch("popctl.alerts.notifier.run_command", return_value=_ok())

    assert notifier._play_sound(None, config)
    run.assert_called_once_with(["aplay", config.fallback_sounds[-1]], timeout=30.0)


def test_aplay_only_skips_configured_ogg_for_wav_fallback(mocker):
    """Configured Ogg yields to the WAV fallback because aplay cannot decode it."""
    config = AlertsConfig(ws_url="ws://x", sound_players=["aplay"])
    mocker.patch("popctl.alerts.notifier.command_exists", return_value=True)
    mocker.patch("popctl.alerts.notifier.os.path.isfile", return_value=True)
    run = mocker.patch("popctl.alerts.notifier.run_command", return_value=_ok())

    assert notifier._play_sound("/configured.ogg", config)
    run.assert_called_once_with(["aplay", config.fallback_sounds[-1]], timeout=30.0)


@pytest.mark.parametrize("player", ["pw-play", "paplay", "canberra-gtk-play", "ffplay", "mpv"])
def test_non_aplay_players_use_bundled_ogg_when_sound_is_unconfigured(mocker, player: str):
    config = AlertsConfig(ws_url="ws://x", sound_players=[player], fallback_sounds=[])
    mocker.patch("popctl.alerts.notifier.command_exists", return_value=True)
    run = mocker.patch("popctl.alerts.notifier.run_command", return_value=_ok())

    assert notifier._play_sound(None, config)
    run.assert_called_once()
    assert run.call_args.args[0][0] == player
    assert run.call_args.args[0][-1] == notifier._BUNDLED_SOUND


def test_sound_player_respects_configured_order(mocker):
    config = AlertsConfig(ws_url="ws://x", sound_players=["mpv", "pw-play"])
    mocker.patch(
        "popctl.alerts.notifier.command_exists",
        side_effect=lambda command: command in {"mpv", "pw-play"},
    )
    mocker.patch("popctl.alerts.notifier.os.path.isfile", return_value=True)
    run = mocker.patch("popctl.alerts.notifier.run_command", return_value=_ok())

    assert notifier._play_sound("/s.ogg", config)
    run.assert_called_once_with(
        ["mpv", "--no-video", "--really-quiet", "--terminal=no", "--keep-open=no", "/s.ogg"],
        timeout=30.0,
    )


def test_deliver_logs_error_when_neither_channel_delivers(mocker, caplog):
    import logging

    mocker.patch("popctl.alerts.notifier.command_exists", return_value=False)
    mocker.patch("popctl.alerts.notifier.os.path.isfile", return_value=False)
    with caplog.at_level(logging.ERROR, logger="popctl.alerts"):
        notifier.deliver(_alert(), AlertsConfig(ws_url="ws://x", fallback_sounds=[]))
    assert any("NOT delivered" in r.message for r in caplog.records), (
        "both channels failing must escalate to a single ERROR"
    )


def test_deliver_no_error_when_notification_succeeds(mocker, caplog):
    import logging

    # notify-send works, sound has no player → partial, but NOT a total failure → no ERROR.
    mocker.patch(
        "popctl.alerts.notifier.command_exists",
        side_effect=lambda c: c == "notify-send",
    )
    mocker.patch("popctl.alerts.notifier.os.path.isfile", return_value=False)
    mocker.patch("popctl.alerts.notifier.run_command", return_value=_ok())
    with caplog.at_level(logging.ERROR, logger="popctl.alerts"):
        notifier.deliver(_alert(), AlertsConfig(ws_url="ws://x", fallback_sounds=[]))
    assert not any("NOT delivered" in r.message for r in caplog.records)
