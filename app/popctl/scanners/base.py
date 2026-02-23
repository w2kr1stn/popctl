"""Abstract base class for package scanners.

This module defines the Scanner interface that all package source
scanners must implement, and shared parsing helpers.
"""

import logging
from abc import ABC, abstractmethod
from collections.abc import Iterator

from popctl.models.package import PackageSource, ScannedPackage

logger = logging.getLogger(__name__)


def parse_tab_fields(
    line: str,
    source_label: str,
    min_fields: int = 2,
) -> tuple[str, str, list[str]] | None:
    """Parse a tab-separated line into (name, version, remaining_parts).

    Shared guard-clause logic for APT and Flatpak scanners that both
    split on tabs, check minimum field count, and validate name/version.

    Args:
        line: Raw tab-separated line from a package manager.
        source_label: Label for debug messages (e.g., "dpkg", "flatpak").
        min_fields: Minimum number of tab-separated fields required.

    Returns:
        Tuple of (name, version, remaining_parts) on success, None on
        malformed input.
    """
    parts = line.split("\t")
    if len(parts) < min_fields:
        logger.debug(
            "Skipping malformed %s line (parts=%d): %r",
            source_label,
            len(parts),
            line[:100],
        )
        return None

    name = parts[0].strip()
    version = parts[1].strip()

    if not name or not version:
        logger.debug(
            "Skipping %s line with empty name/version: %r",
            source_label,
            line[:100],
        )
        return None

    return name, version, parts


class Scanner(ABC):
    """Abstract base class for all package scanners.

    Scanners are responsible for querying a package manager
    and yielding information about installed packages.

    Example:
        >>> scanner = AptScanner()
        >>> if scanner.is_available():
        ...     for pkg in scanner.scan():
        ...         print(f"{pkg.name}: {pkg.version}")
    """

    source: PackageSource

    @abstractmethod
    def scan(self) -> Iterator[ScannedPackage]:
        """Scan and yield all installed packages from this source.

        Yields:
            ScannedPackage instances for each installed package.

        Raises:
            RuntimeError: If the package manager is not available.
        """

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this package manager is available on the system.

        Returns:
            True if the package manager can be used, False otherwise.
        """
