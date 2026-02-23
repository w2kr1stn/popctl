"""Package models for system scanning and classification.

This module defines the core data structures for representing
packages from various sources (APT, Flatpak, Snap).
"""

from dataclasses import dataclass, field
from enum import Enum


class PackageSource(Enum):
    """Enumeration of supported package sources."""

    APT = "apt"
    FLATPAK = "flatpak"
    SNAP = "snap"


# Derived constant: all source key strings for iteration.
# Use this instead of hardcoding ("apt", "flatpak") to avoid missing sources.
PACKAGE_SOURCE_KEYS: tuple[str, ...] = tuple(s.value for s in PackageSource)


class PackageStatus(Enum):
    """Package installation status.

    Distinguishes between packages explicitly installed by the user
    and those automatically installed as dependencies.
    """

    MANUAL = "manual"
    AUTO_INSTALLED = "auto"


@dataclass(frozen=True, slots=True)
class ScannedPackage:
    """Represents a package discovered during system scanning.

    This is an immutable data structure that captures all relevant
    information about an installed package.

    Attributes:
        name: Package name (e.g., 'firefox', 'com.spotify.Client')
        source: Package manager that installed this package
        version: Installed version string
        status: Whether manually or automatically installed
        description: Human-readable package description
        size_bytes: Installed size in bytes (if available)
    """

    name: str
    source: PackageSource
    version: str
    status: PackageStatus
    description: str | None = field(default=None)
    size_bytes: int | None = field(default=None)

    def __post_init__(self) -> None:
        """Validate package data after initialization."""
        if not self.name:
            msg = "Package name cannot be empty"
            raise ValueError(msg)
        if not self.version:
            msg = "Package version cannot be empty"
            raise ValueError(msg)

    @property
    def is_manual(self) -> bool:
        """Check if package was manually installed."""
        return self.status == PackageStatus.MANUAL


# Type alias replacing the former ScanResult dataclass (models/scan_result.py)
ScanResult = tuple[ScannedPackage, ...]
