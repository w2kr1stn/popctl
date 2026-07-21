from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import typer
from rich.table import Table

from popctl.advisor.config import (
    AdvisorConfigError,
    ProviderChoice,
    load_advisor_config,
)
from popctl.alerts.config import get_alerts_config_path, load_alerts_config
from popctl.core.paths import get_config_dir
from popctl.dotfiles.config import DotfilesConfigError, load_dotfiles_config
from popctl.dotfiles.desktop import has_desktop_session_hint
from popctl.dotfiles.repo import (
    DotfilesRepo,
    DotfilesRepoError,
    RemoteUrlError,
    TransportOutcome,
    validate_remote_url,
)
from popctl.dotfiles.state import get_dotfiles_state_dir
from popctl.utils.desktop import DesktopFamily, normalize_desktop_family
from popctl.utils.formatting import console
from popctl.utils.shell import command_exists

CheckStatus = Literal["ready", "missing", "warning", "skipped"]

_INSTALL_HINTS: dict[str, str] = {
    "dpkg-query": "sudo apt install dpkg",
    "apt-mark": "sudo apt install apt",
    "apt-get": "sudo apt install apt",
    "sudo": "sudo apt install sudo",
    "flatpak": "sudo apt install flatpak",
    "snap": "sudo apt install snapd",
    "claude": "npm install -g @anthropic-ai/claude-code",
    "gemini": "npm install -g @google/gemini-cli",
    "codex": "npm install -g @openai/codex",
    "notify-send": "sudo apt install libnotify-bin",
    "age": "sudo apt install age",
    "zstd": "sudo apt install zstd",
    "tar": "sudo apt install tar",
    "rclone": "sudo apt install rclone",
    "git": "sudo apt install git",
    "dconf": "sudo apt install dconf-cli",
}
_ADVISOR_CONFIG_FIELDS = frozenset({"provider", "model", "api_key", "timeout_seconds"})
_DOTFILES_REACHABILITY_TIMEOUT_SECONDS = 5.0

@dataclass(frozen=True, slots=True)
class DoctorCheck:
    name: str
    status: CheckStatus
    detail: str
    hint: str | None = None


def _binary_check(command: str) -> DoctorCheck:
    if command_exists(command):
        return DoctorCheck(command, "ready", "Available")
    return DoctorCheck(command, "missing", "Missing", _INSTALL_HINTS[command])


def _print_section(title: str, checks: list[DoctorCheck]) -> None:
    table = Table(
        title=title,
        show_header=True,
        header_style="bold_header",
        border_style="border",
    )
    table.add_column("Check", style="bold")
    table.add_column("Status", width=12)
    table.add_column("Details")

    for check in checks:
        style = {
            "ready": "success",
            "missing": "error",
            "warning": "warning",
            "skipped": "muted",
        }[check.status]
        table.add_row(check.name, f"[{style}]{check.status}[/{style}]", check.detail)

    console.print(table)
    for check in checks:
        if check.hint is not None:
            console.print(f"  Install {check.name}: [bold]{check.hint}[/bold]")


def _redacted_advisor_config_error_detail(error: AdvisorConfigError) -> str:
    prefix = "advisor.toml is invalid: "
    message = str(error)
    if not message.startswith(prefix):
        return "advisor.toml is invalid"

    fields = [
        field
        for field in message.removeprefix(prefix).split(", ")
        if field in _ADVISOR_CONFIG_FIELDS
    ]
    if not fields:
        return "advisor.toml is invalid"
    return prefix + ", ".join(fields)


def _advisor_checks() -> tuple[list[DoctorCheck], bool]:
    providers = list(ProviderChoice)
    try:
        config = load_advisor_config()
    except AdvisorConfigError as exc:
        advisor_path = get_config_dir() / "advisor.toml"
        if not advisor_path.exists():
            config = None
            config_check = DoctorCheck(
                "Configuration",
                "warning",
                "Not configured yet — choose one of: "
                f"{', '.join(provider.value for provider in providers)}. "
                "Run popctl setup to configure it.",
            )
        else:
            config = None
            config_check = DoctorCheck(
                "Configuration",
                "warning",
                f"Cannot read config: {_redacted_advisor_config_error_detail(exc)}",
            )
    else:
        config_check = DoctorCheck(
            "Configuration", "ready", f"Configured provider: {config.provider}"
        )

    checks = [config_check]
    if config is not None:
        auth_detail = (
            "API key configured (stored in advisor.toml)"
            if config.api_key
            else "using the provider CLI's own login"
        )
        checks.append(DoctorCheck("Authentication", "ready", auth_detail))
    configured_provider = config.provider if config is not None else None
    configured_provider_ready = True
    providers.sort(key=lambda provider: provider.value != configured_provider)
    for provider in providers:
        is_configured = provider.value == configured_provider
        label = f"{provider.value} CLI"
        if configured_provider is None:
            label += " (available provider)"
        elif is_configured:
            label += " (configured)"
        else:
            label += " (alternative)"

        check = _binary_check(provider.value)
        checks.append(DoctorCheck(label, check.status, check.detail, check.hint))
        if is_configured:
            configured_provider_ready = check.status == "ready"

    return checks, configured_provider_ready


def _alerts_checks() -> tuple[list[DoctorCheck], str]:
    alerts_path = get_alerts_config_path()
    try:
        config = load_alerts_config(alerts_path)
    except FileNotFoundError:
        config_check = DoctorCheck(
            "Configuration",
            "warning",
            f"Setup required — config absent: {alerts_path}. Run popctl alerts init-config.",
        )
        sound_check = DoctorCheck(
            "Sound player",
            "skipped",
            "Skipped until a valid alerts configuration is available",
        )
        return [_binary_check("notify-send"), sound_check, config_check], "setup required"
    except (OSError, ValueError) as exc:
        config_check = DoctorCheck(
            "Configuration",
            "warning",
            f"Setup required — cannot load {alerts_path}: {exc}",
        )
        sound_check = DoctorCheck(
            "Sound player",
            "skipped",
            "Skipped until a valid alerts configuration is available",
        )
        return [_binary_check("notify-send"), sound_check, config_check], "setup required"

    notify_check = _binary_check("notify-send")
    available_players = [player for player in config.sound_players if command_exists(player)]
    if available_players:
        sound_check = DoctorCheck(
            "Sound player",
            "ready",
            f"Available: {available_players[0]}",
        )
    else:
        sound_check = DoctorCheck(
            "Sound player",
            "warning",
            "No configured sound player is available",
        )

    config_check = DoctorCheck(
        "Configuration",
        "ready",
        f"Loaded: {alerts_path}",
    )
    if notify_check.status != "ready":
        readiness = "unavailable"
    elif sound_check.status != "ready":
        readiness = "warning"
    else:
        readiness = "ready"
    return [notify_check, sound_check, config_check], readiness


def _backup_checks() -> list[DoctorCheck]:
    checks = [_binary_check(command) for command in ("age", "zstd", "tar", "rclone")]
    rclone_check = checks[-1]
    checks[-1] = DoctorCheck(
        "rclone (remote targets only)",
        rclone_check.status,
        (
            "Available when a remote target is configured"
            if rclone_check.status == "ready"
            else "Not installed — needed only for remote targets"
        ),
        rclone_check.hint,
    )

    backup_path = get_config_dir() / "backup.toml"
    checks.append(
        DoctorCheck(
            "Configuration",
            "ready" if backup_path.exists() else "skipped",
            (
                f"Present: {backup_path}"
                if backup_path.exists()
                else f"Absent: {backup_path}. Run popctl backup init."
            ),
        )
    )
    return checks


def _dotfiles_skipped_checks(detail: str) -> list[DoctorCheck]:
    return [
        DoctorCheck("Repository", "skipped", detail),
        DoctorCheck("Remote", "skipped", detail),
        DoctorCheck("Reachability", "skipped", detail),
        DoctorCheck("GitHub privacy recheck", "skipped", detail),
    ]


def _dotfiles_reachability_check(repo: DotfilesRepo, remote_url: str) -> DoctorCheck:
    try:
        result = repo.ls_remote(
            remote_url,
            timeout_seconds=_DOTFILES_REACHABILITY_TIMEOUT_SECONDS,
        )
    except (DotfilesRepoError, OSError):
        return DoctorCheck(
            "Reachability",
            "warning",
            "Could not run the controlled remote reachability probe",
        )

    details: dict[TransportOutcome, tuple[CheckStatus, str]] = {
        TransportOutcome.SUCCESS: ("ready", "Remote is reachable"),
        TransportOutcome.OFFLINE: (
            "warning",
            "Remote is offline or unreachable; dotfiles commands can use cached refs",
        ),
        TransportOutcome.AUTH: (
            "warning",
            "Remote authentication failed; check Git or SSH credentials",
        ),
        TransportOutcome.TIMEOUT: (
            "warning",
            "Remote reachability probe timed out",
        ),
        TransportOutcome.OTHER: (
            "warning",
            "Remote reachability probe failed; inspect the remote and network",
        ),
    }
    status, detail = details[result.transport.outcome]
    return DoctorCheck("Reachability", status, detail)


def _dotfiles_privacy_check() -> DoctorCheck:
    if command_exists("gh"):
        return DoctorCheck(
            "GitHub privacy recheck",
            "ready",
            "Available — automatic pushes recheck GitHub repository visibility",
        )
    return DoctorCheck(
        "GitHub privacy recheck",
        "warning",
        "gh is not installed, so automatic pushes cannot recheck repository visibility. "
        "Install gh for per-push privacy verification.",
    )


def _dotfiles_checks() -> list[DoctorCheck]:
    git_check = _binary_check("git")
    config_path = get_config_dir() / "dotfiles.toml"
    checks = [git_check]
    if not config_path.exists():
        checks.append(
            DoctorCheck(
                "Configuration",
                "warning",
                f"Setup required — config absent: {config_path}. Run popctl dotfiles init.",
            )
        )
        checks.extend(_dotfiles_skipped_checks("Skipped until dotfiles are initialized"))
        return checks

    try:
        config = load_dotfiles_config(config_path)
    except DotfilesConfigError:
        checks.append(
            DoctorCheck(
                "Configuration",
                "warning",
                "Cannot load dotfiles.toml; fix it or rerun popctl dotfiles init",
            )
        )
        checks.extend(_dotfiles_skipped_checks("Skipped until dotfiles configuration is valid"))
        return checks

    checks.append(DoctorCheck("Configuration", "ready", f"Loaded: {config_path}"))
    repo_present = config.bare_repo.is_dir()
    checks.append(
        DoctorCheck(
            "Repository",
            "ready" if repo_present else "warning",
            (
                f"Present: {config.bare_repo}"
                if repo_present
                else (
                    f"Missing: {config.bare_repo}. Restore it or run "
                    "popctl dotfiles init --from <url>."
                )
            ),
        )
    )
    try:
        remote_url = validate_remote_url(config.remote_url)
    except RemoteUrlError:
        checks.append(DoctorCheck("Remote", "warning", "Configured remote is missing or invalid"))
        checks.append(
            DoctorCheck("Reachability", "skipped", "Skipped until a valid remote is configured")
        )
        checks.append(_dotfiles_privacy_check())
        return checks

    checks.append(DoctorCheck("Remote", "ready", "Configured"))
    if git_check.status != "ready":
        checks.append(DoctorCheck("Reachability", "skipped", "Skipped until git is installed"))
    elif not repo_present:
        checks.append(
            DoctorCheck(
                "Reachability",
                "skipped",
                "Skipped until the bare repository exists",
            )
        )
    else:
        try:
            repo = DotfilesRepo(
                config.bare_repo,
                home=Path.home(),
                state_dir=get_dotfiles_state_dir(),
            )
        except (DotfilesRepoError, OSError):
            checks.append(
                DoctorCheck(
                    "Reachability",
                    "warning",
                    "Could not prepare the controlled remote reachability probe",
                )
            )
        else:
            checks.append(_dotfiles_reachability_check(repo, remote_url))
    checks.append(_dotfiles_privacy_check())
    return checks


def _desktop_settings_checks() -> list[DoctorCheck]:
    try:
        enabled = load_dotfiles_config().desktop_settings.enabled
    except DotfilesConfigError:
        enabled_check = DoctorCheck(
            "Enabled",
            "warning",
            "Cannot determine enabled state until dotfiles.toml is valid",
        )
    else:
        enabled_check = DoctorCheck(
            "Enabled",
            "ready" if enabled else "warning",
            "Enabled" if enabled else "Disabled in dotfiles.toml; capture and load are skipped",
        )

    dconf_check = _binary_check("dconf")
    if dconf_check.status != "ready":
        dconf_check = DoctorCheck(
            "dconf",
            "warning",
            "Not installed; desktop capture and load will be skipped",
            dconf_check.hint,
        )
    has_session = has_desktop_session_hint()
    session_check = DoctorCheck(
        "User session",
        "ready" if has_session else "warning",
        (
            "User-session hint detected"
            if has_session
            else "No user-session hint; enter a desktop session before running dotfiles apply"
        ),
    )
    family = normalize_desktop_family(
        os.environ.get("XDG_CURRENT_DESKTOP"),
        os.environ.get("XDG_SESSION_DESKTOP"),
    )
    family_check = DoctorCheck(
        "Desktop family",
        "warning" if family is DesktopFamily.UNKNOWN else "ready",
        (
            "Unknown or conflicting desktop family"
            if family is DesktopFamily.UNKNOWN
            else f"Detected: {family.value}"
        ),
    )
    return [enabled_check, dconf_check, session_check, family_check]


def doctor() -> None:
    package_checks = [
        _binary_check(command) for command in ("dpkg-query", "apt-mark", "apt-get", "sudo")
    ]
    package_ready = all(check.status == "ready" for check in package_checks)
    _print_section(
        "Package management (core) — " + ("ready" if package_ready else "unavailable"),
        package_checks,
    )

    source_checks: list[DoctorCheck] = []
    for source in ("flatpak", "snap"):
        check = _binary_check(source)
        source_checks.append(
            DoctorCheck(
                source,
                check.status if check.status == "ready" else "skipped",
                (
                    "Available"
                    if check.status == "ready"
                    else "Not installed — source skipped"
                ),
                check.hint,
            )
        )
    _print_section("Optional package sources", source_checks)

    advisor_checks, configured_provider_ready = _advisor_checks()
    advisor_configured = advisor_checks[0].status == "ready"
    advisor_warning = not advisor_configured or not configured_provider_ready
    _print_section(
        "AI advisor (core feature) — " + ("warning" if advisor_warning else "ready"),
        advisor_checks,
    )
    if advisor_configured and not configured_provider_ready:
        console.print(
            "[warning]Advisor warning:[/] The configured advisor CLI is unavailable. "
            "This does not fail doctor because [bold]popctl sync --no-advisor[/bold] "
            "remains usable."
        )

    alerts_checks, alerts_readiness = _alerts_checks()
    _print_section(
        "Desktop alerts (optional) — " + alerts_readiness,
        alerts_checks,
    )

    _print_section("Backup (optional)", _backup_checks())

    _print_section("Dotfiles (optional)", _dotfiles_checks())

    _print_section("Desktop settings (optional)", _desktop_settings_checks())

    if not package_ready:
        console.print(
            "[error]Summary:[/] Package management is unavailable; install the missing core tools "
            "and run [bold]popctl doctor[/bold] again."
        )
        raise typer.Exit(code=1)

    if advisor_warning:
        console.print(
            "[warning]Summary:[/] Package management is ready. Advisor setup needs attention, "
            "but this warning does not fail doctor."
        )
    else:
        console.print(
            "[success]Summary:[/] Core package management is ready; optional capabilities are "
            "reported above."
        )
