"""Sync command implementation.

Orchestrates the full system synchronization pipeline in a single
invocation: init -> diff -> advisor -> advisor-apply -> system-apply.
"""

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from popctl.cli.display import (
    create_actions_table,
    create_results_table,
    print_actions_summary,
    print_results_summary,
)
from popctl.cli.types import SourceChoice, get_available_scanners, get_scanners
from popctl.core.actions import diff_to_actions
from popctl.core.diff import DiffEngine, DiffResult
from popctl.core.executor import execute_actions, get_available_operators, record_actions_to_history
from popctl.core.manifest import manifest_exists, save_manifest
from popctl.scanners.base import Scanner
from popctl.utils.formatting import (
    console,
    print_error,
    print_info,
    print_success,
    print_warning,
)

logger = logging.getLogger(__name__)

app = typer.Typer(
    help="Full system synchronization.",
    invoke_without_command=True,
)


def _ensure_manifest() -> None:
    """Auto-create manifest if it does not exist.

    Reuses init logic: scans the system for manually installed packages,
    creates a manifest, and saves it to the default config directory.

    Raises:
        typer.Exit: If manifest creation fails.
    """
    if manifest_exists():
        return

    print_info("No manifest found. Auto-initializing from current system...")

    from popctl.cli.commands.init import _collect_manual_packages, _create_manifest
    from popctl.core.paths import ensure_config_dir

    scanners = get_available_scanners()
    if not scanners:
        print_error("No package managers available (APT or Flatpak required).")
        raise typer.Exit(code=1)

    source_names = [s.source.value.upper() for s in scanners]
    print_info(f"Scanning system packages: {', '.join(source_names)}")

    try:
        packages, _skipped = _collect_manual_packages(scanners)
    except RuntimeError as e:
        print_error(f"Scan failed: {e}")
        raise typer.Exit(code=1) from e

    if not packages:
        print_warning("No manually installed packages found (excluding protected system packages).")

    manifest = _create_manifest(packages)

    ensure_config_dir()

    try:
        saved_path = save_manifest(manifest)
        print_success(f"Manifest created: {saved_path}")
    except OSError as e:
        print_error(f"Failed to save manifest: {e}")
        raise typer.Exit(code=1) from e


def _compute_diff(source: SourceChoice) -> DiffResult:
    """Compute diff between manifest and system state.

    Args:
        source: Package source filter.

    Returns:
        Diff result with NEW, MISSING, and EXTRA entries.

    Raises:
        typer.Exit: If diff computation fails.
    """
    from popctl.core.manifest import require_manifest

    manifest = require_manifest()

    scanners = get_scanners(source)
    available_scanners: list[Scanner] = []

    for scanner in scanners:
        if scanner.is_available():
            available_scanners.append(scanner)
        else:
            print_warning(f"{scanner.source.value.upper()} package manager is not available.")

    if not available_scanners:
        print_error("No package managers are available on this system.")
        raise typer.Exit(code=1)

    source_filter = source.value if source != SourceChoice.ALL else None
    engine = DiffEngine(manifest)

    try:
        return engine.compute_diff(available_scanners, source_filter)
    except RuntimeError as e:
        print_error(f"Scan failed: {e}")
        raise typer.Exit(code=1) from e


def _run_advisor(diff_result: DiffResult, auto: bool) -> None:
    """Run AI advisor to classify NEW packages.

    If the advisor produces decisions, they are applied to the manifest.
    Advisor failures are non-fatal: a warning is printed and sync continues.

    Args:
        diff_result: Current diff result containing NEW packages.
        auto: If True, run headless advisor; otherwise interactive.
    """
    if not diff_result.new:
        print_info("No NEW packages to classify. Skipping advisor.")
        return

    print_info(f"{len(diff_result.new)} NEW package(s) found. Running advisor...")

    from popctl.advisor import AgentRunner
    from popctl.cli.commands.advisor import (
        _create_workspace,
        _load_or_create_config,
        _scan_system,
    )

    try:
        config = _load_or_create_config()
    except (OSError, RuntimeError) as e:
        print_warning(f"Could not load advisor config: {e}")
        return

    try:
        scan_result = _scan_system()
    except SystemExit:
        print_warning("System scan for advisor failed. Continuing without advisor.")
        return

    try:
        workspace_dir = _create_workspace(scan_result)
    except (OSError, RuntimeError) as e:
        print_warning(f"Could not create advisor workspace: {e}")
        return

    runner = AgentRunner(config)

    try:
        if auto:
            result = runner.run_headless(workspace_dir)
        else:
            result = runner.launch_interactive(workspace_dir)
    except FileNotFoundError:
        print_warning("Advisor CLI tool not found. Continuing without advisor.")
        return
    except (OSError, RuntimeError) as e:
        print_warning(f"Advisor execution failed: {e}")
        return

    if result.error == "manual_mode":
        # Interactive manual-mode: print workspace path and exit
        console.print()
        console.print(result.output)
        raise typer.Exit(code=0)

    if result.success and result.decisions_path:
        print_success("Advisor classification completed.")
        _apply_advisor_decisions(result.decisions_path)
    elif not result.success:
        print_warning(f"Advisor did not produce decisions: {result.error or 'unknown error'}")
        print_info("Continuing with current manifest.")


def _apply_advisor_decisions(decisions_path: Path) -> None:
    """Apply advisor decisions to the manifest.

    Loads decisions, mutates the manifest (add keep/remove entries),
    updates the timestamp, and saves. Errors are non-fatal.

    Args:
        decisions_path: Path to the decisions.toml file.
    """
    from popctl.advisor import import_decisions
    from popctl.cli.commands.advisor import _record_advisor_apply_to_history
    from popctl.core.manifest import ManifestError, load_manifest
    from popctl.models.manifest import PackageEntry

    try:
        decisions = import_decisions(decisions_path.parent)
    except (FileNotFoundError, ValueError) as e:
        print_warning(f"Could not load advisor decisions: {e}")
        return

    try:
        manifest = load_manifest()
    except ManifestError as e:
        print_warning(f"Could not load manifest for advisor apply: {e}")
        return

    # Apply decisions to manifest
    for source in ("apt", "flatpak"):
        source_decisions = decisions.packages.get(source)
        if source_decisions is None:
            continue

        for decision in source_decisions.keep:
            manifest.packages.keep[decision.name] = PackageEntry(
                source=source,  # type: ignore[arg-type]
                status="keep",
                reason=decision.reason,
            )

        for decision in source_decisions.remove:
            manifest.packages.remove[decision.name] = PackageEntry(
                source=source,  # type: ignore[arg-type]
                status="remove",
                reason=decision.reason,
            )

    manifest.meta.updated = datetime.now(UTC)

    try:
        save_manifest(manifest)
        print_success("Advisor decisions applied to manifest.")
    except (OSError, ManifestError) as e:
        print_warning(f"Could not save manifest after advisor apply: {e}")
        return

    try:
        _record_advisor_apply_to_history(decisions)
    except (OSError, RuntimeError) as e:
        print_warning(f"Could not record advisor apply to history: {e}")


@app.callback(invoke_without_command=True)
def sync(
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
            help="Show diff only, no changes.",
        ),
    ] = False,
    source: Annotated[
        SourceChoice,
        typer.Option(
            "--source",
            "-s",
            help="Package source to sync: apt, flatpak, or all.",
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
    no_advisor: Annotated[
        bool,
        typer.Option(
            "--no-advisor",
            help="Skip AI advisor classification phases.",
        ),
    ] = False,
    auto: Annotated[
        bool,
        typer.Option(
            "--auto",
            "-a",
            help="Use headless advisor instead of interactive session.",
        ),
    ] = False,
) -> None:
    """Full system synchronization.

    Orchestrates the complete pipeline: init, diff, advisor, apply.
    Ensures the system matches the manifest in a single command.

    Pipeline phases:
      1. Init: Auto-create manifest if missing
      2. Diff: Compute NEW/MISSING/EXTRA differences
      3. Advisor: Classify NEW packages (unless --no-advisor)
      4. Apply-M: Write advisor decisions to manifest
      5. Re-Diff: Recompute diff after manifest changes
      6. Confirm: Display planned actions, ask confirmation
      7. Apply-S: Execute install/remove/purge on system
      8. History: Record all actions to history

    Examples:
        popctl sync                     # Interactive advisor + system apply
        popctl sync --auto              # Headless advisor + system apply
        popctl sync --no-advisor        # Skip advisor, only MISSING/EXTRA
        popctl sync --dry-run           # Show diff only, no changes
        popctl sync -y -a               # Fully automated (CI-friendly)
        popctl sync --source apt        # Filter to APT packages only
        popctl sync --purge             # Purge instead of remove (APT)
    """
    # Skip if a subcommand is being invoked
    if ctx.invoked_subcommand is not None:
        return

    # Phase 1: Ensure manifest exists
    _ensure_manifest()

    # Phase 2: Compute diff
    diff_result = _compute_diff(source)

    # Check if system is already in sync
    if diff_result.is_in_sync:
        print_success("System is already in sync with manifest. Nothing to do.")
        return

    # Show diff summary
    console.print()
    console.print(
        f"[bold]Diff summary:[/bold] "
        f"[info]{len(diff_result.new)} NEW[/info], "
        f"[warning]{len(diff_result.missing)} MISSING[/warning], "
        f"[error]{len(diff_result.extra)} EXTRA[/error]"
    )

    # Phase 2b: Dry-run stops here
    if dry_run:
        print_info("\nDry-run mode: No changes were made.")
        return

    # Phase 3-5: Advisor (unless --no-advisor or no NEW packages)
    if not no_advisor and diff_result.new:
        _run_advisor(diff_result, auto)

        # Phase 5: Re-diff after advisor changes
        diff_result = _compute_diff(source)

        if diff_result.is_in_sync:
            print_success(
                "System is already in sync with manifest after advisor changes. Nothing to do."
            )
            return

    # Phase 6: Convert to actions and display
    actions = diff_to_actions(diff_result, purge=purge)

    if not actions:
        print_success("No actionable changes. System is in sync with manifest.")
        return

    table = create_actions_table(actions)
    console.print(table)
    print_actions_summary(actions)

    # Confirm unless --yes
    if not yes:
        confirmed = typer.confirm(
            f"\nProceed with {len(actions)} action(s)?",
            default=False,
        )
        if not confirmed:
            print_info("Aborted.")
            raise typer.Exit(code=0)

    # Phase 7: Execute actions
    available_operators = get_available_operators(source)

    console.print("\n[bold]Executing actions...[/bold]\n")
    results = execute_actions(actions, available_operators)

    # Phase 8: Record history
    if results:
        record_actions_to_history(results, command="popctl sync")
        print_info("Actions recorded to history.")

    # Display results
    results_table = create_results_table(results)
    console.print(results_table)
    print_results_summary(results)

    # Exit with error if any action failed
    if any(r.failed for r in results):
        raise typer.Exit(code=1)
