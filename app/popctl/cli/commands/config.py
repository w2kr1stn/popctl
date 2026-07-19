import os
import shlex
import sys
import tomllib
from datetime import date, datetime, time
from pathlib import Path
from typing import Annotated, cast

import tomli_w
import typer

from popctl.alerts.config import get_alerts_config_path
from popctl.cli.display import (
    display_orphan_scan,
    print_deletion_plan,
    print_deletion_results,
)
from popctl.cli.types import (
    OutputFormat,
    collect_domain_orphans,
    post_clean_update,
    require_manifest,
)
from popctl.configs import ConfigOperator
from popctl.core.paths import get_config_dir, get_manifest_path
from popctl.domain.protected import is_protected
from popctl.utils.formatting import (
    print_info,
    print_success,
    print_warning,
)
from popctl.utils.shell import run_interactive

app = typer.Typer(
    help="Scan and clean orphaned configuration files.",
    invoke_without_command=True,
    no_args_is_help=True,
)

_CONFIG_CREATION_COMMANDS = {
    "manifest": "popctl init",
    "advisor": "popctl setup",
    "alerts": "popctl alerts init-config",
    "backup": "popctl backup init",
    "dotfiles": "popctl dotfiles init",
    "theme": "popctl config edit theme",
}
type TomlValue = (
    str
    | int
    | float
    | bool
    | date
    | datetime
    | time
    | list[TomlValue]
    | dict[str, TomlValue]
)
type TomlTable = dict[str, TomlValue]
def _config_locations() -> dict[str, Path]:
    config_dir = get_config_dir()
    return {
        "manifest": get_manifest_path(),
        "advisor": config_dir / "advisor.toml",
        "alerts": get_alerts_config_path(),
        "backup": config_dir / "backup.toml",
        "dotfiles": config_dir / "dotfiles.toml",
        "theme": config_dir / "theme.toml",
    }


def _get_config_path(name: str) -> Path:
    config_path = _config_locations().get(name)
    if config_path is not None:
        return config_path

    valid_names = ", ".join(_CONFIG_CREATION_COMMANDS)
    typer.echo(f"Unknown config name {name!r}. Choose one of: {valid_names}.", err=True)
    raise typer.Exit(code=2)


def _redact_api_key_values(value: TomlValue) -> None:
    if isinstance(value, dict):
        for key, nested_value in value.items():
            if key == "api_key":
                value[key] = "********"
            else:
                _redact_api_key_values(nested_value)
    elif isinstance(value, list):
        for item in value:
            _redact_api_key_values(item)


def _redact_advisor_api_key(contents: str) -> str | None:
    try:
        data = cast(TomlTable, tomllib.loads(contents))
    except tomllib.TOMLDecodeError:
        return None

    _redact_api_key_values(data)
    return tomli_w.dumps(data)


@app.command()
def path() -> None:
    """Show the locations and current existence of popctl configuration files."""
    print_info("popctl configuration paths:")
    for name, config_path in _config_locations().items():
        status = "exists" if config_path.exists() else "missing"
        typer.echo(f"  {name}: {config_path} ({status})")


@app.command()
def show(
    name: Annotated[
        str | None,
        typer.Argument(
            help="Config to show: manifest, advisor, alerts, backup, dotfiles, or theme."
        ),
    ] = None,
) -> None:
    """Print one configuration file, redacting the advisor API key."""
    if name is None:
        path()
        typer.echo("Pass a name to show its contents: popctl config show <name>.")
        return

    config_path = _get_config_path(name)
    if not config_path.exists():
        command = _CONFIG_CREATION_COMMANDS[name]
        typer.echo(
            f"The {name} config has not been created yet. Create it with `{command}`."
        )
        return

    try:
        contents = config_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        if name == "advisor":
            typer.echo(
                "The advisor config file exists but is not valid TOML — fix or recreate it "
                "via popctl setup."
            )
            return
        raise
    except OSError as error:
        typer.echo(f"Could not read {name} config at {config_path}: {error}", err=True)
        raise typer.Exit(code=1) from error

    if name == "advisor":
        contents = _redact_advisor_api_key(contents)
        if contents is None:
            typer.echo(
                "The advisor config file exists but is not valid TOML — fix or recreate it "
                "via popctl setup."
            )
            return
    typer.echo(contents, nl=not contents.endswith("\n"))


@app.command()
def edit(
    name: Annotated[
        str,
        typer.Argument(
            help="Config to edit: manifest, advisor, alerts, backup, dotfiles, or theme."
        ),
    ],
) -> None:
    """Open one configuration file in the configured editor."""
    config_path = _get_config_path(name)
    if name == "dotfiles" and not config_path.exists():
        command = _CONFIG_CREATION_COMMANDS[name]
        typer.echo(
            f"The {name} config has not been created yet. Create it with `{command}`."
        )
        return
    if not sys.stdin.isatty():
        typer.echo(
            f"Cannot open an editor without an interactive terminal. Config path: {config_path}",
            err=True,
        )
        raise typer.Exit(code=1)

    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        typer.echo(f"Could not create config directory {config_path.parent}: {error}", err=True)
        raise typer.Exit(code=1) from error

    editor = os.environ.get("EDITOR") or os.environ.get("VISUAL") or "nano"
    try:
        editor_args = shlex.split(editor)
    except ValueError as error:
        typer.echo(f"Invalid editor command {editor!r}: {error}", err=True)
        raise typer.Exit(code=1) from error
    if not editor_args:
        typer.echo("No editor command is configured.", err=True)
        raise typer.Exit(code=1)

    try:
        exit_code = run_interactive([*editor_args, str(config_path)])
    except OSError as error:
        typer.echo(f"Could not launch editor {editor_args[0]!r}: {error}", err=True)
        raise typer.Exit(code=1) from error
    if exit_code:
        raise typer.Exit(code=exit_code)


@app.command()
def scan(
    output_format: Annotated[
        OutputFormat,
        typer.Option(
            "--format",
            "-f",
            help="Output format.",
            case_sensitive=False,
        ),
    ] = OutputFormat.TABLE,
    export_path: Annotated[
        Path | None,
        typer.Option(
            "--export",
            "-e",
            help="Export results to JSON file.",
        ),
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option(
            "--limit",
            "-l",
            help="Limit number of results.",
        ),
    ] = None,
) -> None:
    """Scan ~/.config/ and shell dotfiles for orphaned configurations."""
    orphans = collect_domain_orphans("configs")

    if not orphans:
        print_success("Configs are clean. No orphaned configurations found.")
        return

    display_orphan_scan(
        "configuration",
        orphans,
        output_format=output_format.value,
        export_path=export_path,
        limit=limit,
        summary_noun="configs",
    )


@app.command()
def clean(
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show what would be deleted."),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip confirmation prompt."),
    ] = False,
) -> None:
    """Clean up config entries marked for removal in manifest."""
    manifest = require_manifest()

    remove_paths = manifest.get_domain_remove("configs")
    if not remove_paths:
        print_info("No config entries marked for removal in manifest.")
        return

    # Check for protected configs
    paths_to_delete: list[str] = []
    for path_str in remove_paths:
        if is_protected(path_str, "configs"):
            print_warning(f"Skipping protected config: {path_str}")
            continue
        paths_to_delete.append(path_str)

    if not paths_to_delete:
        print_info("No config entries to clean (all protected or filtered out).")
        return

    # Display planned deletions
    print_deletion_plan(paths_to_delete, remove_paths, dry_run)

    # Confirm unless --yes or --dry-run
    if not dry_run and not yes:
        confirmed = typer.confirm(
            f"\nProceed with deleting {len(paths_to_delete)} config(s)?",
            default=False,
        )
        if not confirmed:
            print_info("Aborted.")
            raise typer.Exit(code=0)

    # Execute deletions
    operator = ConfigOperator(dry_run=dry_run)
    results = operator.delete(paths_to_delete)

    # Display results
    print_deletion_results(results, show_backup=True)

    # Record to history and update manifest (only actual deletions, not dry-run)
    if not dry_run:
        post_clean_update(
            manifest, "configs", results, paths_to_delete, command="popctl config clean"
        )

    # Exit with error if any deletion failed
    if any(not r.success for r in results):
        raise typer.Exit(code=1)
