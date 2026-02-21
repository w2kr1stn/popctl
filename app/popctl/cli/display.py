"""Shared Rich display functions for actions and results.

Provides reusable table builders and summary printers for displaying
planned actions and execution results across CLI commands (apply, sync).
"""

import json
from pathlib import Path
from typing import Any

import typer
from rich.table import Table

from popctl.domain.manifest import DomainEntry
from popctl.models.action import Action, ActionResult
from popctl.utils.formatting import console, print_error, print_info, print_success


def create_actions_table(actions: list[Action], dry_run: bool = False) -> Table:
    """Create a Rich table displaying planned actions.

    Builds a formatted table with Action, Source, Package, and Reason columns.
    Each action type is styled distinctly: install (added), remove (warning),
    and purge (removed).

    Args:
        actions: List of actions to display.
        dry_run: Whether this is a dry-run (changes table title).

    Returns:
        Rich Table configured for action display.
    """
    title = "Planned Actions (Dry Run)" if dry_run else "Planned Actions"

    table = Table(
        title=title,
        show_header=True,
        header_style="bold_header",
        border_style="border",
    )
    table.add_column("Action", width=8, justify="center")
    table.add_column("Source", width=8)
    table.add_column("Package", no_wrap=True)
    table.add_column("Reason")

    for action in actions:
        # Style based on action type
        if action.is_install:
            action_text = "[added]+install[/added]"
            pkg_style = "added"
        elif action.is_purge:
            action_text = "[removed]-purge[/removed]"
            pkg_style = "removed"
        else:  # REMOVE
            action_text = "[warning]-remove[/warning]"
            pkg_style = "warning"

        table.add_row(
            action_text,
            action.source.value,
            f"[{pkg_style}]{action.package}[/{pkg_style}]",
            f"[muted]{action.reason or ''}[/muted]",
        )

    return table


def create_results_table(results: list[ActionResult]) -> Table:
    """Create a Rich table displaying action results.

    Builds a formatted table with Status, Action, Package, and Message columns.
    Successful results show "OK" status; failed results show "FAIL" with the
    error message.

    Args:
        results: List of action results to display.

    Returns:
        Rich Table configured for results display.
    """
    table = Table(
        title="Results",
        show_header=True,
        header_style="bold_header",
        border_style="border",
    )
    table.add_column("Status", width=8, justify="center")
    table.add_column("Action", width=8)
    table.add_column("Package", no_wrap=True)
    table.add_column("Message")

    for result in results:
        if result.success:
            status = "[success]OK[/success]"
            message = result.message or ""
        else:
            status = "[error]FAIL[/error]"
            message = result.error or "Unknown error"

        action_type = result.action.action_type.value

        table.add_row(
            status,
            action_type,
            result.action.package,
            f"[muted]{message}[/muted]",
        )

    return table


def print_actions_summary(actions: list[Action]) -> None:
    """Print a summary of planned actions.

    Displays counts of install, remove, and purge actions using Rich markup.
    If no actions are provided, produces no output.

    Args:
        actions: List of planned actions.
    """
    install_count = sum(1 for a in actions if a.is_install)
    remove_count = sum(1 for a in actions if a.is_remove)
    purge_count = sum(1 for a in actions if a.is_purge)

    parts: list[str] = []
    if install_count:
        parts.append(f"[added]{install_count} to install[/added]")
    if remove_count:
        parts.append(f"[warning]{remove_count} to remove[/warning]")
    if purge_count:
        parts.append(f"[removed]{purge_count} to purge[/removed]")

    if parts:
        summary = ", ".join(parts)
        console.print(f"\nSummary: {summary}")


def print_results_summary(results: list[ActionResult]) -> None:
    """Print a summary of action results.

    Shows a success message when all actions succeed, or a count of
    succeeded/failed actions when there are failures.

    Args:
        results: List of action results.
    """
    success_count = sum(1 for r in results if r.success)
    fail_count = sum(1 for r in results if r.failed)

    if fail_count == 0:
        print_success(f"All {success_count} action(s) completed successfully.")
    else:
        console.print(
            f"\n[success]{success_count} succeeded[/success], [error]{fail_count} failed[/error]"
        )


def print_deletion_plan(
    paths: list[str],
    entries: dict[str, DomainEntry],
    dry_run: bool,
) -> None:
    """Display planned deletions as a Rich table.

    Used by both filesystem and config clean commands.

    Args:
        paths: List of paths to be deleted.
        entries: Mapping of path strings to DomainEntry with reason metadata.
        dry_run: Whether this is a dry-run (changes table title).
    """
    label = "Planned Deletions (dry-run)" if dry_run else "Planned Deletions"
    table = Table(title=label, show_lines=False)
    table.add_column("Path", style="bold")
    table.add_column("Reason", style="dim")

    for path_str in paths:
        entry = entries.get(path_str)
        reason = "-"
        if isinstance(entry, DomainEntry) and entry.reason:
            reason = entry.reason
        table.add_row(path_str, reason)

    console.print(table)


def export_orphan_results(data: list[dict[str, Any]], export_path: Path) -> None:
    """Export pre-serialized orphan results to a JSON file.

    Handles path resolution, directory creation, and error reporting.
    Used by both filesystem and config scan commands.

    Args:
        data: Pre-serialized list of dicts to write as JSON.
        export_path: Target file path for the JSON export.

    Raises:
        typer.Exit: If export path is a directory or write fails.
    """
    export_path = export_path.resolve()
    if export_path.is_dir():
        print_error(f"Export path is a directory: {export_path}")
        raise typer.Exit(code=1)

    try:
        export_path.parent.mkdir(parents=True, exist_ok=True)
        export_path.write_text(json.dumps(data, indent=2))
        print_info(f"Results exported to {export_path}")
    except OSError as e:
        print_error(f"Failed to export: {e}")
        raise typer.Exit(code=1) from e
