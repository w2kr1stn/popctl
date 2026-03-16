from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class OrphanStatus(Enum):
    ORPHAN = "orphan"
    OWNED = "owned"
    PROTECTED = "protected"


class PathType(Enum):
    DIRECTORY = "directory"
    FILE = "file"
    SYMLINK = "symlink"
    DEAD_SYMLINK = "dead_symlink"


class OrphanReason(Enum):
    NO_PACKAGE_MATCH = "no_package_match"
    STALE_CACHE = "stale_cache"
    DEAD_LINK = "dead_link"


@dataclass(frozen=True, slots=True)
class ScannedEntry:
    path: str
    path_type: PathType
    status: OrphanStatus
    size_bytes: int | None
    mtime: str | None
    parent_target: str | None
    orphan_reason: OrphanReason | None
    confidence: float

    def __post_init__(self) -> None:
        if not self.path:
            msg = "Path cannot be empty"
            raise ValueError(msg)
        if not (0.0 <= self.confidence <= 1.0):
            msg = f"Confidence must be between 0.0 and 1.0, got {self.confidence}"
            raise ValueError(msg)

    def to_dict(self) -> dict[str, Any]:
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
    path: str
    success: bool
    error: str | None = None
    dry_run: bool = False
    backup_path: str | None = None
