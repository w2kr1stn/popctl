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
from popctl.dotfiles.config import (
    DotfilesConfig,
    RemotePrivacyRecord,
    load_dotfiles_config,
)
from popctl.dotfiles.discovery import Candidate
from popctl.dotfiles.repo import (
    MAIN_REF,
    REMOTE_MAIN_REF,
    DotfilesRepo,
    PathClassification,
    PathState,
    RefRelation,
    TransportOutcome,
    TransportResult,
    TreeEntry,
    TreeRead,
)
from popctl.models.history import HistoryActionType
from popctl.utils.shell import CommandResult
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


def _success_transport() -> TransportResult:
    return TransportResult(TransportOutcome.SUCCESS)


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

        def merge_base_relation(self) -> RefRelation:
            return RefRelation.EQUAL if self.advanced else RefRelation.BEHIND

        def classify_paths(self, _tracked: object) -> tuple[PathClassification, ...]:
            return (PathClassification(_PATH, PathState.REMOTE_MOD),)

        def changed_paths(self, old_ref: str, new_ref: str) -> frozenset[str]:
            assert (old_ref, new_ref) == (MAIN_REF, REMOTE_MAIN_REF)
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

        def merge_base_relation(self) -> RefRelation:
            return RefRelation.BEHIND

        def classify_paths(self, _tracked: object) -> tuple[PathClassification, ...]:
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
