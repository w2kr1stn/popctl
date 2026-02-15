"""Filesystem scanner for orphaned directories and files.

Scans XDG user directories and optionally /etc for directories
and files that are not owned by any installed package manager
(dpkg, flatpak, snap). Uses dpkg -S cross-referencing and app
name matching for orphan detection.
"""

import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from popctl.filesystem.models import OrphanReason, PathStatus, PathType, ScannedPath
from popctl.filesystem.protected import is_protected_path
from popctl.utils.shell import run_command

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
        self._installed_packages: set[str] | None = None
        self._installed_apps: set[str] | None = None
        self._dpkg_cache: dict[str, bool] = {}

    def is_available(self) -> bool:
        """Check if the filesystem scanner is available.

        Always returns True because the filesystem always exists.
        """
        return True

    def scan(self) -> Iterator[ScannedPath]:
        """Scan all target directories and yield orphaned paths.

        Iterates over each configured target directory. Skips
        non-existent targets silently. Yields ScannedPath for each
        top-level entry that is classified as an orphan.

        Yields:
            ScannedPath instances for orphaned filesystem entries.
        """
        # Reset caches for each scan session
        self._installed_packages = None
        self._installed_apps = None
        self._dpkg_cache = {}

        for target in self._targets:
            if not target.is_dir():
                continue
            yield from self._scan_directory(target)

    def _scan_directory(self, target: Path) -> Iterator[ScannedPath]:
        """Scan a single target directory for orphaned entries.

        Iterates over top-level entries in the target directory,
        checks ownership, and yields orphaned entries with confidence
        scores.

        Args:
            target: Directory to scan.

        Yields:
            ScannedPath for each orphaned entry.
        """
        try:
            entries = sorted(target.iterdir())
        except PermissionError:
            logger.warning("Permission denied scanning directory: %s", target)
            return

        for entry in entries:
            try:
                path_type = self._get_path_type(entry)
            except OSError:
                logger.warning("Cannot determine type of: %s", entry)
                continue

            # Skip files unless include_files is set
            if path_type == PathType.FILE and not self._include_files:
                continue

            name = entry.name
            status = self._check_ownership(name, entry)

            if status in (PathStatus.OWNED, PathStatus.PROTECTED):
                continue

            # Determine orphan reason
            orphan_reason = self._determine_orphan_reason(path_type, target)
            confidence = self._calculate_confidence(str(target), path_type)
            size = self._get_size(entry)
            mtime = self._get_mtime(entry)

            # Build parent_target as tilde-prefixed path for home dirs
            parent_target = self._format_target(target)

            yield ScannedPath(
                path=str(entry),
                path_type=path_type,
                status=PathStatus.ORPHAN,
                size_bytes=size,
                mtime=mtime,
                parent_target=parent_target,
                orphan_reason=orphan_reason,
                confidence=confidence,
                description=None,
            )

    def _check_ownership(self, name: str, path: Path) -> PathStatus:
        """Determine if a path is owned by an installed package or app.

        Checks in order:
        1. Protected paths list
        2. dpkg ownership (dpkg -S)
        3. App name matching (flatpak/snap)

        Args:
            name: Directory or file name (basename).
            path: Full path to the entry.

        Returns:
            PathStatus indicating ownership classification.
        """
        if is_protected_path(str(path)):
            return PathStatus.PROTECTED

        if self._dpkg_owns_path(path):
            return PathStatus.OWNED

        if self._app_name_matches(name):
            return PathStatus.OWNED

        return PathStatus.ORPHAN

    def _dpkg_owns_path(self, path: Path) -> bool:
        """Check if dpkg knows about files in this path.

        Runs ``dpkg -S <path>`` and caches the result. Returns True
        if the return code is 0 (at least one package owns the path).

        Args:
            path: Filesystem path to check.

        Returns:
            True if a package owns the path, False otherwise.
        """
        path_str = str(path)
        if path_str in self._dpkg_cache:
            return self._dpkg_cache[path_str]

        try:
            result = run_command(["dpkg", "-S", path_str], timeout=10.0)
            owned = result.success
        except (FileNotFoundError, OSError):
            owned = False

        self._dpkg_cache[path_str] = owned
        return owned

    def _app_name_matches(self, name: str) -> bool:
        """Check if name matches any installed flatpak or snap app.

        Performs case-insensitive comparison against installed app names.
        Also checks if the directory name appears as a component in
        reverse-DNS app IDs (e.g., "org.mozilla.firefox" contains "firefox").

        Args:
            name: Directory or file name to check.

        Returns:
            True if the name matches an installed app.
        """
        apps = self._get_installed_apps()
        name_lower = name.lower()

        for app in apps:
            app_lower = app.lower()
            # Exact match
            if name_lower == app_lower:
                return True
            # Reverse-DNS component match (e.g., "firefox" in "org.mozilla.firefox")
            if "." in app_lower:
                components = app_lower.split(".")
                if name_lower in components:
                    return True

        return False

    def _calculate_confidence(self, target: str, path_type: PathType) -> float:
        """Calculate orphan confidence based on target directory.

        Higher confidence means safer to delete. Cache directories
        have the highest confidence; /etc has the lowest.

        Args:
            target: String representation of the scan target directory.
            path_type: Type of the filesystem entry.

        Returns:
            Confidence score between 0.0 and 1.0.
        """
        _ = path_type  # Reserved for future per-type adjustments

        if ".cache" in target:
            return 0.95
        if ".local/share" in target:
            return 0.75
        if ".config" in target:
            return 0.70
        if target.startswith("/etc"):
            return 0.50

        return 0.60

    def _get_installed_packages(self) -> set[str]:
        """Get set of installed package names (cached).

        Runs ``dpkg-query -f '${Package}\\n' -W`` and caches the result.

        Returns:
            Set of installed package names.
        """
        if self._installed_packages is not None:
            return self._installed_packages

        try:
            result = run_command(
                ["dpkg-query", "-f", "${Package}\n", "-W"],
                timeout=30.0,
            )
            if result.success:
                self._installed_packages = {
                    line.strip() for line in result.stdout.strip().split("\n") if line.strip()
                }
            else:
                logger.warning("dpkg-query failed: %s", result.stderr.strip())
                self._installed_packages = set()
        except (FileNotFoundError, OSError) as exc:
            logger.warning("Cannot query installed packages: %s", exc)
            self._installed_packages = set()

        return self._installed_packages

    def _get_installed_apps(self) -> set[str]:
        """Get set of installed app names from flatpak and snap (cached).

        Queries both flatpak and snap for installed applications.
        If either is unavailable, its contribution is an empty set.

        Returns:
            Set of installed application identifiers.
        """
        if self._installed_apps is not None:
            return self._installed_apps

        apps: set[str] = set()

        # Flatpak apps
        try:
            result = run_command(
                ["flatpak", "list", "--app", "--columns=application"],
                timeout=15.0,
            )
            if result.success:
                apps.update(
                    line.strip() for line in result.stdout.strip().split("\n") if line.strip()
                )
        except (FileNotFoundError, OSError):
            pass

        # Snap apps
        try:
            result = run_command(["snap", "list"], timeout=15.0)
            if result.success:
                lines = result.stdout.strip().split("\n")
                # Skip header line
                for line in lines[1:]:
                    parts = line.split()
                    if parts:
                        apps.add(parts[0])
        except (FileNotFoundError, OSError):
            pass

        self._installed_apps = apps
        return self._installed_apps

    def _get_path_type(self, path: Path) -> PathType:
        """Determine the type of a filesystem path.

        Checks for symlinks first (before is_dir/is_file which follow
        symlinks), distinguishing between live and dead symlinks.

        Args:
            path: Path to classify.

        Returns:
            PathType classification.
        """
        if path.is_symlink():
            # Check if symlink target exists (without following further)
            if not path.exists():
                return PathType.DEAD_SYMLINK
            return PathType.SYMLINK

        if path.is_dir():
            return PathType.DIRECTORY

        return PathType.FILE

    def _get_size(self, path: Path) -> int | None:
        """Get size in bytes for a path.

        For files, returns the file size. For directories, returns the
        sum of all files recursively. Returns None on any error.

        Args:
            path: Path to measure.

        Returns:
            Size in bytes, or None if unavailable.
        """
        try:
            if path.is_file() or path.is_symlink():
                return path.lstat().st_size

            if path.is_dir():
                total = 0
                for child in path.rglob("*"):
                    try:
                        if child.is_file():
                            total += child.stat().st_size
                    except OSError:
                        continue
                return total
        except OSError:
            return None

        return None

    def _get_mtime(self, path: Path) -> str | None:
        """Get last modification time as ISO 8601 string.

        Args:
            path: Path to check.

        Returns:
            ISO 8601 formatted modification time, or None on error.
        """
        try:
            stat = path.lstat()
            dt = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
            return dt.isoformat()
        except OSError:
            return None

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
