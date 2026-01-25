"""Abstract base class for package scanners.

This module defines the Scanner interface that all package source
scanners must implement.
"""

from abc import ABC, abstractmethod
from collections.abc import Iterator

from popctl.models.package import PackageSource, PackageStatus, ScannedPackage


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

    @property
    @abstractmethod
    def source(self) -> PackageSource:
        """Return the package source this scanner handles.

        Returns:
            PackageSource enum value (APT, FLATPAK, or SNAP)
        """

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

    def scan_manual_only(self) -> Iterator[ScannedPackage]:
        """Yield only manually-installed packages.

        This is a convenience method that filters the full scan
        to include only packages explicitly installed by the user.

        Yields:
            ScannedPackage instances with status == MANUAL.
        """
        for pkg in self.scan():
            if pkg.status == PackageStatus.MANUAL:
                yield pkg

    def count(self) -> tuple[int, int]:
        """Count total and manual packages.

        Returns:
            Tuple of (total_count, manual_count).
        """
        total = 0
        manual = 0
        for pkg in self.scan():
            total += 1
            if pkg.status == PackageStatus.MANUAL:
                manual += 1
        return total, manual
