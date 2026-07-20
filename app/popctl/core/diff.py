from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING

from popctl.core.baseline import is_package_protected
from popctl.models.action import Action, ActionType, SourceInstallContext
from popctl.models.manifest import PackageSourceType
from popctl.models.package import PackageSource, PackageStatus

if TYPE_CHECKING:
    from popctl.models.manifest import Manifest
    from popctl.scanners.base import Scanner
    from popctl.sources.models import SourcesConfig


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
    source_install_context: SourceInstallContext | None = None

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
    installed_flatpak_locators: set[tuple[str, str, str, str]] = set()

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
            if scanner.source is PackageSource.FLATPAK and pkg.flatpak_locator is not None:
                installed_flatpak_locators.add(pkg.flatpak_locator)

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
        if entry.source == PackageSource.FLATPAK.value and manifest.sources is not None:
            apps = tuple(app for app in manifest.sources.flatpak.apps if app.id == name)
            if apps:
                for app in apps:
                    locator = (app.scope.value, app.id, app.arch, app.branch)
                    if locator not in installed_flatpak_locators:
                        missing_entries.append(
                            DiffEntry(
                                name=name,
                                source=PackageSource.FLATPAK,
                                diff_type=DiffType.MISSING,
                                source_install_context=SourceInstallContext.for_flatpak(app),
                            )
                        )
                continue
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
    new_entries.sort(key=_diff_entry_sort_key)
    missing_entries.sort(key=_diff_entry_sort_key)
    extra_entries.sort(key=_diff_entry_sort_key)

    return DiffResult(
        new=tuple(new_entries),
        missing=tuple(missing_entries),
        extra=tuple(extra_entries),
    )


def _diff_entry_sort_key(entry: DiffEntry) -> tuple[str, str, str, str, str]:
    context = entry.source_install_context
    if context is None or not context.is_flatpak:
        return (entry.source.value, entry.name, "", "", "")
    return (
        entry.source.value,
        entry.name,
        context.flatpak_scope.value if context.flatpak_scope is not None else "",
        context.flatpak_arch or "",
        context.flatpak_branch or "",
    )


def _install_actions_for_entry(
    entry: DiffEntry,
    sources: SourcesConfig | None,
) -> list[Action]:
    if entry.source is PackageSource.FLATPAK and entry.source_install_context is not None:
        if sources is None:
            raise ValueError(f"Flatpak package has no source configuration: {entry.name}")
        context = entry.source_install_context
        if context.flatpak_scope is None or context.flatpak_remote is None:
            raise ValueError(f"Flatpak package has incomplete source context: {entry.name}")
        remotes = {(remote.scope, remote.name): remote for remote in sources.flatpak.remotes}
        remote = remotes.get((context.flatpak_scope, context.flatpak_remote))
        if remote is None or remote.replay_mode.value != "replay":
            raise ValueError(f"Flatpak app has no replayable remote source: {entry.name}")
        return [
            Action(
                ActionType.INSTALL,
                entry.name,
                entry.source,
                context,
            )
        ]

    if sources is None or entry.source is PackageSource.APT:
        return [Action(ActionType.INSTALL, entry.name, entry.source)]

    if entry.source is PackageSource.SNAP:
        channels = [channel for channel in sources.snap.packages if channel.name == entry.name]
        if not channels:
            return [Action(ActionType.INSTALL, entry.name, entry.source)]
        if len(channels) != 1:
            raise ValueError(f"Snap package has ambiguous source context: {entry.name}")
        if channels[0].replay_mode.value != "replay":
            raise ValueError(f"Snap package has no replayable source context: {entry.name}")
        return [
            Action(
                ActionType.INSTALL,
                entry.name,
                entry.source,
                SourceInstallContext.for_snap(channels[0]),
            )
        ]

    apps = [app for app in sources.flatpak.apps if app.id == entry.name]
    if not apps:
        return [Action(ActionType.INSTALL, entry.name, entry.source)]
    remotes = {(remote.scope, remote.name): remote for remote in sources.flatpak.remotes}
    for app in apps:
        remote = remotes.get((app.scope, app.origin))
        if remote is None or remote.replay_mode.value != "replay":
            raise ValueError(f"Flatpak app has no replayable remote source: {entry.name}")
    return [
        Action(
            ActionType.INSTALL,
            entry.name,
            entry.source,
            SourceInstallContext.for_flatpak(app),
        )
        for app in apps
    ]


def diff_to_actions(
    diff_result: DiffResult,
    purge: bool = False,
    sources: SourcesConfig | None = None,
) -> list[Action]:
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
        actions.extend(_install_actions_for_entry(entry, sources))

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
