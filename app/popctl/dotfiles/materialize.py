from __future__ import annotations

import hashlib
import os
import secrets
import stat
from collections.abc import Collection, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from popctl.dotfiles.state import (
    DotfilesRecoveryError,
    MaterializationPlan,
    PlannedPath,
    PlanOperation,
    prepare_materialization_plan,
    record_completed_path,
)


class HomePathError(Exception):
    pass


class MaterializationError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class HomeFileSnapshot:
    content: bytes
    mode: str


@dataclass(frozen=True, slots=True)
class MaterializationSource:
    path: str
    oid: str
    mode: str
    content: bytes


def canonical_home_relative_path(path: str) -> str:
    if not path or "\\" in path:
        raise HomePathError("Dotfiles paths must be non-empty POSIX relative paths")
    pure_path = PurePosixPath(path)
    if pure_path.is_absolute() or pure_path == PurePosixPath("."):
        raise HomePathError(f"Dotfiles path is not home-relative: {path}")
    if any(part in {"", ".", "..", ".git"} for part in pure_path.parts):
        raise HomePathError(f"Unsafe dotfiles path: {path}")
    canonical = pure_path.as_posix()
    if canonical != path:
        raise HomePathError(f"Non-canonical dotfiles path: {path}")
    return canonical


@contextmanager
def open_home_parent(
    home: Path,
    path: str,
    *,
    create: bool = False,
) -> Iterator[tuple[int, str]]:
    canonical = canonical_home_relative_path(path)
    parts = PurePosixPath(canonical).parts
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        descriptor = os.open(home, flags)
    except OSError as e:
        raise HomePathError(f"Cannot open home directory {home}: {e}") from e
    try:
        home_stat = os.fstat(descriptor)
        if not stat.S_ISDIR(home_stat.st_mode):
            raise HomePathError(f"Home path is not a directory: {home}")
        for component in parts[:-1]:
            try:
                next_descriptor = os.open(component, flags, dir_fd=descriptor)
            except FileNotFoundError:
                if not create:
                    raise HomePathError(f"Missing parent directory for {canonical}") from None
                try:
                    os.mkdir(component, mode=0o700, dir_fd=descriptor)
                    next_descriptor = os.open(component, flags, dir_fd=descriptor)
                except OSError as e:
                    raise HomePathError(f"Cannot create parent for {canonical}: {e}") from e
            except OSError as e:
                raise HomePathError(f"Unsafe parent for {canonical}: {e}") from e
            os.close(descriptor)
            descriptor = next_descriptor
        yield descriptor, parts[-1]
    finally:
        os.close(descriptor)


def read_home_regular_file(home: Path, path: str) -> HomeFileSnapshot:
    canonical = canonical_home_relative_path(path)
    flags = os.O_RDONLY | os.O_NOFOLLOW
    with open_home_parent(home, canonical) as (parent_descriptor, name):
        try:
            descriptor = os.open(name, flags, dir_fd=parent_descriptor)
        except FileNotFoundError:
            raise
        except OSError as e:
            raise HomePathError(f"Cannot open source {canonical}: {e}") from e
        try:
            source_stat = os.fstat(descriptor)
            if not stat.S_ISREG(source_stat.st_mode):
                raise HomePathError(f"Source is not a regular file: {canonical}")
            chunks: list[bytes] = []
            while True:
                chunk = os.read(descriptor, 1_048_576)
                if not chunk:
                    break
                chunks.append(chunk)
        finally:
            os.close(descriptor)
    return HomeFileSnapshot(
        content=b"".join(chunks),
        mode="100755" if source_stat.st_mode & stat.S_IXUSR else "100644",
    )


def preflight_materialization(
    *,
    operation: PlanOperation,
    source_ref: str,
    source_tree_oid: str,
    sources: Collection[MaterializationSource],
    base_files: Mapping[str, HomeFileSnapshot],
    home: Path,
) -> MaterializationPlan:
    entries: list[PlannedPath] = []
    seen_paths: set[str] = set()
    for source in sorted(sources, key=lambda item: item.path):
        path = canonical_home_relative_path(source.path)
        if path in seen_paths:
            raise MaterializationError(f"Duplicate materialization source path: {path}")
        seen_paths.add(path)
        if source.mode not in {"100644", "100755"}:
            raise MaterializationError(
                f"Unsupported materialization mode for {path}: {source.mode}"
            )
        target = _read_target_snapshot(home, path)
        source_snapshot = HomeFileSnapshot(source.content, source.mode)
        if target is None:
            action = "create"
            expected_fingerprint = None
        elif target == source_snapshot:
            action = "noop"
            expected_fingerprint = _fingerprint(target)
        elif base_files.get(path) == target:
            action = "replace"
            expected_fingerprint = _fingerprint(target)
        else:
            raise MaterializationError(f"Refusing to overwrite differing dotfiles target: {path}")
        entries.append(
            PlannedPath(
                path=path,
                oid=source.oid,
                mode=source.mode,
                action=action,
                expected_target_fingerprint=expected_fingerprint,
            )
        )
    return MaterializationPlan(
        operation=operation,
        source_ref=source_ref,
        source_tree_oid=source_tree_oid,
        entries=tuple(entries),
    )


def render_materialization_plan(plan: MaterializationPlan) -> tuple[str, ...]:
    reasons = {
        "create": "target is absent",
        "replace": "target matches the tracked base",
        "noop": "target already matches source",
    }
    return tuple(
        f"{entry.action}\t~/{entry.path}\t{reasons.get(entry.action, entry.action)}"
        for entry in plan.entries
    )


def execute_materialization_plan(
    plan: MaterializationPlan,
    *,
    sources: Collection[MaterializationSource],
    home: Path,
    state_dir: Path,
) -> tuple[str, ...]:
    source_by_path = {source.path: source for source in sources}
    if set(source_by_path) != {entry.path for entry in plan.entries}:
        raise MaterializationError("Materialization sources do not match the immutable plan")
    prepare_materialization_plan(plan, state_dir)
    changed: list[str] = []
    for entry in plan.entries:
        source = source_by_path[entry.path]
        desired = HomeFileSnapshot(source.content, source.mode)
        current = _read_target_snapshot(home, entry.path)
        if current == desired:
            record_completed_path(plan, entry.path, state_dir)
            continue
        current_fingerprint = _fingerprint(current) if current is not None else None
        if current_fingerprint != entry.expected_target_fingerprint:
            raise DotfilesRecoveryError(
                f"Refusing to overwrite changed dotfiles target {entry.path}; "
                "recover the target manually, then retry."
            )
        _replace_target(home, entry, source.content)
        record_completed_path(plan, entry.path, state_dir)
        changed.append(entry.path)
    return tuple(changed)


def _read_target_snapshot(home: Path, path: str) -> HomeFileSnapshot | None:
    try:
        with open_home_parent(home, path) as (parent_descriptor, name):
            try:
                descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_descriptor)
            except FileNotFoundError:
                return None
            except OSError as e:
                raise HomePathError(f"Unsafe target for {path}: {e}") from e
            try:
                target_stat = os.fstat(descriptor)
                if not stat.S_ISREG(target_stat.st_mode):
                    raise HomePathError(f"Target is not a regular file: {path}")
                chunks: list[bytes] = []
                while True:
                    chunk = os.read(descriptor, 1_048_576)
                    if not chunk:
                        break
                    chunks.append(chunk)
            finally:
                os.close(descriptor)
    except HomePathError as e:
        if str(e).startswith("Missing parent directory"):
            return None
        raise MaterializationError(str(e)) from e
    return HomeFileSnapshot(
        content=b"".join(chunks),
        mode="100755" if target_stat.st_mode & stat.S_IXUSR else "100644",
    )


def _fingerprint(snapshot: HomeFileSnapshot) -> str:
    digest = hashlib.sha256()
    digest.update(snapshot.mode.encode("ascii"))
    digest.update(b"\0")
    digest.update(snapshot.content)
    return digest.hexdigest()


def _replace_target(home: Path, entry: PlannedPath, content: bytes) -> None:
    temporary_name = f".{PurePosixPath(entry.path).name}.popctl-{secrets.token_hex(12)}.tmp"
    descriptor: int | None = None
    with open_home_parent(home, entry.path, create=True) as (parent_descriptor, name):
        try:
            descriptor = os.open(
                temporary_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent_descriptor,
            )
            _write_all(descriptor, content)
            os.fchmod(descriptor, int(entry.mode[-3:], 8))
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            current = _read_target_in_parent(parent_descriptor, name, entry.path)
            fingerprint = _fingerprint(current) if current is not None else None
            if fingerprint != entry.expected_target_fingerprint:
                raise DotfilesRecoveryError(
                    f"Refusing to overwrite changed dotfiles target {entry.path}; "
                    "recover the target manually, then retry."
                )
            os.replace(
                temporary_name,
                name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
            )
            os.fsync(parent_descriptor)
        except OSError as e:
            raise MaterializationError(f"Could not materialize {entry.path}: {e}") from e
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                os.unlink(temporary_name, dir_fd=parent_descriptor)
            except FileNotFoundError:
                pass
            except OSError:
                pass


def _read_target_in_parent(parent_descriptor: int, name: str, path: str) -> HomeFileSnapshot | None:
    try:
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_descriptor)
    except FileNotFoundError:
        return None
    except OSError as e:
        raise MaterializationError(f"Unsafe target for {path}: {e}") from e
    try:
        target_stat = os.fstat(descriptor)
        if not stat.S_ISREG(target_stat.st_mode):
            raise MaterializationError(f"Target is not a regular file: {path}")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1_048_576)
            if not chunk:
                break
            chunks.append(chunk)
    finally:
        os.close(descriptor)
    return HomeFileSnapshot(
        content=b"".join(chunks),
        mode="100755" if target_stat.st_mode & stat.S_IXUSR else "100644",
    )


def _write_all(descriptor: int, content: bytes) -> None:
    view = memoryview(content)
    while view:
        written = os.write(descriptor, view)
        view = view[written:]
