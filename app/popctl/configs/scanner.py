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
    def __init__(self) -> None:
        self._packages_cache: set[str] | None = None
        self._apps_cache: set[str] | None = None
        self._dpkg_cache: dict[str, bool] = {}
        self._normalized_packages: set[str] | None = None
        self._normalized_apps: set[str] | None = None

    def scan(self) -> Iterator[ScannedEntry]:
        self._reset_caches()
        yield from self._scan_config_dir()
        yield from self._scan_dotfiles()

    def _scan_config_dir(self) -> Iterator[ScannedEntry]:
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
        """Checks: 1) dpkg -S, 2) flatpak/snap app name, 3) normalized name match."""
        if dpkg_owns_path(path, self._dpkg_cache):
            return OrphanStatus.OWNED

        apps = self._ensure_apps_cache()
        if app_name_matches(name, apps):
            return OrphanStatus.OWNED

        if self._normalized_name_match(name):
            return OrphanStatus.OWNED

        return OrphanStatus.ORPHAN

    def _normalized_name_match(self, name: str) -> bool:
        """Strips dots/dashes for fuzzy matching (e.g. "libreoffice" vs "libre-office")."""
        name_normalized = name.lower().replace(".", "").replace("-", "")
        self._ensure_packages_cache()
        assert self._normalized_packages is not None
        if name_normalized in self._normalized_packages:
            return True
        self._ensure_apps_cache()
        assert self._normalized_apps is not None
        return name_normalized in self._normalized_apps

    def _ensure_packages_cache(self) -> set[str]:
        if self._packages_cache is None:
            self._packages_cache = get_installed_packages()
            self._normalized_packages = {
                p.lower().replace(".", "").replace("-", "") for p in self._packages_cache
            }
        return self._packages_cache

    def _ensure_apps_cache(self) -> set[str]:
        if self._apps_cache is None:
            self._apps_cache = get_installed_apps()
            self._normalized_apps = {
                a.lower().replace(".", "").replace("-", "") for a in self._apps_cache
            }
        return self._apps_cache

    def _reset_caches(self) -> None:
        self._packages_cache = None
        self._apps_cache = None
        self._dpkg_cache = {}
        self._normalized_packages = None
        self._normalized_apps = None
