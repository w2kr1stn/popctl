from typing import Annotated

import typer

from popctl.core.baseline import is_package_protected
from popctl.core.executor import execute_actions
from popctl.core.state import INVERSE_ACTION_TYPES, get_last_reversible, mark_entry_reversed
from popctl.models.action import Action, ActionType
from popctl.models.history import HistoryActionType, HistoryEntry
from popctl.operators import get_available_operators
from popctl.utils.formatting import console, print_error, print_info, print_success

app = typer.Typer(
    name="undo",
    help="Undo the last reversible action.",
    invoke_without_command=True,
)


@app.callback(invoke_without_command=True)
def undo(
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            "-n",
            help="Show what would be undone without executing.",
        ),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="Skip confirmation prompt.",
        ),
    ] = False,
) -> None:
    """Undo the last reversible action.

    Reverses the most recent action that can be undone:
    - install -> remove
    - remove -> install
    - purge -> install (config lost)

    Examples:
        popctl undo              # Undo with confirmation
        popctl undo --dry-run    # Preview only
        popctl undo -y           # Skip confirmation
    """
    entry = get_last_reversible()

    if entry is None:
        print_info("No reversible actions in history.")
        return

    # Show what will be undone
    _show_undo_preview(entry)

    if dry_run:
        print_info("[dry-run] No changes made.")
        return

    # Confirm
    if not yes:
        confirm = typer.confirm("Do you want to undo this action?")
        if not confirm:
            print_info("Cancelled.")
            return

    # Execute undo
    success = _execute_undo(entry)

    if success:
        mark_entry_reversed(entry)
        print_success("Action undone successfully.")
    else:
        print_error("Failed to undo action. Check the output above.")
        raise typer.Exit(code=1)


def _show_undo_preview(entry: HistoryEntry) -> None:
    inverse_type = INVERSE_ACTION_TYPES.get(entry.action_type)
    inverse = inverse_type.value if inverse_type else "unknown"

    console.print(f"\n[bold]Undo: {entry.action_type.value} -> {inverse}[/bold]")
    console.print(f"  ID: {entry.id[:8]}")
    console.print(f"  Date: {entry.timestamp}")
    console.print(f"  Packages ({len(entry.items)}):")
    for item in entry.items[:10]:
        source_label = item.source.value if item.source else "unknown"
        console.print(f"    - {item.name} ({source_label})")
    if len(entry.items) > 10:
        console.print(f"    ... and {len(entry.items) - 10} more")
    console.print()


def _execute_undo(entry: HistoryEntry) -> bool:
    # Determine inverse action type
    if entry.action_type == HistoryActionType.INSTALL:
        inverse_action = ActionType.REMOVE
    else:  # REMOVE or PURGE
        inverse_action = ActionType.INSTALL

    # Build actions from history items (only items with a package source)
    actions = [
        Action(action_type=inverse_action, package=item.name, source=item.source)
        for item in entry.items
        if item.source is not None
    ]

    # Filter out protected packages for REMOVE actions (defense in depth)
    if inverse_action == ActionType.REMOVE:
        actions = [a for a in actions if not is_package_protected(a.package)]

    if not actions:
        return True

    # Execute using shared pipeline
    operators = get_available_operators(dry_run=False)
    results = execute_actions(actions, operators)

    # Report failed packages
    failed_results = [r for r in results if not r.success]
    if failed_results:
        console.print("\n[error]Failed packages:[/error]")
        for result in failed_results:
            error_msg = result.detail or "Unknown error"
            console.print(f"  - {result.action.package}: {error_msg}")
        console.print()

    return len(failed_results) == 0
