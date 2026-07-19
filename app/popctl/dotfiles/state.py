import fcntl
import json
import os
import shutil
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Any, cast

from popctl.core.paths import get_state_dir

PLAN_SCHEMA = 1
JOURNAL_SCHEMA = 1
INIT_JOURNAL_SCHEMA = 1


class DotfilesStateError(Exception):
    pass


class DotfilesLockError(DotfilesStateError):
    pass


class DotfilesPlanMismatchError(DotfilesStateError):
    pass


class DotfilesRecoveryError(DotfilesStateError):
    pass


class PlanOperation(str, Enum):
    APPLY = "apply"
    INBOUND_SYNC = "inbound-sync"


class InitPhase(str, Enum):
    PREPARED = "prepared"
    STORE_PROMOTED = "store-promoted"
    CONFIG_WRITTEN = "config-written"


@dataclass(frozen=True, slots=True)
class PlannedPath:
    path: str
    oid: str
    mode: str
    action: str
    expected_target_fingerprint: str | None

    def __post_init__(self) -> None:
        if not self.path or not self.oid or not self.mode or not self.action:
            msg = "Plan entries require path, OID, mode, and action"
            raise ValueError(msg)

    def to_dict(self) -> dict[str, str | None]:
        return {
            "path": self.path,
            "oid": self.oid,
            "mode": self.mode,
            "action": self.action,
            "expected_target_fingerprint": self.expected_target_fingerprint,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PlannedPath:
        fingerprint = data.get("expected_target_fingerprint")
        if fingerprint is not None and not isinstance(fingerprint, str):
            msg = "Expected target fingerprint must be a string or null"
            raise DotfilesStateError(msg)
        return cls(
            path=_required_string(data, "path"),
            oid=_required_string(data, "oid"),
            mode=_required_string(data, "mode"),
            action=_required_string(data, "action"),
            expected_target_fingerprint=fingerprint,
        )


@dataclass(frozen=True, slots=True)
class MaterializationPlan:
    operation: PlanOperation
    source_ref: str
    source_tree_oid: str
    entries: tuple[PlannedPath, ...]
    schema: int = PLAN_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != PLAN_SCHEMA:
            msg = f"Unsupported plan schema: {self.schema}"
            raise ValueError(msg)
        if not self.source_ref or not self.source_tree_oid:
            msg = "Plans require source ref and tree OID"
            raise ValueError(msg)
        paths = [entry.path for entry in self.entries]
        if len(paths) != len(set(paths)):
            msg = "Plan entries must have unique paths"
            raise ValueError(msg)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "operation": self.operation.value,
            "source_ref": self.source_ref,
            "source_tree_oid": self.source_tree_oid,
            "entries": [entry.to_dict() for entry in self.entries],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MaterializationPlan:
        raw_entries: object = data.get("entries")
        if not isinstance(raw_entries, list):
            msg = "Plan entries must be a list"
            raise DotfilesStateError(msg)
        entries = cast("list[object]", raw_entries)
        try:
            operation = PlanOperation(_required_string(data, "operation"))
        except ValueError as e:
            raise DotfilesStateError("Unknown plan operation") from e
        schema = data.get("schema")
        if not isinstance(schema, int):
            msg = "Plan schema must be an integer"
            raise DotfilesStateError(msg)
        try:
            return cls(
                operation=operation,
                source_ref=_required_string(data, "source_ref"),
                source_tree_oid=_required_string(data, "source_tree_oid"),
                entries=tuple(_planned_path_from_value(value) for value in entries),
                schema=schema,
            )
        except ValueError as e:
            raise DotfilesStateError(str(e)) from e


@dataclass(frozen=True, slots=True)
class CompletedPathsJournal:
    operation: PlanOperation
    source_ref: str
    source_tree_oid: str
    completed_paths: tuple[str, ...] = ()
    schema: int = JOURNAL_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != JOURNAL_SCHEMA:
            msg = f"Unsupported completed-paths journal schema: {self.schema}"
            raise ValueError(msg)
        if not self.source_ref or not self.source_tree_oid:
            msg = "Completed-paths journals require source ref and tree OID"
            raise ValueError(msg)
        if any(not path for path in self.completed_paths):
            msg = "Completed paths cannot be empty"
            raise ValueError(msg)
        if len(self.completed_paths) != len(set(self.completed_paths)):
            msg = "Completed paths must be unique"
            raise ValueError(msg)

    @classmethod
    def for_plan(cls, plan: MaterializationPlan) -> CompletedPathsJournal:
        return cls(
            operation=plan.operation,
            source_ref=plan.source_ref,
            source_tree_oid=plan.source_tree_oid,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "operation": self.operation.value,
            "source_ref": self.source_ref,
            "source_tree_oid": self.source_tree_oid,
            "completed_paths": list(self.completed_paths),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CompletedPathsJournal:
        raw_paths: object = data.get("completed_paths")
        if not isinstance(raw_paths, list):
            msg = "Completed paths must be a list of strings"
            raise DotfilesStateError(msg)
        paths = cast("list[object]", raw_paths)
        completed_paths: list[str] = []
        for raw_path in paths:
            if not isinstance(raw_path, str):
                msg = "Completed paths must be a list of strings"
                raise DotfilesStateError(msg)
            completed_paths.append(raw_path)
        schema = data.get("schema")
        if not isinstance(schema, int):
            msg = "Completed-paths journal schema must be an integer"
            raise DotfilesStateError(msg)
        try:
            return cls(
                operation=PlanOperation(_required_string(data, "operation")),
                source_ref=_required_string(data, "source_ref"),
                source_tree_oid=_required_string(data, "source_tree_oid"),
                completed_paths=tuple(completed_paths),
                schema=schema,
            )
        except ValueError as e:
            raise DotfilesStateError(str(e)) from e


@dataclass(frozen=True, slots=True)
class InitFinalizationJournal:
    temporary_store: Path
    final_store: Path
    config_path: Path
    phase: InitPhase
    created_remote: str | None = None
    schema: int = INIT_JOURNAL_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != INIT_JOURNAL_SCHEMA:
            msg = f"Unsupported init journal schema: {self.schema}"
            raise ValueError(msg)
        if self.temporary_store == self.final_store:
            msg = "Temporary and final stores must differ"
            raise ValueError(msg)

    def with_phase(self, phase: InitPhase) -> InitFinalizationJournal:
        return InitFinalizationJournal(
            temporary_store=self.temporary_store,
            final_store=self.final_store,
            config_path=self.config_path,
            phase=phase,
            created_remote=self.created_remote,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "temporary_store": str(self.temporary_store),
            "final_store": str(self.final_store),
            "config_path": str(self.config_path),
            "phase": self.phase.value,
            "created_remote": self.created_remote,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InitFinalizationJournal:
        schema = data.get("schema")
        created_remote = data.get("created_remote")
        if not isinstance(schema, int):
            msg = "Init journal schema must be an integer"
            raise DotfilesStateError(msg)
        if created_remote is not None and not isinstance(created_remote, str):
            msg = "Created remote must be a string or null"
            raise DotfilesStateError(msg)
        try:
            return cls(
                temporary_store=Path(_required_string(data, "temporary_store")),
                final_store=Path(_required_string(data, "final_store")),
                config_path=Path(_required_string(data, "config_path")),
                phase=InitPhase(_required_string(data, "phase")),
                created_remote=created_remote,
                schema=schema,
            )
        except ValueError as e:
            raise DotfilesStateError(str(e)) from e


@dataclass(frozen=True, slots=True)
class InitRecovery:
    reusable_remote: str | None
    removed_stores: tuple[Path, ...]


def get_dotfiles_state_dir() -> Path:
    return get_state_dir() / "dotfiles"


def get_dotfiles_lock_path(state_dir: Path | None = None) -> Path:
    return (state_dir or get_dotfiles_state_dir()) / "lock"


def get_plan_path(operation: PlanOperation, state_dir: Path | None = None) -> Path:
    return (state_dir or get_dotfiles_state_dir()) / f"{operation.value}-plan.json"


def get_completed_paths_journal_path(
    operation: PlanOperation, state_dir: Path | None = None
) -> Path:
    return (state_dir or get_dotfiles_state_dir()) / f"{operation.value}-completed-paths.json"


def get_init_finalization_journal_path(state_dir: Path | None = None) -> Path:
    return (state_dir or get_dotfiles_state_dir()) / "init-finalization.json"


@contextmanager
def dotfiles_lock(state_dir: Path | None = None) -> Iterator[None]:
    lock_path = get_dotfiles_lock_path(state_dir)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as e:
            raise DotfilesLockError("Another dotfiles operation is already running") from e
        try:
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def save_materialization_plan(plan: MaterializationPlan, state_dir: Path | None = None) -> Path:
    path = get_plan_path(plan.operation, state_dir)
    _atomic_write_json(path, plan.to_dict())
    return path


def load_materialization_plan(
    operation: PlanOperation, state_dir: Path | None = None
) -> MaterializationPlan:
    path = get_plan_path(operation, state_dir)
    plan = MaterializationPlan.from_dict(_load_json(path, "plan"))
    if plan.operation is not operation:
        msg = f"Plan at {path} is for {plan.operation.value}, not {operation.value}"
        raise DotfilesStateError(msg)
    return plan


def save_completed_paths_journal(
    journal: CompletedPathsJournal, state_dir: Path | None = None
) -> Path:
    path = get_completed_paths_journal_path(journal.operation, state_dir)
    _atomic_write_json(path, journal.to_dict())
    return path


def load_completed_paths_journal(
    operation: PlanOperation, state_dir: Path | None = None
) -> CompletedPathsJournal:
    path = get_completed_paths_journal_path(operation, state_dir)
    journal = CompletedPathsJournal.from_dict(_load_json(path, "completed-paths journal"))
    if journal.operation is not operation:
        msg = f"Completed-paths journal at {path} is for {journal.operation.value}"
        raise DotfilesStateError(msg)
    return journal


def prepare_materialization_plan(plan: MaterializationPlan, state_dir: Path | None = None) -> None:
    plan_path = get_plan_path(plan.operation, state_dir)
    journal_path = get_completed_paths_journal_path(plan.operation, state_dir)
    if plan_path.exists():
        existing_plan = load_materialization_plan(plan.operation, state_dir)
        if existing_plan != plan:
            msg = "Refusing to replace an incomplete dotfiles materialization plan"
            raise DotfilesPlanMismatchError(msg)
    elif journal_path.exists():
        msg = "Completed-paths journal exists without its immutable plan"
        raise DotfilesStateError(msg)
    else:
        save_materialization_plan(plan, state_dir)
    if journal_path.exists():
        _verify_journal_for_plan(load_completed_paths_journal(plan.operation, state_dir), plan)
    else:
        save_completed_paths_journal(CompletedPathsJournal.for_plan(plan), state_dir)


def record_completed_path(
    plan: MaterializationPlan,
    path: str,
    state_dir: Path | None = None,
) -> None:
    if path not in {entry.path for entry in plan.entries}:
        msg = f"Path is not in the immutable {plan.operation.value} plan: {path}"
        raise DotfilesStateError(msg)
    journal = load_completed_paths_journal(plan.operation, state_dir)
    _verify_journal_for_plan(journal, plan)
    if path in journal.completed_paths:
        return
    save_completed_paths_journal(
        CompletedPathsJournal(
            operation=journal.operation,
            source_ref=journal.source_ref,
            source_tree_oid=journal.source_tree_oid,
            completed_paths=(*journal.completed_paths, path),
        ),
        state_dir,
    )


def clear_materialization_state(
    operation: PlanOperation,
    state_dir: Path | None = None,
) -> None:
    for path in (
        get_completed_paths_journal_path(operation, state_dir),
        get_plan_path(operation, state_dir),
    ):
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        except OSError as e:
            raise DotfilesStateError(
                f"Failed to clear completed {operation.value} materialization state: {e}"
            ) from e


def complete_materialization_state(
    plan: MaterializationPlan,
    state_dir: Path | None = None,
) -> None:
    plan_path = get_plan_path(plan.operation, state_dir)
    if plan_path.exists() and load_materialization_plan(plan.operation, state_dir) != plan:
        raise DotfilesPlanMismatchError("Materialization state does not match the validated source")
    complete_materialization_state_for_source(
        plan.operation,
        source_ref=plan.source_ref,
        source_tree_oid=plan.source_tree_oid,
        state_dir=state_dir,
    )


def complete_materialization_state_for_source(
    operation: PlanOperation,
    *,
    source_ref: str,
    source_tree_oid: str,
    state_dir: Path | None = None,
) -> None:
    plan_path = get_plan_path(operation, state_dir)
    journal_path = get_completed_paths_journal_path(operation, state_dir)
    if not plan_path.exists() and not journal_path.exists():
        return
    if not plan_path.exists() or not journal_path.exists():
        raise DotfilesStateError("Dotfiles materialization state is incomplete")
    existing_plan = load_materialization_plan(operation, state_dir)
    if existing_plan.source_ref != source_ref or existing_plan.source_tree_oid != source_tree_oid:
        raise DotfilesPlanMismatchError("Materialization state does not match the validated source")
    journal = load_completed_paths_journal(operation, state_dir)
    _verify_journal_for_plan(journal, existing_plan)
    if set(journal.completed_paths) != {entry.path for entry in existing_plan.entries}:
        raise DotfilesRecoveryError(
            "Dotfiles materialization is incomplete; retry against the original source."
        )
    clear_materialization_state(operation, state_dir)


def resume_completed_path(
    plan: MaterializationPlan,
    entry: PlannedPath,
    target_matches_planned_result: Callable[[PlannedPath], bool],
    state_dir: Path | None = None,
) -> bool:
    if entry not in plan.entries:
        msg = f"Path is not in the immutable {plan.operation.value} plan: {entry.path}"
        raise DotfilesStateError(msg)
    journal = load_completed_paths_journal(plan.operation, state_dir)
    _verify_journal_for_plan(journal, plan)
    if entry.path in journal.completed_paths:
        return True
    if target_matches_planned_result(entry):
        record_completed_path(plan, entry.path, state_dir)
        return True
    msg = (
        f"Refusing to overwrite unjournaled dotfiles target {entry.path}; "
        "recover the target manually, then retry."
    )
    raise DotfilesRecoveryError(msg)


def save_init_finalization_journal(
    journal: InitFinalizationJournal, state_dir: Path | None = None
) -> Path:
    path = get_init_finalization_journal_path(state_dir)
    _atomic_write_json(path, journal.to_dict())
    return path


def load_init_finalization_journal(state_dir: Path | None = None) -> InitFinalizationJournal | None:
    path = get_init_finalization_journal_path(state_dir)
    if not path.exists():
        return None
    return InitFinalizationJournal.from_dict(_load_json(path, "init finalization journal"))


def clear_init_finalization_journal(state_dir: Path | None = None) -> None:
    with suppress(FileNotFoundError):
        get_init_finalization_journal_path(state_dir).unlink()


def recover_init_finalization(state_dir: Path | None = None) -> InitRecovery | None:
    journal = load_init_finalization_journal(state_dir)
    if journal is None:
        return None
    removed_stores: list[Path] = []
    if journal.phase is InitPhase.PREPARED:
        _remove_owned_store(journal.temporary_store, removed_stores)
    elif journal.phase is InitPhase.STORE_PROMOTED:
        _remove_owned_store(journal.temporary_store, removed_stores)
        _remove_owned_store(journal.final_store, removed_stores)
    clear_init_finalization_journal(state_dir)
    return InitRecovery(
        reusable_remote=journal.created_remote,
        removed_stores=tuple(removed_stores),
    )


def _required_string(data: dict[str, Any], field: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value:
        msg = f"{field} must be a non-empty string"
        raise DotfilesStateError(msg)
    return value


def _planned_path_from_value(value: object) -> PlannedPath:
    if not isinstance(value, dict):
        msg = "Plan entries must be objects"
        raise DotfilesStateError(msg)
    return PlannedPath.from_dict(cast("dict[str, Any]", value))


def _verify_journal_for_plan(journal: CompletedPathsJournal, plan: MaterializationPlan) -> None:
    if (
        journal.operation is not plan.operation
        or journal.source_ref != plan.source_ref
        or journal.source_tree_oid != plan.source_tree_oid
    ):
        msg = "Completed-paths journal does not match the immutable materialization plan"
        raise DotfilesPlanMismatchError(msg)
    plan_paths = {entry.path for entry in plan.entries}
    if not set(journal.completed_paths).issubset(plan_paths):
        msg = "Completed-paths journal contains a path outside the immutable plan"
        raise DotfilesPlanMismatchError(msg)


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            delete=False,
            suffix=".tmp",
        ) as f:
            temporary_path = Path(f.name)
            json.dump(data, f, separators=(",", ":"))
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary_path, path)
    except OSError as e:
        if temporary_path is not None:
            with suppress(OSError):
                temporary_path.unlink(missing_ok=True)
        raise DotfilesStateError(f"Failed to persist {path}: {e}") from e


def _load_json(path: Path, artifact_name: str) -> dict[str, Any]:
    try:
        with path.open(encoding="utf-8") as f:
            data: object = json.load(f)
    except FileNotFoundError as e:
        raise DotfilesStateError(f"Missing {artifact_name}: {path}") from e
    except (OSError, json.JSONDecodeError) as e:
        raise DotfilesStateError(f"Invalid {artifact_name} {path}: {e}") from e
    if not isinstance(data, dict):
        msg = f"Invalid {artifact_name} {path}: expected an object"
        raise DotfilesStateError(msg)
    return cast("dict[str, Any]", data)


def _remove_owned_store(path: Path, removed_stores: list[Path]) -> None:
    if not path.exists() and not path.is_symlink():
        return
    try:
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
    except OSError as e:
        raise DotfilesStateError(f"Failed to remove unfinished dotfiles store {path}: {e}") from e
    removed_stores.append(path)
