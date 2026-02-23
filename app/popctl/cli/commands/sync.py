"""Sync command implementation.

Orchestrates the full system synchronization pipeline in a single
invocation: init -> diff -> advisor -> advisor-apply -> system-apply,
followed by optional filesystem scanning and cleanup phases.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Annotated

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

if TYPE_CHECKING:
    from popctl.advisor.exchange import FilesystemDecisions
    from popctl.filesystem.models import ScannedPath

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
    no_filesystem: Annotated[
        bool,
        typer.Option(
            "--no-filesystem",
            help="Skip filesystem scanning and cleanup phases.",
        ),
    ] = False,
) -> None:
    """Full system synchronization.

    Orchestrates the complete pipeline: init, diff, advisor, apply,
    and optional filesystem scanning and cleanup.
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
      9. FS-Scan: Scan filesystem for orphaned entries (unless --no-filesystem)
      10. FS-Advisor: Classify filesystem findings via advisor
      11. FS-Apply: Apply filesystem decisions to manifest
      12. FS-Clean: Delete orphaned directories/files
      13. FS-History: Record filesystem deletions to history

    Examples:
        popctl sync                     # Interactive advisor + system apply
        popctl sync --auto              # Headless advisor + system apply
        popctl sync --no-advisor        # Skip advisor, only MISSING/EXTRA
        popctl sync --dry-run           # Show diff only, no changes
        popctl sync -y -a               # Fully automated (CI-friendly)
        popctl sync --source apt        # Filter to APT packages only
        popctl sync --purge             # Purge instead of remove (APT)
        popctl sync --no-filesystem     # Skip filesystem phases
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
        # Phase 9-13: Filesystem phases (even when packages are in sync)
        if not no_filesystem:
            _run_filesystem_phases(dry_run=dry_run, yes=yes, no_advisor=no_advisor, auto=auto)
        return

    # Show diff summary
    console.print()
    console.print(
        f"[bold]Diff summary:[/bold] "
        f"[info]{len(diff_result.new)} NEW[/info], "
        f"[warning]{len(diff_result.missing)} MISSING[/warning], "
        f"[error]{len(diff_result.extra)} EXTRA[/error]"
    )

    # Phase 2b: Dry-run stops here (for packages)
    if dry_run:
        print_info("\nDry-run mode: No package changes were made.")
        # Phase 9-13: Filesystem phases in dry-run mode
        if not no_filesystem:
            _run_filesystem_phases(dry_run=True, yes=yes, no_advisor=no_advisor, auto=auto)
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
            # Phase 9-13: Filesystem phases
            if not no_filesystem:
                _run_filesystem_phases(dry_run=dry_run, yes=yes, no_advisor=no_advisor, auto=auto)
            return

    # Phase 6: Convert to actions and display
    actions = diff_to_actions(diff_result, purge=purge)

    if not actions:
        print_success("No actionable changes. System is in sync with manifest.")
        # Phase 9-13: Filesystem phases
        if not no_filesystem:
            _run_filesystem_phases(dry_run=dry_run, yes=yes, no_advisor=no_advisor, auto=auto)
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

    # Phase 9-13: Filesystem scanning and cleanup (unless --no-filesystem)
    if not no_filesystem:
        _run_filesystem_phases(dry_run=dry_run, yes=yes, no_advisor=no_advisor, auto=auto)

    # Exit with error if any action failed
    if any(r.failed for r in results):
        raise typer.Exit(code=1)


# =============================================================================
# Filesystem phases (9-13)
# =============================================================================


def _run_filesystem_phases(
    *,
    dry_run: bool,
    yes: bool,
    no_advisor: bool,
    auto: bool,
) -> None:
    """Run filesystem scanning and cleanup phases (9-13).

    These phases are non-fatal: failures print warnings and continue.
    The entire filesystem pipeline is wrapped to ensure that errors
    never propagate up to crash the sync command.

    Args:
        dry_run: If True, show findings but do not delete.
        yes: If True, skip confirmation prompts.
        no_advisor: If True, skip filesystem advisor classification.
        auto: If True, use headless advisor instead of interactive.
    """
    console.print("\n[bold]Phase 9: Filesystem scan[/bold]")

    # Phase 9: FS-Scan
    orphans = _fs_scan()
    if not orphans:
        print_info("No orphaned filesystem entries found. Skipping filesystem phases.")
        return

    print_info(f"Found {len(orphans)} orphaned filesystem entries.")

    if dry_run:
        # In dry-run, just show the orphans
        _fs_display_orphans(orphans)
        print_info("Dry-run mode: No filesystem changes made.")
        return

    # Phase 10: FS-Advisor (if not --no-advisor)
    fs_decisions: FilesystemDecisions | None = None
    if not no_advisor:
        console.print("\n[bold]Phase 10: Filesystem advisor[/bold]")
        fs_decisions = _fs_run_advisor(orphans, auto)

    # Phase 11: FS-Apply (apply advisor decisions to manifest)
    if fs_decisions:
        console.print("\n[bold]Phase 11: Apply filesystem decisions[/bold]")
        _fs_apply_decisions(fs_decisions)

    # Phase 12: FS-Clean (delete paths marked for removal)
    console.print("\n[bold]Phase 12: Filesystem cleanup[/bold]")
    deleted_paths = _fs_clean(yes=yes)

    # Phase 13: FS-History (record deletions)
    if deleted_paths:
        console.print("\n[bold]Phase 13: Filesystem history[/bold]")
        _fs_record_history(deleted_paths)


def _fs_scan() -> list[ScannedPath]:
    """Phase 9: Scan filesystem for orphaned entries.

    Returns:
        List of ScannedPath objects with ORPHAN status.
        Empty list if the scan fails or finds nothing.
    """
    from popctl.filesystem.models import PathStatus
    from popctl.filesystem.scanner import FilesystemScanner

    try:
        scanner = FilesystemScanner()
        return [p for p in scanner.scan() if p.status == PathStatus.ORPHAN]
    except (OSError, RuntimeError) as e:
        print_warning(f"Filesystem scan failed: {e}")
        return []


def _fs_display_orphans(orphans: list[ScannedPath]) -> None:
    """Display filesystem orphans in a summary table.

    Shows up to 20 entries in sync context with a hint to use
    ``popctl fs scan`` for the full list.

    Args:
        orphans: List of orphaned filesystem entries to display.
    """
    from rich.table import Table

    table = Table(title="Orphaned Filesystem Entries", show_lines=False)
    table.add_column("Path", style="bold")
    table.add_column("Type", width=10)
    table.add_column("Confidence", justify="right", width=10)

    display_limit = 20
    for p in orphans[:display_limit]:
        conf = f"{p.confidence:.0%}"
        table.add_row(p.path, p.path_type.value, conf)

    console.print(table)
    if len(orphans) > display_limit:
        console.print(
            f"[dim]... and {len(orphans) - display_limit} more. "
            "Use 'popctl fs scan' for full list.[/dim]"
        )


def _fs_run_advisor(
    orphans: list[ScannedPath],
    auto: bool,
) -> FilesystemDecisions | None:
    """Phase 10: Run advisor to classify filesystem orphans.

    For MVP, filesystem advisor integration is not yet wired into the
    sync pipeline. This function prints a note and returns None.
    Full standalone FS advisor can be implemented in a future iteration.

    Args:
        orphans: List of orphaned filesystem entries.
        auto: If True, use headless advisor mode.

    Returns:
        Filesystem decisions or None if advisor is unavailable/fails.
    """
    _ = auto  # Reserved for future advisor integration

    from popctl.advisor.exchange import FilesystemOrphanEntry

    # Convert ScannedPath objects to FilesystemOrphanEntry for advisor
    fs_orphan_entries = [
        FilesystemOrphanEntry(
            path=p.path,
            path_type=p.path_type.value,
            size_bytes=p.size_bytes,
            mtime=p.mtime,
            parent_target=p.parent_target,
            orphan_reason=p.orphan_reason.value if p.orphan_reason else "unknown",
            confidence=p.confidence,
        )
        for p in orphans
    ]

    print_info(f"Classifying {len(fs_orphan_entries)} filesystem orphan(s) via advisor...")

    # NOTE: For MVP, filesystem advisor classification is not yet
    # integrated into the sync pipeline. The advisor would classify
    # orphans alongside packages.
    print_warning("Filesystem advisor classification is not yet integrated into sync pipeline.")
    print_info("Use 'popctl advisor session' for interactive filesystem classification.")
    return None


def _fs_apply_decisions(fs_decisions: FilesystemDecisions) -> None:
    """Phase 11: Apply filesystem advisor decisions to manifest.

    Merges the advisor's keep/remove classifications into the manifest's
    filesystem section, preserving existing entries that are not
    reclassified.

    Args:
        fs_decisions: Filesystem decisions from the advisor.
    """
    from popctl.core.manifest import ManifestError, load_manifest
    from popctl.filesystem.manifest import FilesystemConfig, FilesystemEntry

    try:
        manifest = load_manifest()
    except ManifestError as e:
        print_warning(f"Could not load manifest for filesystem apply: {e}")
        return

    # Build filesystem config from decisions
    keep_entries: dict[str, FilesystemEntry] = {}
    remove_entries: dict[str, FilesystemEntry] = {}

    for decision in fs_decisions.keep:
        keep_entries[decision.path] = FilesystemEntry(
            reason=decision.reason,
            category=decision.category,
        )

    for decision in fs_decisions.remove:
        remove_entries[decision.path] = FilesystemEntry(
            reason=decision.reason,
            category=decision.category,
        )

    # Merge with existing filesystem config
    existing = manifest.filesystem
    if existing:
        # Preserve existing entries, add new ones
        for path, entry in existing.keep.items():
            if path not in keep_entries and path not in remove_entries:
                keep_entries[path] = entry
        for path, entry in existing.remove.items():
            if path not in keep_entries and path not in remove_entries:
                remove_entries[path] = entry

    manifest.filesystem = FilesystemConfig(keep=keep_entries, remove=remove_entries)
    manifest.meta.updated = datetime.now(UTC)

    try:
        save_manifest(manifest)
        print_success(
            f"Filesystem decisions applied to manifest "
            f"({len(keep_entries)} keep, {len(remove_entries)} remove)."
        )
    except (OSError, ManifestError) as e:
        print_warning(f"Could not save manifest after filesystem apply: {e}")


def _fs_clean(*, yes: bool) -> list[str]:
    """Phase 12: Delete paths marked for removal in manifest.

    Loads the manifest, finds paths in the ``[filesystem.remove]``
    section, prompts for confirmation (unless ``--yes``), then
    delegates deletion to FilesystemOperator.

    Args:
        yes: If True, skip confirmation prompt.

    Returns:
        List of paths that were successfully deleted.
    """
    from popctl.core.manifest import ManifestError, load_manifest
    from popctl.filesystem.operator import FilesystemOperator

    try:
        manifest = load_manifest()
    except ManifestError as e:
        print_warning(f"Could not load manifest for filesystem cleanup: {e}")
        return []

    remove_paths = manifest.get_fs_remove_paths()
    if not remove_paths:
        print_info("No filesystem entries marked for removal.")
        return []

    paths_to_delete = list(remove_paths.keys())
    print_info(f"{len(paths_to_delete)} path(s) marked for removal.")

    # Confirm unless --yes
    if not yes:
        for p in paths_to_delete:
            console.print(f"  [error]DELETE[/] {p}")
        confirmed = typer.confirm(
            f"\nDelete {len(paths_to_delete)} filesystem path(s)?",
            default=False,
        )
        if not confirmed:
            print_info("Filesystem cleanup skipped.")
            return []

    operator = FilesystemOperator()
    results = operator.delete(paths_to_delete)

    successful = [r.path for r in results if r.success]
    failed = [r for r in results if not r.success]

    if successful:
        print_success(f"Deleted {len(successful)} path(s).")
    if failed:
        for r in failed:
            print_warning(f"Failed to delete {r.path}: {r.error}")

    return successful


def _fs_record_history(deleted_paths: list[str]) -> None:
    """Phase 13: Record filesystem deletions to history.

    Args:
        deleted_paths: Paths that were successfully deleted.
    """
    from popctl.filesystem.history import record_fs_deletions

    try:
        record_fs_deletions(deleted_paths, command="popctl sync")
        print_info("Filesystem deletions recorded to history.")
    except (OSError, RuntimeError) as e:
        print_warning(f"Could not record filesystem history: {e}")
