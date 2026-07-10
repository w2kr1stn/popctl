from __future__ import annotations

import platform
import sys

import typer

from popctl.advisor.config import (
    AdvisorConfig,
    AdvisorConfigError,
    ProviderChoice,
    load_advisor_config,
    save_advisor_config,
)
from popctl.cli.commands.init import init_manifest
from popctl.core.manifest import manifest_exists
from popctl.core.paths import get_config_dir
from popctl.utils.formatting import console, print_error, print_info, print_success, print_warning
from popctl.utils.shell import command_exists

app = typer.Typer(
    help="Guided first-run setup for popctl.",
    invoke_without_command=True,
)

_CORE_BINARIES = ("dpkg-query", "apt-mark", "apt-get", "sudo")
_PROVIDER_DESCRIPTIONS: dict[ProviderChoice, str] = {
    ProviderChoice.CLAUDE: "Anthropic Claude Code",
    ProviderChoice.GEMINI: "Google Gemini CLI",
    ProviderChoice.CODEX: "OpenAI Codex CLI",
}
_PROVIDER_INSTALL_COMMANDS: dict[ProviderChoice, str] = {
    ProviderChoice.CLAUDE: "curl -fsSL claude.ai/install.sh | bash",
    ProviderChoice.GEMINI: "npm install -g @google/gemini-cli",
    ProviderChoice.CODEX: "npm install -g @openai/codex",
}
_PROVIDER_SELECTIONS: dict[str, ProviderChoice] = {
    "1": ProviderChoice.CLAUDE,
    "2": ProviderChoice.GEMINI,
    "3": ProviderChoice.CODEX,
}


def _is_interactive() -> bool:
    return sys.stdin.isatty()


def _print_static_guide() -> None:
    console.print("[bold]popctl setup needs an interactive terminal.[/]")
    typer.echo("1. Check your system: popctl doctor")
    typer.echo("2. Run the guided wizard in a terminal: popctl setup")
    typer.echo("3. Create a manifest: popctl init")
    typer.echo("4. Set up optional alerts: popctl alerts init-config")
    typer.echo("5. Install the optional alerts service: popctl alerts install-service")
    typer.echo("6. Set up encrypted backups: popctl backup init")
    typer.echo("7. Synchronize your system: popctl sync")


def _get_distribution() -> tuple[str, bool]:
    try:
        os_release = platform.freedesktop_os_release()
    except OSError:
        return "unknown", False

    distro = os_release.get("ID") or os_release.get("NAME") or "unknown"
    identifiers = " ".join(
        str(os_release.get(field, "")).lower() for field in ("ID", "ID_LIKE")
    )
    return str(distro), any(name in identifiers for name in ("debian", "ubuntu"))


def _check_core_binaries() -> bool:
    distro, is_supported = _get_distribution()
    if not is_supported:
        console.print("[error]Unsupported distribution.[/]")
        typer.echo(
            "popctl targets Debian/Ubuntu-based systems. "
            f"Detected distribution: {distro}."
        )
        return False

    missing_binaries = [command for command in _CORE_BINARIES if not command_exists(command)]
    if not missing_binaries:
        return True

    console.print("[error]Core package management is unavailable.[/]")
    typer.echo(f"Missing core tools: {', '.join(missing_binaries)}")
    typer.echo("Run popctl doctor to see detailed checks after installing the missing core tools.")
    return False


def _prompt_provider(default: ProviderChoice) -> ProviderChoice | None:
    while True:
        value = typer.prompt(
            "Choose an AI advisor (name, number, or 'skip')", default=default.value
        )
        if value.strip().lower() in {"4", "skip"}:
            return None
        selected = _PROVIDER_SELECTIONS.get(value.strip().lower())
        if selected is None:
            try:
                selected = ProviderChoice(value.strip().lower())
            except ValueError:
                typer.echo("Please choose claude, gemini, or codex.")
                continue
        return selected


def _prompt_auth_method() -> str:
    while True:
        method = typer.prompt("Authentication method", default="1").strip()
        if method in {"1", "2"}:
            return method
        typer.echo("Please enter 1 or 2.")


def _save_advisor_choice(config: AdvisorConfig, provider: ProviderChoice, api_key: str) -> None:
    model = config.model if config.provider == provider.value else None
    updated_config = config.model_copy(
        update={
            "provider": provider.value,
            "model": model,
            "api_key": api_key,
        }
    )
    save_advisor_config(updated_config)


def _load_advisor_config_for_setup() -> AdvisorConfig:
    try:
        return load_advisor_config()
    except AdvisorConfigError as exc:
        if (get_config_dir() / "advisor.toml").exists():
            print_error(f"Could not read advisor configuration: {exc}")
            raise typer.Exit(code=1) from exc
        return AdvisorConfig()


def _configure_advisor() -> str:
    console.print()
    console.print("[bold]AI advisor[/bold]")
    typer.echo("Choose an AI provider CLI (command-line app) to classify packages.")
    for number, provider in enumerate(ProviderChoice, start=1):
        console.print(f"  {number}. [bold]{provider.value}[/] — {_PROVIDER_DESCRIPTIONS[provider]}")
    console.print("  4. [bold]skip[/] — Skip AI setup for now")

    config = _load_advisor_config_for_setup()
    provider = _prompt_provider(ProviderChoice(config.provider))
    if provider is None:
        return "AI advisor: skipped"

    if not command_exists(provider.value):
        print_warning(f"The {provider.value} CLI is not installed.")
        typer.echo(f"Install it with: {_PROVIDER_INSTALL_COMMANDS[provider]}")
        typer.echo("You can continue setup and install it later.")

    typer.echo("  [1] The provider CLI is already logged in")
    typer.echo("  [2] Enter an API key now")
    auth_method = _prompt_auth_method()
    api_key = ""
    if auth_method == "2":
        typer.echo(
            "Your API key will be stored in ~/.config/popctl/advisor.toml, readable only by you."
        )
        api_key = typer.prompt("API key", hide_input=True)
        if not api_key:
            print_warning("No API key entered; the provider CLI login will be used instead.")

    if not typer.confirm("Save these AI advisor settings?", default=True):
        typer.echo("AI advisor setup skipped; no configuration was saved.")
        return "AI advisor: skipped"

    try:
        _save_advisor_choice(config, provider, api_key)
    except AdvisorConfigError as exc:
        print_error(f"Could not save advisor configuration: {exc}")
        raise typer.Exit(code=1) from exc

    auth_summary = "API key saved" if api_key else "provider CLI login"
    return f"AI advisor: {provider.value} ({auth_summary})"


def _configure_manifest() -> tuple[str, bool]:
    if manifest_exists():
        return "Manifest: already present", True

    if typer.confirm("No manifest found. Create one from this system now?", default=False):
        init_manifest()
        return "Manifest: created", True

    typer.echo("run popctl init when ready")
    return "Manifest: not created yet", False


def _configure_optional_features() -> list[str]:
    configured: list[str] = []
    if typer.confirm("Would you like to set up desktop alerts?", default=False):
        print_info("Run popctl alerts init-config to create the alerts configuration.")
        print_info("Then run popctl alerts install-service to install the optional service.")
        configured.append("Desktop alerts: next steps shown")
    else:
        configured.append("Desktop alerts: skipped")

    if typer.confirm("Would you like to set up encrypted backups?", default=False):
        print_info(
            "Run popctl backup init to create an encrypted backup identity and configuration."
        )
        configured.append("Encrypted backups: next step shown")
    else:
        configured.append("Encrypted backups: skipped")
    return configured


def _print_summary(configured: list[str], *, manifest_available: bool) -> None:
    console.print()
    console.print("[success]Setup summary[/]")
    for item in configured:
        console.print(f"[success]✓[/] {item}")
    console.print()
    if manifest_available:
        print_success("Next: popctl sync (or popctl doctor to re-check).")
    else:
        print_success(
            "Next: run popctl init first. popctl sync can also create a manifest on its first run."
        )


@app.callback()
def setup() -> None:
    """Guide a first-time user through popctl's non-destructive setup steps."""
    if not _is_interactive():
        _print_static_guide()
        return

    console.print("[bold]Welcome to popctl setup[/bold]")
    typer.echo("Checking core APT tools...")
    if not _check_core_binaries():
        raise typer.Exit(code=1)

    advisor_summary = _configure_advisor()
    manifest_summary, manifest_available = _configure_manifest()
    configured = [advisor_summary, manifest_summary]
    configured.extend(_configure_optional_features())
    _print_summary(configured, manifest_available=manifest_available)
