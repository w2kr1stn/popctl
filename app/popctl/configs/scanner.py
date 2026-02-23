"""Config scanner for orphaned configuration directories and dotfiles.

Scans ~/.config/ top-level entries and shell dotfiles in the user's
home directory for configurations that are not owned by any installed
package manager (dpkg, flatpak, snap). Uses dpkg -S cross-referencing
and app name matching for orphan detection.
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
    get_installed_packages,
    get_path_mtime,
    get_path_size,
)
from popctl.domain.protected import is_protected

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
        self._normalized_packages: set[str] | None = None
        self._normalized_apps: set[str] | None = None

    def scan(self) -> Iterator[ScannedEntry]:
        """Scan for orphaned configs. Yields only ORPHAN-status entries.

        Scans ``~/.config/`` top-level entries and known shell dotfiles
        in the home directory. Protected and owned entries are silently
        skipped.

        Yields:
            ScannedEntry instances for orphaned configuration entries.
        """
        self._reset_caches()
        yield from self._scan_config_dir()
        yield from self._scan_dotfiles()

    def _scan_config_dir(self) -> Iterator[ScannedEntry]:
        """Scan ~/.config/ top-level entries only (not recursive).

        Iterates over direct children of ``~/.config/``, skipping
        protected entries and entries owned by installed packages.

        Yields:
            ScannedEntry for each orphaned entry in ``~/.config/``.
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

    def _scan_dotfiles(self) -> Iterator[ScannedEntry]:
        """Scan known shell dotfiles in the home directory.

        Checks a predefined list of shell-related dotfiles in the
        user's home directory. Only existing files are evaluated.

        Yields:
            ScannedEntry for each orphaned dotfile.
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

    def _process_entry(self, entry: Path) -> Iterator[ScannedEntry]:
        """Process a single filesystem entry for orphan detection.

        Checks protection status and ownership, then yields a
        ScannedEntry if the entry is classified as an orphan.

        Args:
            entry: Path to evaluate.

        Yields:
            ScannedEntry if the entry is an orphan.
        """
        name = entry.name
        path_str = str(entry)

        if is_protected(path_str, "configs"):
            return

        # Detect dead symlinks before checking ownership
        if entry.is_symlink() and not entry.exists():
            yield ScannedEntry(
                path=path_str,
                path_type=PathType.DEAD_SYMLINK,
                status=OrphanStatus.ORPHAN,
                size_bytes=get_path_size(entry),
                mtime=get_path_mtime(entry),
                parent_target=None,
                orphan_reason=OrphanReason.DEAD_LINK,
                confidence=_CONFIDENCE_FILE,
            )
            return

        path_type = classify_path_type(entry)
        status = self._check_ownership(name, entry)

        if status != OrphanStatus.ORPHAN:
            return

        orphan_reason = OrphanReason.NO_PACKAGE_MATCH
        confidence = _CONFIDENCE_DIRECTORY if path_type == PathType.DIRECTORY else _CONFIDENCE_FILE

        yield ScannedEntry(
            path=path_str,
            path_type=path_type,
            status=OrphanStatus.ORPHAN,
            size_bytes=get_path_size(entry),
            mtime=get_path_mtime(entry),
            parent_target=None,
            orphan_reason=orphan_reason,
            confidence=confidence,
        )

    def _check_ownership(self, name: str, path: Path) -> OrphanStatus:
        """Determine if config is owned by an installed package or app.

        Checks in order:
        1. ``dpkg -S <path>`` -- OWNED if match
        2. App name matching (flatpak + snap) -- OWNED if match
        3. Normalized name matching (dpkg packages + apps) -- OWNED if match
        4. Otherwise -- ORPHAN

        Args:
            name: Entry name (basename of the path).
            path: Full path to the configuration entry.

        Returns:
            OrphanStatus indicating ownership classification.
        """
        if dpkg_owns_path(path, self._dpkg_cache):
            return OrphanStatus.OWNED

        apps = self._ensure_apps_cache()
        if app_name_matches(name, apps):
            return OrphanStatus.OWNED

        if self._normalized_name_match(name):
            return OrphanStatus.OWNED

        return OrphanStatus.ORPHAN

    def _normalized_name_match(self, name: str) -> bool:
        """Config-specific: normalized comparison against packages and apps.

        Lowercases and strips dots/dashes from the name, then compares
        against dpkg package names and flatpak/snap app identifiers.
        This catches cases like ``libreoffice`` matching ``libre-office``.

        Args:
            name: Directory or file name to check.

        Returns:
            True if the normalized name matches any installed package or app.
        """
        name_normalized = name.lower().replace(".", "").replace("-", "")
        self._ensure_packages_cache()
        assert self._normalized_packages is not None
        if name_normalized in self._normalized_packages:
            return True
        self._ensure_apps_cache()
        assert self._normalized_apps is not None
        return name_normalized in self._normalized_apps

    def _ensure_packages_cache(self) -> set[str]:
        """Ensure packages cache is populated and return it."""
        if self._packages_cache is None:
            self._packages_cache = get_installed_packages()
            self._normalized_packages = {
                p.lower().replace(".", "").replace("-", "") for p in self._packages_cache
            }
        return self._packages_cache

    def _ensure_apps_cache(self) -> set[str]:
        """Ensure apps cache is populated and return it."""
        if self._apps_cache is None:
            self._apps_cache = get_installed_apps()
            self._normalized_apps = {
                a.lower().replace(".", "").replace("-", "") for a in self._apps_cache
            }
        return self._apps_cache

    def _reset_caches(self) -> None:
        """Reset all caches. Called at start of each scan()."""
        self._packages_cache = None
        self._apps_cache = None
        self._dpkg_cache = {}
        self._normalized_packages = None
        self._normalized_apps = None
