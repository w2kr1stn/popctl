from typing import Annotated

import typer

from popctl.cli.display import (
    create_actions_table,
    create_results_table,
    print_actions_summary,
    print_results_summary,
)
from popctl.cli.types import SourceChoice, compute_system_diff
from popctl.core.diff import diff_to_actions
from popctl.core.executor import execute_actions, record_actions_to_history
from popctl.operators import get_available_operators
from popctl.utils.formatting import (
    console,
    print_info,
    print_success,
)

app = typer.Typer(
    help="Apply manifest to system.",
    invoke_without_command=True,
)


@app.callback(invoke_without_command=True)
def apply_manifest(
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="Skip confirmation prompt and proceed.",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            "-n",
            help="Show what would be done without making changes.",
        ),
    ] = False,
    source: Annotated[
        SourceChoice,
        typer.Option(
            "--source",
            "-s",
            help="Package source to apply: apt, flatpak, snap, or all.",
            case_sensitive=False,
        ),
    ] = SourceChoice.ALL,
    purge: Annotated[
        bool,
        typer.Option(
            "--purge",
            "-p",
            help="Use purge instead of remove for APT/Snap packages (removes config files).",
        ),
    ] = False,
) -> None:
    """Apply manifest to system.

    Installs missing packages and removes extra packages based on the
    manifest configuration.

    Actions performed:
      - MISSING packages (in manifest, not installed): Install
      - EXTRA packages (marked for removal, still installed): Remove/Purge

    NEW packages (installed but not in manifest) are NOT removed automatically.
    You must explicitly add them to the manifest's remove list.

    Protected system packages are never removed regardless of manifest settings.

    Examples:
        popctl apply --dry-run          # Preview changes
        popctl apply --yes              # Apply without confirmation
        popctl apply --source apt       # Only APT packages
        popctl apply --purge            # Remove APT packages with configs
    """
    # Compute diff (exits on failure)
    diff_result = compute_system_diff(source)

    # Convert diff to actions
    actions = diff_to_actions(diff_result, purge=purge)

    # Check if there's anything to do
    if not actions:
        print_success("System is already in sync with manifest. Nothing to do.")
        return

    # Show planned actions
    table = create_actions_table(actions, dry_run)
    console.print(table)
    print_actions_summary(actions)

    # If dry-run, stop here
    if dry_run:
        print_info("\nDry-run mode: No changes were made.")
        return

    # Confirm unless --yes was provided
    if not yes and not typer.confirm(f"\nProceed with {len(actions)} action(s)?", default=False):
        print_info("Aborted.")
        raise typer.Exit(code=0)

    # Get available operators (filters out unavailable package managers)
    available_operators = get_available_operators(source.to_package_source())

    # Execute actions
    console.print("\n[bold]Executing actions...[/bold]\n")

    results = execute_actions(actions, available_operators)

    # Record successful actions to history
    if results:
        record_actions_to_history(results)
        print_info("Actions recorded to history.")

    # Show results
    results_table = create_results_table(results)
    console.print(results_table)
    print_results_summary(results)

    # Exit with error code if any action failed
    if any(r.failed for r in results):
        raise typer.Exit(code=1)
