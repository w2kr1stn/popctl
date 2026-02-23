"""Config domain models for orphan detection and scanning.

This module defines the core data structures for representing
configuration entries discovered during system scanning, including
config types, statuses, and orphan classification reasons.
"""

from dataclasses import dataclass
from enum import Enum


class ConfigType(str, Enum):
    """Type of configuration entry.

    Attributes:
        DIRECTORY: Configuration directory (e.g., ``~/.config/vlc/``).
        FILE: Configuration file (e.g., ``~/.gitconfig``).
    """

    DIRECTORY = "directory"
    FILE = "file"


class ConfigStatus(str, Enum):
    """Classification status of a scanned configuration entry.

    Attributes:
        ORPHAN: No installed package owns this configuration.
        OWNED: An installed package owns this configuration.
        PROTECTED: Configuration is on the protected list and must not be deleted.
        UNKNOWN: Ownership could not be determined.
    """

    ORPHAN = "orphan"
    OWNED = "owned"
    PROTECTED = "protected"
    UNKNOWN = "unknown"


class ConfigOrphanReason(str, Enum):
    """Reason why a configuration entry is considered orphaned.

    Attributes:
        APP_NOT_INSTALLED: The parent application is not installed.
        NO_PACKAGE_MATCH: ``dpkg -S`` found no owning package.
        DEAD_LINK: Symbolic link target no longer exists.
    """

    APP_NOT_INSTALLED = "app_not_installed"
    NO_PACKAGE_MATCH = "no_package_match"
    DEAD_LINK = "dead_link"


@dataclass(frozen=True, slots=True)
class ScannedConfig:
    """Represents a configuration entry discovered during scanning.

    This is an immutable data structure that captures all relevant
    information about a scanned configuration path, including its type,
    ownership status, and orphan classification.

    Attributes:
        path: Absolute path to the configuration entry.
        config_type: Type of the configuration entry (directory or file).
        status: Classification status (orphan, owned, protected, unknown).
        size_bytes: Size in bytes (recursive for directories, None if unavailable).
        mtime: Last modification time in ISO 8601 format (None if unavailable).
        orphan_reason: Reason for orphan classification (None if not orphaned).
        confidence: Orphan confidence score (0.0 to 1.0).
        description: Human-readable description of the entry.
    """

    path: str
    config_type: ConfigType
    status: ConfigStatus
    size_bytes: int | None
    mtime: str | None
    orphan_reason: ConfigOrphanReason | None
    confidence: float
    description: str | None

    def __post_init__(self) -> None:
        """Validate scanned config data after initialization."""
        if not self.path:
            msg = "Path cannot be empty"
            raise ValueError(msg)
        if not (0.0 <= self.confidence <= 1.0):
            msg = f"Confidence must be between 0.0 and 1.0, got {self.confidence}"
            raise ValueError(msg)
