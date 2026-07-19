import json
from dataclasses import replace
from pathlib import Path

import pytest
from popctl.dotfiles.state import (
    CompletedPathsJournal,
    DotfilesLockError,
    DotfilesPlanMismatchError,
    DotfilesRecoveryError,
    InitFinalizationJournal,
    InitPhase,
    MaterializationPlan,
    PlannedPath,
    PlanOperation,
    clear_materialization_state,
    complete_materialization_state_for_source,
    dotfiles_lock,
    get_completed_paths_journal_path,
    get_plan_path,
    load_completed_paths_journal,
    load_materialization_plan,
    prepare_materialization_plan,
    record_completed_path,
    recover_init_finalization,
    resume_completed_path,
    save_init_finalization_journal,
)


def _plan(operation: PlanOperation) -> MaterializationPlan:
    return MaterializationPlan(
        operation=operation,
        source_ref="refs/remotes/origin/main",
        source_tree_oid="a" * 40,
        entries=(
            PlannedPath(
                path=".config/one/config.toml",
                oid="b" * 40,
                mode="100644",
                action="replace",
                expected_target_fingerprint="c" * 64,
            ),
            PlannedPath(
                path=".config/two/config.toml",
                oid="d" * 40,
                mode="100755",
                action="create",
                expected_target_fingerprint=None,
            ),
        ),
    )


@pytest.mark.parametrize("operation", [PlanOperation.APPLY, PlanOperation.INBOUND_SYNC])
class TestMaterializationState:
    def test_persists_immutable_plan_and_empty_journal(
        self, tmp_path: Path, operation: PlanOperation
    ) -> None:
        plan = _plan(operation)

        prepare_materialization_plan(plan, tmp_path)

        assert load_materialization_plan(operation, tmp_path) == plan
        assert load_completed_paths_journal(operation, tmp_path) == CompletedPathsJournal.for_plan(
            plan
        )
        persisted_plan = json.loads(get_plan_path(operation, tmp_path).read_text(encoding="utf-8"))
        assert persisted_plan["schema"] == 1
        assert persisted_plan["source_ref"] == plan.source_ref
        assert persisted_plan["source_tree_oid"] == plan.source_tree_oid
        assert [entry["path"] for entry in persisted_plan["entries"]] == [
            entry.path for entry in plan.entries
        ]
        assert get_completed_paths_journal_path(operation, tmp_path).exists()

    def test_retry_journals_equal_target_and_refuses_differing_target(
        self, tmp_path: Path, operation: PlanOperation
    ) -> None:
        plan = _plan(operation)
        prepare_materialization_plan(plan, tmp_path)

        assert resume_completed_path(plan, plan.entries[0], lambda _entry: True, tmp_path)
        assert resume_completed_path(plan, plan.entries[0], lambda _entry: False, tmp_path)
        assert load_completed_paths_journal(operation, tmp_path).completed_paths == (
            plan.entries[0].path,
        )

        with pytest.raises(DotfilesRecoveryError, match="recover the target manually"):
            resume_completed_path(plan, plan.entries[1], lambda _entry: False, tmp_path)

        assert load_completed_paths_journal(operation, tmp_path).completed_paths == (
            plan.entries[0].path,
        )

    def test_refuses_a_different_retry_plan(self, tmp_path: Path, operation: PlanOperation) -> None:
        plan = _plan(operation)
        prepare_materialization_plan(plan, tmp_path)

        with pytest.raises(DotfilesPlanMismatchError, match="incomplete dotfiles"):
            prepare_materialization_plan(replace(plan, source_tree_oid="e" * 40), tmp_path)

    def test_clears_completed_state_after_ref_advance(
        self, tmp_path: Path, operation: PlanOperation
    ) -> None:
        prepare_materialization_plan(_plan(operation), tmp_path)

        clear_materialization_state(operation, tmp_path)

        assert not get_plan_path(operation, tmp_path).exists()
        assert not get_completed_paths_journal_path(operation, tmp_path).exists()

    def test_clears_a_completed_state_by_validated_source(
        self, tmp_path: Path, operation: PlanOperation
    ) -> None:
        plan = _plan(operation)
        prepare_materialization_plan(plan, tmp_path)
        for entry in plan.entries:
            record_completed_path(plan, entry.path, tmp_path)

        complete_materialization_state_for_source(
            operation,
            source_ref=plan.source_ref,
            source_tree_oid=plan.source_tree_oid,
            state_dir=tmp_path,
        )

        assert not get_plan_path(operation, tmp_path).exists()
        assert not get_completed_paths_journal_path(operation, tmp_path).exists()

    def test_recovers_a_plan_only_retirement_by_validated_source(
        self, tmp_path: Path, operation: PlanOperation
    ) -> None:
        plan = _plan(operation)
        prepare_materialization_plan(plan, tmp_path)
        for entry in plan.entries:
            record_completed_path(plan, entry.path, tmp_path)
        get_completed_paths_journal_path(operation, tmp_path).unlink()

        complete_materialization_state_for_source(
            operation,
            source_ref=plan.source_ref,
            source_tree_oid=plan.source_tree_oid,
            state_dir=tmp_path,
            recover_plan_only=True,
        )

        assert not get_plan_path(operation, tmp_path).exists()
        assert not get_completed_paths_journal_path(operation, tmp_path).exists()


class TestInitFinalizationRecovery:
    def test_no_journal_needs_no_recovery(self, tmp_path: Path) -> None:
        assert recover_init_finalization(tmp_path / "state") is None

    def test_recovers_store_promoted_before_config_write(self, tmp_path: Path) -> None:
        temporary_store = tmp_path / ".dotfiles.git.tmp"
        final_store = tmp_path / "dotfiles.git"
        temporary_store.mkdir()
        final_store.mkdir()
        config_path = tmp_path / "dotfiles.toml"
        config_path.write_text("remote_url = ''\n", encoding="utf-8")
        journal = InitFinalizationJournal(
            temporary_store=temporary_store,
            final_store=final_store,
            config_path=config_path,
            phase=InitPhase.STORE_PROMOTED,
            created_remote="git@github.com:example/dotfiles.git",
        )
        save_init_finalization_journal(journal, tmp_path / "state")

        recovery = recover_init_finalization(tmp_path / "state")

        assert recovery is not None
        assert recovery.reusable_remote == journal.created_remote
        assert set(recovery.removed_stores) == {temporary_store, final_store}
        assert not temporary_store.exists()
        assert not final_store.exists()
        assert recovery.removed_configs == (config_path,)
        assert not config_path.exists()

    def test_recovers_store_promotion_before_its_journal_phase_is_written(
        self, tmp_path: Path
    ) -> None:
        final_store = tmp_path / "dotfiles.git"
        final_store.mkdir()
        journal = InitFinalizationJournal(
            temporary_store=tmp_path / ".dotfiles.git.tmp",
            final_store=final_store,
            config_path=tmp_path / "dotfiles.toml",
            phase=InitPhase.PREPARED,
        )
        save_init_finalization_journal(journal, tmp_path / "state")

        recovery = recover_init_finalization(tmp_path / "state")

        assert recovery is not None
        assert recovery.removed_stores == (final_store,)
        assert recovery.removed_configs == ()
        assert not final_store.exists()

    def test_keeps_finalized_store_and_config_after_config_write(self, tmp_path: Path) -> None:
        final_store = tmp_path / "dotfiles.git"
        final_store.mkdir()
        config_path = tmp_path / "dotfiles.toml"
        config_path.write_text("remote_url = ''\n", encoding="utf-8")
        journal = InitFinalizationJournal(
            temporary_store=tmp_path / ".dotfiles.git.tmp",
            final_store=final_store,
            config_path=config_path,
            phase=InitPhase.CONFIG_WRITTEN,
        )
        save_init_finalization_journal(journal, tmp_path / "state")

        recovery = recover_init_finalization(tmp_path / "state")

        assert recovery is not None
        assert recovery.removed_stores == ()
        assert recovery.removed_configs == ()
        assert final_store.exists()
        assert config_path.exists()


class TestDotfilesLock:
    def test_contention_has_no_plan_side_effect(self, tmp_path: Path) -> None:
        state_dir = tmp_path / "state"
        with dotfiles_lock(state_dir):
            with pytest.raises(DotfilesLockError), dotfiles_lock(state_dir):
                pytest.fail("contended lock was acquired")
            assert not get_plan_path(PlanOperation.APPLY, state_dir).exists()

    def test_releases_lock_after_exception(self, tmp_path: Path) -> None:
        state_dir = tmp_path / "state"

        with pytest.raises(RuntimeError), dotfiles_lock(state_dir):
            raise RuntimeError("interrupted")

        with dotfiles_lock(state_dir):
            assert True
