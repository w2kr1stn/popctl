import json
from datetime import datetime
from typing import Annotated

import typer
from rich.table import Table

from popctl.core.state import get_history
from popctl.models.history import HistoryEntry
from popctl.utils.formatting import console, print_error, print_info, print_warning

app = typer.Typer(
    name="history",
    help="View history of package changes.",
    invoke_without_command=True,
)


@app.callback(invoke_without_command=True)
def history(
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
    # Validate since format before passing to core
    if since:
        try:
            datetime.fromisoformat(since)
        except ValueError:
            print_error(f"Invalid date format: {since}. Use YYYY-MM-DD.")
            raise typer.Exit(code=1) from None

    entries, corrupt_count = get_history(limit=limit, since=since)

    if corrupt_count > 0:
        print_warning(f"{corrupt_count} corrupt history line(s) were skipped.")

    if not entries:
        print_info("No history entries found.")
        return

    if json_output:
        _print_json(entries)
    else:
        _print_table(entries)


def _print_table(entries: list[HistoryEntry]) -> None:
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
            datetime.fromisoformat(entry.timestamp.replace("Z", "+00:00")).strftime(
                "%Y-%m-%d %H:%M"
            ),
            entry.action_type.value,
            pkg_names,
            "[green]Yes[/]" if entry.reversible else "[red]No[/]",
        )

    console.print(table)


def _print_json(entries: list[HistoryEntry]) -> None:
    output = [entry.to_dict() for entry in entries]
    console.print_json(json.dumps(output, indent=2))
