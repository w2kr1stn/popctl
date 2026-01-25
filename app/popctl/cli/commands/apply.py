"""Apply command implementation.

Applies the manifest to the system by installing missing packages
and removing extra packages.
"""

from enum import Enum
from typing import Annotated

import typer
from rich.table import Table

from popctl.core.baseline import is_protected
from popctl.core.diff import DiffEngine, DiffResult
from popctl.core.manifest import (
    ManifestError,
    ManifestNotFoundError,
    load_manifest,
)
from popctl.core.paths import get_manifest_path
from popctl.models.action import (
    Action,
    ActionResult,
    create_install_action,
    create_remove_action,
)
from popctl.models.package import PackageSource
from popctl.operators.apt import AptOperator
from popctl.operators.base import Operator
from popctl.operators.flatpak import FlatpakOperator
from popctl.scanners.apt import AptScanner
from popctl.scanners.base import Scanner
from popctl.scanners.flatpak import FlatpakScanner
from popctl.utils.formatting import (
    console,
    print_error,
    print_info,
    print_success,
    print_warning,
)

app = typer.Typer(
    help="Apply manifest to system.",
    invoke_without_command=True,
)


class SourceChoice(str, Enum):
    """Available package sources for apply filtering."""

    APT = "apt"
    FLATPAK = "flatpak"
    ALL = "all"


def _get_scanners(source: SourceChoice) -> list[Scanner]:
    """Get scanner instances based on source selection.

    Args:
        source: The source choice (apt, flatpak, or all).

    Returns:
        List of scanner instances.
    """
    scanners: list[Scanner] = []

    if source in (SourceChoice.APT, SourceChoice.ALL):
        scanners.append(AptScanner())

    if source in (SourceChoice.FLATPAK, SourceChoice.ALL):
        scanners.append(FlatpakScanner())

    return scanners


def _get_operators(source: SourceChoice, dry_run: bool) -> list[Operator]:
    """Get operator instances based on source selection.

    Args:
        source: The source choice (apt, flatpak, or all).
        dry_run: Whether to run in dry-run mode.

    Returns:
        List of operator instances.
    """
    operators: list[Operator] = []

    if source in (SourceChoice.APT, SourceChoice.ALL):
        operators.append(AptOperator(dry_run=dry_run))

    if source in (SourceChoice.FLATPAK, SourceChoice.ALL):
        operators.append(FlatpakOperator(dry_run=dry_run))

    return operators


def _source_to_package_source(source_str: str) -> PackageSource:
    """Convert source string to PackageSource enum.

    Args:
        source_str: Source string ("apt" or "flatpak").

    Returns:
        Corresponding PackageSource enum value.
    """
    source_map = {
        "apt": PackageSource.APT,
        "flatpak": PackageSource.FLATPAK,
    }
    return source_map[source_str]


def _diff_to_actions(diff_result: DiffResult, purge: bool = False) -> list[Action]:
    """Convert diff result to list of actions.

    Only MISSING and EXTRA diffs are converted to actions:
    - MISSING: Package in manifest but not installed -> INSTALL
    - EXTRA: Package marked for removal but still installed -> REMOVE/PURGE

    NEW packages (installed but not in manifest) are ignored - the user
    must explicitly add them to the remove list in the manifest.

    Protected packages are excluded from removal actions.

    Args:
        diff_result: Result from DiffEngine.compute_diff().
        purge: If True, use PURGE instead of REMOVE for APT packages.

    Returns:
        List of Action objects to execute.
    """
    actions: list[Action] = []

    # MISSING -> INSTALL
    for entry in diff_result.missing:
        pkg_source = _source_to_package_source(entry.source)
        action = create_install_action(
            package=entry.name,
            source=pkg_source,
            reason="Package in manifest but not installed",
        )
        actions.append(action)

    # EXTRA -> REMOVE/PURGE
    for entry in diff_result.extra:
        # Skip protected packages (should not happen as DiffEngine filters them,
        # but defense in depth)
        if is_protected(entry.name):
            continue

        pkg_source = _source_to_package_source(entry.source)

        # Purge only applies to APT packages
        use_purge = purge and pkg_source == PackageSource.APT

        action = create_remove_action(
            package=entry.name,
            source=pkg_source,
            reason="Package marked for removal in manifest",
            purge=use_purge,
        )
        actions.append(action)

    return actions


def _create_actions_table(actions: list[Action], dry_run: bool) -> Table:
    """Create a Rich table displaying planned actions.

    Args:
        actions: List of actions to display.
        dry_run: Whether this is a dry-run.

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


def _create_results_table(results: list[ActionResult]) -> Table:
    """Create a Rich table displaying action results.

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


def _print_actions_summary(actions: list[Action]) -> None:
    """Print a summary of planned actions.

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


def _print_results_summary(results: list[ActionResult]) -> None:
    """Print a summary of action results.

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


def _execute_actions(
    actions: list[Action],
    operators: list[Operator],
) -> list[ActionResult]:
    """Execute actions using the appropriate operators.

    Args:
        actions: List of actions to execute.
        operators: List of available operators.

    Returns:
        List of ActionResult for all actions.
    """
    results: list[ActionResult] = []

    # Group actions by source
    actions_by_source: dict[PackageSource, list[Action]] = {}
    for action in actions:
        if action.source not in actions_by_source:
            actions_by_source[action.source] = []
        actions_by_source[action.source].append(action)

    # Execute actions for each source
    for operator in operators:
        source_actions = actions_by_source.get(operator.source, [])
        if source_actions:
            source_results = operator.execute(source_actions)
            results.extend(source_results)

    return results


def _confirm_actions(action_count: int) -> bool:
    """Prompt user to confirm action execution.

    Args:
        action_count: Number of actions to be executed.

    Returns:
        True if user confirms, False otherwise.
    """
    return typer.confirm(
        f"\nProceed with {action_count} action(s)?",
        default=False,
    )


@app.callback(invoke_without_command=True)
def apply_manifest(
    ctx: typer.Context,
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
            help="Package source to apply: apt, flatpak, or all.",
            case_sensitive=False,
        ),
    ] = SourceChoice.ALL,
    purge: Annotated[
        bool,
        typer.Option(
            "--purge",
            "-p",
            help="Use purge instead of remove for APT packages (removes config files).",
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
    # Skip if a subcommand is being invoked
    if ctx.invoked_subcommand is not None:
        return

    # Load manifest
    try:
        manifest = load_manifest()
    except ManifestNotFoundError as e:
        print_error(f"Manifest not found: {get_manifest_path()}")
        print_info("Run 'popctl init' to create a manifest from your current system.")
        raise typer.Exit(code=1) from e
    except ManifestError as e:
        print_error(f"Failed to load manifest: {e}")
        raise typer.Exit(code=1) from e

    # Get scanners and check availability
    scanners = _get_scanners(source)
    available_scanners: list[Scanner] = []

    for scanner in scanners:
        if scanner.is_available():
            available_scanners.append(scanner)
        else:
            print_warning(f"{scanner.source.value.upper()} package manager is not available.")

    if not available_scanners:
        print_error("No package managers are available on this system.")
        raise typer.Exit(code=1)

    # Compute diff
    source_filter = source.value if source != SourceChoice.ALL else None
    engine = DiffEngine(manifest)

    try:
        diff_result = engine.compute_diff(available_scanners, source_filter)
    except RuntimeError as e:
        print_error(f"Scan failed: {e}")
        raise typer.Exit(code=1) from e

    # Convert diff to actions
    actions = _diff_to_actions(diff_result, purge=purge)

    # Check if there's anything to do
    if not actions:
        print_success("System is already in sync with manifest. Nothing to do.")
        return

    # Show planned actions
    table = _create_actions_table(actions, dry_run)
    console.print(table)
    _print_actions_summary(actions)

    # If dry-run, stop here
    if dry_run:
        print_info("\nDry-run mode: No changes were made.")
        return

    # Confirm unless --yes was provided
    if not yes and not _confirm_actions(len(actions)):
        print_info("Aborted.")
        raise typer.Exit(code=0)

    # Get operators and check availability
    operators = _get_operators(source, dry_run=False)
    available_operators: list[Operator] = []

    for operator in operators:
        if operator.is_available():
            available_operators.append(operator)

    # Execute actions
    console.print("\n[bold]Executing actions...[/bold]\n")

    results = _execute_actions(actions, available_operators)

    # Show results
    results_table = _create_results_table(results)
    console.print(results_table)
    _print_results_summary(results)

    # Exit with error code if any action failed
    if any(r.failed for r in results):
        raise typer.Exit(code=1)
