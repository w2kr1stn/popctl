"""Shared domain models for filesystem and config modules."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class OrphanStatus(str, Enum):
    """Classification status of a scanned entry.

    Attributes:
        ORPHAN: No matching installed package found for this entry.
        OWNED: Entry is owned by an installed package (dpkg/flatpak/snap).
        PROTECTED: Entry matches a protected pattern and must not be deleted.
    """

    ORPHAN = "orphan"
    OWNED = "owned"
    PROTECTED = "protected"


class PathType(str, Enum):
    """Type of filesystem or configuration entry.

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


class OrphanReason(str, Enum):
    """Reason why an entry is considered orphaned.

    Attributes:
        NO_PACKAGE_MATCH: dpkg -S found no owning package.
        STALE_CACHE: Cache directory with no corresponding active application.
        DEAD_LINK: Symbolic link whose target no longer exists.
    """

    NO_PACKAGE_MATCH = "no_package_match"
    STALE_CACHE = "stale_cache"
    DEAD_LINK = "dead_link"


@dataclass(frozen=True, slots=True)
class ScannedEntry:
    """Represents a scanned filesystem or config entry.

    Unified model for both filesystem and config scanning domains.
    The parent_target field is only populated for filesystem entries.

    Attributes:
        path: Absolute filesystem path.
        path_type: Type of the entry (directory, file, symlink).
        status: Classification status (orphan, owned, protected).
        size_bytes: Size in bytes (recursive for directories, None if unavailable).
        mtime: Last modification time in ISO 8601 format (None if unavailable).
        parent_target: Scan target root directory (filesystem only, None for configs).
        orphan_reason: Reason for orphan classification (None if not orphaned).
        confidence: Orphan confidence score (0.0 to 1.0).
    """

    path: str
    path_type: PathType
    status: OrphanStatus
    size_bytes: int | None
    mtime: str | None
    parent_target: str | None
    orphan_reason: OrphanReason | None
    confidence: float

    def __post_init__(self) -> None:
        """Validate scanned entry data after initialization."""
        if not self.path:
            msg = "Path cannot be empty"
            raise ValueError(msg)
        if not (0.0 <= self.confidence <= 1.0):
            msg = f"Confidence must be between 0.0 and 1.0, got {self.confidence}"
            raise ValueError(msg)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for JSON export."""
        result: dict[str, Any] = {
            "path": self.path,
            "path_type": self.path_type.value,
            "status": self.status.value,
            "size_bytes": self.size_bytes,
            "mtime": self.mtime,
            "orphan_reason": self.orphan_reason.value if self.orphan_reason else None,
            "confidence": self.confidence,
        }
        if self.parent_target is not None:
            result["parent_target"] = self.parent_target
        return result


@dataclass(frozen=True, slots=True)
class DomainActionResult:
    """Result of a domain deletion operation (filesystem or config).

    Attributes:
        path: Absolute path that was operated on.
        success: Whether the operation completed successfully.
        error: Error message if the operation failed, None otherwise.
        dry_run: Whether this was a dry-run (no actual deletion).
    """

    path: str
    success: bool
    error: str | None = None
    dry_run: bool = False
