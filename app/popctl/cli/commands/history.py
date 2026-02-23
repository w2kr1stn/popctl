"""History command for viewing past actions.

This module provides the `popctl history` command for viewing
the history of package management operations.
"""

import json
from datetime import datetime
from typing import Annotated

import typer
from rich.table import Table

from popctl.core.state import StateManager
from popctl.models.history import HistoryEntry
from popctl.utils.formatting import console, print_info

app = typer.Typer(
    name="history",
    help="View history of package changes.",
    invoke_without_command=True,
)


@app.callback(invoke_without_command=True)
def history(
    ctx: typer.Context,
    limit: Annotated[
        int,
        typer.Option(
            "--limit",
            "-n",
            help="Maximum number of entries to show.",
        ),
    ] = 20,
    since: Annotated[
        str | None,
        typer.Option(
            "--since",
            help="Show entries since date (YYYY-MM-DD).",
        ),
    ] = None,
    json_output: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Output as JSON.",
        ),
    ] = False,
) -> None:
    """Show history of package changes.

    Displays a list of past package management operations that have been
    recorded by popctl. Each entry shows the action type, affected packages,
    timestamp, and whether the action can be undone.

    Examples:
        popctl history              # Show last 20 entries
        popctl history -n 50        # Show last 50 entries
        popctl history --since 2026-01-01
        popctl history --json       # JSON output for scripting
    """
    if ctx.invoked_subcommand is not None:
        return

    state = StateManager()
    entries = state.get_history(limit=limit)

    # Apply since filter if provided
    if since:
        try:
            # Parse since date and make it timezone-aware (UTC) for comparison
            since_parsed = datetime.fromisoformat(since)
            # If no timezone info, treat as start of day UTC
            if since_parsed.tzinfo is None:
                since_date = since_parsed.strftime("%Y-%m-%d")
                entries = [e for e in entries if e.timestamp[:10] >= since_date]
            else:
                # Both have timezone info, can compare directly
                entries = [
                    e
                    for e in entries
                    if datetime.fromisoformat(e.timestamp.replace("Z", "+00:00")) >= since_parsed
                ]
        except ValueError:
            typer.echo(f"Invalid date format: {since}. Use YYYY-MM-DD.", err=True)
            raise typer.Exit(code=1) from None

    if not entries:
        print_info("No history entries found.")
        return

    if json_output:
        _print_json(entries)
    else:
        _print_table(entries)


def _print_table(entries: list[HistoryEntry]) -> None:
    """Print history as Rich table.

    Creates a formatted table showing history entries with columns for
    ID, timestamp, action type, packages, and undo availability.

    Args:
        entries: List of history entries to display.
    """
    table = Table(title="Package History")
    table.add_column("ID", style="dim")
    table.add_column("Timestamp", style="cyan")
    table.add_column("Action", style="green")
    table.add_column("Packages", style="white")
    table.add_column("Undo?", style="yellow")

    for entry in entries:
        pkg_count = len(entry.items)
        pkg_names = ", ".join(item.name for item in entry.items[:3])
        if pkg_count > 3:
            pkg_names += f" (+{pkg_count - 3} more)"

        table.add_row(
            entry.id[:8],
            _format_timestamp(entry.timestamp),
            entry.action_type.value,
            pkg_names,
            "[green]Yes[/]" if entry.reversible else "[red]No[/]",
        )

    console.print(table)


def _format_timestamp(iso_timestamp: str) -> str:
    """Format ISO timestamp for display.

    Converts an ISO 8601 timestamp to a human-readable format.

    Args:
        iso_timestamp: ISO format timestamp string.

    Returns:
        Formatted timestamp string (YYYY-MM-DD HH:MM).
    """
    dt = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
    return dt.strftime("%Y-%m-%d %H:%M")


def _print_json(entries: list[HistoryEntry]) -> None:
    """Print history as JSON.

    Outputs history entries as formatted JSON for scripting and
    integration with other tools.

    Args:
        entries: List of history entries to output.
    """
    output = [entry.to_dict() for entry in entries]
    console.print(json.dumps(output, indent=2))
