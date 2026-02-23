"""Config scanning and cleanup commands.

Provides commands to scan for orphaned configuration files and dotfiles,
and clean up entries marked for removal in the manifest.
"""

import json
from enum import Enum
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from popctl.configs.history import record_config_deletions
from popctl.configs.manifest import ConfigEntry
from popctl.configs.models import ConfigStatus, ScannedConfig
from popctl.configs.operator import ConfigActionResult, ConfigOperator
from popctl.configs.protected import is_protected_config
from popctl.configs.scanner import ConfigScanner
from popctl.core.manifest import require_manifest
from popctl.utils.formatting import (
    console,
    print_error,
    print_info,
    print_success,
    print_warning,
)

app = typer.Typer(
    help="Scan and clean orphaned configuration files.",
    invoke_without_command=True,
    no_args_is_help=True,
)


class OutputFormat(str, Enum):
    """Output format options for config scan."""

    TABLE = "table"
    JSON = "json"


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
    scanner = ConfigScanner()

    # Collect orphans only (ORPHAN status -- scanner already filters, but be explicit)
    orphans: list[ScannedConfig] = []
    for config in scanner.scan():
        if config.status == ConfigStatus.ORPHAN:
            orphans.append(config)

    if not orphans:
        print_success("Configs are clean. No orphaned configurations found.")
        return

    # Sort by confidence descending (most confident orphans first)
    orphans.sort(key=lambda c: c.confidence, reverse=True)

    # Apply limit
    display_orphans = orphans[:limit] if limit else orphans

    # Handle export
    if export_path is not None:
        _export_results(orphans, export_path)  # Export ALL, not limited

    # JSON output
    if output_format == OutputFormat.JSON:
        _print_json(display_orphans)
        return

    # Table output (default)
    _print_table(display_orphans)

    # Summary
    total_size = sum(c.size_bytes or 0 for c in orphans)
    size_str = _format_size(total_size)
    console.print(f"\n[dim]Found {len(orphans)} orphaned configs ({size_str} total)[/dim]")
    if limit and len(display_orphans) < len(orphans):
        console.print(
            f"[dim](showing {len(display_orphans)} of {len(orphans)}, limited to {limit})[/dim]"
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

    remove_paths = manifest.get_config_remove_paths()
    if not remove_paths:
        print_info("No config entries marked for removal in manifest.")
        return

    # Check for protected configs
    paths_to_delete: list[str] = []
    for path_str in remove_paths:
        if is_protected_config(path_str):
            print_warning(f"Skipping protected config: {path_str}")
            continue
        paths_to_delete.append(path_str)

    if not paths_to_delete:
        print_info("No config entries to clean (all protected or filtered out).")
        return

    # Display planned deletions
    _print_deletion_plan(paths_to_delete, remove_paths, dry_run)

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
    _print_deletion_results(results)

    # Record to history (only actual deletions, not dry-run)
    if not dry_run:
        successful_paths = [r.path for r in results if r.success]
        if successful_paths:
            try:
                record_config_deletions(successful_paths, command="popctl config clean")
                print_info("Deletions recorded to history.")
            except (OSError, RuntimeError) as e:
                print_warning(f"Could not record to history: {e}")

    # Exit with error if any deletion failed
    if any(not r.success for r in results):
        raise typer.Exit(code=1)


# === Private helper functions ===


def _print_table(orphans: list[ScannedConfig]) -> None:
    """Display orphans as a Rich table."""
    table = Table(title="Orphaned Configuration Entries", show_lines=False)
    table.add_column("Path", style="bold")
    table.add_column("Type", width=10)
    table.add_column("Size", justify="right", width=10)
    table.add_column("Confidence", justify="right", width=10)
    table.add_column("Reason", style="dim")

    for c in orphans:
        size_str = _format_size(c.size_bytes) if c.size_bytes else "-"
        conf_str = f"{c.confidence:.0%}"
        reason = c.orphan_reason.value if c.orphan_reason else "-"
        table.add_row(c.path, c.config_type.value, size_str, conf_str, reason)

    console.print(table)


def _print_json(orphans: list[ScannedConfig]) -> None:
    """Display orphans as JSON."""
    data = [
        {
            "path": c.path,
            "config_type": c.config_type.value,
            "status": c.status.value,
            "size_bytes": c.size_bytes,
            "mtime": c.mtime,
            "orphan_reason": c.orphan_reason.value if c.orphan_reason else None,
            "confidence": c.confidence,
            "description": c.description,
        }
        for c in orphans
    ]
    console.print_json(json.dumps(data))


def _export_results(orphans: list[ScannedConfig], export_path: Path) -> None:
    """Export orphan results to a JSON file."""
    export_path = export_path.resolve()
    if export_path.is_dir():
        print_error(f"Export path is a directory: {export_path}")
        raise typer.Exit(code=1)

    data = [
        {
            "path": c.path,
            "config_type": c.config_type.value,
            "status": c.status.value,
            "size_bytes": c.size_bytes,
            "mtime": c.mtime,
            "orphan_reason": c.orphan_reason.value if c.orphan_reason else None,
            "confidence": c.confidence,
        }
        for c in orphans
    ]
    try:
        export_path.parent.mkdir(parents=True, exist_ok=True)
        export_path.write_text(json.dumps(data, indent=2))
        print_info(f"Results exported to {export_path}")
    except OSError as e:
        print_error(f"Failed to export: {e}")
        raise typer.Exit(code=1) from e


def _print_deletion_plan(
    paths: list[str],
    entries: dict[str, ConfigEntry],
    dry_run: bool,
) -> None:
    """Display planned deletions."""
    label = "Planned Deletions (dry-run)" if dry_run else "Planned Deletions"
    table = Table(title=label, show_lines=False)
    table.add_column("Path", style="bold")
    table.add_column("Reason", style="dim")

    for path_str in paths:
        entry = entries.get(path_str)
        reason = "-"
        if isinstance(entry, ConfigEntry) and entry.reason:
            reason = entry.reason
        table.add_row(path_str, reason)

    console.print(table)


def _print_deletion_results(results: list[ConfigActionResult]) -> None:
    """Display deletion results with backup paths."""
    table = Table(title="Deletion Results", show_lines=False)
    table.add_column("Path", style="bold")
    table.add_column("Status", width=10)
    table.add_column("Backup", style="dim")

    for r in results:
        if r.dry_run:
            status = "[info]dry-run[/]"
            backup = "-"
        elif r.success:
            status = "[success]deleted[/]"
            backup = r.backup_path or "no backup"
        else:
            status = "[error]failed[/]"
            backup = r.error or "Unknown error"
        table.add_row(r.path, status, backup)

    console.print(table)

    success_count = sum(1 for r in results if r.success)
    fail_count = sum(1 for r in results if not r.success and not r.dry_run)
    dry_count = sum(1 for r in results if r.dry_run)

    if dry_count:
        print_info(f"Dry-run: {dry_count} config(s) would be deleted.")
    elif fail_count:
        print_warning(f"{success_count} succeeded, {fail_count} failed")
    else:
        print_success(f"All {success_count} config(s) processed successfully.")


def _format_size(size_bytes: int | None) -> str:
    """Format byte count as human-readable string."""
    if size_bytes is None or size_bytes == 0:
        return "0 B"
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if abs(size) < 1024:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TB"
