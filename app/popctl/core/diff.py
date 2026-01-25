"""Diff engine for comparing manifest with system state.

This module provides the DiffEngine class that compares the declared
manifest state with the actual installed packages on the system.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from popctl.core.baseline import is_protected
from popctl.models.package import PackageStatus

if TYPE_CHECKING:
    from popctl.models.manifest import Manifest
    from popctl.scanners.base import Scanner


class DiffType(Enum):
    """Type of difference between manifest and system state.

    Attributes:
        NEW: Package is installed on system but not tracked in manifest.
             Action: Add to manifest (keep) or uninstall.
        MISSING: Package is in manifest (keep) but not installed.
             Action: Install or remove from manifest.
        EXTRA: Package is marked for removal but still installed.
             Action: Remove from system.
    """

    NEW = "new"
    MISSING = "missing"
    EXTRA = "extra"


@dataclass(frozen=True, slots=True)
class DiffEntry:
    """Represents a single difference between manifest and system.

    Attributes:
        name: Package name.
        source: Package source ("apt" or "flatpak").
        diff_type: Type of difference (new, missing, or extra).
        version: Current installed version (if available).
        description: Package description (if available).
    """

    name: str
    source: str
    diff_type: DiffType
    version: str | None = None
    description: str | None = None


@dataclass(frozen=True, slots=True)
class DiffResult:
    """Result of comparing manifest with system state.

    Contains lists of packages in each difference category.

    Attributes:
        new: Packages installed but not in manifest.
        missing: Packages in manifest but not installed.
        extra: Packages marked for removal but still installed.
    """

    new: tuple[DiffEntry, ...]
    missing: tuple[DiffEntry, ...]
    extra: tuple[DiffEntry, ...]

    @property
    def is_in_sync(self) -> bool:
        """Check if system is in sync with manifest.

        Returns:
            True if there are no differences, False otherwise.
        """
        return not (self.new or self.missing or self.extra)

    @property
    def total_changes(self) -> int:
        """Total number of differences found.

        Returns:
            Sum of new, missing, and extra packages.
        """
        return len(self.new) + len(self.missing) + len(self.extra)

    def to_dict(self) -> dict[str, object]:
        """Convert to dictionary for JSON serialization.

        Returns:
            Dictionary representation of the diff result.
        """
        return {
            "in_sync": self.is_in_sync,
            "summary": {
                "new": len(self.new),
                "missing": len(self.missing),
                "extra": len(self.extra),
                "total": self.total_changes,
            },
            "new": [_entry_to_dict(e) for e in self.new],
            "missing": [_entry_to_dict(e) for e in self.missing],
            "extra": [_entry_to_dict(e) for e in self.extra],
        }


def _entry_to_dict(entry: DiffEntry) -> dict[str, str]:
    """Convert a DiffEntry to a dictionary.

    Args:
        entry: The DiffEntry to convert.

    Returns:
        Dictionary with non-None fields.
    """
    result: dict[str, str] = {
        "name": entry.name,
        "source": entry.source,
    }
    if entry.version is not None:
        result["version"] = entry.version
    if entry.description is not None:
        result["description"] = entry.description
    return result


class DiffEngine:
    """Engine for computing differences between manifest and system state.

    The DiffEngine compares what packages should be installed (according to
    the manifest) with what is actually installed on the system.

    Example:
        >>> from popctl.core.diff import DiffEngine
        >>> from popctl.core.manifest import load_manifest
        >>> manifest = load_manifest()
        >>> engine = DiffEngine(manifest)
        >>> result = engine.compute_diff([AptScanner(), FlatpakScanner()])
        >>> if result.is_in_sync:
        ...     print("System matches manifest!")
    """

    def __init__(self, manifest: Manifest) -> None:
        """Initialize the DiffEngine with a manifest.

        Args:
            manifest: The manifest describing desired system state.
        """
        self.manifest = manifest

    def compute_diff(
        self,
        scanners: list[Scanner],
        source_filter: str | None = None,
    ) -> DiffResult:
        """Compare manifest against current system state.

        Scans the system using provided scanners and computes differences
        between the manifest and installed packages.

        Only manually installed packages are considered (auto-installed
        dependencies are ignored). Protected system packages are also
        excluded from the diff.

        Args:
            scanners: List of Scanner instances to use for scanning.
            source_filter: Optional filter for package source ("apt" or "flatpak").

        Returns:
            DiffResult containing all differences found.
        """
        # Collect currently installed manual packages from system
        installed: dict[str, tuple[str, str | None, str | None]] = {}

        for scanner in scanners:
            if not scanner.is_available():
                continue

            source_name = scanner.source.value

            # Skip if source filter is active and doesn't match
            if source_filter and source_name != source_filter:
                continue

            for pkg in scanner.scan():
                # Only consider manually installed packages
                if pkg.status != PackageStatus.MANUAL:
                    continue

                # Skip protected packages
                if is_protected(pkg.name):
                    continue

                installed[pkg.name] = (source_name, pkg.version, pkg.description)

        # Get packages from manifest
        keep_packages = self.manifest.get_keep_packages(source_filter)  # type: ignore[arg-type]
        remove_packages = self.manifest.get_remove_packages(source_filter)  # type: ignore[arg-type]

        # Compute NEW: installed but not in manifest
        new_entries: list[DiffEntry] = []
        for name, (source, version, desc) in installed.items():
            if name not in keep_packages and name not in remove_packages:
                new_entries.append(
                    DiffEntry(
                        name=name,
                        source=source,
                        diff_type=DiffType.NEW,
                        version=version,
                        description=desc,
                    )
                )

        # Compute MISSING: in manifest.keep but not installed
        missing_entries: list[DiffEntry] = []
        for name, entry in keep_packages.items():
            if name not in installed:
                # Skip protected packages from missing check too
                if is_protected(name):
                    continue
                missing_entries.append(
                    DiffEntry(
                        name=name,
                        source=entry.source,
                        diff_type=DiffType.MISSING,
                    )
                )

        # Compute EXTRA: in manifest.remove but still installed
        extra_entries: list[DiffEntry] = []
        for name, _entry in remove_packages.items():
            if name in installed:
                source, version, desc = installed[name]
                extra_entries.append(
                    DiffEntry(
                        name=name,
                        source=source,
                        diff_type=DiffType.EXTRA,
                        version=version,
                        description=desc,
                    )
                )

        # Sort all entries by source and name for consistent output
        new_entries.sort(key=lambda e: (e.source, e.name))
        missing_entries.sort(key=lambda e: (e.source, e.name))
        extra_entries.sort(key=lambda e: (e.source, e.name))

        return DiffResult(
            new=tuple(new_entries),
            missing=tuple(missing_entries),
            extra=tuple(extra_entries),
        )
