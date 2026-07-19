from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from collections.abc import Collection, Iterable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Never, cast

import typer
from rich.table import Table

from popctl.advisor import AgentRunner, get_session_manager, import_decisions
from popctl.advisor.config import load_or_create_config
from popctl.advisor.exchange import (
    DotfilesDecisions,
    DotfilesReviewFinalization,
    finalize_dotfiles_review,
)
from popctl.advisor.prompts import DOTFILES_INITIAL_PROMPT
from popctl.advisor.workspace import create_dotfiles_session_workspace, ensure_advisor_sessions_dir
from popctl.cli.types import SourceChoice, compute_system_diff
from popctl.core.state import record_action
from popctl.dotfiles.config import (
    DotfilesConfig,
    DotfilesConfigError,
    RemotePrivacyRecord,
    get_dotfiles_config_path,
    load_dotfiles_config,
    save_dotfiles_config,
)
from popctl.dotfiles.discovery import DiscoveryResult, discover_dotfiles
from popctl.dotfiles.materialize import (
    HomeFileSnapshot,
    MaterializationError,
    MaterializationSource,
    execute_materialization_plan,
    preflight_materialization,
    read_home_regular_file,
    render_materialization_plan,
)
from popctl.dotfiles.repo import (
    MAIN_REF,
    REMOTE_MAIN_REF,
    DotfilesRepo,
    DotfilesRepoError,
    PathClassification,
    PathState,
    RefRelation,
    TransportOutcome,
    TreeEntry,
    TreeRead,
    TreeValidationError,
    validate_remote_url,
)
from popctl.dotfiles.secret_filter import SecretVerdictKind, scan_dotfile, scan_dotfile_bytes
from popctl.dotfiles.state import (
    DotfilesLockError,
    DotfilesRecoveryError,
    DotfilesStateError,
    InitFinalizationJournal,
    InitPhase,
    MaterializationPlan,
    PlanOperation,
    clear_init_finalization_journal,
    complete_materialization_state,
    complete_materialization_state_for_source,
    dotfiles_lock,
    get_dotfiles_state_dir,
    get_plan_path,
    load_materialization_plan,
    recover_init_finalization,
    retire_completed_materialization_state_for_local_ref,
    save_init_finalization_journal,
)
from popctl.models.history import HistoryActionType, HistoryItem, create_history_entry
from popctl.utils.formatting import console, print_error, print_info, print_success, print_warning
from popctl.utils.shell import run_command

app = typer.Typer(
    name="dotfiles",
    help="Version and restore private user dotfiles.",
    no_args_is_help=True,
)


class DotfilesCommandError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class ReviewResult:
    finalization: DotfilesReviewFinalization
    config: DotfilesConfig
    cancelled: bool = False


def _interactive() -> bool:
    return sys.stdin.isatty()


def _refuse(message: str) -> Never:
    raise DotfilesCommandError(message)


def _state_dir() -> Path:
    return get_dotfiles_state_dir()


def _load_initialized() -> DotfilesConfig:
    path = get_dotfiles_config_path()
    if not path.exists():
        _refuse("Dotfiles are not initialized. Run 'popctl dotfiles init' first.")
    try:
        config = load_dotfiles_config(path)
    except DotfilesConfigError as e:
        _refuse(str(e))
    if not config.remote_url:
        _refuse("Dotfiles configuration has no remote URL. Re-run 'popctl dotfiles init'.")
    if not config.bare_repo.is_dir():
        _refuse(
            f"Dotfiles repository is missing at {config.bare_repo}. "
            "Restore it or run 'popctl dotfiles init --from <url>'."
        )
    return config


def _tracked_entries(repo: DotfilesRepo) -> tuple[TreeEntry, ...]:
    if repo.ref_oid(MAIN_REF) is None:
        return ()
    return repo.read_tree(MAIN_REF).entries


def _tracked_paths(entries: Collection[TreeEntry]) -> tuple[str, ...]:
    return tuple(sorted(entry.path for entry in entries))


def _base_files(repo: DotfilesRepo, entries: Collection[TreeEntry]) -> dict[str, HomeFileSnapshot]:
    return {
        entry.path: HomeFileSnapshot(repo.read_blob(entry.oid), entry.mode) for entry in entries
    }


def _sources(repo: DotfilesRepo, entries: Iterable[TreeEntry]) -> tuple[MaterializationSource, ...]:
    return tuple(
        MaterializationSource(
            path=entry.path,
            oid=entry.oid,
            mode=entry.mode,
            content=repo.read_blob(entry.oid),
        )
        for entry in sorted(entries, key=lambda item: item.path)
    )


def _preflight_or_resume_materialization(
    *,
    operation: PlanOperation,
    source_ref: str,
    source_tree_oid: str,
    sources: tuple[MaterializationSource, ...],
    base_files: dict[str, HomeFileSnapshot],
    home: Path,
) -> MaterializationPlan:
    plan_path = get_plan_path(operation, _state_dir())
    if plan_path.exists():
        plan = load_materialization_plan(operation, _state_dir())
        expected_sources = {(source.path, source.oid, source.mode) for source in sources}
        planned_sources = {(entry.path, entry.oid, entry.mode) for entry in plan.entries}
        if (
            plan.source_ref != source_ref
            or plan.source_tree_oid != source_tree_oid
            or planned_sources != expected_sources
        ):
            raise DotfilesStateError("Materialization state does not match the validated source")
        return plan
    return preflight_materialization(
        operation=operation,
        source_ref=source_ref,
        source_tree_oid=source_tree_oid,
        sources=sources,
        base_files=base_files,
        home=home,
    )


def _record_dotfiles_action(
    action_type: HistoryActionType,
    paths: Collection[str],
    *,
    repo: DotfilesRepo,
) -> None:
    names = tuple(sorted(set(paths))) or ("dotfiles",)
    try:
        record_action(
            create_history_entry(
                action_type,
                [HistoryItem(name=path) for path in names],
                reversible=False,
                metadata={
                    "main": repo.ref_oid(MAIN_REF) or "",
                    "remote_main": repo.ref_oid(REMOTE_MAIN_REF) or "",
                },
            )
        )
    except (OSError, RuntimeError) as e:
        print_warning(f"Dotfiles change completed, but history could not be recorded: {e}")


def _transport_detail(outcome: TransportOutcome) -> str:
    return {
        TransportOutcome.OFFLINE: "offline",
        TransportOutcome.TIMEOUT: "timed out",
        TransportOutcome.AUTH: "authentication failed",
        TransportOutcome.OTHER: "failed",
    }.get(outcome, "failed")


def _require_transport(result_outcome: TransportOutcome, *, action: str) -> None:
    if result_outcome is not TransportOutcome.SUCCESS:
        _refuse(f"{action} {_transport_detail(result_outcome)}.")


def _remote_slug(url: str) -> str:
    if url.startswith("https://github.com/"):
        return url.removeprefix("https://github.com/").removesuffix(".git")
    return url.removeprefix("git@github.com:").removesuffix(".git")


def _gh_is_private(url: str) -> bool | None:
    result = run_command(["gh", "repo", "view", _remote_slug(url), "--json", "isPrivate"])
    if not result.success:
        return None
    try:
        data: object = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    value = cast("dict[str, object]", data).get("isPrivate") if isinstance(data, dict) else None
    return value if isinstance(value, bool) else None


def _acquire_private_remote(
    url: str,
    *,
    allow_create: bool,
    interactive: bool,
) -> tuple[RemotePrivacyRecord, bool]:
    if shutil.which("gh") is not None:
        private = _gh_is_private(url)
        created = False
        if private is None and allow_create:
            result = run_command(["gh", "repo", "create", _remote_slug(url), "--private"])
            if not result.success:
                _refuse("Could not create the requested private GitHub repository.")
            created = True
            private = _gh_is_private(url)
        if private is not True:
            _refuse(f"GitHub could not verify that {url} is a private repository.")
        return (
            RemotePrivacyRecord(canonical_remote_url=url, method="verified"),
            created,
        )
    if not interactive:
        _refuse(
            "GitHub CLI is unavailable, so remote privacy cannot be verified "
            "non-interactively. Install gh or rerun interactively to acknowledge the exact URL."
        )
    acknowledged = typer.confirm(
        f"Privacy is unverified. Confirm that this exact destination is private: {url}",
        default=False,
    )
    if not acknowledged:
        _refuse("Private-remote acknowledgement was declined.")
    return RemotePrivacyRecord(canonical_remote_url=url, method="acknowledged"), False


def _pre_push_privacy(config: DotfilesConfig, *, interactive: bool) -> DotfilesConfig:
    canonical_url = validate_remote_url(config.remote_url)
    if shutil.which("gh") is not None:
        if _gh_is_private(canonical_url) is not True:
            _refuse("GitHub privacy recheck failed; refusing to push dotfiles.")
        return config.with_remote_privacy_record(canonical_url, method="verified")
    record = config.remote_privacy
    if (
        record is not None
        and record.method == "acknowledged"
        and record.canonical_remote_url == canonical_url
    ):
        return config
    if not interactive:
        _refuse(
            "The remote URL lacks a matching unverified-private acknowledgement; "
            "rerun sync interactively to acknowledge it."
        )
    acknowledged = typer.confirm(
        f"Privacy is unverified. Confirm that this exact destination is private: {canonical_url}",
        default=False,
    )
    if not acknowledged:
        _refuse("Private-remote acknowledgement was declined.")
    return config.with_remote_privacy_record(canonical_url, method="acknowledged")


def _select_normal_remote(
    remote: str | None,
    *,
    recovered_remote: str | None,
    interactive: bool,
) -> str:
    if remote is not None:
        try:
            return validate_remote_url(remote)
        except DotfilesRepoError as e:
            _refuse(str(e))
    if recovered_remote is not None:
        print_info(
            f"Reusing the remote created by the interrupted initialization: {recovered_remote}"
        )
        return recovered_remote
    if not interactive:
        _refuse("Specify --remote <url> when running dotfiles init non-interactively.")
    owner = typer.prompt("GitHub owner")
    name = typer.prompt("GitHub repository", default="popctl-dotfiles")
    candidate = f"https://github.com/{owner}/{name}.git"
    try:
        return validate_remote_url(candidate)
    except DotfilesRepoError as e:
        _refuse(str(e))


def _acknowledge_discovery_ambiguities(
    discovery: DiscoveryResult,
    *,
    home: Path,
    allowlist: Collection[str],
    interactive: bool,
) -> tuple[DiscoveryResult, tuple[str, ...]]:
    accepted = set(allowlist)
    for blocked in discovery.blocked:
        verdict = scan_dotfile(home / blocked.path, home=home, ambiguous_content_allowlist=accepted)
        if not verdict.allowlistable:
            continue
        if not interactive:
            print_warning(
                f"Blocked ambiguous dotfile ~/{blocked.path} ({verdict.category}); "
                "non-interactive init will not acknowledge it."
            )
            continue
        confirmed = typer.confirm(
            f"Allow ambiguous content for ~/{blocked.path} ({verdict.category})?",
            default=False,
        )
        if confirmed:
            accepted.add(blocked.path)
    updated = discover_dotfiles(
        home,
        ambiguous_content_allowlist=accepted,
    )
    return updated, tuple(sorted(accepted))


def _acknowledge_tree_ambiguities(
    repo: DotfilesRepo,
    tree: TreeRead,
    *,
    allowlist: Collection[str],
    interactive: bool,
) -> tuple[str, ...]:
    accepted = set(allowlist)
    for entry in sorted(tree.entries, key=lambda item: item.path):
        verdict = scan_dotfile_bytes(
            entry.path,
            repo.read_blob(entry.oid),
            ambiguous_content_allowlist=accepted,
        )
        if verdict.kind is not SecretVerdictKind.DENIED_AMBIGUOUS_CONTENT:
            continue
        if not interactive:
            _refuse(
                f"Remote tree has unacknowledged ambiguous content at {entry.path} "
                f"({verdict.category}); rerun interactively."
            )
        confirmed = typer.confirm(
            f"Allow ambiguous content for ~/{entry.path} ({verdict.category})?",
            default=False,
        )
        if not confirmed:
            _refuse(f"Ambiguous remote content acknowledgement declined for {entry.path}.")
        accepted.add(entry.path)
    return tuple(sorted(accepted))


def _review_candidates(
    discovery: DiscoveryResult,
    config: DotfilesConfig,
    *,
    interactive: bool,
) -> ReviewResult:
    empty = DotfilesReviewFinalization((), (), ())
    if not discovery.candidates:
        return ReviewResult(empty, config)
    advisor_config = load_or_create_config()
    session = get_session_manager()
    sessions_dir = ensure_advisor_sessions_dir(use_djinn=session is not None)
    workspace = create_dotfiles_session_workspace(discovery, sessions_dir)
    runner = AgentRunner(advisor_config, session=session)
    result = (
        runner.launch_interactive(workspace, DOTFILES_INITIAL_PROMPT)
        if interactive
        else runner.run_headless(workspace, DOTFILES_INITIAL_PROMPT)
    )
    if not result.success or result.decisions_path is None:
        if interactive:
            _refuse(f"Dotfiles advisor failed: {result.error or 'no decisions produced'}")
        print_warning("Dotfiles advisor did not produce decisions; no new paths will be added.")
        return ReviewResult(empty, config)
    try:
        imported = import_decisions(result.decisions_path, discovery=discovery)
    except (OSError, ValueError) as e:
        _refuse(f"Invalid dotfiles advisor decisions: {e}")
    decisions = imported.dotfiles
    if decisions is None:
        _refuse("Dotfiles advisor produced no [dotfiles] decisions section.")
    if not interactive:
        print_info("New dotfile candidates were reported but not added non-interactively.")
        return ReviewResult(empty, config)
    _display_review(decisions)
    if not typer.confirm("Confirm these dotfiles track/ignore decisions?", default=False):
        return ReviewResult(empty, config, cancelled=True)
    captured: dict[str, object] = {}

    def capture(paths: tuple[str, ...], updated: DotfilesConfig) -> None:
        captured["paths"] = paths
        captured["config"] = updated

    finalization = finalize_dotfiles_review(
        decisions,
        discovery,
        config,
        confirmed=True,
        finalize_operation=capture,
    )
    updated = captured.get("config")
    if not isinstance(updated, DotfilesConfig):
        _refuse("Dotfiles review could not be finalized.")
    return ReviewResult(finalization, updated)


def _display_review(decisions: DotfilesDecisions) -> None:
    table = Table(title="Dotfiles review")
    table.add_column("Decision")
    table.add_column("Path")
    for action, values in (
        ("track", decisions.track),
        ("ignore", decisions.ignore),
        ("ask", decisions.ask),
    ):
        for decision in values:
            table.add_row(action, f"~/{decision.path}")
    console.print(table)


def _promote_initialized_store(
    *,
    temporary_store: Path,
    final_store: Path,
    config: DotfilesConfig,
    created_remote: str | None,
) -> None:
    journal = InitFinalizationJournal(
        temporary_store=temporary_store,
        final_store=final_store,
        config_path=get_dotfiles_config_path(),
        phase=InitPhase.PREPARED,
        created_remote=created_remote,
    )
    save_init_finalization_journal(journal, _state_dir())
    try:
        os.replace(temporary_store, final_store)
    except OSError as e:
        raise DotfilesCommandError(f"Could not promote dotfiles repository: {e}") from e
    journal = journal.with_phase(InitPhase.STORE_PROMOTED)
    save_init_finalization_journal(journal, _state_dir())
    try:
        save_dotfiles_config(config)
    except DotfilesConfigError as e:
        raise DotfilesCommandError(str(e)) from e
    save_init_finalization_journal(journal.with_phase(InitPhase.CONFIG_WRITTEN), _state_dir())
    clear_init_finalization_journal(_state_dir())


def _cleanup_unpromoted_store(path: Path) -> None:
    with suppress(OSError):
        if path.exists():
            shutil.rmtree(path)


def _ensure_empty_destination(repo: DotfilesRepo, url: str) -> None:
    listing = repo.ls_remote_all_refs(url)
    _require_transport(listing.transport.outcome, action="Remote reachability check")
    if listing.refs:
        _refuse(
            "The destination already has refs. Use 'popctl dotfiles init --from "
            f"{url}' to bootstrap from an existing popctl repository."
        )


def _remote_tree_or_refuse(
    repo: DotfilesRepo,
    tracked: Collection[str],
    allowlist: Collection[str],
    *,
    source_ref: str = REMOTE_MAIN_REF,
    require_tracked_paths: bool = True,
) -> TreeRead:
    tree = repo.validate_tree(source_ref, ambiguous_content_allowlist=allowlist)
    if not require_tracked_paths:
        return tree
    source_paths = {entry.path for entry in tree.entries}
    dropped = sorted(set(tracked) - source_paths)
    if dropped:
        _print_recovery(repo, dropped)
        _refuse("Remote source drops tracked path(s): " + ", ".join(dropped))
    return tree


def _print_recovery(
    repo: DotfilesRepo,
    paths: Collection[str],
    *,
    divergence: bool = False,
) -> None:
    if divergence:
        print_info(
            f'git --git-dir="{repo.bare_repo}" --work-tree="$HOME" '
            "log --left-right main...origin/main"
        )
    for path in sorted(set(paths)):
        print_info(
            f'git --git-dir="{repo.bare_repo}" --work-tree="$HOME" diff origin/main -- "{path}"'
        )
    print_info(f'git --git-dir="{repo.bare_repo}" --work-tree="$HOME" merge origin/main')
    print_info("Resolve with plain Git, then rerun 'popctl dotfiles sync'.")


def _refuse_sync_conflicts(
    repo: DotfilesRepo,
    relation: RefRelation,
    classifications: Collection[PathClassification],
) -> None:
    conflicts = [
        item.path
        for item in classifications
        if getattr(item, "state", None) in {PathState.BOTH_CHANGED, PathState.CONFLICTED}
    ]
    if relation is RefRelation.DIVERGED:
        changed = [item.path for item in classifications if item.state is not PathState.CLEAN]
        _print_recovery(repo, changed, divergence=True)
        _refuse("Dotfiles histories have diverged.")
    if conflicts:
        _print_recovery(repo, conflicts)
        _refuse("Conflicted dotfiles paths: " + ", ".join(sorted(conflicts)))


def _refuse_remote_changes_to_deleted_paths(
    repo: DotfilesRepo,
    classifications: Collection[PathClassification],
    remote_changed: Collection[str],
) -> None:
    deleted = {item.path for item in classifications if item.state is PathState.MISSING}
    changed_deleted = tuple(sorted(deleted & set(remote_changed)))
    if changed_deleted:
        _print_recovery(repo, changed_deleted)
        _refuse("Remote changes locally deleted tracked path(s): " + ", ".join(changed_deleted))


def _safe_changed_tracked_paths(repo: DotfilesRepo, tracked: Collection[str]) -> tuple[str, ...]:
    changed = repo.work_tree_changed_paths(tracked)
    present: list[str] = []
    for path in sorted(changed):
        try:
            read_home_regular_file(repo.home, path)
        except FileNotFoundError:
            print_warning(f"Tracked dotfile is missing and will not be committed: ~/{path}")
            continue
        except MaterializationError as e:
            _refuse(str(e))
        except Exception as e:
            _refuse(f"Unsafe tracked dotfile {path}: {e}")
        present.append(path)
    return tuple(present)


def _push_or_refuse(
    repo: DotfilesRepo,
    config: DotfilesConfig,
    *,
    interactive: bool,
) -> DotfilesConfig:
    updated = _pre_push_privacy(config, interactive=interactive)
    if updated != config:
        save_dotfiles_config(updated)
    transport = repo.push(updated.remote_url)
    if not transport.success:
        _refuse(
            f"Dotfiles push {_transport_detail(transport.outcome)}; local commit remains pending."
        )
    return updated


def _push_pending_empty_remote(
    repo: DotfilesRepo,
    config: DotfilesConfig,
    *,
    interactive: bool,
) -> None:
    if repo.ref_oid(MAIN_REF) is None:
        _refuse("Remote dotfiles main ref is absent and no local commit is available to push.")
    _push_or_refuse(repo, config, interactive=interactive)
    print_success("Pushed pending dotfiles commit to the empty remote.")


def _is_missing_remote_main_ref(transport_stderr: str) -> bool:
    lowered = transport_stderr.lower()
    return "couldn't find remote ref" in lowered or "could not find remote ref" in lowered


@app.command()
def init(
    remote: Annotated[
        str | None,
        typer.Option("--remote", help="Private GitHub repository URL."),
    ] = None,
    from_url: Annotated[
        str | None,
        typer.Option("--from", help="Bootstrap from an existing popctl dotfiles repository."),
    ] = None,
) -> None:
    """Create or bootstrap the private dotfiles repository."""
    if remote is not None and from_url is not None:
        print_error("--remote and --from are mutually exclusive.")
        raise typer.Exit(code=1)
    interactive = _interactive()
    try:
        with dotfiles_lock(_state_dir()):
            recovery = recover_init_finalization(_state_dir())
            config_path = get_dotfiles_config_path()
            probe = load_dotfiles_config() if config_path.exists() else DotfilesConfig()
            if config_path.exists() or probe.bare_repo.exists():
                _refuse(
                    "Dotfiles are already initialized. Use 'popctl dotfiles status' or "
                    "'popctl dotfiles init --from <url>' on a fresh machine."
                )
            if from_url is not None:
                _init_from(from_url, interactive=interactive, final_store=probe.bare_repo)
            else:
                _init_new(
                    remote,
                    interactive=interactive,
                    final_store=probe.bare_repo,
                    recovered_remote=recovery.reusable_remote if recovery is not None else None,
                )
    except (
        DotfilesCommandError,
        DotfilesConfigError,
        DotfilesLockError,
        DotfilesRepoError,
        DotfilesStateError,
        TreeValidationError,
    ) as e:
        print_error(str(e))
        raise typer.Exit(code=1) from None


def _init_new(
    remote: str | None,
    *,
    interactive: bool,
    final_store: Path,
    recovered_remote: str | None,
) -> None:
    home = Path.home()
    initial_discovery = discover_dotfiles(home)
    discovery, allowlist = _acknowledge_discovery_ambiguities(
        initial_discovery,
        home=home,
        allowlist=(),
        interactive=interactive,
    )
    review = _review_candidates(
        discovery,
        DotfilesConfig(bare_repo=final_store),
        interactive=interactive,
    )
    if review.cancelled:
        print_info("Dotfiles initialization cancelled; no repository was created.")
        return
    if not review.finalization.tracked_paths:
        _refuse(
            "No dotfiles were selected. Review at least one safe leaf file, then rerun "
            "'popctl dotfiles init'."
        )
    canonical_url = _select_normal_remote(
        remote,
        recovered_remote=recovered_remote,
        interactive=interactive,
    )
    privacy, created = _acquire_private_remote(
        canonical_url,
        allow_create=True,
        interactive=interactive,
    )
    final_store.parent.mkdir(parents=True, exist_ok=True)
    temporary_store = Path(tempfile.mkdtemp(prefix=".dotfiles.git.", dir=final_store.parent))
    repo = DotfilesRepo(temporary_store, home=home, state_dir=_state_dir())
    promoted = False
    try:
        if created:
            save_init_finalization_journal(
                InitFinalizationJournal(
                    temporary_store=temporary_store,
                    final_store=final_store,
                    config_path=get_dotfiles_config_path(),
                    phase=InitPhase.PREPARED,
                    created_remote=canonical_url,
                ),
                _state_dir(),
            )
        repo.initialize_bare()
        repo.setup_remote(canonical_url)
        _ensure_empty_destination(repo, canonical_url)
        repo.checked_commit(
            review.finalization.tracked_paths,
            "Initialize popctl dotfiles",
            ambiguous_content_allowlist=allowlist,
        )
        repo.create_marker()
        config = review.config.model_copy(
            update={
                "bare_repo": final_store,
                "remote_url": canonical_url,
                "ambiguous_content_allowlist": list(allowlist),
                "remote_privacy": privacy,
            }
        )
        _promote_initialized_store(
            temporary_store=temporary_store,
            final_store=final_store,
            config=config,
            created_remote=canonical_url if created else None,
        )
        promoted = True
        repo = DotfilesRepo(final_store, home=home, state_dir=_state_dir())
        config = _pre_push_privacy(config, interactive=interactive)
        save_dotfiles_config(config)
        transport = repo.push(canonical_url)
        _record_dotfiles_action(
            HistoryActionType.DOTFILES_INIT,
            review.finalization.tracked_paths,
            repo=repo,
        )
        if not transport.success:
            print_warning(
                f"Initial dotfiles push {_transport_detail(transport.outcome)}; "
                "the initialized local commit is pending and the next online sync will retry."
            )
            return
        print_success("Initialized private dotfiles repository.")
    finally:
        if not promoted:
            _cleanup_unpromoted_store(temporary_store)


def _init_from(from_url: str, *, interactive: bool, final_store: Path) -> None:
    try:
        canonical_url = validate_remote_url(from_url)
    except DotfilesRepoError as e:
        _refuse(str(e))
    privacy, _created = _acquire_private_remote(
        canonical_url,
        allow_create=False,
        interactive=interactive,
    )
    final_store.parent.mkdir(parents=True, exist_ok=True)
    temporary_store = Path(tempfile.mkdtemp(prefix=".dotfiles.git.", dir=final_store.parent))
    repo = DotfilesRepo(temporary_store, home=Path.home(), state_dir=_state_dir())
    promoted = False
    try:
        repo.initialize_bare()
        repo.setup_remote(canonical_url)
        main_result = repo.fetch(canonical_url)
        _require_transport(main_result.outcome, action="Bootstrap fetch")
        marker_result = repo.fetch_marker(canonical_url)
        _require_transport(marker_result.outcome, action="Format-marker fetch")
        if repo.ref_oid(REMOTE_MAIN_REF) is None or not repo.verify_marker():
            _refuse(
                "Remote is not a valid popctl dotfiles repository "
                "(main ref or format marker missing)."
            )
        raw_tree = repo.read_tree(REMOTE_MAIN_REF)
        allowlist = _acknowledge_tree_ambiguities(
            repo,
            raw_tree,
            allowlist=(),
            interactive=interactive,
        )
        tree = repo.validate_tree(REMOTE_MAIN_REF, ambiguous_content_allowlist=allowlist)
        config = DotfilesConfig(
            bare_repo=final_store,
            remote_url=canonical_url,
            ambiguous_content_allowlist=list(allowlist),
            remote_privacy=privacy,
        )
        _promote_initialized_store(
            temporary_store=temporary_store,
            final_store=final_store,
            config=config,
            created_remote=None,
        )
        promoted = True
        repo = DotfilesRepo(final_store, home=Path.home(), state_dir=_state_dir())
        _record_dotfiles_action(
            HistoryActionType.DOTFILES_INIT,
            [entry.path for entry in tree.entries],
            repo=repo,
        )
        print_success("Bootstrapped private dotfiles repository.")
        print_info("Next: run 'popctl dotfiles apply'.")
    finally:
        if not promoted:
            _cleanup_unpromoted_store(temporary_store)


@app.command()
def status() -> None:
    """Report dotfiles state without changing local files or refs."""
    try:
        config = _load_initialized()
        repo = DotfilesRepo(
            config.bare_repo,
            home=Path.home(),
            state_dir=_state_dir(),
            read_only=True,
        )
        fetch = repo.fetch(config.remote_url, status=True)
        if not fetch.success:
            if fetch.outcome is not TransportOutcome.OFFLINE:
                _refuse(f"Status fetch {_transport_detail(fetch.outcome)}.")
            print_warning("Offline: using cached origin/main for dotfiles status.")
        if repo.ref_oid(REMOTE_MAIN_REF) is None:
            _refuse("No cached remote dotfiles ref is available.")
        entries = _tracked_entries(repo)
        if not entries:
            print_info("Dotfiles bootstrap is pending; run 'popctl dotfiles apply'.")
            return
        relation = repo.merge_base_relation()
        classifications = repo.classify_paths(_tracked_paths(entries))
        table = Table(title="Dotfiles status")
        table.add_column("Path")
        table.add_column("State")
        for item in classifications:
            table.add_row(f"~/{item.path}", item.public_state.value)
        console.print(table)
        print_info(f"Branch relation: {relation.value}")
        discovery = discover_dotfiles(
            Path.home(),
            tracked_files=_tracked_paths(entries),
            ignored=config.ignored,
            ambiguous_content_allowlist=config.ambiguous_content_allowlist,
        )
        _display_discovery(discovery)
        if relation is RefRelation.DIVERGED:
            _print_recovery(repo, (), divergence=True)
            _refuse("Dotfiles histories have diverged.")
    except (DotfilesCommandError, DotfilesConfigError, DotfilesRepoError, TreeValidationError) as e:
        print_error(str(e))
        raise typer.Exit(code=1) from None


def _display_discovery(discovery: DiscoveryResult) -> None:
    if discovery.candidates:
        print_info(
            "New track-candidates: " + ", ".join(f"~/{item.path}" for item in discovery.candidates)
        )
    for blocked in discovery.blocked:
        print_warning(f"Blocked ~/{blocked.path}: {blocked.category}")


@app.command()
def sync() -> None:
    """Fetch, safely synchronize, curate, and push dotfiles."""
    interactive = _interactive()
    try:
        with dotfiles_lock(_state_dir()):
            config = _load_initialized()
            repo = DotfilesRepo(config.bare_repo, home=Path.home(), state_dir=_state_dir())
            listing = repo.ls_remote_all_refs(config.remote_url)
            if listing.transport.success and not listing.refs:
                _push_pending_empty_remote(repo, config, interactive=interactive)
                return
            fetch = repo.fetch(config.remote_url)
            if fetch.success:
                _sync_online(repo, config, interactive=interactive)
            elif _is_missing_remote_main_ref(fetch.stderr):
                _refuse(
                    "Remote dotfiles main ref is absent but the remote is not proven empty; "
                    "restore its main ref or use a genuinely empty remote before retrying sync."
                )
            elif fetch.outcome is TransportOutcome.OFFLINE:
                _sync_offline(repo, config)
            else:
                _refuse(f"Dotfiles fetch {_transport_detail(fetch.outcome)}.")
    except (
        DotfilesCommandError,
        DotfilesConfigError,
        DotfilesLockError,
        DotfilesRecoveryError,
        DotfilesRepoError,
        DotfilesStateError,
        MaterializationError,
        TreeValidationError,
    ) as e:
        print_error(str(e))
        raise typer.Exit(code=1) from None


def _sync_online(repo: DotfilesRepo, config: DotfilesConfig, *, interactive: bool) -> None:
    source_oid = repo.ref_oid(REMOTE_MAIN_REF)
    if source_oid is None:
        _refuse("Remote dotfiles main ref is absent.")
    local_oid = repo.ref_oid(MAIN_REF)
    if local_oid is not None:
        retire_completed_materialization_state_for_local_ref(
            PlanOperation.INBOUND_SYNC,
            local_source_ref=local_oid,
            state_dir=_state_dir(),
        )
    base_entries = _tracked_entries(repo)
    tracked = _tracked_paths(base_entries)
    relation = repo.merge_base_relation(remote_ref=source_oid)
    remote_tree = _remote_tree_or_refuse(
        repo,
        tracked,
        config.ambiguous_content_allowlist,
        source_ref=source_oid,
        require_tracked_paths=relation in {RefRelation.EQUAL, RefRelation.BEHIND},
    )
    classifications = repo.classify_paths(tracked, remote_ref=source_oid) if base_entries else ()
    _refuse_sync_conflicts(repo, relation, classifications)
    if relation in {RefRelation.EQUAL, RefRelation.AHEAD}:
        complete_materialization_state_for_source(
            operation=PlanOperation.INBOUND_SYNC,
            source_ref=source_oid,
            source_tree_oid=remote_tree.tree_oid,
            state_dir=_state_dir(),
            recover_plan_only=relation is RefRelation.EQUAL,
        )
    inbound_paths: tuple[str, ...] = ()
    if relation is RefRelation.BEHIND or relation is RefRelation.BOOTSTRAP_BEHIND:
        expected = repo.ref_oid(MAIN_REF)
        if relation is RefRelation.BEHIND:
            remote_modified = repo.changed_paths(MAIN_REF, source_oid)
            _refuse_remote_changes_to_deleted_paths(repo, classifications, remote_modified)
        else:
            remote_modified = {entry.path for entry in remote_tree.entries}
        inbound_paths = tuple(sorted(remote_modified))
        sources = _sources(
            repo,
            (entry for entry in remote_tree.entries if entry.path in remote_modified),
        )
        inbound_plan = None
        if sources:
            inbound_plan = _preflight_or_resume_materialization(
                operation=PlanOperation.INBOUND_SYNC,
                source_ref=source_oid,
                source_tree_oid=remote_tree.tree_oid,
                sources=sources,
                base_files=_base_files(repo, base_entries),
                home=repo.home,
            )
            execute_materialization_plan(
                inbound_plan,
                sources=sources,
                home=repo.home,
                state_dir=_state_dir(),
            )
        if not repo.conditional_advance_ref(MAIN_REF, source_oid, expected):
            _refuse("Dotfiles main ref changed while synchronizing; retry sync.")
        if inbound_plan is not None:
            complete_materialization_state(inbound_plan, _state_dir())
        base_entries = repo.read_tree(MAIN_REF).entries
        tracked = _tracked_paths(base_entries)
    elif relation is RefRelation.BOOTSTRAP_UNBORN:
        _refuse("No local or remote dotfiles main ref is available.")

    discovery = discover_dotfiles(
        repo.home,
        tracked_files=tracked,
        ignored=config.ignored,
        ambiguous_content_allowlist=config.ambiguous_content_allowlist,
    )
    _display_discovery(discovery)
    review = _review_candidates(discovery, config, interactive=interactive)
    if review.cancelled:
        print_info("Dotfiles review cancelled; no local commit was created.")
        if repo.merge_base_relation() is RefRelation.AHEAD:
            _push_or_refuse(repo, config, interactive=interactive)
        if inbound_paths:
            _record_dotfiles_action(HistoryActionType.DOTFILES_SYNC, inbound_paths, repo=repo)
        return
    changed = _safe_changed_tracked_paths(repo, tracked)
    commit_paths = tuple(sorted(set(changed) | set(review.finalization.tracked_paths)))
    committed_paths: tuple[str, ...] = ()
    if commit_paths:
        result = repo.checked_commit(
            commit_paths,
            "Sync popctl dotfiles",
            ambiguous_content_allowlist=review.config.ambiguous_content_allowlist,
            expected_base_oid=repo.ref_oid(MAIN_REF),
        )
        committed_paths = result.paths
    if review.config != config:
        save_dotfiles_config(review.config)
        config = review.config
    relation_after = repo.merge_base_relation()
    if relation_after is RefRelation.AHEAD:
        config = _push_or_refuse(repo, config, interactive=interactive)
    affected = tuple(
        sorted(set(inbound_paths) | set(committed_paths) | set(review.finalization.ignored_paths))
    )
    if affected:
        _record_dotfiles_action(HistoryActionType.DOTFILES_SYNC, affected, repo=repo)
    if committed_paths:
        print_success("Dotfiles synchronized and pushed.")
    elif relation_after is RefRelation.EQUAL:
        print_info("Dotfiles are already synchronized.")


def _sync_offline(repo: DotfilesRepo, config: DotfilesConfig) -> None:
    print_warning(
        "Offline: using cached origin/main; remote content will not be materialized or pushed."
    )
    if repo.ref_oid(REMOTE_MAIN_REF) is None:
        _refuse("No cached remote dotfiles ref is available.")
    base_entries = _tracked_entries(repo)
    tracked = _tracked_paths(base_entries)
    relation = repo.merge_base_relation()
    _remote_tree_or_refuse(
        repo,
        tracked,
        config.ambiguous_content_allowlist,
        require_tracked_paths=relation in {RefRelation.EQUAL, RefRelation.BEHIND},
    )
    classifications = repo.classify_paths(tracked) if base_entries else ()
    _refuse_sync_conflicts(repo, relation, classifications)
    if relation is RefRelation.BEHIND or relation is RefRelation.BOOTSTRAP_BEHIND:
        print_warning("Cached remote is ahead; local changes were not committed (pending remote).")
        return
    if relation is RefRelation.BOOTSTRAP_UNBORN:
        _refuse("No local or cached remote dotfiles main ref is available.")
    changed = _safe_changed_tracked_paths(repo, tracked)
    if not changed:
        print_info("No local dotfiles changes to commit while offline.")
        return
    result = repo.checked_commit(
        changed,
        "Sync popctl dotfiles (offline)",
        ambiguous_content_allowlist=config.ambiguous_content_allowlist,
        expected_base_oid=repo.ref_oid(MAIN_REF),
    )
    _record_dotfiles_action(HistoryActionType.DOTFILES_SYNC, result.paths, repo=repo)
    print_success("Committed local dotfiles changes offline; push is pending.")


@app.command()
def apply(
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", "-n", help="Show the no-clobber plan without writing."),
    ] = False,
) -> None:
    """Materialize validated tracked files without a checkout or merge."""
    try:
        if dry_run:
            _run_apply(dry_run=True)
        else:
            with dotfiles_lock(_state_dir()):
                _run_apply(dry_run=False)
    except (
        DotfilesCommandError,
        DotfilesConfigError,
        DotfilesLockError,
        DotfilesRecoveryError,
        DotfilesRepoError,
        DotfilesStateError,
        MaterializationError,
        TreeValidationError,
    ) as e:
        print_error(str(e))
        raise typer.Exit(code=1) from None


def _run_apply(*, dry_run: bool) -> None:
    diff = compute_system_diff(SourceChoice.ALL, silent_warnings=True)
    if diff.missing:
        _refuse(
            "Package manifest is incomplete; install missing packages before "
            "applying dotfiles."
        )
    config = _load_initialized()
    repo = DotfilesRepo(
        config.bare_repo,
        home=Path.home(),
        state_dir=_state_dir(),
        read_only=dry_run,
    )
    source_oid: str | None = None
    if dry_run:
        temporary_fetch = repo.fetch_temporary_main(config.remote_url)
        fetch = temporary_fetch.transport
        source_oid = temporary_fetch.source_oid
    else:
        fetch = repo.fetch(config.remote_url)
    if not fetch.success:
        if fetch.outcome is not TransportOutcome.OFFLINE:
            _refuse(f"Apply fetch {_transport_detail(fetch.outcome)}.")
        print_warning("Offline: applying from cached origin/main.")
    _apply_source(repo, config, dry_run=dry_run, source_oid=source_oid)


def _apply_source(
    repo: DotfilesRepo,
    config: DotfilesConfig,
    *,
    dry_run: bool,
    source_oid: str | None = None,
) -> None:
    source_oid = source_oid or repo.ref_oid(REMOTE_MAIN_REF)
    if source_oid is None:
        _refuse("No fetched or cached remote dotfiles ref is available.")
    base_entries = _tracked_entries(repo)
    tracked = _tracked_paths(base_entries)
    relation = repo.merge_base_relation(remote_ref=source_oid)
    if relation in {RefRelation.AHEAD, RefRelation.DIVERGED}:
        _print_recovery(repo, tracked, divergence=relation is RefRelation.DIVERGED)
        _refuse("Local dotfiles history is ahead of or diverged from the source; refusing apply.")
    if relation is RefRelation.BOOTSTRAP_UNBORN:
        _refuse("No local or remote dotfiles source ref is available.")
    source_tree = _remote_tree_or_refuse(
        repo,
        tracked,
        config.ambiguous_content_allowlist,
        source_ref=source_oid,
        require_tracked_paths=relation in {RefRelation.EQUAL, RefRelation.BEHIND},
    )
    sources = _sources(repo, source_tree.entries)
    expected = repo.ref_oid(MAIN_REF)
    if not dry_run and expected is not None:
        retire_completed_materialization_state_for_local_ref(
            PlanOperation.APPLY,
            local_source_ref=expected,
            state_dir=_state_dir(),
        )
    plan = _preflight_or_resume_materialization(
        operation=PlanOperation.APPLY,
        source_ref=source_oid,
        source_tree_oid=source_tree.tree_oid,
        sources=sources,
        base_files=_base_files(repo, base_entries),
        home=repo.home,
    )
    if dry_run:
        for line in render_materialization_plan(plan):
            console.print(line)
        return
    needs_write = any(entry.action != "noop" for entry in plan.entries)
    if not needs_write and relation is RefRelation.EQUAL:
        complete_materialization_state_for_source(
            PlanOperation.APPLY,
            source_ref=source_oid,
            source_tree_oid=source_tree.tree_oid,
            state_dir=_state_dir(),
            recover_plan_only=True,
        )
        print_info("Dotfiles already match the validated source.")
        return
    changed = execute_materialization_plan(
        plan,
        sources=sources,
        home=repo.home,
        state_dir=_state_dir(),
    )
    if not repo.conditional_advance_ref(MAIN_REF, source_oid, expected):
        _refuse("Dotfiles main ref changed while applying; retry apply.")
    complete_materialization_state(plan, _state_dir())
    _record_dotfiles_action(
        HistoryActionType.DOTFILES_APPLY,
        changed or tuple(entry.path for entry in plan.entries),
        repo=repo,
    )
    if changed:
        print_success("Applied validated dotfiles.")
    else:
        print_info("Validated source matched existing files; local ref advanced.")
