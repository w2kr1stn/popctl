"""Scan result model for JSON export.

This module defines the data structure for exporting scan results
to JSON with proper metadata.
"""

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from popctl.models.package import ScannedPackage


@dataclass(frozen=True, slots=True)
class ScanMetadata:
    """Metadata for a scan result.

    Attributes:
        timestamp: ISO format timestamp when the scan was performed.
        hostname: Name of the machine that was scanned.
        popctl_version: Version of popctl that performed the scan.
        sources: Tuple of package sources that were scanned (immutable).
        manual_only: Whether only manually installed packages were included.
    """

    timestamp: str
    hostname: str
    popctl_version: str
    sources: tuple[str, ...]
    manual_only: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "timestamp": self.timestamp,
            "hostname": self.hostname,
            "popctl_version": self.popctl_version,
            "sources": list(self.sources),  # Convert back to list for JSON
            "manual_only": self.manual_only,
        }


@dataclass(frozen=True, slots=True)
class ScanResult:
    """Complete scan result for export.

    Attributes:
        metadata: Scan metadata including timestamp and hostname.
        packages: List of scanned packages.
        summary: Package count summary.
    """

    metadata: ScanMetadata
    packages: list[ScannedPackage]
    summary: dict[str, int] = field(default_factory=lambda: {})

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "metadata": self.metadata.to_dict(),
            "packages": [_package_to_dict(pkg) for pkg in self.packages],
            "summary": self.summary,
        }

    @classmethod
    def create(
        cls,
        packages: list[ScannedPackage],
        sources: list[str],
        manual_only: bool = False,
    ) -> ScanResult:
        """Create a ScanResult with auto-generated metadata.

        Args:
            packages: List of scanned packages.
            sources: List of source names that were scanned.
            manual_only: Whether only manual packages were included.

        Returns:
            ScanResult with populated metadata and summary.
        """
        import socket

        from popctl import __version__

        # Count packages by source
        summary: dict[str, int] = {}
        manual_count = 0
        auto_count = 0

        for pkg in packages:
            source_key = pkg.source.value
            summary[source_key] = summary.get(source_key, 0) + 1
            if pkg.is_manual:
                manual_count += 1
            else:
                auto_count += 1

        summary["total"] = len(packages)
        summary["manual"] = manual_count
        summary["auto"] = auto_count

        metadata = ScanMetadata(
            timestamp=datetime.now(UTC).isoformat(),
            hostname=socket.gethostname(),
            popctl_version=__version__,
            sources=tuple(sources),
            manual_only=manual_only,
        )

        return cls(metadata=metadata, packages=packages, summary=summary)


def _package_to_dict(pkg: ScannedPackage) -> dict[str, Any]:
    """Convert a ScannedPackage to a dictionary.

    Args:
        pkg: The package to convert.

    Returns:
        Dictionary representation of the package.
    """
    return {
        "name": pkg.name,
        "source": pkg.source.value,
        "version": pkg.version,
        "status": pkg.status.value,
        "description": pkg.description,
        "size_bytes": pkg.size_bytes,
        "install_date": pkg.install_date,
        "classification": pkg.classification,
        "confidence": pkg.confidence,
        "reason": pkg.reason,
        "category": pkg.category,
    }
