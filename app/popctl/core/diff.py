from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from popctl.core.baseline import is_package_protected
from popctl.models.action import Action, ActionType
from popctl.models.manifest import PackageSourceType
from popctl.models.package import PackageSource, PackageStatus

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
    name: str
    source: PackageSource
    diff_type: DiffType
    version: str | None = None
    description: str | None = None

    def to_dict(self) -> dict[str, str]:
        result: dict[str, str] = {
            "name": self.name,
            "source": self.source.value,
        }
        if self.version is not None:
            result["version"] = self.version
        if self.description is not None:
            result["description"] = self.description
        return result


@dataclass(frozen=True, slots=True)
class DiffResult:
    new: tuple[DiffEntry, ...]
    missing: tuple[DiffEntry, ...]
    extra: tuple[DiffEntry, ...]

    @property
    def is_in_sync(self) -> bool:
        return not (self.new or self.missing or self.extra)

    @property
    def total_changes(self) -> int:
        return len(self.new) + len(self.missing) + len(self.extra)

    def to_dict(self) -> dict[str, object]:
        return {
            "in_sync": self.is_in_sync,
            "summary": {
                "new": len(self.new),
                "missing": len(self.missing),
                "extra": len(self.extra),
                "total": self.total_changes,
            },
            "new": [e.to_dict() for e in self.new],
            "missing": [e.to_dict() for e in self.missing],
            "extra": [e.to_dict() for e in self.extra],
        }


def compute_diff(
    manifest: Manifest,
    scanners: list[Scanner],
    source_filter: PackageSourceType | None = None,
) -> DiffResult:
    """Compare manifest against current system state.

    Scans the system using provided scanners and computes differences
    between the manifest and installed packages.

    Only manually installed packages are considered (auto-installed
    dependencies are ignored). Protected system packages are also
    excluded from the diff.

    Args:
        manifest: The manifest describing desired system state.
        scanners: List of Scanner instances to use for scanning.
        source_filter: Optional filter for package source ("apt", "flatpak", or "snap").

    Returns:
        DiffResult containing all differences found.
    """
    # Collect currently installed manual packages from system
    installed: dict[str, tuple[PackageSource, str | None, str | None]] = {}

    for scanner in scanners:
        # Skip if source filter is active and doesn't match
        if source_filter and scanner.source.value != source_filter:
            continue

        for pkg in scanner.scan():
            # Only consider manually installed packages
            if pkg.status != PackageStatus.MANUAL:
                continue

            # Skip protected packages
            if is_package_protected(pkg.name):
                continue

            installed[pkg.name] = (scanner.source, pkg.version, pkg.description)

    # Get packages from manifest
    keep_packages = manifest.get_keep_packages(source_filter)
    remove_packages = manifest.get_remove_packages(source_filter)

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
            if is_package_protected(name):
                continue
            missing_entries.append(
                DiffEntry(
                    name=name,
                    source=PackageSource(entry.source),
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
    new_entries.sort(key=lambda e: (e.source.value, e.name))
    missing_entries.sort(key=lambda e: (e.source.value, e.name))
    extra_entries.sort(key=lambda e: (e.source.value, e.name))

    return DiffResult(
        new=tuple(new_entries),
        missing=tuple(missing_entries),
        extra=tuple(extra_entries),
    )


def diff_to_actions(diff_result: DiffResult, purge: bool = False) -> list[Action]:
    """Convert diff result to list of actions.

    Only MISSING and EXTRA diffs are converted to actions:
    - MISSING: Package in manifest but not installed -> INSTALL
    - EXTRA: Package marked for removal but still installed -> REMOVE/PURGE

    NEW packages (installed but not in manifest) are ignored - the user
    must explicitly add them to the remove list in the manifest.

    Note: Protected packages are already filtered by compute_diff() and
    will not appear in the diff_result.

    Args:
        diff_result: Result from compute_diff().
        purge: If True, use PURGE instead of REMOVE for APT and Snap packages.

    Returns:
        List of Action objects to execute.
    """
    actions: list[Action] = []

    # MISSING -> INSTALL
    for entry in diff_result.missing:
        action = Action(
            action_type=ActionType.INSTALL,
            package=entry.name,
            source=entry.source,
        )
        actions.append(action)

    # EXTRA -> REMOVE/PURGE
    for entry in diff_result.extra:
        # Purge applies to APT and Snap packages
        use_purge = purge and entry.source in (PackageSource.APT, PackageSource.SNAP)

        action = Action(
            action_type=ActionType.PURGE if use_purge else ActionType.REMOVE,
            package=entry.name,
            source=entry.source,
        )
        actions.append(action)

    return actions
