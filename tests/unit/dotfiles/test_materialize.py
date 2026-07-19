from __future__ import annotations

from pathlib import Path

import pytest
from popctl.dotfiles.materialize import (
    HomeFileSnapshot,
    MaterializationError,
    MaterializationSource,
    execute_materialization_plan,
    preflight_materialization,
    render_materialization_plan,
)
from popctl.dotfiles.state import DotfilesRecoveryError, PlanOperation


def _source(path: str, content: bytes, mode: str = "100644") -> MaterializationSource:
    return MaterializationSource(path=path, oid="a" * 40, mode=mode, content=content)


def test_preflight_and_executor_create_nested_safe_file(tmp_path: Path) -> None:
    home = tmp_path / "home"
    state = tmp_path / "state"
    home.mkdir()
    source = _source(".config/tool/config", b"new\n", "100755")

    plan = preflight_materialization(
        operation=PlanOperation.APPLY,
        source_ref="refs/remotes/origin/main",
        source_tree_oid="b" * 40,
        sources=[source],
        base_files={},
        home=home,
    )

    assert render_materialization_plan(plan) == ("create\t~/.config/tool/config\ttarget is absent",)
    assert execute_materialization_plan(plan, sources=[source], home=home, state_dir=state) == (
        ".config/tool/config",
    )
    target = home / ".config/tool/config"
    assert target.read_bytes() == b"new\n"
    assert target.stat().st_mode & 0o777 == 0o755


def test_preflight_allows_clean_base_replacement_and_refuses_clobber(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    target = home / ".config/tool/config"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"base\n")
    source = _source(".config/tool/config", b"remote\n")
    base = {".config/tool/config": HomeFileSnapshot(b"base\n", "100644")}

    plan = preflight_materialization(
        operation=PlanOperation.INBOUND_SYNC,
        source_ref="refs/remotes/origin/main",
        source_tree_oid="b" * 40,
        sources=[source],
        base_files=base,
        home=home,
    )
    assert plan.entries[0].action == "replace"

    target.write_bytes(b"local\n")
    with pytest.raises(MaterializationError, match="differing"):
        preflight_materialization(
            operation=PlanOperation.INBOUND_SYNC,
            source_ref="refs/remotes/origin/main",
            source_tree_oid="b" * 40,
            sources=[source],
            base_files=base,
            home=home,
        )


def test_executor_recovers_the_replace_to_journal_crash_window(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / "home"
    state = tmp_path / "state"
    home.mkdir()
    target = home / ".config/tool/config"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"base\n")
    source = _source(".config/tool/config", b"remote\n")
    plan = preflight_materialization(
        operation=PlanOperation.APPLY,
        source_ref="refs/remotes/origin/main",
        source_tree_oid="b" * 40,
        sources=[source],
        base_files={".config/tool/config": HomeFileSnapshot(b"base\n", "100644")},
        home=home,
    )

    def crash_after_replace(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("injected journal crash")

    monkeypatch.setattr("popctl.dotfiles.materialize.record_completed_path", crash_after_replace)
    with pytest.raises(RuntimeError, match="journal crash"):
        execute_materialization_plan(plan, sources=[source], home=home, state_dir=state)
    assert target.read_bytes() == b"remote\n"
    assert not list(target.parent.glob("*.tmp"))

    monkeypatch.undo()
    assert execute_materialization_plan(plan, sources=[source], home=home, state_dir=state) == ()


def test_executor_refuses_post_preflight_mutation_and_symlink_parent(tmp_path: Path) -> None:
    home = tmp_path / "home"
    state = tmp_path / "state"
    home.mkdir()
    target = home / ".config/tool/config"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"base\n")
    source = _source(".config/tool/config", b"remote\n")
    plan = preflight_materialization(
        operation=PlanOperation.APPLY,
        source_ref="refs/remotes/origin/main",
        source_tree_oid="b" * 40,
        sources=[source],
        base_files={".config/tool/config": HomeFileSnapshot(b"base\n", "100644")},
        home=home,
    )
    target.write_bytes(b"changed\n")

    with pytest.raises(DotfilesRecoveryError, match="changed dotfiles target"):
        execute_materialization_plan(plan, sources=[source], home=home, state_dir=state)

    (home / ".config").rename(home / ".config-real")
    (home / ".config").symlink_to(".config-real")
    with pytest.raises(MaterializationError, match="Unsafe parent"):
        preflight_materialization(
            operation=PlanOperation.APPLY,
            source_ref="refs/remotes/origin/main",
            source_tree_oid="b" * 40,
            sources=[source],
            base_files={},
            home=home,
        )
