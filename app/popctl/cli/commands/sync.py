"""Sync command implementation.

Orchestrates the full system synchronization pipeline in a single
invocation: init -> diff -> advisor -> advisor-apply -> system-apply,
followed by optional filesystem scanning and cleanup phases.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Literal

import typer

from popctl.advisor import (
    AgentRunner,
    cleanup_empty_sessions,
    delete_session,
    import_decisions,
)
from popctl.advisor.config import AdvisorConfigError, load_or_create_config
from popctl.advisor.exchange import (
    DecisionsResult,
    DomainDecisions,
    apply_decisions_to_manifest,
    apply_domain_decisions_to_manifest,
    record_advisor_apply_to_history,
)
from popctl.advisor.runner import MANUAL_MODE_SENTINEL
from popctl.advisor.scanning import scan_system
from popctl.advisor.workspace import create_session_workspace, ensure_advisor_sessions_dir
from popctl.cli.display import (
    create_actions_table,
    create_results_table,
    print_actions_summary,
    print_orphan_table,
    print_results_summary,
)
from popctl.cli.types import (
    SourceChoice,
    collect_domain_orphans,
    compute_system_diff,
)
from popctl.configs import ConfigOperator
from popctl.core.diff import DiffResult, diff_to_actions
from popctl.core.executor import execute_actions, record_actions_to_history
from popctl.core.manifest import (
    ManifestError,
    load_manifest,
    manifest_exists,
    save_manifest,
    scan_and_create_manifest,
)
from popctl.core.paths import get_manifest_path, get_state_dir
from popctl.core.state import record_domain_deletions
from popctl.domain.models import ScannedEntry
from popctl.domain.protected import is_protected
from popctl.filesystem import FilesystemOperator
from popctl.operators import get_available_operators
from popctl.scanners import get_available_scanners
from popctl.utils.formatting import (
    console,
    print_error,
    print_info,
    print_success,
    print_warning,
)

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
        manifest, packages, _ = scan_and_create_manifest(scanners)
    except RuntimeError as e:
        print_error(f"Scan failed: {e}")
        raise typer.Exit(code=1) from e

    if not packages:
        print_warning("No manually installed packages found (excluding protected system packages).")

    try:
        saved_path = save_manifest(manifest)
        print_success(f"Manifest created: {saved_path}")
    except OSError as e:
        print_error(f"Failed to save manifest: {e}")
        raise typer.Exit(code=1) from e


def _invoke_advisor(
    *,
    auto: bool,
    domain: str,
    filesystem_orphans: list[dict[str, Any]] | None = None,
    config_orphans: list[dict[str, Any]] | None = None,
    review: bool = False,
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
        review: If True, advisor reviews existing classifications.

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
    except RuntimeError:
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
            domain=domain,
            review=review,
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
        cleanup_empty_sessions(sessions_dir)
        return None

    if result.error == MANUAL_MODE_SENTINEL:
        console.print()
        console.print(result.output)
        cleanup_empty_sessions(sessions_dir)
        return None

    if not result.success or not result.decisions_path:
        print_warning(
            f"{domain.capitalize()} advisor did not produce decisions: "
            f"{result.error or 'unknown error'}"
        )
        cleanup_empty_sessions(sessions_dir)
        return None

    try:
        decisions = import_decisions(result.decisions_path)
    except (FileNotFoundError, ValueError) as e:
        print_warning(f"Could not load advisor decisions: {e}")
        return None

    # Delete ephemeral session (sync applies immediately)
    delete_session(result.decisions_path)
    return decisions


def _run_advisor(diff_result: DiffResult, auto: bool, *, review: bool = False) -> None:
    """Run AI advisor to classify NEW packages.

    If the advisor produces decisions, they are applied to the manifest.
    Advisor failures are non-fatal: a warning is printed and sync continues.

    Args:
        diff_result: Current diff result containing NEW packages.
        auto: If True, run headless advisor; otherwise interactive.
        review: If True, advisor reviews existing classifications.
    """
    if review:
        print_info("Review mode: running advisor to review existing classifications...")
    else:
        print_info(f"{len(diff_result.new)} NEW package(s) found. Running advisor...")

    decisions = _invoke_advisor(auto=auto, domain="packages", review=review)
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


def _sync_packages(
    *,
    source: SourceChoice,
    yes: bool,
    dry_run: bool,
    purge: bool,
    no_advisor: bool,
    auto: bool,
    review: bool,
) -> bool:
    """Run the package synchronization pipeline.

    Encapsulates diff, advisor, action execution, and history recording.
    Domain orphan phases are intentionally NOT called here; the caller
    (``sync()``) runs them once after this function returns.

    Args:
        source: Package source filter (apt, flatpak, or all).
        yes: If True, skip confirmation prompts.
        dry_run: If True, show diff only without executing.
        purge: If True, use purge instead of remove for APT packages.
        no_advisor: If True, skip AI advisor classification.
        auto: If True, use headless advisor instead of interactive.

    Returns:
        True if any executed action failed, False otherwise.

    Raises:
        typer.Exit: If the user aborts at the confirmation prompt (code=0).
    """
    # Phase 2: Compute diff
    diff_result = compute_system_diff(source)

    # Check if system is already in sync
    if diff_result.is_in_sync and not review:
        print_success("System is already in sync with manifest. Nothing to do.")
        return False

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
        return False

    # Phase 3-5: Advisor (unless --no-advisor or no NEW packages)
    if not no_advisor and (diff_result.new or review):
        _run_advisor(diff_result, auto, review=review)

        # Phase 5: Re-diff after advisor changes
        diff_result = compute_system_diff(source)

        if diff_result.is_in_sync:
            print_success(
                "System is already in sync with manifest after advisor changes. Nothing to do."
            )
            return False

    # Phase 6: Convert to actions and display
    actions = diff_to_actions(diff_result, purge=purge)

    if not actions:
        print_success("No actionable changes. System is in sync with manifest.")
        return False

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

    return any(r.failed for r in results)


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
    review: Annotated[
        bool,
        typer.Option(
            "--review",
            help="Force advisor session to review existing manifest classifications.",
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

    # Package sync phases
    any_failed = _sync_packages(
        source=source,
        yes=yes,
        dry_run=dry_run,
        purge=purge,
        no_advisor=no_advisor,
        auto=auto,
        review=review,
    )

    # Domain orphan phases (always run after package sync)
    _run_both_orphan_phases(
        dry_run=dry_run,
        yes=yes,
        no_advisor=no_advisor,
        auto=auto,
        no_filesystem=no_filesystem,
        no_configs=no_configs,
    )

    if any_failed:
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
        List of orphan entries sorted by confidence (desc).
        Empty list if the scan fails or finds nothing.
    """
    try:
        return collect_domain_orphans(domain)
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
    entries: list[dict[str, Any]] = []
    for p in orphans:
        d = {k: v for k, v in p.to_dict().items() if v is not None}
        d.setdefault("orphan_reason", "unknown")
        entries.append(d)
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

    Thin CLI wrapper around apply_domain_decisions_to_manifest() that
    handles manifest I/O and user-facing output.

    Args:
        domain: Which domain section to update ("filesystem" or "configs").
        decisions: Domain decisions from the advisor.
    """
    try:
        manifest = load_manifest()
    except ManifestError as e:
        print_warning(f"Could not load manifest for {domain} apply: {e}")
        return

    ask_decisions = apply_domain_decisions_to_manifest(manifest, domain, decisions)
    manifest.meta.updated = datetime.now(UTC)

    try:
        save_manifest(manifest)
        print_success(
            f"{domain.capitalize()} decisions applied to manifest "
            f"({len(decisions.keep)} keep, {len(decisions.remove)} remove)."
        )
    except (OSError, ManifestError) as e:
        print_warning(f"Could not save manifest after {domain} apply: {e}")
        return

    if ask_decisions:
        print_warning(
            f"{len(ask_decisions)} {domain} path(s) require manual decision. "
            "Run 'popctl advisor session' to classify them interactively."
        )
        for decision in ask_decisions:
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

    # Filter out protected paths (defense-in-depth: operators also check)
    paths_to_delete: list[str] = []
    for path_str in remove_paths:
        if is_protected(path_str, domain):
            print_warning(f"Skipping protected {label} path: {path_str}")
            continue
        paths_to_delete.append(path_str)
    if not paths_to_delete:
        print_info(f"No unprotected {label} entries to delete.")
        return []
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

        # Remove successfully deleted paths from manifest.
        # Use original paths_to_delete (tilde form) as manifest keys,
        # not result.path (expanded by operator to absolute form).
        section = manifest.filesystem if is_fs else manifest.configs
        if section:
            for result, original_path in zip(results, paths_to_delete, strict=True):
                if result.success:
                    section.remove.pop(original_path, None)
            manifest.meta.updated = datetime.now(UTC)
            try:
                save_manifest(manifest)
            except (OSError, ManifestError) as e:
                print_warning(f"Could not update manifest after {label} cleanup: {e}")

    if failed:
        for r in failed:
            print_warning(f"Failed to delete {r.path}: {r.error}")

    return successful
