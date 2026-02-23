"""Filesystem scanning and cleanup commands.

Provides commands to scan for orphaned directories and files,
and clean up entries marked for removal in the manifest.
"""

import json
from enum import Enum
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from popctl.core.manifest import require_manifest
from popctl.filesystem.history import record_fs_deletions
from popctl.filesystem.manifest import FilesystemEntry
from popctl.filesystem.models import PathStatus, ScannedPath
from popctl.filesystem.operator import FilesystemActionResult, FilesystemOperator
from popctl.filesystem.scanner import FilesystemScanner
from popctl.utils.formatting import (
    console,
    print_error,
    print_info,
    print_success,
    print_warning,
)

app = typer.Typer(
    help="Filesystem scanning and cleanup.",
    invoke_without_command=True,
    no_args_is_help=True,
)


class OutputFormat(str, Enum):
    """Output format options for filesystem scan."""

    TABLE = "table"
    JSON = "json"


@app.command()
def scan(
    files: Annotated[
        bool,
        typer.Option("--files", help="Include individual stale files."),
    ] = False,
    include_etc: Annotated[
        bool,
        typer.Option("--include-etc", help="Include /etc in scan targets."),
    ] = False,
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
    """Scan filesystem for orphaned directories and files."""
    scanner = FilesystemScanner(include_files=files, include_etc=include_etc)

    # Collect orphans only (ORPHAN status)
    orphans: list[ScannedPath] = []
    for path in scanner.scan():
        if path.status == PathStatus.ORPHAN:
            orphans.append(path)

    if not orphans:
        print_success("Filesystem is clean. No orphaned entries found.")
        return

    # Sort by confidence descending (most confident orphans first)
    orphans.sort(key=lambda p: p.confidence, reverse=True)

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
    total_size = sum(p.size_bytes or 0 for p in orphans)
    size_str = _format_size(total_size)
    console.print(f"\n[dim]Found {len(orphans)} orphaned entries ({size_str} total)[/dim]")
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
    include_etc: Annotated[
        bool,
        typer.Option("--include-etc", help="Include /etc paths."),
    ] = False,
) -> None:
    """Clean up filesystem entries marked for removal in manifest."""
    manifest = require_manifest()

    remove_paths = manifest.get_fs_remove_paths()
    if not remove_paths:
        print_info("No filesystem entries marked for removal in manifest.")
        return

    # Filter out /etc paths unless --include-etc
    paths_to_delete: list[str] = []
    for path_str in remove_paths:
        if path_str.startswith("/etc") and not include_etc:
            print_warning(f"Skipping /etc path (use --include-etc): {path_str}")
            continue
        paths_to_delete.append(path_str)

    if not paths_to_delete:
        print_info("No filesystem entries to clean (all filtered out).")
        return

    # Display planned deletions
    _print_deletion_plan(paths_to_delete, remove_paths, dry_run)

    # Confirm unless --yes or --dry-run
    if not dry_run and not yes:
        confirmed = typer.confirm(
            f"\nProceed with deleting {len(paths_to_delete)} path(s)?",
            default=False,
        )
        if not confirmed:
            print_info("Aborted.")
            raise typer.Exit(code=0)

    # Execute deletions
    operator = FilesystemOperator(dry_run=dry_run)
    results = operator.delete(paths_to_delete)

    # Display results
    _print_deletion_results(results)

    # Record to history (only actual deletions, not dry-run)
    if not dry_run:
        successful_paths = [r.path for r in results if r.success]
        if successful_paths:
            try:
                record_fs_deletions(successful_paths, command="popctl fs clean")
                print_info("Deletions recorded to history.")
            except (OSError, RuntimeError) as e:
                print_warning(f"Could not record to history: {e}")

    # Exit with error if any deletion failed
    if any(not r.success for r in results):
        raise typer.Exit(code=1)


# === Private helper functions ===


def _print_table(orphans: list[ScannedPath]) -> None:
    """Display orphans as a Rich table."""
    table = Table(title="Orphaned Filesystem Entries", show_lines=False)
    table.add_column("Path", style="bold")
    table.add_column("Type", width=10)
    table.add_column("Size", justify="right", width=10)
    table.add_column("Confidence", justify="right", width=10)
    table.add_column("Reason", style="dim")

    for p in orphans:
        size_str = _format_size(p.size_bytes) if p.size_bytes else "-"
        conf_str = f"{p.confidence:.0%}"
        reason = p.orphan_reason.value if p.orphan_reason else "-"
        table.add_row(p.path, p.path_type.value, size_str, conf_str, reason)

    console.print(table)


def _print_json(orphans: list[ScannedPath]) -> None:
    """Display orphans as JSON."""
    data = [
        {
            "path": p.path,
            "path_type": p.path_type.value,
            "status": p.status.value,
            "size_bytes": p.size_bytes,
            "mtime": p.mtime,
            "parent_target": p.parent_target,
            "orphan_reason": p.orphan_reason.value if p.orphan_reason else None,
            "confidence": p.confidence,
            "description": p.description,
        }
        for p in orphans
    ]
    console.print_json(json.dumps(data))


def _export_results(orphans: list[ScannedPath], export_path: Path) -> None:
    """Export orphan results to a JSON file."""
    export_path = export_path.resolve()
    if export_path.is_dir():
        print_error(f"Export path is a directory: {export_path}")
        raise typer.Exit(code=1)

    data = [
        {
            "path": p.path,
            "path_type": p.path_type.value,
            "status": p.status.value,
            "size_bytes": p.size_bytes,
            "mtime": p.mtime,
            "parent_target": p.parent_target,
            "orphan_reason": p.orphan_reason.value if p.orphan_reason else None,
            "confidence": p.confidence,
        }
        for p in orphans
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
    entries: dict[str, FilesystemEntry],
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
        if isinstance(entry, FilesystemEntry) and entry.reason:
            reason = entry.reason
        table.add_row(path_str, reason)

    console.print(table)


def _print_deletion_results(results: list[FilesystemActionResult]) -> None:
    """Display deletion results."""
    table = Table(title="Deletion Results", show_lines=False)
    table.add_column("Path", style="bold")
    table.add_column("Status", width=10)
    table.add_column("Details", style="dim")

    for r in results:
        if r.dry_run:
            status = "[info]dry-run[/]"
            detail = "Would delete"
        elif r.success:
            status = "[success]deleted[/]"
            detail = ""
        else:
            status = "[error]failed[/]"
            detail = r.error or "Unknown error"
        table.add_row(r.path, status, detail)

    console.print(table)

    success_count = sum(1 for r in results if r.success)
    fail_count = sum(1 for r in results if not r.success and not r.dry_run)
    dry_count = sum(1 for r in results if r.dry_run)

    if dry_count:
        print_info(f"Dry-run: {dry_count} path(s) would be deleted.")
    elif fail_count:
        print_warning(f"{success_count} succeeded, {fail_count} failed")
    else:
        print_success(f"All {success_count} path(s) processed successfully.")


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
