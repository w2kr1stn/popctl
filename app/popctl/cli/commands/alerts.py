"""popctl alerts — desktop reminder notifications from a WebSocket alert sink."""

import logging
import os
import sys
from contextlib import suppress
from datetime import datetime
from importlib import resources
from pathlib import Path
from shutil import which
from uuid import uuid4

import typer

from popctl.alerts import daemon, notifier
from popctl.alerts.config import AlertsConfig, get_alerts_config_path, load_alerts_config
from popctl.alerts.protocol import Alert
from popctl.utils.formatting import print_error, print_info, print_success, print_warning
from popctl.utils.shell import run_command

app = typer.Typer(
    help="Desktop reminder alerts (client for a compatible WebSocket alert sink).",
    no_args_is_help=True,
)

_KINDS = ("preeve", "pre", "warning")
_TEMPLATES_PACKAGE = "popctl.data"
_SERVICE_TEMPLATE_TOKEN = "{{ popctl_executable }}"


def _read_template(name: str) -> str:
    return (
        resources.files(_TEMPLATES_PACKAGE)
        .joinpath("templates")
        .joinpath(name)
        .read_text(encoding="utf-8")
    )


def _resolve_popctl_executable() -> str:
    executable = which("popctl") or sys.argv[0]
    return str(Path(executable).expanduser().resolve())


def _get_service_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / "popctl-alerts.service"


@app.command()
def watch() -> None:
    """Connect to the alert sink and show desktop notifications until interrupted."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    try:
        config = load_alerts_config()
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    try:
        daemon.run(config)
    except KeyboardInterrupt:
        raise typer.Exit(0) from None


@app.command("init-config")
def init_config(
    force: bool = typer.Option(False, "--force", help="Overwrite an existing alerts config."),
) -> None:
    """Create an editable alerts config from the packaged template."""
    config_path = get_alerts_config_path()
    if config_path.exists() and not force:
        print_error(
            f"Alerts config already exists at {config_path}. Use --force to overwrite it."
        )
        raise typer.Exit(code=1)

    if force and config_path.exists():
        print_warning(f"Overwriting existing alerts config: {config_path}")

    temporary_path: Path | None = None
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        if config_path.exists():
            config_path.chmod(0o600)

        temporary_path = config_path.with_name(f".{config_path.name}.{uuid4().hex}.tmp")
        file_descriptor = os.open(
            temporary_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(file_descriptor, "w", encoding="utf-8") as f:
            f.write(_read_template("alerts.toml"))
            f.flush()
            os.fsync(f.fileno())
        temporary_path.chmod(0o600)
        os.replace(temporary_path, config_path)
    except OSError as exc:
        if temporary_path is not None:
            with suppress(OSError):
                temporary_path.unlink(missing_ok=True)
        print_error(f"Could not write alerts config at {config_path}: {exc}")
        raise typer.Exit(code=1) from exc

    print_success(f"Created alerts config: {config_path}")
    print_info("Next: edit ws_url (the alert server address) before running popctl alerts watch.")


@app.command("install-service")
def install_service(
    force: bool = typer.Option(False, "--force", help="Overwrite an existing alerts service."),
) -> None:
    """Install a user systemd service for the alerts watcher."""
    service_path = _get_service_path()
    if service_path.exists() and not force:
        print_error(
            f"Alerts service already exists at {service_path}. Use --force to overwrite it."
        )
        raise typer.Exit(code=1)

    if force and service_path.exists():
        print_warning(f"Overwriting existing alerts service: {service_path}")

    try:
        service_template = _read_template("popctl-alerts.service")
        service_content = service_template.replace(
            _SERVICE_TEMPLATE_TOKEN, _resolve_popctl_executable()
        )
        if service_content == service_template:
            raise ValueError("service template has no popctl executable placeholder")
        service_path.parent.mkdir(parents=True, exist_ok=True)
        service_path.write_text(service_content, encoding="utf-8")
    except (OSError, ValueError) as exc:
        print_error(f"Could not install alerts service at {service_path}: {exc}")
        raise typer.Exit(code=1) from exc

    print_success(f"Installed alerts service: {service_path}")
    try:
        reload_result = run_command(["systemctl", "--user", "daemon-reload"])
    except OSError as exc:
        print_warning(f"Could not reload user systemd units: {exc}")
        print_info("Run these commands manually when user systemd is available:")
        typer.echo("  systemctl --user daemon-reload")
        typer.echo("  systemctl --user enable --now popctl-alerts.service")
        return

    if reload_result.success:
        print_success("Reloaded user systemd units.")
        print_info("Enable the service with:")
        typer.echo("  systemctl --user enable --now popctl-alerts.service")
        return

    detail = reload_result.stderr.strip() or f"exit code {reload_result.returncode}"
    print_warning(f"Could not reload user systemd units: {detail}")
    print_info("Run these commands manually when user systemd is available:")
    typer.echo("  systemctl --user daemon-reload")
    typer.echo("  systemctl --user enable --now popctl-alerts.service")


@app.command()
def test(
    kind: str = typer.Option("warning", "--kind", "-k", help="preeve | pre | warning"),
) -> None:
    """Fire one sample notification + sound to verify the desktop setup.

    Works without a config file (uses defaults) so it can validate that the notification
    persists and a sound plays before deployment. A *malformed* config is reported as an
    error rather than silently falling back to defaults.
    """
    if kind not in _KINDS:
        raise typer.BadParameter(f"kind must be one of {', '.join(_KINDS)}")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        config = load_alerts_config()
    except FileNotFoundError:
        config = AlertsConfig(ws_url="ws://unused")
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    sample = Alert.model_validate(
        {
            "kind": kind,
            "event_id": "test",
            "title": "popctl test alert",
            "start_time": datetime.now().isoformat(),
            "online": False,
            "location": "Test",
        }
    )
    notifier.deliver(sample, config)
    typer.echo(f"Fired a '{kind}' test alert (config: {get_alerts_config_path()}).")
