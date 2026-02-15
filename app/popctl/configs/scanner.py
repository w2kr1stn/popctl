"""Config scanner for orphaned configuration directories and dotfiles.

Scans ~/.config/ top-level entries and shell dotfiles in the user's
home directory for configurations that are not owned by any installed
package manager (dpkg, flatpak, snap). Uses dpkg -S cross-referencing
and app name matching for orphan detection.
"""

import logging
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from popctl.configs.models import ConfigOrphanReason, ConfigStatus, ConfigType, ScannedConfig
from popctl.configs.protected import is_protected_config
from popctl.utils.shell import run_command

logger = logging.getLogger(__name__)

# Known shell dotfiles to scan in the user's home directory.
_SHELL_DOTFILES: tuple[str, ...] = (
    ".bashrc",
    ".bash_profile",
    ".profile",
    ".zshrc",
    ".zprofile",
    ".vimrc",
    ".gitconfig",
    ".tmux.conf",
    ".wgetrc",
    ".curlrc",
)

# Confidence scores by config type.
_CONFIDENCE_DIRECTORY: float = 0.70
_CONFIDENCE_FILE: float = 0.60


class ConfigScanner:
    """Scans ~/.config/ and shell dotfiles for orphaned configurations.

    Identifies top-level entries in ~/.config/ and known shell dotfiles
    in the home directory that are not owned by any installed package
    (dpkg, flatpak, snap) and are not in the protected configs list.

    Caches for installed packages and apps are lazily populated and
    reset at the start of each ``scan()`` call.
    """

    def __init__(self) -> None:
        self._packages_cache: set[str] | None = None
        self._apps_cache: set[str] | None = None
        self._dpkg_cache: dict[str, bool] = {}

    def scan(self) -> Iterator[ScannedConfig]:
        """Scan for orphaned configs. Yields only ORPHAN-status entries.

        Scans ``~/.config/`` top-level entries and known shell dotfiles
        in the home directory. Protected and owned entries are silently
        skipped.

        Yields:
            ScannedConfig instances for orphaned configuration entries.
        """
        self._reset_caches()
        yield from self._scan_config_dir()
        yield from self._scan_dotfiles()

    def _scan_config_dir(self) -> Iterator[ScannedConfig]:
        """Scan ~/.config/ top-level entries only (not recursive).

        Iterates over direct children of ``~/.config/``, skipping
        protected entries and entries owned by installed packages.

        Yields:
            ScannedConfig for each orphaned entry in ``~/.config/``.
        """
        config_dir = Path.home() / ".config"

        if not config_dir.is_dir():
            return

        try:
            entries = sorted(config_dir.iterdir())
        except PermissionError:
            logger.warning("Permission denied scanning directory: %s", config_dir)
            return

        for entry in entries:
            try:
                yield from self._process_entry(entry)
            except PermissionError:
                logger.warning("Permission denied accessing: %s", entry)
                continue

    def _scan_dotfiles(self) -> Iterator[ScannedConfig]:
        """Scan known shell dotfiles in the home directory.

        Checks a predefined list of shell-related dotfiles in the
        user's home directory. Only existing files are evaluated.

        Yields:
            ScannedConfig for each orphaned dotfile.
        """
        home = Path.home()

        for dotfile_name in _SHELL_DOTFILES:
            dotfile = home / dotfile_name
            if not dotfile.exists() and not dotfile.is_symlink():
                continue

            try:
                yield from self._process_entry(dotfile)
            except PermissionError:
                logger.warning("Permission denied accessing: %s", dotfile)
                continue

    def _process_entry(self, entry: Path) -> Iterator[ScannedConfig]:
        """Process a single filesystem entry for orphan detection.

        Checks protection status and ownership, then yields a
        ScannedConfig if the entry is classified as an orphan.

        Args:
            entry: Path to evaluate.

        Yields:
            ScannedConfig if the entry is an orphan.
        """
        name = entry.name
        path_str = str(entry)

        if is_protected_config(path_str):
            return

        # Detect dead symlinks before checking ownership
        if entry.is_symlink() and not entry.exists():
            yield ScannedConfig(
                path=path_str,
                config_type=ConfigType.FILE,
                status=ConfigStatus.ORPHAN,
                size_bytes=self._get_size(entry),
                mtime=self._get_mtime(entry),
                orphan_reason=ConfigOrphanReason.DEAD_LINK,
                confidence=_CONFIDENCE_FILE,
                description=None,
            )
            return

        config_type = self._get_config_type(entry)
        status = self._check_ownership(name, entry)

        if status != ConfigStatus.ORPHAN:
            return

        orphan_reason = ConfigOrphanReason.NO_PACKAGE_MATCH
        confidence = (
            _CONFIDENCE_DIRECTORY if config_type == ConfigType.DIRECTORY else _CONFIDENCE_FILE
        )

        yield ScannedConfig(
            path=path_str,
            config_type=config_type,
            status=ConfigStatus.ORPHAN,
            size_bytes=self._get_size(entry),
            mtime=self._get_mtime(entry),
            orphan_reason=orphan_reason,
            confidence=confidence,
            description=None,
        )

    def _check_ownership(self, name: str, path: Path) -> ConfigStatus:
        """Determine if config is owned by an installed package or app.

        Checks in order:
        1. ``dpkg -S <path>`` -- OWNED if match
        2. App name matching (dpkg + flatpak + snap names) -- OWNED if match
        3. Otherwise -- ORPHAN

        Args:
            name: Entry name (basename of the path).
            path: Full path to the configuration entry.

        Returns:
            ConfigStatus indicating ownership classification.
        """
        if self._dpkg_owns_path(path):
            return ConfigStatus.OWNED

        if self._app_name_matches(name):
            return ConfigStatus.OWNED

        return ConfigStatus.ORPHAN

    def _dpkg_owns_path(self, path: Path) -> bool:
        """Check if dpkg -S reports ownership of this path.

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
        """Check if name matches any installed app (dpkg/flatpak/snap).

        Normalizes the name by lowercasing and stripping dots and
        dashes, then compares against installed package names and
        application identifiers (flatpak reverse-DNS components,
        snap names).

        Args:
            name: Directory or file name to check.

        Returns:
            True if the name matches an installed app.
        """
        name_normalized = name.lower().replace(".", "").replace("-", "")

        # Check against dpkg package names
        packages = self._get_installed_packages()
        for pkg in packages:
            pkg_normalized = pkg.lower().replace(".", "").replace("-", "")
            if name_normalized == pkg_normalized:
                return True

        # Check against flatpak + snap app names
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
            # Normalized comparison
            app_normalized = app_lower.replace(".", "").replace("-", "")
            if name_normalized == app_normalized:
                return True

        return False

    def _get_installed_packages(self) -> set[str]:
        """Get set of installed dpkg package names (lazy-cached).

        Runs ``dpkg-query --showformat='${Package}\\n' -W`` and
        caches the result for the duration of the scan.

        Returns:
            Set of installed package names.
        """
        if self._packages_cache is not None:
            return self._packages_cache

        try:
            result = run_command(
                ["dpkg-query", "--showformat=${Package}\n", "-W"],
                timeout=30.0,
            )
            if result.success:
                self._packages_cache = {
                    line.strip() for line in result.stdout.strip().split("\n") if line.strip()
                }
            else:
                logger.warning("dpkg-query failed: %s", result.stderr.strip())
                self._packages_cache = set()
        except (FileNotFoundError, OSError) as exc:
            logger.warning("Cannot query installed packages: %s", exc)
            self._packages_cache = set()

        return self._packages_cache

    def _get_installed_apps(self) -> set[str]:
        """Get set of installed app names from flatpak + snap (lazy-cached).

        Queries both flatpak and snap for installed applications.
        If either is unavailable, its contribution is an empty set.

        Returns:
            Set of installed application identifiers.
        """
        if self._apps_cache is not None:
            return self._apps_cache

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

        self._apps_cache = apps
        return self._apps_cache

    def _get_config_type(self, path: Path) -> ConfigType:
        """Determine ConfigType for a path.

        Args:
            path: Path to classify.

        Returns:
            ConfigType.DIRECTORY for directories, ConfigType.FILE otherwise.
        """
        if path.is_dir() and not path.is_symlink():
            return ConfigType.DIRECTORY
        return ConfigType.FILE

    def _get_size(self, path: Path) -> int | None:
        """Get size in bytes (recursive for directories).

        For files and symlinks, returns the lstat size. For directories,
        returns the sum of all files recursively.

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

    def _reset_caches(self) -> None:
        """Reset all caches. Called at start of each scan()."""
        self._packages_cache = None
        self._apps_cache = None
        self._dpkg_cache = {}
