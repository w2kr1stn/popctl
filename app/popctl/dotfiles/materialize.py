from __future__ import annotations

import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath


class HomePathError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class HomeFileSnapshot:
    content: bytes
    mode: str


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
