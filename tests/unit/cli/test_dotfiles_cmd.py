from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from popctl.advisor.exchange import DotfilesReviewFinalization
from popctl.advisor.runner import AgentResult
from popctl.cli.commands import dotfiles
from popctl.cli.main import app
from popctl.core.state import get_history
from popctl.dotfiles import desktop, materialize, state
from popctl.dotfiles.config import (
    DesktopSettingsConfig,
    DotfilesConfig,
    RemotePrivacyRecord,
    get_dotfiles_config_path,
    load_dotfiles_config,
    save_dotfiles_config,
)
from popctl.dotfiles.desktop import (
    DEFAULT_ROOTS,
    DESKTOP_SETTINGS_ARTIFACT_PATH,
    DesktopCaptureResult,
    DesktopCaptureStatus,
    DesktopLoadResult,
    DesktopLoadStatus,
    DesktopSettingsSection,
    parse_desktop_settings_artifact,
    render_desktop_settings_artifact,
)
from popctl.dotfiles.discovery import Candidate
from popctl.dotfiles.repo import (
    MAIN_REF,
    REMOTE_MAIN_REF,
    DotfilesRepo,
    LsRemoteResult,
    PathClassification,
    PathState,
    RefRelation,
    RemoteRef,
    TemporaryFetchResult,
    TransportOutcome,
    TransportResult,
    TreeEntry,
    TreeRead,
)
from popctl.dotfiles.state import (
    DotfilesStateError,
    InitFinalizationJournal,
    PlanOperation,
    get_completed_paths_journal_path,
    get_dotfiles_lock_path,
    get_init_finalization_journal_path,
    get_plan_path,
    load_completed_paths_journal,
    recover_init_finalization,
)
from popctl.models.history import HistoryActionType
from popctl.utils.shell import BytesCommandResult, CommandResult
from typer.testing import CliRunner

from tests.unit.dotfiles.conftest import RealGitEnvironment
from tests.unit.dotfiles.conftest import real_git as _real_git

runner = CliRunner()

_REMOTE = "https://github.com/example/popctl-dotfiles.git"
_PATH = ".config/tool/config"
_NEW_PATH = ".config/tool/new-config"


@pytest.fixture
def real_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RealGitEnvironment:
    return _real_git.__wrapped__(tmp_path, monkeypatch)


def _write(home: Path, content: bytes) -> None:
    target = home / _PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)


def test_reserved_entries_never_become_home_sources_or_history_paths(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    entries = (
        TreeEntry("100644", _PATH, "a" * 40),
        TreeEntry("100644", DESKTOP_SETTINGS_ARTIFACT_PATH, "b" * 40),
    )

    class Repository:
        bare_repo = tmp_path / "dotfiles.git"

        @staticmethod
        def read_blob(oid: str) -> bytes:
            return {"a" * 40: b"home\n", "b" * 40: b"artifact\n"}[oid]

        @staticmethod
        def ref_oid(_ref: str) -> str:
            return "c" * 40

    recorded: list[object] = []
    monkeypatch.setattr(dotfiles, "record_action", recorded.append)
    repository = Repository()

    assert dotfiles._tracked_paths(entries) == (_PATH,)
    assert tuple(source.path for source in dotfiles._sources(repository, entries)) == (_PATH,)
    assert tuple(dotfiles._base_files(repository, entries)) == (_PATH,)
    dotfiles._record_dotfiles_action(
        HistoryActionType.DOTFILES_SYNC,
        (_PATH, DESKTOP_SETTINGS_ARTIFACT_PATH),
        repo=repository,
    )

    assert len(recorded) == 1
    assert tuple(item.name for item in recorded[0].items) == (_PATH,)


def _success_transport() -> TransportResult:
    return TransportResult(TransportOutcome.SUCCESS)


def _configured_source_repo(
    real_git: RealGitEnvironment,
    tmp_path: Path,
    *,
    target: bytes = b"base\n",
) -> tuple[DotfilesRepo, DotfilesConfig, str, str]:
    state_dir = real_git.state_home / "popctl" / "dotfiles"
    repository = DotfilesRepo(tmp_path / "dotfiles.git", home=real_git.home, state_dir=state_dir)
    repository.initialize_bare()
    _write(real_git.home, b"base\n")
    base_oid = repository.checked_commit((_PATH,), "base").commit_oid
    _write(real_git.home, b"remote\n")
    source_oid = repository.checked_commit((_PATH,), "source").commit_oid
    assert repository.conditional_advance_ref(REMOTE_MAIN_REF, source_oid, None)
    assert repository.conditional_advance_ref(MAIN_REF, base_oid, source_oid)
    _write(real_git.home, target)
    config = DotfilesConfig(
        bare_repo=repository.bare_repo,
        remote_url=_REMOTE,
        remote_privacy=RemotePrivacyRecord(canonical_remote_url=_REMOTE, method="acknowledged"),
    )
    save_dotfiles_config(config)
    return repository, config, base_oid, source_oid


def _add_remote_descendant(
    repository: DotfilesRepo,
    real_git: RealGitEnvironment,
    *,
    base_oid: str,
    source_oid: str,
) -> str:
    assert repository.conditional_advance_ref(MAIN_REF, source_oid, base_oid)
    _write(real_git.home, b"newer remote\n")
    newer_oid = repository.checked_commit((_PATH,), "newer remote").commit_oid
    assert repository.conditional_advance_ref(MAIN_REF, base_oid, newer_oid)
    _write(real_git.home, b"base\n")
    return newer_oid


def _owned_asset_state(state_dir: Path) -> dict[str, tuple[bytes, int]]:
    return {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in (state_dir / "git").iterdir()
    }


def _review_with_track(
    _discovery: object,
    config: DotfilesConfig,
    *,
    interactive: bool,
) -> dotfiles.ReviewResult:
    return dotfiles.ReviewResult(
        DotfilesReviewFinalization((_PATH,), (), ()),
        config,
    )


def _route_network_to_local_remote(
    monkeypatch: pytest.MonkeyPatch, remote_store: DotfilesRepo
) -> str:
    local_url = f"file://{remote_store.bare_repo}"
    original_network_git = DotfilesRepo._network_git

    def local_network_git(
        repository: DotfilesRepo,
        args: list[str],
        canonical_url: str,
        *,
        timeout_seconds: float = 30.0,
    ) -> BytesCommandResult:
        local_args = [local_url if argument == canonical_url else argument for argument in args]
        return original_network_git(
            repository,
            local_args,
            canonical_url,
            timeout_seconds=timeout_seconds,
        )

    monkeypatch.setattr(DotfilesRepo, "_network_git", local_network_git)
    return local_url


def test_init_status_sync_and_dry_run_apply_real_git(
    real_git: RealGitEnvironment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write(real_git.home, b"initial\n")
    monkeypatch.setattr(dotfiles, "_review_candidates", _review_with_track)
    monkeypatch.setattr(
        dotfiles,
        "_acquire_private_remote",
        lambda url, **_kwargs: (
            RemotePrivacyRecord(canonical_remote_url=url, method="verified"),
            False,
        ),
    )
    monkeypatch.setattr(dotfiles, "_ensure_empty_destination", lambda *_args: None)
    monkeypatch.setattr(dotfiles, "_pre_push_privacy", lambda config, **_kwargs: config)
    monkeypatch.setattr(DotfilesRepo, "push", lambda *_args: _success_transport())

    initialized = runner.invoke(app, ["dotfiles", "init", "--remote", _REMOTE])

    assert initialized.exit_code == 0, initialized.output
    config = load_dotfiles_config()
    repository = DotfilesRepo(config.bare_repo, home=real_git.home)
    main = repository.ref_oid(MAIN_REF)
    assert main is not None
    assert repository.conditional_advance_ref(REMOTE_MAIN_REF, main, None)
    history, _ = get_history()
    assert history[0].action_type is HistoryActionType.DOTFILES_INIT

    monkeypatch.setattr(DotfilesRepo, "fetch", lambda *_args, **_kwargs: _success_transport())
    monkeypatch.setattr(
        DotfilesRepo,
        "ls_remote_all_refs",
        lambda *_args: LsRemoteResult(_success_transport(), (RemoteRef(main, MAIN_REF),)),
    )
    status = runner.invoke(app, ["dotfiles", "status"])

    assert status.exit_code == 0, status.output
    assert _PATH in status.output

    _write(real_git.home, b"changed\n")
    synced = runner.invoke(app, ["dotfiles", "sync"])

    assert synced.exit_code == 0, synced.output
    assert repository.ref_oid(MAIN_REF) != main
    history, _ = get_history()
    assert history[0].action_type is HistoryActionType.DOTFILES_SYNC
    updated_main = repository.ref_oid(MAIN_REF)
    assert updated_main is not None
    assert repository.conditional_advance_ref(REMOTE_MAIN_REF, updated_main, main)
    monkeypatch.setattr(
        DotfilesRepo,
        "fetch_temporary_main",
        lambda *_args: TemporaryFetchResult(_success_transport(), updated_main),
    )

    monkeypatch.setattr(
        dotfiles,
        "compute_system_diff",
        lambda *_args, **_kwargs: SimpleNamespace(missing=[]),
    )
    plan_path = real_git.state_home / "popctl" / "dotfiles" / "apply-plan.json"
    dry_run = runner.invoke(app, ["dotfiles", "apply", "--dry-run"])

    assert dry_run.exit_code == 0, dry_run.output
    assert _PATH in dry_run.output
    assert not plan_path.exists()


def test_init_refusals_and_noninteractive_no_auto_add(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dotfiles, "_interactive", lambda: False)
    monkeypatch.setattr(
        dotfiles,
        "discover_dotfiles",
        lambda *_args, **_kwargs: dotfiles.DiscoveryResult((), ()),
    )

    no_selection = runner.invoke(app, ["dotfiles", "init", "--remote", _REMOTE])
    exclusive = runner.invoke(app, ["dotfiles", "init", "--remote", _REMOTE, "--from", _REMOTE])

    assert no_selection.exit_code == 1
    assert "No dotfiles were selected" in no_selection.output
    assert exclusive.exit_code == 1
    assert "mutually exclusive" in exclusive.output


def test_pre_push_privacy_requires_bound_acknowledgement(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dotfiles.shutil, "which", lambda _name: None)
    config = DotfilesConfig(
        remote_url=_REMOTE,
        remote_privacy=RemotePrivacyRecord(
            canonical_remote_url="https://github.com/example/other.git",
            method="acknowledged",
        ),
    )

    with pytest.raises(dotfiles.DotfilesCommandError, match="matching unverified-private"):
        dotfiles._pre_push_privacy(config, interactive=False)


def test_remote_selection_and_privacy_acquisition_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    answers = iter(("example", "dotfiles"))
    monkeypatch.setattr(dotfiles.typer, "prompt", lambda *_args, **_kwargs: next(answers))
    assert (
        dotfiles._select_normal_remote(None, recovered_remote=None, interactive=True)
        == "https://github.com/example/dotfiles.git"
    )

    monkeypatch.setattr(dotfiles.shutil, "which", lambda _name: "/usr/bin/gh")
    monkeypatch.setattr(
        dotfiles,
        "run_command",
        lambda *_args, **_kwargs: CommandResult('{"isPrivate": true}', "", 0),
    )
    privacy, created = dotfiles._acquire_private_remote(
        _REMOTE,
        allow_create=False,
        interactive=False,
    )
    assert privacy.method == "verified"
    assert not created

    calls: list[list[str]] = []

    def create_then_verify(args: list[str], **_kwargs: object) -> CommandResult:
        calls.append(args)
        if args[2] == "view" and len(calls) == 1:
            return CommandResult("", "not found", 1)
        return CommandResult('{"isPrivate": true}', "", 0)

    monkeypatch.setattr(dotfiles, "run_command", create_then_verify)
    _privacy, created = dotfiles._acquire_private_remote(
        _REMOTE,
        allow_create=True,
        interactive=False,
    )
    assert created
    assert ["gh", "repo", "create", "example/popctl-dotfiles", "--private"] in calls


def test_tree_acknowledgement_and_interactive_review_finalization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = MagicMock()
    source.read_blob.return_value = b"TOKEN=perhaps\n"
    tree = TreeRead(
        ref=REMOTE_MAIN_REF,
        tree_oid="a" * 40,
        entries=(TreeEntry("100644", ".config/tool/config.env", "b" * 40),),
    )
    with pytest.raises(dotfiles.DotfilesCommandError, match="unacknowledged ambiguous"):
        dotfiles._acknowledge_tree_ambiguities(source, tree, allowlist=(), interactive=False)

    monkeypatch.setattr(dotfiles.typer, "confirm", lambda *_args, **_kwargs: True)
    assert dotfiles._acknowledge_tree_ambiguities(source, tree, allowlist=(), interactive=True) == (
        ".config/tool/config.env",
    )

    home = tmp_path / "home"
    home.mkdir()
    path = home / _PATH
    path.parent.mkdir(parents=True)
    path.write_text("safe\n", encoding="utf-8")
    monkeypatch.setattr(dotfiles.Path, "home", lambda: home)
    discovery = dotfiles.DiscoveryResult((Candidate(_PATH, ".config"),), ())
    decisions_path = tmp_path / "decisions.toml"
    decisions_path.write_text(
        "[dotfiles]\n"
        "track = [{ path = '.config/tool/config', reason = 'config', confidence = 1.0 }]\n"
        "ignore = []\nask = []\n",
        encoding="utf-8",
    )
    result = AgentResult(success=True, output="", decisions_path=decisions_path)
    runner = MagicMock()
    runner.launch_interactive.return_value = result
    monkeypatch.setattr(dotfiles, "load_or_create_config", lambda: MagicMock())
    monkeypatch.setattr(dotfiles, "get_session_manager", lambda: None)
    monkeypatch.setattr(dotfiles, "ensure_advisor_sessions_dir", lambda **_kwargs: tmp_path)
    monkeypatch.setattr(dotfiles, "create_dotfiles_session_workspace", lambda *_args: tmp_path)
    monkeypatch.setattr(dotfiles, "AgentRunner", lambda *_args, **_kwargs: runner)

    reviewed = dotfiles._review_candidates(
        discovery,
        DotfilesConfig(),
        interactive=True,
    )

    assert reviewed.finalization.tracked_paths == (_PATH,)


def test_init_from_promotes_validated_temporary_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = TreeRead(
        ref=REMOTE_MAIN_REF,
        tree_oid="a" * 40,
        entries=(TreeEntry("100644", _PATH, "b" * 40),),
    )

    class BootstrapRepo:
        def __init__(self, bare_repo: Path, *, home: Path, state_dir: Path) -> None:
            self.bare_repo = bare_repo
            self.home = home

        def initialize_bare(self) -> None:
            return None

        def setup_remote(self, _url: str) -> None:
            return None

        def fetch(self, _url: str) -> TransportResult:
            return _success_transport()

        def fetch_marker(self, _url: str) -> TransportResult:
            return _success_transport()

        def ref_oid(self, ref: str) -> str | None:
            return "c" * 40 if ref == REMOTE_MAIN_REF else None

        def verify_marker(self) -> bool:
            return True

        def read_tree(self, _ref: str) -> TreeRead:
            return tree

        def read_blob(self, _oid: str) -> bytes:
            return b"safe\n"

        def validate_tree(self, _ref: str, *, ambiguous_content_allowlist: object) -> TreeRead:
            return tree

    final_store = tmp_path / "dotfiles.git"
    monkeypatch.setattr(dotfiles, "DotfilesRepo", BootstrapRepo)
    monkeypatch.setattr(
        dotfiles,
        "_acquire_private_remote",
        lambda url, **_kwargs: (
            RemotePrivacyRecord(canonical_remote_url=url, method="verified"),
            False,
        ),
    )
    monkeypatch.setattr(dotfiles, "_record_dotfiles_action", lambda *_args, **_kwargs: None)

    dotfiles._init_from(_REMOTE, interactive=False, final_store=final_store)

    assert final_store.is_dir()
    assert load_dotfiles_config().remote_url == _REMOTE


def test_offline_sync_commits_only_safe_cached_local_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _write(home, b"changed\n")
    tree = TreeRead(
        ref=MAIN_REF,
        tree_oid="a" * 40,
        entries=(TreeEntry("100644", _PATH, "b" * 40),),
    )
    committed: list[tuple[str, ...]] = []

    class OfflineRepo:
        def __init__(self) -> None:
            self.bare_repo = tmp_path / "dotfiles.git"
            self.home = home

        def ref_oid(self, ref: str) -> str | None:
            return "c" * 40 if ref in {MAIN_REF, REMOTE_MAIN_REF} else None

        def read_tree(self, _ref: str) -> TreeRead:
            return tree

        def validate_tree(self, _ref: str, *, ambiguous_content_allowlist: object) -> TreeRead:
            return tree

        def merge_base_relation(self) -> RefRelation:
            return RefRelation.EQUAL

        def classify_paths(self, _tracked: object) -> tuple[PathClassification, ...]:
            return ()

        def work_tree_changed_paths(self, _tracked: object) -> frozenset[str]:
            return frozenset({_PATH})

        def checked_commit(
            self,
            paths: tuple[str, ...],
            *_args: object,
            **_kwargs: object,
        ) -> object:
            committed.append(paths)
            return SimpleNamespace(paths=paths)

    monkeypatch.setattr(dotfiles, "_record_dotfiles_action", lambda *_args, **_kwargs: None)

    dotfiles._sync_offline(OfflineRepo(), DotfilesConfig(remote_url=_REMOTE))

    assert committed == [(_PATH,)]


def test_offline_sync_commits_an_artifact_only_capture_when_cache_is_not_ahead(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = TreeRead(ref=MAIN_REF, tree_oid="a" * 40, entries=())
    committed: list[tuple[tuple[str, ...], dict[str, object]]] = []

    class OfflineRepo:
        bare_repo = tmp_path / "dotfiles.git"
        home = tmp_path / "home"

        def ref_oid(self, ref: str) -> str | None:
            return "c" * 40 if ref in {MAIN_REF, REMOTE_MAIN_REF} else None

        def read_tree(self, _ref: str) -> TreeRead:
            return tree

        def validate_tree(self, _ref: str, *, ambiguous_content_allowlist: object) -> TreeRead:
            return tree

        def merge_base_relation(self) -> RefRelation:
            return RefRelation.EQUAL

        def classify_paths(self, _tracked: object) -> tuple[PathClassification, ...]:
            return ()

        def work_tree_changed_paths(self, _tracked: object) -> frozenset[str]:
            return frozenset()

        def checked_commit(
            self,
            paths: tuple[str, ...],
            *_args: object,
            **kwargs: object,
        ) -> object:
            committed.append((paths, kwargs))
            return SimpleNamespace(paths=paths)

    monkeypatch.setattr(
        dotfiles,
        "capture_desktop_settings",
        lambda *_args, **_kwargs: DesktopCaptureResult(
            DesktopCaptureStatus.CHANGED,
            artifact=b"desktop artifact",
        ),
    )
    monkeypatch.setattr(dotfiles, "_record_dotfiles_action", lambda *_args, **_kwargs: None)

    dotfiles._sync_offline(OfflineRepo(), DotfilesConfig(remote_url=_REMOTE))

    assert committed == [
        (
            (DESKTOP_SETTINGS_ARTIFACT_PATH,),
            {
                "ambiguous_content_allowlist": [],
                "expected_base_oid": "c" * 40,
                "desktop_settings_artifact": b"desktop artifact",
                "desktop_extra_roots": (),
                "desktop_ambiguous_root_allowlist": (),
            },
        )
    ]


def test_offline_sync_defers_capture_when_the_cache_is_ahead(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = TreeRead(ref=REMOTE_MAIN_REF, tree_oid="a" * 40, entries=())

    class CacheAheadRepo:
        bare_repo = tmp_path / "dotfiles.git"

        def ref_oid(self, ref: str) -> str | None:
            return "c" * 40 if ref in {MAIN_REF, REMOTE_MAIN_REF} else None

        def read_tree(self, _ref: str) -> TreeRead:
            return tree

        def validate_tree(self, _ref: str, *, ambiguous_content_allowlist: object) -> TreeRead:
            return tree

        def merge_base_relation(self) -> RefRelation:
            return RefRelation.BEHIND

        def classify_paths(self, _tracked: object) -> tuple[PathClassification, ...]:
            return ()

    monkeypatch.setattr(
        dotfiles,
        "capture_desktop_settings",
        lambda *_args, **_kwargs: pytest.fail("capture must be deferred while cache is ahead"),
    )

    dotfiles._sync_offline(CacheAheadRepo(), DotfilesConfig(remote_url=_REMOTE))


def test_disabled_desktop_capture_does_not_block_an_ordinary_offline_sync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _write(home, b"changed\n")
    tree = TreeRead(
        ref=MAIN_REF,
        tree_oid="a" * 40,
        entries=(TreeEntry("100644", _PATH, "b" * 40),),
    )
    committed: list[tuple[str, ...]] = []

    class OfflineRepo:
        bare_repo = tmp_path / "dotfiles.git"

        def __init__(self) -> None:
            self.home = home

        def ref_oid(self, ref: str) -> str | None:
            return "c" * 40 if ref in {MAIN_REF, REMOTE_MAIN_REF} else None

        def read_tree(self, _ref: str) -> TreeRead:
            return tree

        def validate_tree(self, _ref: str, *, ambiguous_content_allowlist: object) -> TreeRead:
            return tree

        def merge_base_relation(self) -> RefRelation:
            return RefRelation.EQUAL

        def classify_paths(self, _tracked: object) -> tuple[PathClassification, ...]:
            return ()

        def work_tree_changed_paths(self, _tracked: object) -> frozenset[str]:
            return frozenset({_PATH})

        def checked_commit(
            self,
            paths: tuple[str, ...],
            *_args: object,
            **kwargs: object,
        ) -> object:
            assert kwargs["desktop_settings_artifact"] is None
            committed.append(paths)
            return SimpleNamespace(paths=paths)

    monkeypatch.setattr(
        desktop,
        "normalize_desktop_family",
        lambda *_args: pytest.fail("disabled capture must not detect a family"),
    )
    monkeypatch.setattr(
        desktop,
        "run_command",
        lambda *_args, **_kwargs: pytest.fail("disabled capture must not dump dconf"),
    )
    monkeypatch.setattr(dotfiles, "_record_dotfiles_action", lambda *_args, **_kwargs: None)

    dotfiles._sync_offline(
        OfflineRepo(),
        DotfilesConfig(
            remote_url=_REMOTE,
            desktop_settings=DesktopSettingsConfig(enabled=False),
        ),
    )

    assert committed == [(_PATH,)]


def test_empty_remote_sync_captures_before_pushing_pending_history(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = TreeRead(ref=MAIN_REF, tree_oid="a" * 40, entries=())
    committed: list[tuple[tuple[str, ...], dict[str, object]]] = []
    pushed: list[bool] = []

    class EmptyRemoteRepo:
        def ref_oid(self, _ref: str) -> str:
            return "c" * 40

        def validate_tree(self, _ref: str, **_kwargs: object) -> TreeRead:
            return tree

        def checked_commit(
            self,
            paths: tuple[str, ...],
            *_args: object,
            **kwargs: object,
        ) -> object:
            committed.append((paths, kwargs))
            return SimpleNamespace(paths=paths)

    monkeypatch.setattr(
        dotfiles,
        "capture_desktop_settings",
        lambda *_args, **_kwargs: DesktopCaptureResult(
            DesktopCaptureStatus.CHANGED,
            artifact=b"desktop artifact",
        ),
    )
    monkeypatch.setattr(
        dotfiles,
        "_push_pending_empty_remote",
        lambda *_args, **_kwargs: pushed.append(True),
    )

    dotfiles._sync_empty_remote(
        EmptyRemoteRepo(),
        DotfilesConfig(remote_url=_REMOTE),
        interactive=False,
    )

    assert committed[0][0] == (DESKTOP_SETTINGS_ARTIFACT_PATH,)
    assert committed[0][1]["desktop_settings_artifact"] == b"desktop artifact"
    assert pushed == [True]


def test_online_sync_materializes_changed_and_new_remote_leaves(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _write(home, b"base\n")
    base = TreeRead(
        ref=MAIN_REF,
        tree_oid="a" * 40,
        entries=(TreeEntry("100644", _PATH, "b" * 40),),
    )
    remote = TreeRead(
        ref=REMOTE_MAIN_REF,
        tree_oid="c" * 40,
        entries=(
            TreeEntry("100644", _PATH, "d" * 40),
            TreeEntry("100755", _NEW_PATH, "e" * 40),
        ),
    )

    class OnlineRepo:
        def __init__(self) -> None:
            self.bare_repo = tmp_path / "dotfiles.git"
            self.home = home
            self.advanced = False

        def ref_oid(self, ref: str) -> str | None:
            if ref == REMOTE_MAIN_REF:
                return "c" * 40
            if ref == MAIN_REF:
                return "c" * 40 if self.advanced else "a" * 40
            return None

        def read_tree(self, ref: str) -> TreeRead:
            if ref == REMOTE_MAIN_REF:
                return remote
            return remote if self.advanced else base

        def validate_tree(self, _ref: str, *, ambiguous_content_allowlist: object) -> TreeRead:
            return remote

        def read_blob(self, oid: str) -> bytes:
            return {
                "b" * 40: b"base\n",
                "d" * 40: b"remote\n",
                "e" * 40: b"new\n",
            }[oid]

        def merge_base_relation(self, **_kwargs: object) -> RefRelation:
            return RefRelation.EQUAL if self.advanced else RefRelation.BEHIND

        def classify_paths(
            self, _tracked: object, **_kwargs: object
        ) -> tuple[PathClassification, ...]:
            return (PathClassification(_PATH, PathState.REMOTE_MOD),)

        def changed_paths(self, old_ref: str, new_ref: str) -> frozenset[str]:
            assert (old_ref, new_ref) == (MAIN_REF, "c" * 40)
            return frozenset({_PATH, _NEW_PATH})

        def conditional_advance_ref(self, *_args: object) -> bool:
            self.advanced = True
            return True

        def work_tree_changed_paths(self, _tracked: object) -> frozenset[str]:
            return frozenset()

    repository = OnlineRepo()
    config = DotfilesConfig(remote_url=_REMOTE)
    monkeypatch.setattr(dotfiles, "_state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(
        dotfiles,
        "discover_dotfiles",
        lambda *_args, **_kwargs: dotfiles.DiscoveryResult((), ()),
    )
    monkeypatch.setattr(
        dotfiles,
        "_review_candidates",
        lambda *_args, **_kwargs: dotfiles.ReviewResult(
            DotfilesReviewFinalization((), (), ()), config
        ),
    )
    monkeypatch.setattr(dotfiles, "_record_dotfiles_action", lambda *_args, **_kwargs: None)

    dotfiles._sync_online(repository, config, interactive=False)

    assert (home / _PATH).read_bytes() == b"remote\n"
    assert (home / _NEW_PATH).read_bytes() == b"new\n"
    assert (home / _NEW_PATH).stat().st_mode & 0o777 == 0o755


def test_online_sync_commits_and_pushes_capture_when_review_is_cancelled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tree = TreeRead(ref=REMOTE_MAIN_REF, tree_oid="a" * 40, entries=())
    commits: list[tuple[str, ...]] = []
    pushes: list[bool] = []

    class OnlineRepo:
        bare_repo = tmp_path / "dotfiles.git"
        home = tmp_path / "home"

        def __init__(self) -> None:
            self.committed = False

        def ref_oid(self, _ref: str) -> str:
            return "a" * 40

        def read_tree(self, _ref: str) -> TreeRead:
            return tree

        def validate_tree(self, _ref: str, *, ambiguous_content_allowlist: object) -> TreeRead:
            return tree

        def merge_base_relation(self, **_kwargs: object) -> RefRelation:
            return RefRelation.AHEAD if self.committed else RefRelation.EQUAL

        def classify_paths(
            self, _tracked: object, **_kwargs: object
        ) -> tuple[PathClassification, ...]:
            return ()

        def checked_commit(
            self,
            paths: tuple[str, ...],
            *_args: object,
            **_kwargs: object,
        ) -> object:
            self.committed = True
            commits.append(paths)
            return SimpleNamespace(paths=paths)

    config = DotfilesConfig(remote_url=_REMOTE)
    monkeypatch.setattr(
        dotfiles,
        "capture_desktop_settings",
        lambda *_args, **_kwargs: DesktopCaptureResult(
            DesktopCaptureStatus.CHANGED,
            artifact=b"desktop artifact",
        ),
    )
    monkeypatch.setattr(
        dotfiles,
        "discover_dotfiles",
        lambda *_args, **_kwargs: dotfiles.DiscoveryResult((), ()),
    )
    monkeypatch.setattr(
        dotfiles,
        "_review_candidates",
        lambda *_args, **_kwargs: dotfiles.ReviewResult(
            DotfilesReviewFinalization((), (), ()), config, cancelled=True
        ),
    )
    monkeypatch.setattr(dotfiles, "_push_or_refuse", lambda *_args, **_kwargs: pushes.append(True))
    monkeypatch.setattr(dotfiles, "_record_dotfiles_action", lambda *_args, **_kwargs: None)

    dotfiles._sync_online(OnlineRepo(), config, interactive=False)

    assert commits == [(DESKTOP_SETTINGS_ARTIFACT_PATH,)]
    assert pushes == [True]


def test_hard_secret_capture_failure_does_not_block_an_ordinary_sync(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _write(home, b"changed\n")
    tree = TreeRead(
        ref=REMOTE_MAIN_REF,
        tree_oid="a" * 40,
        entries=(TreeEntry("100644", _PATH, "b" * 40),),
    )
    commits: list[tuple[str, ...]] = []
    pushes: list[bool] = []

    class OnlineRepo:
        bare_repo = tmp_path / "dotfiles.git"

        def __init__(self) -> None:
            self.committed = False
            self.home = home

        def ref_oid(self, _ref: str) -> str:
            return "a" * 40

        def read_tree(self, _ref: str) -> TreeRead:
            return tree

        def validate_tree(self, _ref: str, *, ambiguous_content_allowlist: object) -> TreeRead:
            return tree

        def read_blob(self, _oid: str) -> bytes:
            return b"base\n"

        def merge_base_relation(self, **_kwargs: object) -> RefRelation:
            return RefRelation.AHEAD if self.committed else RefRelation.EQUAL

        def classify_paths(
            self, _tracked: object, **_kwargs: object
        ) -> tuple[PathClassification, ...]:
            return ()

        def work_tree_changed_paths(self, _tracked: object) -> frozenset[str]:
            return frozenset({_PATH})

        def checked_commit(
            self,
            paths: tuple[str, ...],
            *_args: object,
            **_kwargs: object,
        ) -> object:
            self.committed = True
            commits.append(paths)
            return SimpleNamespace(paths=paths)

    config = DotfilesConfig(remote_url=_REMOTE)
    monkeypatch.setattr(
        dotfiles,
        "capture_desktop_settings",
        lambda *_args, **_kwargs: DesktopCaptureResult(
            DesktopCaptureStatus.SECRET_REJECTED,
            detail="authorization",
            prior_retained=True,
        ),
    )
    monkeypatch.setattr(
        dotfiles,
        "discover_dotfiles",
        lambda *_args, **_kwargs: dotfiles.DiscoveryResult((), ()),
    )
    monkeypatch.setattr(
        dotfiles,
        "_review_candidates",
        lambda *_args, **_kwargs: dotfiles.ReviewResult(
            DotfilesReviewFinalization((), (), ()), config
        ),
    )
    monkeypatch.setattr(dotfiles, "_push_or_refuse", lambda *_args, **_kwargs: pushes.append(True))
    monkeypatch.setattr(dotfiles, "_record_dotfiles_action", lambda *_args, **_kwargs: None)

    dotfiles._sync_online(OnlineRepo(), config, interactive=False)

    assert commits == [(_PATH,)]
    assert pushes == [True]


@pytest.mark.real_git
def test_online_sync_commits_a_real_in_memory_desktop_artifact(
    real_git: RealGitEnvironment,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = DotfilesRepo(
        tmp_path / "dotfiles.git",
        home=real_git.home,
        state_dir=real_git.state_home / "popctl" / "dotfiles",
    )
    repository.initialize_bare()
    _write(real_git.home, b"base\n")
    base_oid = repository.checked_commit((_PATH,), "base").commit_oid
    assert repository.conditional_advance_ref(REMOTE_MAIN_REF, base_oid, None)
    config = DotfilesConfig(bare_repo=repository.bare_repo, remote_url=_REMOTE)
    calls: list[tuple[list[str], dict[str, str] | None]] = []

    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    monkeypatch.delenv("XDG_SESSION_DESKTOP", raising=False)
    monkeypatch.setattr(desktop.shutil, "which", lambda _name: "/usr/bin/dconf")

    def dump(args: list[str], **kwargs: object) -> CommandResult:
        calls.append((args, kwargs.get("env") if isinstance(kwargs.get("env"), dict) else None))
        return CommandResult("[settings]\nvalue='captured'\n", "", 0)

    monkeypatch.setattr(desktop, "run_command", dump)
    monkeypatch.setattr(
        dotfiles,
        "discover_dotfiles",
        lambda *_args, **_kwargs: dotfiles.DiscoveryResult((), ()),
    )
    monkeypatch.setattr(
        dotfiles,
        "_review_candidates",
        lambda *_args, **_kwargs: dotfiles.ReviewResult(
            DotfilesReviewFinalization((), (), ()), config
        ),
    )
    monkeypatch.setattr(dotfiles, "_push_or_refuse", lambda *_args, **_kwargs: config)
    monkeypatch.setattr(dotfiles, "_record_dotfiles_action", lambda *_args, **_kwargs: None)

    dotfiles._sync_online(repository, config, interactive=False)

    entries = repository.read_tree(MAIN_REF).entries
    artifact_entry = next(
        entry for entry in entries if entry.path == DESKTOP_SETTINGS_ARTIFACT_PATH
    )
    artifact = parse_desktop_settings_artifact(repository.read_blob(artifact_entry.oid))
    assert artifact.family == "GNOME"
    assert artifact.roots == config.desktop_settings.effective_roots
    assert calls == [
        (["dconf", "dump", root], {"LC_ALL": "C"})
        for root in config.desktop_settings.effective_roots
    ]


def test_online_sync_refuses_remote_change_to_locally_deleted_path(tmp_path: Path) -> None:
    base = TreeRead(
        ref=MAIN_REF,
        tree_oid="a" * 40,
        entries=(TreeEntry("100644", _PATH, "b" * 40),),
    )
    remote = TreeRead(
        ref=REMOTE_MAIN_REF,
        tree_oid="c" * 40,
        entries=(TreeEntry("100644", _PATH, "d" * 40),),
    )

    class DeletedPathRepo:
        bare_repo = tmp_path / "dotfiles.git"

        def ref_oid(self, ref: str) -> str | None:
            return "a" * 40 if ref == MAIN_REF else "c" * 40

        def read_tree(self, _ref: str) -> TreeRead:
            return base

        def validate_tree(self, _ref: str, *, ambiguous_content_allowlist: object) -> TreeRead:
            return remote

        def merge_base_relation(self, **_kwargs: object) -> RefRelation:
            return RefRelation.BEHIND

        def classify_paths(
            self, _tracked: object, **_kwargs: object
        ) -> tuple[PathClassification, ...]:
            return (PathClassification(_PATH, PathState.MISSING),)

        def changed_paths(self, _old_ref: str, _new_ref: str) -> frozenset[str]:
            return frozenset({_PATH})

    with pytest.raises(dotfiles.DotfilesCommandError, match="locally deleted"):
        dotfiles._sync_online(
            DeletedPathRepo(),
            DotfilesConfig(remote_url=_REMOTE),
            interactive=False,
        )


def test_remote_tree_deletion_has_path_qualified_recovery(tmp_path: Path) -> None:
    tree = TreeRead(ref=REMOTE_MAIN_REF, tree_oid="a" * 40, entries=())
    repository = MagicMock()
    repository.bare_repo = tmp_path / "dotfiles.git"
    repository.validate_tree.return_value = tree

    with pytest.raises(dotfiles.DotfilesCommandError, match="drops tracked"):
        dotfiles._remote_tree_or_refuse(repository, (_PATH,), ())


def test_conflict_recovery_is_a_refusal_with_plain_git_commands(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository = SimpleNamespace(bare_repo=tmp_path / "dotfiles.git")
    classifications = [PathClassification(_PATH, PathState.BOTH_CHANGED)]

    with pytest.raises(dotfiles.DotfilesCommandError, match="Conflicted"):
        dotfiles._refuse_sync_conflicts(repository, RefRelation.EQUAL, classifications)

    captured = capsys.readouterr()
    assert "diff origin/main" in captured.out
    assert "merge origin/main" in captured.out


def test_apply_package_gate_refuses_before_repo_access(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        dotfiles,
        "compute_system_diff",
        lambda *_args, **_kwargs: SimpleNamespace(missing=["missing-package"]),
    )
    monkeypatch.setattr(dotfiles, "_load_initialized", lambda: pytest.fail("repo was accessed"))

    result = runner.invoke(app, ["dotfiles", "apply"])

    assert result.exit_code == 1
    assert "missing packages" in result.output


def test_apply_source_materializes_before_creating_bootstrap_main(
    real_git: RealGitEnvironment,
    tmp_path: Path,
) -> None:
    _write(real_git.home, b"from remote\n")
    repository = DotfilesRepo(
        tmp_path / "dotfiles.git",
        home=real_git.home,
        state_dir=real_git.state_home / "popctl" / "dotfiles",
    )
    repository.initialize_bare()
    committed = repository.checked_commit((_PATH,), "source")
    repository.create_marker(committed.commit_oid)
    assert repository.conditional_advance_ref(REMOTE_MAIN_REF, committed.commit_oid, None)
    repository._content_git(["update-ref", "-d", MAIN_REF])
    (real_git.home / _PATH).unlink()

    dotfiles._apply_source(
        repository,
        DotfilesConfig(bare_repo=repository.bare_repo, remote_url=_REMOTE),
        dry_run=False,
    )

    assert (real_git.home / _PATH).read_bytes() == b"from remote\n"
    assert repository.ref_oid(MAIN_REF) == committed.commit_oid
    history, _ = get_history()
    assert history[0].action_type is HistoryActionType.DOTFILES_APPLY

    dotfiles._apply_source(
        repository,
        DotfilesConfig(bare_repo=repository.bare_repo, remote_url=_REMOTE),
        dry_run=False,
    )
    history_after, _ = get_history()
    assert history_after == history


@pytest.mark.parametrize(
    ("result", "next_step"),
    (
        (DesktopLoadResult(DesktopLoadStatus.DISABLED), "enable it in dotfiles.toml"),
        (DesktopLoadResult(DesktopLoadStatus.NO_ARTIFACT), "run dotfiles sync"),
        (DesktopLoadResult(DesktopLoadStatus.NO_DCONF), "install dconf-cli"),
        (DesktopLoadResult(DesktopLoadStatus.NO_SESSION), "enter a desktop session"),
        (
            DesktopLoadResult(
                DesktopLoadStatus.FAMILY_MISMATCH,
                family=desktop.DesktopFamily.GNOME,
                artifact_family=desktop.DesktopFamily.COSMIC,
            ),
            "matching desktop family",
        ),
        (DesktopLoadResult(DesktopLoadStatus.UNKNOWN_FAMILY), "supported desktop session"),
        (DesktopLoadResult(DesktopLoadStatus.INVALID_ARTIFACT), "repair or regenerate"),
        (
            DesktopLoadResult(DesktopLoadStatus.FAILED, root="/org/example/", detail="failed"),
            "rerun dotfiles apply",
        ),
    ),
)
def test_desktop_load_reports_each_skip_with_a_next_step(
    result: DesktopLoadResult,
    next_step: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    dotfiles._report_desktop_load(result)

    captured = capsys.readouterr()
    assert next_step in captured.out + captured.err


def test_desktop_load_reports_locked_key_and_suppressed_root_guidance(
    capsys: pytest.CaptureFixture[str],
) -> None:
    dotfiles._report_desktop_load(
        DesktopLoadResult(
            DesktopLoadStatus.APPLIED,
            applied_roots=("/org/example/applied/",),
            suppressed_roots=("/org/example/suppressed/",),
        )
    )

    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert "locked keys" in output
    assert "unknown schemas" in output
    assert "Update dotfiles.toml" in output


@pytest.mark.real_git
def test_apply_loads_reserved_artifact_after_materialization_and_records_metadata(
    real_git: RealGitEnvironment,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = DEFAULT_ROOTS[0]
    artifact = render_desktop_settings_artifact(
        "GNOME", (DesktopSettingsSection(root, b"[settings]\nvalue='restored'\n"),)
    )
    repository = DotfilesRepo(
        tmp_path / "dotfiles.git",
        home=real_git.home,
        state_dir=real_git.state_home / "popctl" / "dotfiles",
    )
    repository.initialize_bare()
    _write(real_git.home, b"from remote\n")
    committed = repository.checked_commit(
        (_PATH, DESKTOP_SETTINGS_ARTIFACT_PATH),
        "source",
        desktop_settings_artifact=artifact,
    )
    repository.create_marker(committed.commit_oid)
    assert repository.conditional_advance_ref(REMOTE_MAIN_REF, committed.commit_oid, None)
    repository._content_git(["update-ref", "-d", MAIN_REF])
    (real_git.home / _PATH).unlink()
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    monkeypatch.delenv("XDG_SESSION_DESKTOP", raising=False)
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")
    monkeypatch.setattr(desktop.shutil, "which", lambda _name: "/usr/bin/dconf")
    calls: list[tuple[list[str], dict[str, object]]] = []

    def load(args: list[str], **kwargs: object) -> CommandResult:
        calls.append((args, kwargs))
        return CommandResult("", "", 0)

    monkeypatch.setattr(desktop, "run_command", load)
    dotfiles._apply_source(
        repository,
        DotfilesConfig(bare_repo=repository.bare_repo, remote_url=_REMOTE),
        dry_run=False,
    )

    assert (real_git.home / _PATH).read_bytes() == b"from remote\n"
    assert not (real_git.home / DESKTOP_SETTINGS_ARTIFACT_PATH).exists()
    assert calls == [
        (
            ["dconf", "load", "-f", root],
            {"input_text": "[settings]\nvalue='restored'\n", "env": {"LC_ALL": "C"}},
        )
    ]
    history, _ = get_history()
    assert history[0].metadata["desktop_settings_applied_roots"] == root
    assert history[0].metadata["desktop_settings_family"] == "GNOME"


@pytest.mark.real_git
def test_noop_reapply_retries_desktop_load_and_records_metadata(
    real_git: RealGitEnvironment,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = DEFAULT_ROOTS[0]
    artifact = render_desktop_settings_artifact("GNOME", (DesktopSettingsSection(root, b""),))
    repository = DotfilesRepo(
        tmp_path / "dotfiles.git",
        home=real_git.home,
        state_dir=real_git.state_home / "popctl" / "dotfiles",
    )
    repository.initialize_bare()
    _write(real_git.home, b"from remote\n")
    committed = repository.checked_commit(
        (_PATH, DESKTOP_SETTINGS_ARTIFACT_PATH),
        "source",
        desktop_settings_artifact=artifact,
    )
    repository.create_marker(committed.commit_oid)
    assert repository.conditional_advance_ref(REMOTE_MAIN_REF, committed.commit_oid, None)
    repository._content_git(["update-ref", "-d", MAIN_REF])
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    monkeypatch.delenv("XDG_SESSION_DESKTOP", raising=False)
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")
    monkeypatch.setattr(desktop.shutil, "which", lambda _name: "/usr/bin/dconf")
    outcomes = iter(
        (
            CommandResult("", "Error: Failed to connect to D-Bus: Connection refused", 1),
            CommandResult("", "", 0),
        )
    )
    monkeypatch.setattr(desktop, "run_command", lambda *_args, **_kwargs: next(outcomes))
    config = DotfilesConfig(bare_repo=repository.bare_repo, remote_url=_REMOTE)

    dotfiles._apply_source(repository, config, dry_run=False)
    dotfiles._apply_source(repository, config, dry_run=False)

    history, _ = get_history()
    assert len(history) == 2
    assert history[0].items == ()
    assert history[0].metadata["desktop_settings_applied_roots"] == root


@pytest.mark.real_git
@pytest.mark.parametrize(
    "status",
    (
        DesktopLoadStatus.DISABLED,
        DesktopLoadStatus.NO_ARTIFACT,
        DesktopLoadStatus.NO_DCONF,
        DesktopLoadStatus.NO_SESSION,
        DesktopLoadStatus.FAMILY_MISMATCH,
        DesktopLoadStatus.UNKNOWN_FAMILY,
        DesktopLoadStatus.INVALID_ARTIFACT,
    ),
)
def test_apply_nonfatal_desktop_skips_materialize_files_and_exit_zero(
    real_git: RealGitEnvironment,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: DesktopLoadStatus,
) -> None:
    repository = DotfilesRepo(
        tmp_path / "dotfiles.git",
        home=real_git.home,
        state_dir=real_git.state_home / "popctl" / "dotfiles",
    )
    repository.initialize_bare()
    _write(real_git.home, b"from source\n")
    committed = repository.checked_commit((_PATH,), "source")
    repository.create_marker(committed.commit_oid)
    assert repository.conditional_advance_ref(REMOTE_MAIN_REF, committed.commit_oid, None)
    (real_git.home / _PATH).unlink()
    config = DotfilesConfig(bare_repo=repository.bare_repo, remote_url=_REMOTE)
    finalized_after_materialization: list[bool] = []

    def skip_after_materialization(*_args: object, **_kwargs: object) -> DesktopLoadResult:
        finalized_after_materialization.append((real_git.home / _PATH).exists())
        return DesktopLoadResult(status)

    monkeypatch.setattr(
        dotfiles,
        "compute_system_diff",
        lambda *_args, **_kwargs: SimpleNamespace(missing=[]),
    )
    monkeypatch.setattr(dotfiles, "_load_initialized", lambda: config)
    monkeypatch.setattr(DotfilesRepo, "fetch", lambda *_args, **_kwargs: _success_transport())
    monkeypatch.setattr(dotfiles, "_load_desktop_settings_for_apply", skip_after_materialization)
    monkeypatch.setattr(desktop, "run_command", lambda *_args, **_kwargs: pytest.fail("dconf"))

    result = runner.invoke(app, ["dotfiles", "apply"])

    assert result.exit_code == 0, result.output
    assert (real_git.home / _PATH).read_bytes() == b"from source\n"
    assert finalized_after_materialization == [True]


@pytest.mark.real_git
def test_apply_dry_run_previews_valid_artifact_without_dconf_or_persistent_writes(
    real_git: RealGitEnvironment,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = DEFAULT_ROOTS[0]
    artifact = render_desktop_settings_artifact("GNOME", (DesktopSettingsSection(root, b""),))
    state_dir = real_git.state_home / "popctl" / "dotfiles"
    repository = DotfilesRepo(tmp_path / "dotfiles.git", home=real_git.home, state_dir=state_dir)
    repository.initialize_bare()
    _write(real_git.home, b"from source\n")
    committed = repository.checked_commit(
        (_PATH, DESKTOP_SETTINGS_ARTIFACT_PATH),
        "source",
        desktop_settings_artifact=artifact,
    )
    repository.create_marker(committed.commit_oid)
    assert repository.conditional_advance_ref(REMOTE_MAIN_REF, committed.commit_oid, None)
    before_home = (real_git.home / _PATH).read_bytes()
    assets_before = _owned_asset_state(state_dir)
    config = DotfilesConfig(bare_repo=repository.bare_repo, remote_url=_REMOTE)
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    monkeypatch.delenv("XDG_SESSION_DESKTOP", raising=False)
    monkeypatch.setattr(desktop.shutil, "which", lambda _name: pytest.fail("dconf lookup"))
    monkeypatch.setattr(desktop, "run_command", lambda *_args, **_kwargs: pytest.fail("dconf"))
    monkeypatch.setattr(
        dotfiles,
        "compute_system_diff",
        lambda *_args, **_kwargs: SimpleNamespace(missing=[]),
    )
    monkeypatch.setattr(dotfiles, "_load_initialized", lambda: config)
    monkeypatch.setattr(
        DotfilesRepo,
        "fetch_temporary_main",
        lambda *_args, **_kwargs: TemporaryFetchResult(_success_transport(), committed.commit_oid),
    )

    result = runner.invoke(app, ["dotfiles", "apply", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "Desktop-settings dry-run" in result.output
    assert (real_git.home / _PATH).read_bytes() == before_home
    assert not (real_git.home / DESKTOP_SETTINGS_ARTIFACT_PATH).exists()
    assert get_history()[0] == []
    assert _owned_asset_state(state_dir) == assets_before


@pytest.mark.real_git
@pytest.mark.parametrize("no_op", (False, True))
def test_apply_desktop_finalizer_follows_successful_materialization(
    real_git: RealGitEnvironment,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    no_op: bool,
) -> None:
    repository = DotfilesRepo(
        tmp_path / "dotfiles.git",
        home=real_git.home,
        state_dir=real_git.state_home / "popctl" / "dotfiles",
    )
    repository.initialize_bare()
    _write(real_git.home, b"source\n")
    committed = repository.checked_commit((_PATH,), "source")
    repository.create_marker(committed.commit_oid)
    assert repository.conditional_advance_ref(REMOTE_MAIN_REF, committed.commit_oid, None)
    if not no_op:
        (real_git.home / _PATH).unlink()
    events: list[str] = []
    original_execute = dotfiles.execute_materialization_plan

    def execute(*args: object, **kwargs: object) -> tuple[str, ...]:
        events.append("materialize")
        return original_execute(*args, **kwargs)

    def finalize(*_args: object, **_kwargs: object) -> DesktopLoadResult:
        events.append("desktop")
        return DesktopLoadResult(DesktopLoadStatus.DISABLED)

    def record(*_args: object, **_kwargs: object) -> None:
        events.append("history")

    monkeypatch.setattr(dotfiles, "execute_materialization_plan", execute)
    monkeypatch.setattr(dotfiles, "_load_desktop_settings_for_apply", finalize)
    monkeypatch.setattr(dotfiles, "_record_dotfiles_action", record)
    dotfiles._apply_source(
        repository,
        DotfilesConfig(bare_repo=repository.bare_repo, remote_url=_REMOTE),
        dry_run=False,
    )

    assert events == ["materialize", "desktop", "history"]


@pytest.mark.real_git
def test_apply_materialization_failure_never_runs_desktop_finalizer(
    real_git: RealGitEnvironment,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = DotfilesRepo(
        tmp_path / "dotfiles.git",
        home=real_git.home,
        state_dir=real_git.state_home / "popctl" / "dotfiles",
    )
    repository.initialize_bare()
    _write(real_git.home, b"source\n")
    committed = repository.checked_commit((_PATH,), "source")
    repository.create_marker(committed.commit_oid)
    assert repository.conditional_advance_ref(REMOTE_MAIN_REF, committed.commit_oid, None)
    (real_git.home / _PATH).unlink()

    def fail_materialization(*_args: object, **_kwargs: object) -> tuple[str, ...]:
        raise materialize.MaterializationError("write failed")

    monkeypatch.setattr(
        dotfiles,
        "execute_materialization_plan",
        fail_materialization,
    )
    monkeypatch.setattr(
        dotfiles,
        "_load_desktop_settings_for_apply",
        lambda *_args, **_kwargs: pytest.fail("desktop finalizer"),
    )

    with pytest.raises(materialize.MaterializationError, match="write failed"):
        dotfiles._apply_source(
            repository,
            DotfilesConfig(bare_repo=repository.bare_repo, remote_url=_REMOTE),
            dry_run=False,
        )


@pytest.mark.real_git
@pytest.mark.parametrize("operation", ["apply", "sync"])
def test_materialization_pins_the_validated_source_when_origin_moves(
    real_git: RealGitEnvironment,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    repository, config, base_oid, source_oid = _configured_source_repo(real_git, tmp_path)
    newer_oid = _add_remote_descendant(
        repository,
        real_git,
        base_oid=base_oid,
        source_oid=source_oid,
    )
    original_execute = dotfiles.execute_materialization_plan

    def materialize_then_advance_origin(*args: object, **kwargs: object) -> tuple[str, ...]:
        changed = original_execute(*args, **kwargs)
        assert repository.conditional_advance_ref(REMOTE_MAIN_REF, newer_oid, source_oid)
        return changed

    monkeypatch.setattr(dotfiles, "execute_materialization_plan", materialize_then_advance_origin)
    monkeypatch.setattr(dotfiles, "_record_dotfiles_action", lambda *_args, **_kwargs: None)
    if operation == "apply":
        dotfiles._apply_source(repository, config, dry_run=False)
    else:
        monkeypatch.setattr(
            dotfiles,
            "discover_dotfiles",
            lambda *_args, **_kwargs: dotfiles.DiscoveryResult((), ()),
        )
        monkeypatch.setattr(
            dotfiles,
            "_review_candidates",
            lambda *_args, **_kwargs: dotfiles.ReviewResult(
                DotfilesReviewFinalization((), (), ()), config
            ),
        )
        dotfiles._sync_online(repository, config, interactive=False)

    assert (real_git.home / _PATH).read_bytes() == b"remote\n"
    assert repository.ref_oid(MAIN_REF) == source_oid
    assert repository.ref_oid(REMOTE_MAIN_REF) == newer_oid


@pytest.mark.real_git
def test_apply_dry_run_with_a_differing_source_has_status_only_mutations(
    real_git: RealGitEnvironment,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, _config, base_oid, source_oid = _configured_source_repo(real_git, tmp_path)
    state_dir = real_git.state_home / "popctl" / "dotfiles"
    assets_before = _owned_asset_state(state_dir)
    history_before, _ = get_history()
    fetch_calls: list[bool] = []

    def cached_temporary_fetch(_self: DotfilesRepo, _url: str) -> TemporaryFetchResult:
        fetch_calls.append(True)
        return TemporaryFetchResult(_success_transport(), source_oid)

    monkeypatch.setattr(DotfilesRepo, "fetch_temporary_main", cached_temporary_fetch)
    monkeypatch.setattr(
        dotfiles,
        "compute_system_diff",
        lambda *_args, **_kwargs: SimpleNamespace(missing=[]),
    )

    result = runner.invoke(app, ["dotfiles", "apply", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "replace" in result.output
    assert fetch_calls == [True]
    assert (real_git.home / _PATH).read_bytes() == b"base\n"
    assert repository.ref_oid(MAIN_REF) == base_oid
    assert repository.ref_oid(REMOTE_MAIN_REF) == source_oid
    assert not get_plan_path(PlanOperation.APPLY, state_dir).exists()
    assert not get_completed_paths_journal_path(PlanOperation.APPLY, state_dir).exists()
    assert not get_dotfiles_lock_path(state_dir).exists()
    assert get_history()[0] == history_before
    assert _owned_asset_state(state_dir) == assets_before


@pytest.mark.real_git
def test_apply_dry_run_fetches_the_remote_tip_without_advancing_origin_main(
    real_git: RealGitEnvironment,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = real_git.state_home / "popctl" / "dotfiles"
    remote_store = DotfilesRepo(
        tmp_path / "remote.git",
        home=real_git.home,
        state_dir=real_git.state_home / "popctl" / "remote",
    )
    remote_store.initialize_bare()
    _route_network_to_local_remote(monkeypatch, remote_store)
    repository = DotfilesRepo(tmp_path / "dotfiles.git", home=real_git.home, state_dir=state_dir)
    repository.initialize_bare()
    repository.setup_remote(_REMOTE)
    _write(real_git.home, b"base\n")
    base_oid = repository.checked_commit((_PATH,), "base").commit_oid
    repository.create_marker(base_oid)
    assert repository.push(_REMOTE).success
    assert repository.conditional_advance_ref(REMOTE_MAIN_REF, base_oid, None)
    _write(real_git.home, b"remote\n")
    remote_oid = repository.checked_commit((_PATH,), "remote").commit_oid
    assert repository.push(_REMOTE).success
    assert repository.conditional_advance_ref(MAIN_REF, base_oid, remote_oid)
    _write(real_git.home, b"base\n")
    config = DotfilesConfig(
        bare_repo=repository.bare_repo,
        remote_url=_REMOTE,
        remote_privacy=RemotePrivacyRecord(canonical_remote_url=_REMOTE, method="acknowledged"),
    )
    save_dotfiles_config(config)
    home_before = (real_git.home / _PATH).read_bytes()
    assets_before = _owned_asset_state(state_dir)
    history_before, _ = get_history()
    monkeypatch.setattr(
        dotfiles,
        "compute_system_diff",
        lambda *_args, **_kwargs: SimpleNamespace(missing=[]),
    )

    result = runner.invoke(app, ["dotfiles", "apply", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "replace" in result.output
    assert (real_git.home / _PATH).read_bytes() == home_before
    assert repository.ref_oid(MAIN_REF) == base_oid
    assert repository.ref_oid(REMOTE_MAIN_REF) == base_oid
    assert not get_plan_path(PlanOperation.APPLY, state_dir).exists()
    assert not get_completed_paths_journal_path(PlanOperation.APPLY, state_dir).exists()
    assert not get_dotfiles_lock_path(state_dir).exists()
    assert get_history()[0] == history_before
    assert _owned_asset_state(state_dir) == assets_before


@pytest.mark.real_git
def test_sync_pushes_a_pending_initial_commit_to_an_empty_remote(
    real_git: RealGitEnvironment,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = real_git.state_home / "popctl" / "dotfiles"
    remote_store = DotfilesRepo(
        tmp_path / "remote.git",
        home=real_git.home,
        state_dir=real_git.state_home / "popctl" / "remote",
    )
    remote_store.initialize_bare()
    _route_network_to_local_remote(monkeypatch, remote_store)
    _write(real_git.home, b"initial\n")
    monkeypatch.setattr(dotfiles.shutil, "which", lambda _name: None)
    monkeypatch.setattr(dotfiles, "_review_candidates", _review_with_track)
    monkeypatch.setattr(
        dotfiles,
        "_acquire_private_remote",
        lambda url, **_kwargs: (
            RemotePrivacyRecord(canonical_remote_url=url, method="acknowledged"),
            False,
        ),
    )
    original_push = DotfilesRepo.push
    attempts = 0

    def fail_only_the_initial_push(repository: DotfilesRepo, url: str) -> TransportResult:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return TransportResult(TransportOutcome.OTHER, "injected initial push failure", 1)
        return original_push(repository, url)

    monkeypatch.setattr(DotfilesRepo, "push", fail_only_the_initial_push)

    initialized = runner.invoke(app, ["dotfiles", "init", "--remote", _REMOTE])
    config = load_dotfiles_config()
    repository = DotfilesRepo(config.bare_repo, home=real_git.home, state_dir=state_dir)
    local_oid = repository.ref_oid(MAIN_REF)

    assert initialized.exit_code == 0, initialized.output
    assert local_oid is not None
    assert remote_store.ref_oid(MAIN_REF) is None
    synchronized = runner.invoke(app, ["dotfiles", "sync"])

    assert synchronized.exit_code == 0, synchronized.output
    assert attempts == 2
    assert remote_store.ref_oid(MAIN_REF) == local_oid


@pytest.mark.real_git
def test_sync_refuses_a_nonempty_remote_without_main(
    real_git: RealGitEnvironment,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = real_git.state_home / "popctl" / "dotfiles"
    remote_store = DotfilesRepo(
        tmp_path / "remote.git",
        home=real_git.home,
        state_dir=real_git.state_home / "popctl" / "remote",
    )
    remote_store.initialize_bare()
    _route_network_to_local_remote(monkeypatch, remote_store)
    repository = DotfilesRepo(tmp_path / "dotfiles.git", home=real_git.home, state_dir=state_dir)
    repository.initialize_bare()
    repository.setup_remote(_REMOTE)
    _write(real_git.home, b"initial\n")
    local_oid = repository.checked_commit((_PATH,), "initial").commit_oid
    repository.create_marker(local_oid)
    assert repository.push(_REMOTE).success
    remote_store._content_git(["update-ref", "-d", MAIN_REF])
    assert remote_store.ref_oid(MAIN_REF) is None
    assert remote_store.verify_marker()
    save_dotfiles_config(
        DotfilesConfig(
            bare_repo=repository.bare_repo,
            remote_url=_REMOTE,
            remote_privacy=RemotePrivacyRecord(
                canonical_remote_url=_REMOTE,
                method="acknowledged",
            ),
        )
    )
    monkeypatch.setattr(
        dotfiles,
        "_pre_push_privacy",
        lambda *_args, **_kwargs: pytest.fail("sync must not push to a nonempty remote"),
    )

    result = runner.invoke(app, ["dotfiles", "sync"])

    assert result.exit_code == 1
    assert "not proven empty" in result.output
    assert remote_store.ref_oid(MAIN_REF) is None
    assert remote_store.verify_marker()


@pytest.mark.real_git
def test_sync_pushes_a_local_ahead_tracked_path_addition(
    real_git: RealGitEnvironment,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = real_git.state_home / "popctl" / "dotfiles"
    remote_store = DotfilesRepo(
        tmp_path / "remote.git",
        home=real_git.home,
        state_dir=real_git.state_home / "popctl" / "remote",
    )
    remote_store.initialize_bare()
    _route_network_to_local_remote(monkeypatch, remote_store)
    repository = DotfilesRepo(tmp_path / "dotfiles.git", home=real_git.home, state_dir=state_dir)
    repository.initialize_bare()
    repository.setup_remote(_REMOTE)
    _write(real_git.home, b"base\n")
    base_oid = repository.checked_commit((_PATH,), "base").commit_oid
    repository.create_marker(base_oid)
    assert repository.push(_REMOTE).success
    assert repository.conditional_advance_ref(REMOTE_MAIN_REF, base_oid, None)
    added_path = real_git.home / _NEW_PATH
    added_path.parent.mkdir(parents=True, exist_ok=True)
    added_path.write_bytes(b"local addition\n")
    local_oid = repository.checked_commit((_NEW_PATH,), "add tracked path").commit_oid
    config = DotfilesConfig(
        bare_repo=repository.bare_repo,
        remote_url=_REMOTE,
        remote_privacy=RemotePrivacyRecord(canonical_remote_url=_REMOTE, method="acknowledged"),
    )
    save_dotfiles_config(config)
    monkeypatch.setattr(
        dotfiles,
        "discover_dotfiles",
        lambda *_args, **_kwargs: dotfiles.DiscoveryResult((), ()),
    )
    monkeypatch.setattr(
        dotfiles,
        "_review_candidates",
        lambda *_args, **_kwargs: dotfiles.ReviewResult(
            DotfilesReviewFinalization((), (), ()), config
        ),
    )
    monkeypatch.setattr(dotfiles, "_pre_push_privacy", lambda config, **_kwargs: config)

    result = runner.invoke(app, ["dotfiles", "sync"])

    assert result.exit_code == 0, result.output
    assert remote_store.ref_oid(MAIN_REF) == local_oid
    assert {entry.path for entry in remote_store.read_tree(MAIN_REF).entries} == {
        _PATH,
        _NEW_PATH,
    }


@pytest.mark.real_git
def test_sync_retires_completed_state_after_the_remote_advances_again(
    real_git: RealGitEnvironment,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, config, base_oid, source_oid = _configured_source_repo(real_git, tmp_path)
    state_dir = real_git.state_home / "popctl" / "dotfiles"
    newer_oid = _add_remote_descendant(
        repository,
        real_git,
        base_oid=base_oid,
        source_oid=source_oid,
    )
    monkeypatch.setattr(
        dotfiles,
        "discover_dotfiles",
        lambda *_args, **_kwargs: dotfiles.DiscoveryResult((), ()),
    )
    monkeypatch.setattr(
        dotfiles,
        "_review_candidates",
        lambda *_args, **_kwargs: dotfiles.ReviewResult(
            DotfilesReviewFinalization((), (), ()), config
        ),
    )
    monkeypatch.setattr(dotfiles, "_record_dotfiles_action", lambda *_args, **_kwargs: None)
    original_clear = state.clear_materialization_state

    def crash_before_retiring_completed_state(
        operation: PlanOperation, state_dir: Path | None = None
    ) -> None:
        raise DotfilesStateError("injected completed-state retirement crash")

    monkeypatch.setattr(state, "clear_materialization_state", crash_before_retiring_completed_state)
    with pytest.raises(DotfilesStateError, match="completed-state retirement"):
        dotfiles._sync_online(repository, config, interactive=False)

    assert repository.ref_oid(MAIN_REF) == source_oid
    assert get_plan_path(PlanOperation.INBOUND_SYNC, state_dir).exists()
    assert get_completed_paths_journal_path(PlanOperation.INBOUND_SYNC, state_dir).exists()
    assert repository.conditional_advance_ref(REMOTE_MAIN_REF, newer_oid, source_oid)
    monkeypatch.setattr(state, "clear_materialization_state", original_clear)

    dotfiles._sync_online(repository, config, interactive=False)

    assert repository.ref_oid(MAIN_REF) == newer_oid
    assert (real_git.home / _PATH).read_bytes() == b"newer remote\n"
    assert not get_plan_path(PlanOperation.INBOUND_SYNC, state_dir).exists()
    assert not get_completed_paths_journal_path(PlanOperation.INBOUND_SYNC, state_dir).exists()


@pytest.mark.real_git
def test_apply_retires_completed_state_before_preflighting_new_source(
    real_git: RealGitEnvironment,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, config, base_oid, source_oid = _configured_source_repo(real_git, tmp_path)
    state_dir = real_git.state_home / "popctl" / "dotfiles"
    original_clear = state.clear_materialization_state

    def crash_before_retiring_completed_state(
        operation: PlanOperation, state_dir: Path | None = None
    ) -> None:
        raise DotfilesStateError("injected completed-state retirement crash")

    monkeypatch.setattr(state, "clear_materialization_state", crash_before_retiring_completed_state)
    with pytest.raises(DotfilesStateError, match="completed-state retirement"):
        dotfiles._apply_source(repository, config, dry_run=False)

    assert repository.ref_oid(MAIN_REF) == source_oid
    assert get_plan_path(PlanOperation.APPLY, state_dir).exists()
    assert get_completed_paths_journal_path(PlanOperation.APPLY, state_dir).exists()
    _write(real_git.home, b"newer remote\n")
    newer_oid = repository.checked_commit((_PATH,), "newer remote").commit_oid
    assert repository.conditional_advance_ref(REMOTE_MAIN_REF, newer_oid, source_oid)
    assert repository.conditional_advance_ref(MAIN_REF, source_oid, newer_oid)
    _write(real_git.home, b"remote\n")
    monkeypatch.setattr(state, "clear_materialization_state", original_clear)

    dotfiles._apply_source(repository, config, dry_run=False)

    assert repository.ref_oid(MAIN_REF) == newer_oid
    assert (real_git.home / _PATH).read_bytes() == b"newer remote\n"
    assert not get_plan_path(PlanOperation.APPLY, state_dir).exists()
    assert not get_completed_paths_journal_path(PlanOperation.APPLY, state_dir).exists()
    assert base_oid != newer_oid


@pytest.mark.real_git
@pytest.mark.parametrize("failure_call", [2, 3])
def test_real_init_promotion_crash_recovers_and_reruns(
    real_git: RealGitEnvironment,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_call: int,
) -> None:
    state_dir = real_git.state_home / "popctl" / "dotfiles"
    final_store = tmp_path / "dotfiles.git"
    config = DotfilesConfig(bare_repo=final_store, remote_url=_REMOTE)
    original_save_journal = dotfiles.save_init_finalization_journal
    calls = 0

    def fail_after_promotion_phase(
        journal: InitFinalizationJournal, state_dir_arg: Path | None = None
    ) -> Path:
        nonlocal calls
        calls += 1
        if calls == failure_call:
            raise DotfilesStateError("injected promotion crash")
        return original_save_journal(journal, state_dir_arg)

    temporary_store = tmp_path / "temporary.git"
    temporary_store.mkdir()
    monkeypatch.setattr(dotfiles, "save_init_finalization_journal", fail_after_promotion_phase)
    with pytest.raises(DotfilesStateError, match="promotion crash"):
        dotfiles._promote_initialized_store(
            temporary_store=temporary_store,
            final_store=final_store,
            config=config,
            created_remote=None,
        )

    recovery = recover_init_finalization(state_dir)

    assert recovery is not None
    assert not final_store.exists()
    assert not get_dotfiles_config_path().exists()
    monkeypatch.setattr(dotfiles, "save_init_finalization_journal", original_save_journal)
    retry_store = tmp_path / "retry.git"
    retry_store.mkdir()
    dotfiles._promote_initialized_store(
        temporary_store=retry_store,
        final_store=final_store,
        config=config,
        created_remote=None,
    )

    assert final_store.exists()
    assert get_dotfiles_config_path().exists()
    assert not get_init_finalization_journal_path(state_dir).exists()


@pytest.mark.real_git
def test_cli_init_from_status_sync_and_apply_bootstraps_a_real_local_remote(
    real_git: RealGitEnvironment,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = real_git.state_home / "popctl" / "dotfiles"
    remote_store = DotfilesRepo(
        tmp_path / "remote.git",
        home=real_git.home,
        state_dir=real_git.state_home / "popctl" / "remote",
    )
    remote_store.initialize_bare()
    source = DotfilesRepo(
        tmp_path / "source.git",
        home=real_git.home,
        state_dir=real_git.state_home / "popctl" / "source",
    )
    source.initialize_bare()
    _write(real_git.home, b"bootstrap source\n")
    source_oid = source.checked_commit((_PATH,), "bootstrap source").commit_oid
    source.create_marker(source_oid)
    local_url = f"file://{remote_store.bare_repo}"
    source._install_test_remote(local_url)
    pushed = source._network_git(
        ["push", local_url, f"{MAIN_REF}:{MAIN_REF}", "refs/tags/popctl-dotfiles-format-v1"],
        local_url,
    )
    assert pushed.success
    (real_git.home / _PATH).unlink()
    _route_network_to_local_remote(monkeypatch, remote_store)
    monkeypatch.setattr(
        dotfiles,
        "_acquire_private_remote",
        lambda url, **_kwargs: (
            RemotePrivacyRecord(canonical_remote_url=url, method="acknowledged"),
            False,
        ),
    )
    monkeypatch.setattr(
        dotfiles,
        "compute_system_diff",
        lambda *_args, **_kwargs: SimpleNamespace(missing=[]),
    )

    initialized = runner.invoke(app, ["dotfiles", "init", "--from", _REMOTE])
    status = runner.invoke(app, ["dotfiles", "status"])
    synchronized = runner.invoke(app, ["dotfiles", "sync"])
    applied = runner.invoke(app, ["dotfiles", "apply"])
    config = load_dotfiles_config()
    repository = DotfilesRepo(config.bare_repo, home=real_git.home, state_dir=state_dir)

    assert initialized.exit_code == 0, initialized.output
    assert status.exit_code == 0, status.output
    assert "bootstrap is pending" in status.output
    assert synchronized.exit_code == 0, synchronized.output
    assert applied.exit_code == 0, applied.output
    assert repository.ref_oid(MAIN_REF) == source_oid
    assert repository.ref_oid(REMOTE_MAIN_REF) == source_oid
    assert (real_git.home / _PATH).read_bytes() == b"bootstrap source\n"


@pytest.mark.real_git
def test_cli_status_divergence_exits_one_without_mutating_local_state(
    real_git: RealGitEnvironment,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = real_git.state_home / "popctl" / "dotfiles"
    remote_store = DotfilesRepo(
        tmp_path / "remote.git",
        home=real_git.home,
        state_dir=real_git.state_home / "popctl" / "remote",
    )
    remote_store.initialize_bare()
    _route_network_to_local_remote(monkeypatch, remote_store)
    repository = DotfilesRepo(tmp_path / "dotfiles.git", home=real_git.home, state_dir=state_dir)
    repository.initialize_bare()
    repository.setup_remote(_REMOTE)
    _write(real_git.home, b"base\n")
    base_oid = repository.checked_commit((_PATH,), "base").commit_oid
    repository.create_marker(base_oid)
    assert repository.push(_REMOTE).success
    assert repository.conditional_advance_ref(REMOTE_MAIN_REF, base_oid, None)
    _write(real_git.home, b"remote\n")
    remote_oid = repository.checked_commit((_PATH,), "remote").commit_oid
    assert repository.push(_REMOTE).success
    assert repository.conditional_advance_ref(MAIN_REF, base_oid, remote_oid)
    _write(real_git.home, b"local\n")
    local_oid = repository.checked_commit((_PATH,), "local").commit_oid
    config = DotfilesConfig(
        bare_repo=repository.bare_repo,
        remote_url=_REMOTE,
        remote_privacy=RemotePrivacyRecord(canonical_remote_url=_REMOTE, method="acknowledged"),
    )
    save_dotfiles_config(config)
    home_before = (real_git.home / _PATH).read_bytes()
    head_before = (repository.bare_repo / "HEAD").read_bytes()
    repo_config_before = (repository.bare_repo / "config").read_bytes()
    index_path = repository.bare_repo / "index"
    index_before = index_path.read_bytes() if index_path.exists() else None
    config_before = get_dotfiles_config_path().read_bytes()
    history_before, _ = get_history()
    assets_before = _owned_asset_state(state_dir)

    result = runner.invoke(app, ["dotfiles", "status"])

    assert result.exit_code == 1
    assert "diverged" in result.output
    assert (real_git.home / _PATH).read_bytes() == home_before
    assert repository.ref_oid(MAIN_REF) == local_oid
    assert repository.ref_oid(REMOTE_MAIN_REF) == remote_oid
    assert (repository.bare_repo / "HEAD").read_bytes() == head_before
    assert (repository.bare_repo / "config").read_bytes() == repo_config_before
    assert (index_path.read_bytes() if index_path.exists() else None) == index_before
    assert get_dotfiles_config_path().read_bytes() == config_before
    assert get_history()[0] == history_before
    assert _owned_asset_state(state_dir) == assets_before
    assert not get_plan_path(PlanOperation.APPLY, state_dir).exists()
    assert not get_plan_path(PlanOperation.INBOUND_SYNC, state_dir).exists()


@pytest.mark.real_git
def test_full_cli_init_status_sync_and_apply_uses_a_real_constrained_transport(
    real_git: RealGitEnvironment,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state_dir = real_git.state_home / "popctl" / "dotfiles"
    remote_store = DotfilesRepo(
        tmp_path / "remote.git",
        home=real_git.home,
        state_dir=real_git.state_home / "popctl" / "remote",
    )
    remote_store.initialize_bare()
    local_url = f"file://{remote_store.bare_repo}"
    original_network_git = DotfilesRepo._network_git

    def local_network_git(
        self: DotfilesRepo,
        args: list[str],
        canonical_url: str,
        *,
        timeout_seconds: float = 30.0,
    ) -> object:
        local_args = [local_url if argument == canonical_url else argument for argument in args]
        return original_network_git(
            self,
            local_args,
            canonical_url,
            timeout_seconds=timeout_seconds,
        )

    def review_current_candidates(
        discovery: dotfiles.DiscoveryResult,
        config: DotfilesConfig,
        *,
        interactive: bool,
    ) -> dotfiles.ReviewResult:
        tracked = (_PATH,) if discovery.candidates else ()
        return dotfiles.ReviewResult(DotfilesReviewFinalization(tracked, (), ()), config)

    monkeypatch.setattr(DotfilesRepo, "_network_git", local_network_git)
    monkeypatch.setattr(dotfiles.shutil, "which", lambda _name: None)
    monkeypatch.setattr(dotfiles, "_review_candidates", review_current_candidates)
    monkeypatch.setattr(
        dotfiles,
        "_acquire_private_remote",
        lambda url, **_kwargs: (
            RemotePrivacyRecord(canonical_remote_url=url, method="acknowledged"),
            False,
        ),
    )
    monkeypatch.setattr(
        dotfiles,
        "compute_system_diff",
        lambda *_args, **_kwargs: SimpleNamespace(missing=[]),
    )
    _write(real_git.home, b"initial\n")

    initialized = runner.invoke(app, ["dotfiles", "init", "--remote", _REMOTE])
    status = runner.invoke(app, ["dotfiles", "status"])
    _write(real_git.home, b"synced\n")
    synced = runner.invoke(app, ["dotfiles", "sync"])

    assert initialized.exit_code == 0, initialized.output
    assert status.exit_code == 0, status.output
    assert synced.exit_code == 0, synced.output
    config = load_dotfiles_config()
    repository = DotfilesRepo(config.bare_repo, home=real_git.home, state_dir=state_dir)
    synced_oid = repository.ref_oid(MAIN_REF)
    assert synced_oid is not None
    _write(real_git.home, b"remote apply\n")
    remote_oid = repository.checked_commit((_PATH,), "remote apply").commit_oid
    assert repository.push(_REMOTE).success
    assert repository.conditional_advance_ref(MAIN_REF, synced_oid, remote_oid)
    _write(real_git.home, b"synced\n")

    applied = runner.invoke(app, ["dotfiles", "apply"])

    assert applied.exit_code == 0, applied.output
    assert (real_git.home / _PATH).read_bytes() == b"remote apply\n"
    assert repository.ref_oid(MAIN_REF) == remote_oid
    assert remote_store.ref_oid(MAIN_REF) == remote_oid


@pytest.mark.real_git
def test_cli_apply_refuses_no_clobber_without_state_mutation(
    real_git: RealGitEnvironment,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, _config, base_oid, source_oid = _configured_source_repo(
        real_git,
        tmp_path,
        target=b"foreign\n",
    )
    state_dir = real_git.state_home / "popctl" / "dotfiles"
    monkeypatch.setattr(DotfilesRepo, "fetch", lambda *_args, **_kwargs: _success_transport())
    monkeypatch.setattr(
        DotfilesRepo,
        "ls_remote_all_refs",
        lambda *_args: LsRemoteResult(_success_transport(), (RemoteRef(source_oid, MAIN_REF),)),
    )
    monkeypatch.setattr(
        dotfiles,
        "compute_system_diff",
        lambda *_args, **_kwargs: SimpleNamespace(missing=[]),
    )

    result = runner.invoke(app, ["dotfiles", "apply"])

    assert result.exit_code == 1
    assert "differing dotfiles target" in result.output
    assert (real_git.home / _PATH).read_bytes() == b"foreign\n"
    assert repository.ref_oid(MAIN_REF) == base_oid
    assert repository.ref_oid(REMOTE_MAIN_REF) == source_oid
    assert not get_plan_path(PlanOperation.APPLY, state_dir).exists()
    assert get_history()[0] == []


@pytest.mark.real_git
@pytest.mark.parametrize("refusal", ["conflict", "divergence"])
def test_cli_sync_refusal_exit_codes_preserve_home_refs_history_and_state(
    real_git: RealGitEnvironment,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    refusal: str,
) -> None:
    repository, _config, base_oid, source_oid = _configured_source_repo(
        real_git,
        tmp_path,
        target=b"local\n",
    )
    if refusal == "divergence":
        _write(real_git.home, b"local divergence\n")
        local_oid = repository.checked_commit((_PATH,), "local divergence").commit_oid
    else:
        local_oid = base_oid
    state_dir = real_git.state_home / "popctl" / "dotfiles"
    monkeypatch.setattr(DotfilesRepo, "fetch", lambda *_args, **_kwargs: _success_transport())
    monkeypatch.setattr(
        DotfilesRepo,
        "ls_remote_all_refs",
        lambda *_args: LsRemoteResult(_success_transport(), (RemoteRef(source_oid, MAIN_REF),)),
    )

    result = runner.invoke(app, ["dotfiles", "sync"])

    assert result.exit_code == 1
    assert ("Conflicted" if refusal == "conflict" else "diverged") in result.output
    assert repository.ref_oid(MAIN_REF) == local_oid
    assert repository.ref_oid(REMOTE_MAIN_REF) == source_oid
    assert (real_git.home / _PATH).read_bytes() == (
        b"local\n" if refusal == "conflict" else b"local divergence\n"
    )
    assert not get_plan_path(PlanOperation.INBOUND_SYNC, state_dir).exists()
    assert get_history()[0] == []


@pytest.mark.real_git
def test_cli_apply_recovers_after_replace_before_journal_record(
    real_git: RealGitEnvironment,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, _config, base_oid, source_oid = _configured_source_repo(real_git, tmp_path)
    state_dir = real_git.state_home / "popctl" / "dotfiles"
    original_record = materialize.record_completed_path
    monkeypatch.setattr(DotfilesRepo, "fetch", lambda *_args, **_kwargs: _success_transport())
    monkeypatch.setattr(
        dotfiles,
        "compute_system_diff",
        lambda *_args, **_kwargs: SimpleNamespace(missing=[]),
    )

    def crash_after_replace(*_args: object, **_kwargs: object) -> None:
        raise DotfilesStateError("crash after replace")

    monkeypatch.setattr(materialize, "record_completed_path", crash_after_replace)
    interrupted = runner.invoke(app, ["dotfiles", "apply"])

    assert interrupted.exit_code == 1
    assert (real_git.home / _PATH).read_bytes() == b"remote\n"
    assert repository.ref_oid(MAIN_REF) == base_oid
    assert repository.ref_oid(REMOTE_MAIN_REF) == source_oid
    assert get_plan_path(PlanOperation.APPLY, state_dir).exists()
    assert get_completed_paths_journal_path(PlanOperation.APPLY, state_dir).exists()
    assert get_history()[0] == []

    monkeypatch.setattr(materialize, "record_completed_path", original_record)
    resumed = runner.invoke(app, ["dotfiles", "apply"])

    assert resumed.exit_code == 0, resumed.output
    assert repository.ref_oid(MAIN_REF) == source_oid
    assert not get_plan_path(PlanOperation.APPLY, state_dir).exists()
    assert not get_completed_paths_journal_path(PlanOperation.APPLY, state_dir).exists()
    assert get_history()[0][0].action_type is HistoryActionType.DOTFILES_APPLY


@pytest.mark.real_git
@pytest.mark.parametrize(
    ("command", "operation"),
    [("apply", PlanOperation.APPLY), ("sync", PlanOperation.INBOUND_SYNC)],
)
def test_cli_materialization_refuses_parent_swap_after_replace_before_journal(
    real_git: RealGitEnvironment,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    command: str,
    operation: PlanOperation,
) -> None:
    repository, _config, base_oid, source_oid = _configured_source_repo(real_git, tmp_path)
    state_dir = real_git.state_home / "popctl" / "dotfiles"
    original_verify = materialize._verify_visible_target
    monkeypatch.setattr(DotfilesRepo, "fetch", lambda *_args, **_kwargs: _success_transport())
    monkeypatch.setattr(
        DotfilesRepo,
        "ls_remote_all_refs",
        lambda *_args: LsRemoteResult(_success_transport(), (RemoteRef(source_oid, MAIN_REF),)),
    )
    monkeypatch.setattr(
        dotfiles,
        "compute_system_diff",
        lambda *_args, **_kwargs: SimpleNamespace(missing=[]),
    )

    def swap_parent_then_verify(
        home: Path, path: str, written_identity: tuple[int, int]
    ) -> None:
        parent = home / ".config"
        parent.rename(home / ".config-moved")
        redirected_parent = tmp_path / "redirected-config"
        redirected_parent.mkdir()
        parent.symlink_to(redirected_parent)
        original_verify(home, path, written_identity)

    monkeypatch.setattr(materialize, "_verify_visible_target", swap_parent_then_verify)
    result = runner.invoke(app, ["dotfiles", command])

    assert result.exit_code == 1
    assert "visible target changed" in result.output
    assert repository.ref_oid(MAIN_REF) == base_oid
    assert repository.ref_oid(REMOTE_MAIN_REF) == source_oid
    assert not (real_git.home / _PATH).exists()
    assert load_completed_paths_journal(operation, state_dir).completed_paths == ()
    assert get_history()[0] == []
