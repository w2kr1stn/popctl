"""Filesystem domain models for orphan detection and scanning.

This module defines the core data structures for representing
filesystem entries discovered during system scanning, including
path types, statuses, and orphan classification reasons.
"""

from dataclasses import dataclass
from enum import Enum


class PathType(str, Enum):
    """Type of filesystem entry.

    Attributes:
        DIRECTORY: Regular directory.
        FILE: Regular file.
        SYMLINK: Symbolic link with a valid target.
        DEAD_SYMLINK: Symbolic link whose target does not exist.
    """

    DIRECTORY = "directory"
    FILE = "file"
    SYMLINK = "symlink"
    DEAD_SYMLINK = "dead_symlink"


class PathStatus(str, Enum):
    """Classification status of a scanned filesystem path.

    Attributes:
        ORPHAN: No matching installed package found for this path.
        OWNED: Path is owned by an installed package (dpkg/flatpak/snap).
        PROTECTED: Path matches a protected pattern and must not be deleted.
        UNKNOWN: Ownership could not be determined.
    """

    ORPHAN = "orphan"
    OWNED = "owned"
    PROTECTED = "protected"
    UNKNOWN = "unknown"


class OrphanReason(str, Enum):
    """Reason why a filesystem path is considered orphaned.

    Attributes:
        NO_PACKAGE_MATCH: dpkg -S found no owning package.
        PACKAGE_UNINSTALLED: A package that previously owned this path was removed.
        STALE_CACHE: Cache directory with no corresponding active application.
        DEAD_LINK: Symbolic link whose target no longer exists.
    """

    NO_PACKAGE_MATCH = "no_package_match"
    PACKAGE_UNINSTALLED = "package_removed"
    STALE_CACHE = "stale_cache"
    DEAD_LINK = "dead_link"


@dataclass(frozen=True, slots=True)
class ScannedPath:
    """Represents a filesystem entry discovered during scanning.

    This is an immutable data structure that captures all relevant
    information about a scanned filesystem path, including its type,
    ownership status, and orphan classification.

    Attributes:
        path: Absolute filesystem path.
        path_type: Type of the filesystem entry (directory, file, symlink).
        status: Classification status (orphan, owned, protected, unknown).
        size_bytes: Size in bytes (recursive for directories, None if unavailable).
        mtime: Last modification time in ISO 8601 format (None if unavailable).
        parent_target: Scan target root directory (e.g., "~/.config").
        orphan_reason: Reason for orphan classification (None if not orphaned).
        confidence: Orphan confidence score (0.0 to 1.0).
        description: Human-readable description of the entry.
    """

    path: str
    path_type: PathType
    status: PathStatus
    size_bytes: int | None
    mtime: str | None
    parent_target: str
    orphan_reason: OrphanReason | None
    confidence: float
    description: str | None

    def __post_init__(self) -> None:
        """Validate scanned path data after initialization."""
        if not self.path:
            msg = "Path cannot be empty"
            raise ValueError(msg)
        if not (0.0 <= self.confidence <= 1.0):
            msg = f"Confidence must be between 0.0 and 1.0, got {self.confidence}"
            raise ValueError(msg)
