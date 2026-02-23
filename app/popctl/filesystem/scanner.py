"""Filesystem scanner for orphaned directories and files.

Scans XDG user directories and optionally /etc for directories
and files that are not owned by any installed package manager
(dpkg, flatpak, snap). Uses dpkg -S cross-referencing and app
name matching for orphan detection.
"""

import logging
from collections.abc import Iterator
from pathlib import Path

from popctl.domain.models import OrphanReason, OrphanStatus, PathType, ScannedEntry
from popctl.domain.ownership import (
    app_name_matches,
    classify_path_type,
    dpkg_owns_path,
    get_installed_apps,
    get_path_mtime,
    get_path_size,
)
from popctl.domain.protected import is_protected

logger = logging.getLogger(__name__)

# Default scan target directories (relative to user home)
# Note: .config is excluded — it is exclusively owned by ConfigScanner
_DEFAULT_HOME_TARGETS: tuple[str, ...] = (
    ".local/share",
    ".cache",
)

_ETC_TARGET: str = "/etc"


class FilesystemScanner:
    """Scans filesystem for orphaned directories and files.

    Identifies top-level entries in XDG user directories that are not
    owned by any installed package (dpkg, flatpak, snap) and are not
    in the protected paths list.

    Args:
        include_files: If True, also scan individual files (not just directories).
        include_etc: If True, include /etc in scan targets.
        targets: Optional explicit list of target directories to scan.
            Defaults to ~/.local/share, ~/.cache (and /etc if
            include_etc is True). Note: ~/.config is handled exclusively
            by ConfigScanner.
    """

    def __init__(
        self,
        *,
        include_files: bool = False,
        include_etc: bool = False,
        targets: tuple[Path, ...] | None = None,
    ) -> None:
        self._include_files = include_files
        self._include_etc = include_etc

        if targets is not None:
            self._targets = targets
        else:
            home = Path.home()
            default = tuple(home / t for t in _DEFAULT_HOME_TARGETS)
            self._targets = (*default, Path(_ETC_TARGET)) if include_etc else default

        # Caches (populated lazily, valid for one scan session)
        self._installed_apps: set[str] | None = None
        self._dpkg_cache: dict[str, bool] = {}

    def scan(self) -> Iterator[ScannedEntry]:
        """Scan all target directories and yield orphaned paths.

        Iterates over each configured target directory. Skips
        non-existent targets silently. Yields ScannedEntry for each
        top-level entry that is classified as an orphan.

        Yields:
            ScannedEntry instances for orphaned filesystem entries.
        """
        # Reset caches for each scan session
        self._installed_apps = None
        self._dpkg_cache = {}

        for target in self._targets:
            if not target.is_dir():
                continue
            yield from self._scan_directory(target)

    def _scan_directory(self, target: Path) -> Iterator[ScannedEntry]:
        """Scan a single target directory for orphaned entries.

        Iterates over top-level entries in the target directory,
        checks ownership, and yields orphaned entries with confidence
        scores.

        Args:
            target: Directory to scan.

        Yields:
            ScannedEntry for each orphaned entry.
        """
        try:
            entries = sorted(target.iterdir())
        except PermissionError:
            logger.warning("Permission denied scanning directory: %s", target)
            return

        for entry in entries:
            try:
                path_type = classify_path_type(entry)
            except OSError:
                logger.warning("Cannot determine type of: %s", entry)
                continue

            # Skip files unless include_files is set
            if path_type == PathType.FILE and not self._include_files:
                continue

            name = entry.name
            status = self._check_ownership(name, entry)

            if status in (OrphanStatus.OWNED, OrphanStatus.PROTECTED):
                continue

            # Determine orphan reason
            orphan_reason = self._determine_orphan_reason(path_type, target)
            confidence = self._calculate_confidence(str(target))
            size = get_path_size(entry)
            mtime = get_path_mtime(entry)

            # Build parent_target as tilde-prefixed path for home dirs
            parent_target = self._format_target(target)

            yield ScannedEntry(
                path=str(entry),
                path_type=path_type,
                status=OrphanStatus.ORPHAN,
                size_bytes=size,
                mtime=mtime,
                parent_target=parent_target,
                orphan_reason=orphan_reason,
                confidence=confidence,
            )

    def _check_ownership(self, name: str, path: Path) -> OrphanStatus:
        """Determine if a path is owned by an installed package or app.

        Checks in order:
        1. Protected paths list
        2. dpkg ownership (dpkg -S)
        3. App name matching (flatpak/snap)

        Args:
            name: Directory or file name (basename).
            path: Full path to the entry.

        Returns:
            OrphanStatus indicating ownership classification.
        """
        if is_protected(str(path), "filesystem"):
            return OrphanStatus.PROTECTED

        if dpkg_owns_path(path, self._dpkg_cache):
            return OrphanStatus.OWNED

        apps = self._ensure_apps_cache()
        if app_name_matches(name, apps):
            return OrphanStatus.OWNED

        return OrphanStatus.ORPHAN

    def _ensure_apps_cache(self) -> set[str]:
        """Ensure apps cache is populated and return it."""
        if self._installed_apps is None:
            self._installed_apps = get_installed_apps()
        return self._installed_apps

    def _calculate_confidence(self, target: str) -> float:
        """Calculate orphan confidence based on target directory.

        Higher confidence means safer to delete. Cache directories
        have the highest confidence; /etc has the lowest.

        Args:
            target: String representation of the scan target directory.

        Returns:
            Confidence score between 0.0 and 1.0.
        """
        if ".cache" in target:
            return 0.95
        if ".local/share" in target:
            return 0.75
        if target.startswith("/etc"):
            return 0.50

        return 0.60

    @staticmethod
    def _format_target(target: Path) -> str:
        """Format a target directory path with tilde for home directories.

        Args:
            target: Target directory path.

        Returns:
            Tilde-prefixed path string for home directories,
            or absolute path string otherwise.
        """
        try:
            home = Path.home()
            relative = target.relative_to(home)
            return f"~/{relative}"
        except ValueError:
            return str(target)

    @staticmethod
    def _determine_orphan_reason(path_type: PathType, target: Path) -> OrphanReason:
        """Determine the orphan reason based on path type and target.

        Args:
            path_type: Type of the filesystem entry.
            target: Scan target directory containing this entry.

        Returns:
            OrphanReason classification.
        """
        if path_type == PathType.DEAD_SYMLINK:
            return OrphanReason.DEAD_LINK

        target_str = str(target)
        if ".cache" in target_str:
            return OrphanReason.STALE_CACHE

        return OrphanReason.NO_PACKAGE_MATCH
