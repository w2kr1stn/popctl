"""Undo command for reverting last action.

This module provides the `popctl undo` command for reversing the most
recent reversible package management action.
"""

from typing import Annotated

import typer

from popctl.core.state import StateManager
from popctl.models.action import Action, ActionType
from popctl.models.history import HistoryActionType, HistoryEntry
from popctl.models.package import PackageSource
from popctl.operators.apt import AptOperator
from popctl.operators.flatpak import FlatpakOperator
from popctl.utils.formatting import console, print_error, print_info, print_success

app = typer.Typer(
    name="undo",
    help="Undo the last reversible action.",
    invoke_without_command=True,
)


@app.callback(invoke_without_command=True)
def undo(
    ctx: typer.Context,
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
    if ctx.invoked_subcommand is not None:
        return

    state = StateManager()
    entry = state.get_last_reversible()

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
        state.mark_entry_reversed(entry.id)
        print_success("Action undone successfully.")
    else:
        print_error("Failed to undo action. Check the output above.")
        raise typer.Exit(code=1)


def _show_undo_preview(entry: HistoryEntry) -> None:
    """Display preview of undo action.

    Shows details about what action will be reversed, including the
    action type, entry ID, timestamp, and affected packages.

    Args:
        entry: The history entry to preview.
    """
    inverse = _get_inverse_action_name(entry.action_type)

    console.print(f"\n[bold]Undo: {entry.action_type.value} -> {inverse}[/bold]")
    console.print(f"  ID: {entry.id[:8]}")
    console.print(f"  Date: {entry.timestamp}")
    console.print(f"  Packages ({len(entry.items)}):")
    for item in entry.items[:10]:
        console.print(f"    - {item.name} ({item.source.value})")
    if len(entry.items) > 10:
        console.print(f"    ... and {len(entry.items) - 10} more")
    console.print()


def _get_inverse_action_name(action_type: HistoryActionType) -> str:
    """Get the inverse action name for display.

    Maps each action type to its corresponding undo action name.

    Args:
        action_type: The original action type.

    Returns:
        Human-readable name of the inverse action.
    """
    inverse_map = {
        HistoryActionType.INSTALL: "remove",
        HistoryActionType.REMOVE: "install",
        HistoryActionType.PURGE: "install",
    }
    return inverse_map.get(action_type, "unknown")


def _execute_undo(entry: HistoryEntry) -> bool:
    """Execute the inverse actions.

    Groups packages by source and executes the appropriate inverse
    operation for each group.

    Args:
        entry: The history entry to undo.

    Returns:
        True if all operations succeeded, False otherwise.
    """
    from popctl.core.baseline import is_protected
    from popctl.models.action import ActionResult

    # Group items by source
    apt_items = [i for i in entry.items if i.source == PackageSource.APT]
    flatpak_items = [i for i in entry.items if i.source == PackageSource.FLATPAK]

    all_results: list[ActionResult] = []

    # Determine inverse action type
    if entry.action_type == HistoryActionType.INSTALL:
        inverse_action = ActionType.REMOVE
    else:  # REMOVE or PURGE
        inverse_action = ActionType.INSTALL

    # Filter out protected packages for REMOVE actions (defense in depth)
    if inverse_action == ActionType.REMOVE:
        apt_items = [i for i in apt_items if not is_protected(i.name)]

    # Execute APT
    if apt_items:
        operator = AptOperator(dry_run=False)
        actions = [
            Action(action_type=inverse_action, package=item.name, source=item.source)
            for item in apt_items
        ]
        results = operator.execute(actions)
        all_results.extend(results)

    # Execute Flatpak
    if flatpak_items:
        operator = FlatpakOperator(dry_run=False)
        actions = [
            Action(action_type=inverse_action, package=item.name, source=item.source)
            for item in flatpak_items
        ]
        results = operator.execute(actions)
        all_results.extend(results)

    # Report failed packages
    failed_results = [r for r in all_results if not r.success]
    if failed_results:
        console.print("\n[error]Failed packages:[/error]")
        for result in failed_results:
            error_msg = result.error or "Unknown error"
            console.print(f"  - {result.action.package}: {error_msg}")
        console.print()

    return len(failed_results) == 0
