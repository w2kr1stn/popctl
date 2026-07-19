from __future__ import annotations

import os
import stat
from collections.abc import Callable, Collection
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Final

from popctl.dotfiles.secret_filter import MAX_CANDIDATE_BYTES as SECRET_FILTER_MAX_CANDIDATE_BYTES
from popctl.dotfiles.secret_filter import SecretVerdict, SecretVerdictKind, scan_dotfile

DISCOVERY_ROOTS: Final[tuple[str, ...]] = (
    ".bashrc",
    ".bash_profile",
    ".profile",
    ".zshrc",
    ".zprofile",
    ".vimrc",
    ".gitconfig",
    ".tmux.conf",
    ".wgetrc",
    ".curlrc",
    ".config",
)

# Discovery bounds prevent one broad configuration tree from turning a status
# check into an unbounded filesystem walk or content scan.
MAX_DISCOVERY_DEPTH: Final = 6
MAX_DISCOVERY_FILES: Final = 1_000
MAX_DIRECTORY_ENTRIES: Final = 1_000
MAX_CANDIDATE_BYTES: Final = SECRET_FILTER_MAX_CANDIDATE_BYTES


class BlockedCandidateKind(str, Enum):
    HARD_EXCLUSION = "hard-exclusion"
    ACTIONABLE = "actionable"


@dataclass(frozen=True, slots=True)
class Candidate:
    path: str
    group: str

    @property
    def display_path(self) -> str:
        return f"~/{self.path}"

    @property
    def home_relative_path(self) -> str:
        return self.path


@dataclass(frozen=True, slots=True)
class BlockedCandidate:
    path: str
    category: str
    kind: BlockedCandidateKind

    @property
    def display_path(self) -> str:
        return f"~/{self.path}"

    @property
    def actionable(self) -> bool:
        return self.kind is BlockedCandidateKind.ACTIONABLE

    @property
    def expected(self) -> bool:
        return self.kind is BlockedCandidateKind.HARD_EXCLUSION

    @property
    def home_relative_path(self) -> str:
        return self.path


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    candidates: tuple[Candidate, ...]
    blocked: tuple[BlockedCandidate, ...]

    @property
    def candidate_paths(self) -> tuple[str, ...]:
        return tuple(candidate.path for candidate in self.candidates)

    @property
    def blocked_candidates(self) -> tuple[BlockedCandidate, ...]:
        return self.blocked


def discover_dotfiles(
    home: Path | None = None,
    *,
    tracked_files: Collection[str] = (),
    ignored: Collection[str] = (),
    ambiguous_content_allowlist: Collection[str] = (),
) -> DiscoveryResult:
    home_path = (home or Path.home()).resolve(strict=False)
    tracked_paths = _canonical_input_paths(tracked_files)
    ignored_paths = _canonical_input_paths(ignored)
    candidates: list[Candidate] = []
    blocked: list[BlockedCandidate] = []
    discovered_files = 0
    file_limit_reached = False

    def block(path: str, category: str, kind: BlockedCandidateKind) -> None:
        blocked.append(BlockedCandidate(path=path, category=category, kind=kind))

    def inspect_file(path: Path, relative_path: str, group: str) -> None:
        nonlocal discovered_files, file_limit_reached
        if file_limit_reached:
            return
        if discovered_files >= MAX_DISCOVERY_FILES:
            block(relative_path, "discovery-file-limit", BlockedCandidateKind.ACTIONABLE)
            file_limit_reached = True
            return
        discovered_files += 1
        try:
            file_stat = path.lstat()
        except OSError:
            block(relative_path, "unreadable-file", BlockedCandidateKind.ACTIONABLE)
            return
        if not stat.S_ISREG(file_stat.st_mode):
            block(relative_path, "non-regular-file", BlockedCandidateKind.ACTIONABLE)
            return
        if file_stat.st_size > MAX_CANDIDATE_BYTES:
            block(relative_path, "oversize", BlockedCandidateKind.ACTIONABLE)
            return
        verdict = scan_dotfile(
            path,
            home=home_path,
            ambiguous_content_allowlist=ambiguous_content_allowlist,
        )
        if not verdict.allowed:
            _block_from_verdict(relative_path, verdict, block)
            return
        if relative_path not in tracked_paths and relative_path not in ignored_paths:
            candidates.append(Candidate(path=relative_path, group=group))

    def walk_directory(path: Path, relative_path: str, group: str, depth: int) -> None:
        if file_limit_reached:
            return
        if depth >= MAX_DISCOVERY_DEPTH:
            block(relative_path, "discovery-depth-limit", BlockedCandidateKind.ACTIONABLE)
            return
        try:
            entries = _bounded_directory_entries(path)
        except _DirectoryEntryLimitError:
            block(relative_path, "discovery-directory-entry-limit", BlockedCandidateKind.ACTIONABLE)
            return
        except OSError:
            block(relative_path, "unreadable-directory", BlockedCandidateKind.ACTIONABLE)
            return
        for entry in entries:
            if file_limit_reached:
                return
            child_relative_path = f"{relative_path}/{entry.name}"
            if not _is_canonical_home_relative_path(child_relative_path):
                block(child_relative_path, "non-canonical-path", BlockedCandidateKind.ACTIONABLE)
                continue
            child_path = path / entry.name
            try:
                if entry.is_dir(follow_symlinks=False):
                    walk_directory(child_path, child_relative_path, group, depth + 1)
                elif entry.is_file(follow_symlinks=False):
                    inspect_file(child_path, child_relative_path, group)
                else:
                    block(child_relative_path, "non-regular-file", BlockedCandidateKind.ACTIONABLE)
            except OSError:
                block(child_relative_path, "unreadable-file", BlockedCandidateKind.ACTIONABLE)

    for root in sorted(DISCOVERY_ROOTS):
        if file_limit_reached:
            break
        root_path = home_path / root
        try:
            root_stat = root_path.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            block(root, "unreadable-path", BlockedCandidateKind.ACTIONABLE)
            continue
        if stat.S_ISDIR(root_stat.st_mode):
            walk_directory(root_path, root, root, depth=1)
        elif stat.S_ISREG(root_stat.st_mode):
            inspect_file(root_path, root, root)
        else:
            block(root, "non-regular-file", BlockedCandidateKind.ACTIONABLE)

    return DiscoveryResult(
        candidates=tuple(sorted(candidates, key=lambda candidate: candidate.path)),
        blocked=tuple(sorted(blocked, key=lambda candidate: (candidate.path, candidate.category))),
    )


def _bounded_directory_entries(path: Path) -> list[os.DirEntry[str]]:
    entries: list[os.DirEntry[str]] = []
    with os.scandir(path) as iterator:
        for entry in iterator:
            entries.append(entry)
            if len(entries) > MAX_DIRECTORY_ENTRIES:
                raise _DirectoryEntryLimitError
    return sorted(entries, key=lambda entry: entry.name)


def _canonical_input_paths(paths: Collection[str]) -> set[str]:
    return {path for path in paths if _is_canonical_home_relative_path(path)}


def _is_canonical_home_relative_path(path: str) -> bool:
    if not path or "\\" in path:
        return False
    pure_path = PurePosixPath(path)
    if pure_path.is_absolute() or pure_path == PurePosixPath("."):
        return False
    return all(part not in {"", ".", "..", ".git"} for part in pure_path.parts)


def _block_from_verdict(
    path: str,
    verdict: SecretVerdict,
    block: Callable[[str, str, BlockedCandidateKind], None],
) -> None:
    category = verdict.category or _verdict_category(verdict.kind)
    kind = (
        BlockedCandidateKind.HARD_EXCLUSION
        if verdict.kind is SecretVerdictKind.DENIED_PATH
        else BlockedCandidateKind.ACTIONABLE
    )
    block(path, category, kind)


def _verdict_category(kind: SecretVerdictKind) -> str:
    return {
        SecretVerdictKind.DENIED_UNAMBIGUOUS_CONTENT: "unambiguous-content",
        SecretVerdictKind.DENIED_AMBIGUOUS_CONTENT: "ambiguous-content",
        SecretVerdictKind.DENIED_UNREADABLE: "unreadable-file",
        SecretVerdictKind.DENIED_BINARY: "binary-content",
        SecretVerdictKind.DENIED_OVERSIZE: "oversize",
    }.get(kind, "blocked")


class _DirectoryEntryLimitError(Exception):
    pass
