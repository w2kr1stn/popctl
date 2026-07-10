"""Side-effecting desktop alert delivery with explicit sound.

Sound is played explicitly rather than via the notification ``sound`` hint, which is
unreliable on some desktops. Failures are logged (never silently swallowed); delivery
is best-effort.
"""

import logging
import os
from pathlib import Path

from popctl.alerts.config import AlertsConfig
from popctl.alerts.protocol import Alert, PlainAlert
from popctl.alerts.render import build_notify_args, build_plain_notify_args, select_sound
from popctl.utils.shell import command_exists, run_command

logger = logging.getLogger("popctl.alerts")

# Bundled default alert tone, used when the user configures no sound. Shipped with the
# package to guarantee an audible alert without configuration.
_BUNDLED_SOUND = str(Path(__file__).parent / "sounds" / "alert.ogg")


def _sound_command(player: str, sound: str) -> list[str]:
    if player == "canberra-gtk-play":
        return [player, "--file", sound]
    if player == "ffplay":
        return [player, "-nodisp", "-autoexit", "-loglevel", "quiet", sound]
    if player == "mpv":
        return [player, "--no-video", "--really-quiet", "--terminal=no", "--keep-open=no", sound]
    return [player, sound]


def _resolve_sound_player(config: AlertsConfig) -> str | None:
    for player in config.sound_players:
        if command_exists(player):
            return player
    return None


def _sound_is_compatible(player: str, sound: str) -> bool:
    return player != "aplay" or Path(sound).suffix.lower() not in {".ogg", ".oga"}


def _resolve_sound(
    configured: str | None, config: AlertsConfig, *, player: str | None = None
) -> str | None:
    """First existing compatible sound: configured, bundled, then system fallbacks.

    Audible-by-default — a missing or unconfigured sound must not silently degrade to
    a soundless alert (the COSMIC failure mode this daemon exists to fix)."""
    for path in [configured, _BUNDLED_SOUND, *config.fallback_sounds]:
        if path and os.path.isfile(path) and (player is None or _sound_is_compatible(player, path)):
            return path
    return None


def deliver(item: Alert | PlainAlert, config: AlertsConfig) -> None:
    if isinstance(item, Alert):
        args = build_notify_args(item, config)
        configured = select_sound(item, config)
        label = f"{item.kind} — {item.title}"
    else:
        args = build_plain_notify_args(item, config)
        configured = config.default_sound or None
        label = item.text.strip().split("\n", 1)[0][:80]

    notified = False
    if not command_exists("notify-send"):
        logger.warning("notify-send not found on PATH; cannot show desktop notification")
    else:
        result = run_command(args, timeout=10.0)
        if result.success:
            notified = True
        else:
            logger.warning(
                "notify-send failed (rc=%s): %s", result.returncode, result.stderr.strip()
            )

    sounded = _play_sound(configured, config)

    if not notified and not sounded:
        # Both channels failed → the alert reached the user through neither. Escalate
        # so it stands out from the individual per-channel warnings above.
        logger.error("alert NOT delivered (no notification and no sound): %s", label)


def _play_sound(configured: str | None, config: AlertsConfig) -> bool:
    """Play the resolved alert sound; return True iff it was played successfully."""
    sound = _resolve_sound(configured, config)
    if not sound:
        logger.warning("no sound file resolved (configured + fallbacks absent); alert is silent")
        return False
    player = _resolve_sound_player(config)
    if player is None:
        logger.warning("no sound player on PATH (tried: %s)", ", ".join(config.sound_players))
        return False
    sound = _resolve_sound(configured, config, player=player)
    if not sound:
        logger.warning(
            "no compatible sound file resolved for %s (configured + fallbacks unsupported); "
            "alert is silent",
            player,
        )
        return False
    result = run_command(_sound_command(player, sound), timeout=30.0)
    if not result.success:
        logger.warning(
            "sound playback via %s failed (rc=%s): %s",
            player, result.returncode, result.stderr.strip(),
        )
        return False
    return True
