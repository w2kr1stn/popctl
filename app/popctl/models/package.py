"""Package models for system scanning and classification.

This module defines the core data structures for representing
packages from various sources (APT, Flatpak, Snap).
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal

# Type alias for classification values
ClassificationType = Literal["keep", "remove", "ask"]


class PackageSource(Enum):
    """Enumeration of supported package sources."""

    APT = "apt"
    FLATPAK = "flatpak"


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
        install_date: ISO format installation date (if available)
        classification: AI classification result ('keep', 'remove', 'ask')
        confidence: Classification confidence score (0.0 - 1.0)
        reason: Explanation for the classification
        category: Package category ('system', 'development', 'media', etc.)
    """

    name: str
    source: PackageSource
    version: str
    status: PackageStatus
    description: str | None = field(default=None)
    size_bytes: int | None = field(default=None)
    install_date: str | None = field(default=None)
    # Classification fields (populated by Claude Advisor)
    classification: ClassificationType | None = field(default=None)
    confidence: float | None = field(default=None)
    reason: str | None = field(default=None)
    category: str | None = field(default=None)

    def __post_init__(self) -> None:
        """Validate package data after initialization."""
        if not self.name:
            msg = "Package name cannot be empty"
            raise ValueError(msg)
        if not self.version:
            msg = "Package version cannot be empty"
            raise ValueError(msg)
        if self.confidence is not None and not (0.0 <= self.confidence <= 1.0):
            msg = f"Confidence must be between 0.0 and 1.0, got {self.confidence}"
            raise ValueError(msg)

    @property
    def is_manual(self) -> bool:
        """Check if package was manually installed."""
        return self.status == PackageStatus.MANUAL

    @property
    def is_auto(self) -> bool:
        """Check if package was auto-installed as dependency."""
        return self.status == PackageStatus.AUTO_INSTALLED

    @property
    def size_human(self) -> str:
        """Return human-readable size string."""
        if self.size_bytes is None:
            return "unknown"

        size = float(self.size_bytes)
        for unit in ("B", "KB", "MB", "GB"):
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"
