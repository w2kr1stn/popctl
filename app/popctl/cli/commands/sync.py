"""Sync command implementation.

Orchestrates the full system synchronization pipeline in a single
invocation: init -> diff -> advisor -> advisor-apply -> system-apply,
followed by optional filesystem scanning and cleanup phases.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

import typer

from popctl.advisor import AgentRunner, import_decisions
from popctl.advisor.config import AdvisorConfigError
from popctl.advisor.exchange import (
    DecisionsResult,
    DomainDecisions,
    OrphanEntry,
    apply_decisions_to_manifest,
)
from popctl.advisor.workspace import create_session_workspace, ensure_advisor_sessions_dir
from popctl.cli.commands.advisor import (
    load_or_create_config,
    record_advisor_apply_to_history,
    scan_system,
)
from popctl.cli.commands.init import collect_manual_packages, create_manifest
from popctl.cli.display import (
    create_actions_table,
    create_results_table,
    print_actions_summary,
    print_orphan_table,
    print_results_summary,
)
from popctl.cli.types import (
    SourceChoice,
    get_available_scanners,
    get_checked_scanners,
    require_manifest,
)
from popctl.configs.operator import ConfigOperator
from popctl.configs.scanner import ConfigScanner
from popctl.core.actions import diff_to_actions
from popctl.core.diff import DiffResult, compute_diff
from popctl.core.executor import execute_actions, get_available_operators, record_actions_to_history
from popctl.core.manifest import ManifestError, load_manifest, manifest_exists, save_manifest
from popctl.core.paths import ensure_config_dir, get_manifest_path, get_state_dir
from popctl.domain.history import record_domain_deletions
from popctl.domain.manifest import DomainConfig, DomainEntry
from popctl.domain.models import OrphanStatus, ScannedEntry
from popctl.filesystem.operator import FilesystemOperator
from popctl.filesystem.scanner import FilesystemScanner
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

    scanners = get_available_scanners()
    if not scanners:
        print_error("No package managers available (APT or Flatpak required).")
        raise typer.Exit(code=1)

    source_names = [s.source.value.upper() for s in scanners]
    print_info(f"Scanning system packages: {', '.join(source_names)}")

    try:
        packages, _skipped = collect_manual_packages(scanners)
    except RuntimeError as e:
        print_error(f"Scan failed: {e}")
        raise typer.Exit(code=1) from e

    if not packages:
        print_warning("No manually installed packages found (excluding protected system packages).")

    manifest = create_manifest(packages)

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
    manifest = require_manifest()

    available_scanners = get_checked_scanners(source)

    source_filter = source.value if source != SourceChoice.ALL else None
    try:
        return compute_diff(manifest, available_scanners, source_filter)
    except RuntimeError as e:
        print_error(f"Scan failed: {e}")
        raise typer.Exit(code=1) from e


def _invoke_advisor(
    *,
    auto: bool,
    domain: str,
    filesystem_orphans: list[OrphanEntry] | None = None,
    config_orphans: list[OrphanEntry] | None = None,
) -> DecisionsResult | None:
    """Shared advisor workflow: config -> scan -> workspace -> run -> import.

    Encapsulates the common boilerplate shared by all three advisor phases
    (packages, filesystem, configs). Each caller provides domain-specific
    orphan data and extracts the relevant section from the result.

    Args:
        auto: If True, run headless advisor; otherwise interactive.
        domain: Human-readable domain label for log messages.
        filesystem_orphans: Optional FS orphan entries for workspace.
        config_orphans: Optional config orphan entries for workspace.

    Returns:
        Parsed DecisionsResult or None on any failure (non-fatal).
    """
    try:
        config = load_or_create_config()
    except (OSError, RuntimeError, AdvisorConfigError) as e:
        print_warning(f"Could not load advisor config: {e}")
        return None

    try:
        scan_result = scan_system()
    except SystemExit:
        print_warning(f"System scan for {domain} advisor failed. Continuing without advisor.")
        return None

    try:
        sessions_dir = ensure_advisor_sessions_dir()
        manifest_path = get_manifest_path()
        memory_path = get_state_dir() / "advisor" / "memory.md"

        workspace_dir = create_session_workspace(
            scan_result,
            sessions_dir,
            manifest_path=manifest_path if manifest_path.exists() else None,
            memory_path=memory_path if memory_path.exists() else None,
            filesystem_orphans=filesystem_orphans,
            config_orphans=config_orphans,
        )
    except (OSError, RuntimeError) as e:
        print_warning(f"Could not create advisor workspace: {e}")
        return None

    runner = AgentRunner(config)

    try:
        if auto:
            result = runner.run_headless(workspace_dir)
        else:
            result = runner.launch_interactive(workspace_dir)
    except (OSError, RuntimeError) as e:
        print_warning(f"Advisor execution failed: {e}")
        return None

    if result.error == "manual_mode":
        console.print()
        console.print(result.output)
        return None

    if not result.success or not result.decisions_path:
        print_warning(
            f"{domain.capitalize()} advisor did not produce decisions: "
            f"{result.error or 'unknown error'}"
        )
        return None

    try:
        return import_decisions(result.decisions_path.parent)
    except (FileNotFoundError, ValueError) as e:
        print_warning(f"Could not load advisor decisions: {e}")
        return None


def _run_advisor(diff_result: DiffResult, auto: bool) -> None:
    """Run AI advisor to classify NEW packages.

    If the advisor produces decisions, they are applied to the manifest.
    Advisor failures are non-fatal: a warning is printed and sync continues.

    Args:
        diff_result: Current diff result containing NEW packages.
        auto: If True, run headless advisor; otherwise interactive.
    """
    print_info(f"{len(diff_result.new)} NEW package(s) found. Running advisor...")

    decisions = _invoke_advisor(auto=auto, domain="packages")
    if decisions:
        print_success("Advisor classification completed.")
        _apply_advisor_decisions(decisions)
    else:
        print_info("Continuing with current manifest.")


def _apply_advisor_decisions(decisions: DecisionsResult) -> None:
    """Apply advisor decisions to the manifest.

    Mutates the manifest (add keep/remove entries), updates the
    timestamp, and saves. Errors are non-fatal.

    Args:
        decisions: Parsed advisor decisions to apply.
    """
    try:
        manifest = load_manifest()
    except ManifestError as e:
        print_warning(f"Could not load manifest for advisor apply: {e}")
        return

    # Apply decisions to manifest
    apply_decisions_to_manifest(manifest, decisions)

    manifest.meta.updated = datetime.now(UTC)

    try:
        save_manifest(manifest)
        print_success("Advisor decisions applied to manifest.")
    except (OSError, ManifestError) as e:
        print_warning(f"Could not save manifest after advisor apply: {e}")
        return

    try:
        record_advisor_apply_to_history(decisions)
    except (OSError, RuntimeError) as e:
        print_warning(f"Could not record advisor apply to history: {e}")


def _run_both_orphan_phases(
    *,
    dry_run: bool,
    yes: bool,
    no_advisor: bool,
    auto: bool,
    no_filesystem: bool,
    no_configs: bool,
) -> None:
    """Run orphan phases for both domains if enabled."""
    if not no_filesystem:
        _run_orphan_phases("filesystem", dry_run=dry_run, yes=yes, no_advisor=no_advisor, auto=auto)
    if not no_configs:
        _run_orphan_phases("configs", dry_run=dry_run, yes=yes, no_advisor=no_advisor, auto=auto)


@app.callback(invoke_without_command=True)
def sync(
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
    no_configs: Annotated[
        bool,
        typer.Option(
            "--no-configs",
            help="Skip config scanning and cleanup phases.",
        ),
    ] = False,
) -> None:
    """Full system synchronization.

    Orchestrates the complete pipeline: init, diff, advisor, apply,
    and optional filesystem scanning and cleanup.
    Ensures the system matches the manifest in a single command.

    Pipeline phases:
      - Init: Auto-create manifest if missing
      - Diff: Compute NEW/MISSING/EXTRA differences
      - Advisor: Classify NEW packages (unless --no-advisor)
      - Apply-M: Write advisor decisions to manifest
      - Re-Diff: Recompute diff after manifest changes
      - Confirm: Display planned actions, ask confirmation
      - Apply-S: Execute install/remove/purge on system
      - History: Record all actions to history
      - FS-Scan: Scan filesystem for orphaned entries (unless --no-filesystem)
      - FS-Advisor: Classify filesystem findings via advisor
      - FS-Apply: Apply filesystem decisions to manifest
      - FS-Clean: Delete orphaned directories/files
      - FS-History: Record filesystem deletions to history
      - Config-Scan: Scan for orphaned configs (unless --no-configs)
      - Config-Advisor: Classify config orphans via advisor
      - Config-Apply: Apply config decisions to manifest
      - Config-Clean: Delete orphaned configs (with backup)
      - Config-History: Record config deletions to history

    Examples:
        popctl sync                     # Interactive advisor + system apply
        popctl sync --auto              # Headless advisor + system apply
        popctl sync --no-advisor        # Skip advisor, only MISSING/EXTRA
        popctl sync --dry-run           # Show diff only, no changes
        popctl sync -y -a               # Fully automated (CI-friendly)
        popctl sync --source apt        # Filter to APT packages only
        popctl sync --purge             # Purge instead of remove (APT)
        popctl sync --no-filesystem     # Skip filesystem phases
        popctl sync --no-configs        # Skip config phases
    """
    # Phase 1: Ensure manifest exists
    _ensure_manifest()

    # Phase 2: Compute diff
    diff_result = _compute_diff(source)

    # Check if system is already in sync
    if diff_result.is_in_sync:
        print_success("System is already in sync with manifest. Nothing to do.")
        _run_both_orphan_phases(
            dry_run=dry_run,
            yes=yes,
            no_advisor=no_advisor,
            auto=auto,
            no_filesystem=no_filesystem,
            no_configs=no_configs,
        )
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
        _run_both_orphan_phases(
            dry_run=True,
            yes=yes,
            no_advisor=no_advisor,
            auto=auto,
            no_filesystem=no_filesystem,
            no_configs=no_configs,
        )
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
            _run_both_orphan_phases(
                dry_run=dry_run,
                yes=yes,
                no_advisor=no_advisor,
                auto=auto,
                no_filesystem=no_filesystem,
                no_configs=no_configs,
            )
            return

    # Phase 6: Convert to actions and display
    actions = diff_to_actions(diff_result, purge=purge)

    if not actions:
        print_success("No actionable changes. System is in sync with manifest.")
        _run_both_orphan_phases(
            dry_run=dry_run,
            yes=yes,
            no_advisor=no_advisor,
            auto=auto,
            no_filesystem=no_filesystem,
            no_configs=no_configs,
        )
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
    available_operators = get_available_operators(source.to_package_source())

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

    # Domain orphan phases
    _run_both_orphan_phases(
        dry_run=dry_run,
        yes=yes,
        no_advisor=no_advisor,
        auto=auto,
        no_filesystem=no_filesystem,
        no_configs=no_configs,
    )

    # Exit with error if any action failed
    if any(r.failed for r in results):
        raise typer.Exit(code=1)


# =============================================================================
# Domain orphan phases
# =============================================================================


def _run_orphan_phases(
    domain: Literal["filesystem", "configs"],
    *,
    dry_run: bool,
    yes: bool,
    no_advisor: bool,
    auto: bool,
) -> None:
    """Generic pipeline for domain orphan scanning and cleanup.

    Handles both filesystem (phases 9-13) and config (phases 14-18)
    domains. Dispatches to domain-specific functions for scan, advisor,
    apply, and cleanup operations. All failures are non-fatal.

    Args:
        domain: Which domain to process.
        dry_run: If True, show findings but do not delete.
        yes: If True, skip confirmation prompts.
        no_advisor: If True, skip advisor classification.
        auto: If True, use headless advisor instead of interactive.
    """
    is_fs = domain == "filesystem"
    display = "filesystem" if is_fs else "config"
    label = display.capitalize()

    # Scan
    console.print(f"\n[bold]{label} Scan[/bold]")
    orphans: list[ScannedEntry] = _domain_scan(domain)

    if not orphans:
        print_info(f"No orphaned {display} entries found. Skipping {display} phases.")
        return

    print_info(f"Found {len(orphans)} orphaned {display} entries.")

    if dry_run:
        hint_cmd = "popctl fs scan" if is_fs else "popctl config scan"
        print_orphan_table(f"Orphaned {label} Entries", orphans, limit=20, hint_cmd=hint_cmd)
        print_info(f"Dry-run mode: No {display} changes made.")
        return

    # Advisor classification
    decisions: DomainDecisions | None = None
    if not no_advisor:
        console.print(f"\n[bold]{label} Advisor[/bold]")
        decisions = _domain_run_advisor(domain, orphans, auto)

    # Apply decisions to manifest
    if decisions:
        console.print(f"\n[bold]Apply {label} Decisions[/bold]")
        _domain_apply_decisions(domain, decisions)

    # Cleanup
    console.print(f"\n[bold]{label} Cleanup[/bold]")
    deleted_paths = _domain_clean(domain, yes=yes)

    # Record to history
    if deleted_paths:
        console.print(f"\n[bold]{label} History[/bold]")
        _record_orphan_history(domain, deleted_paths)


def _record_orphan_history(
    domain: Literal["filesystem", "configs"],
    deleted_paths: list[str],
) -> None:
    """Record domain deletions to history.

    Args:
        domain: Which domain the deletions belong to.
        deleted_paths: Paths that were successfully deleted.
    """
    display = "filesystem" if domain == "filesystem" else "config"

    try:
        record_domain_deletions(domain, deleted_paths, command="popctl sync")
        print_info(f"{display.capitalize()} deletions recorded to history.")
    except (OSError, RuntimeError) as e:
        print_warning(f"Could not record {display} history: {e}")


def _domain_scan(domain: Literal["filesystem", "configs"]) -> list[ScannedEntry]:
    """Scan a domain for orphaned entries.

    Args:
        domain: Which domain to scan.

    Returns:
        List of scanned objects with ORPHAN status.
        Empty list if the scan fails or finds nothing.
    """
    scanner_cls = FilesystemScanner if domain == "filesystem" else ConfigScanner  # type: ignore[assignment]

    try:
        scanner = scanner_cls()
        return [item for item in scanner.scan() if item.status == OrphanStatus.ORPHAN]
    except (OSError, RuntimeError) as e:
        print_warning(f"{domain.capitalize()} scan failed: {e}")
        return []


def _domain_run_advisor(
    domain: Literal["filesystem", "configs"],
    orphans: list[ScannedEntry],
    auto: bool,
) -> DomainDecisions | None:
    """Run advisor to classify domain orphans.

    Converts scanner results to exchange model entries, invokes the
    shared advisor workflow, and extracts the domain decisions.

    Args:
        domain: Which domain to classify.
        orphans: List of orphaned entries from the scanner.
        auto: If True, use headless advisor mode.

    Returns:
        Domain decisions or None if advisor is unavailable/fails.
    """
    entries = [
        OrphanEntry(
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
    key = "filesystem_orphans" if domain == "filesystem" else "config_orphans"
    advisor_kwargs: dict[str, Any] = {key: entries}

    print_info(f"Classifying {len(entries)} {domain} orphan(s) via advisor...")

    result = _invoke_advisor(auto=auto, domain=domain, **advisor_kwargs)
    domain_decisions = getattr(result, domain, None) if result else None
    if domain_decisions is None:
        return None

    print_success(f"{domain.capitalize()} advisor classification completed.")
    return domain_decisions


def _domain_apply_decisions(
    domain: Literal["filesystem", "configs"],
    decisions: DomainDecisions,
) -> None:
    """Apply advisor decisions to the manifest for a domain.

    Merges the advisor's keep/remove classifications into the manifest's
    domain section, preserving existing entries that are not reclassified.

    Args:
        domain: Which domain section to update ("filesystem" or "configs").
        decisions: Domain decisions from the advisor.
    """
    try:
        manifest = load_manifest()
    except ManifestError as e:
        print_warning(f"Could not load manifest for {domain} apply: {e}")
        return

    keep_entries: dict[str, DomainEntry] = {}
    remove_entries: dict[str, DomainEntry] = {}

    for decision in decisions.keep:
        keep_entries[decision.path] = DomainEntry(
            reason=decision.reason,
            category=decision.category,
        )

    for decision in decisions.remove:
        remove_entries[decision.path] = DomainEntry(
            reason=decision.reason,
            category=decision.category,
        )

    existing = getattr(manifest, domain)
    if existing:
        for path, entry in existing.keep.items():
            if path not in keep_entries and path not in remove_entries:
                keep_entries[path] = entry
        for path, entry in existing.remove.items():
            if path not in keep_entries and path not in remove_entries:
                remove_entries[path] = entry

    setattr(manifest, domain, DomainConfig(keep=keep_entries, remove=remove_entries))
    manifest.meta.updated = datetime.now(UTC)

    try:
        save_manifest(manifest)
        print_success(
            f"{domain.capitalize()} decisions applied to manifest "
            f"({len(keep_entries)} keep, {len(remove_entries)} remove)."
        )
    except (OSError, ManifestError) as e:
        print_warning(f"Could not save manifest after {domain} apply: {e}")
        return

    if decisions.ask:
        print_warning(
            f"{len(decisions.ask)} {domain} path(s) require manual decision. "
            "Run 'popctl advisor session' to classify them interactively."
        )
        for decision in decisions.ask:
            console.print(f"  [dim]-[/dim] {decision.path}: {decision.reason}")


def _domain_clean(domain: Literal["filesystem", "configs"], *, yes: bool) -> list[str]:
    """Delete paths marked for removal in manifest for a given domain.

    Loads the manifest, finds paths in the domain's ``[*.remove]``
    section, prompts for confirmation (unless ``--yes``), then
    delegates deletion to the appropriate operator.

    Args:
        domain: Which domain to clean ("filesystem" or "configs").
        yes: If True, skip confirmation prompt.

    Returns:
        List of paths that were successfully deleted.
    """
    is_fs = domain == "filesystem"
    label = "filesystem" if is_fs else "config"

    try:
        manifest = load_manifest()
    except ManifestError as e:
        print_warning(f"Could not load manifest for {label} cleanup: {e}")
        return []

    remove_paths = manifest.get_fs_remove_paths() if is_fs else manifest.get_config_remove_paths()
    if not remove_paths:
        print_info(f"No {label} entries marked for removal.")
        return []

    paths_to_delete = list(remove_paths.keys())
    print_info(f"{len(paths_to_delete)} {label} path(s) marked for removal.")

    if not yes:
        for p in paths_to_delete:
            console.print(f"  [error]DELETE[/] {p}")
        confirmed = typer.confirm(
            f"\nDelete {len(paths_to_delete)} {label} path(s)?",
            default=False,
        )
        if not confirmed:
            print_info(f"{label.capitalize()} cleanup skipped.")
            return []

    operator = FilesystemOperator() if is_fs else ConfigOperator()
    results = operator.delete(paths_to_delete)

    successful = [r.path for r in results if r.success]
    failed = [r for r in results if not r.success]

    if successful:
        if not is_fs:
            for r_ok in results:
                backup = getattr(r_ok, "backup_path", None)
                if r_ok.success and backup:
                    print_info(f"Backed up {r_ok.path} -> {backup}")
        print_success(f"Deleted {len(successful)} {label} path(s).")
    if failed:
        for r in failed:
            print_warning(f"Failed to delete {r.path}: {r.error}")

    return successful
