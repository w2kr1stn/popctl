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
        # Reset caches for each scan session
        self._installed_apps = None
        self._dpkg_cache = {}

        for target in self._targets:
            if not target.is_dir():
                continue
            yield from self._scan_directory(target)

    def _scan_directory(self, target: Path) -> Iterator[ScannedEntry]:
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
        """Checks: 1) protected list, 2) dpkg -S, 3) flatpak/snap app name."""
        if is_protected(str(path), "filesystem"):
            return OrphanStatus.PROTECTED

        if dpkg_owns_path(path, self._dpkg_cache):
            return OrphanStatus.OWNED

        apps = self._ensure_apps_cache()
        if app_name_matches(name, apps):
            return OrphanStatus.OWNED

        return OrphanStatus.ORPHAN

    def _ensure_apps_cache(self) -> set[str]:
        if self._installed_apps is None:
            self._installed_apps = get_installed_apps()
        return self._installed_apps

    def _calculate_confidence(self, target: str) -> float:
        """Higher confidence = safer to delete. .cache highest, /etc lowest."""
        if ".cache" in target:
            return 0.95
        if ".local/share" in target:
            return 0.75
        if target.startswith("/etc"):
            return 0.50

        return 0.60

    @staticmethod
    def _format_target(target: Path) -> str:
        try:
            home = Path.home()
            relative = target.relative_to(home)
            return f"~/{relative}"
        except ValueError:
            return str(target)

    @staticmethod
    def _determine_orphan_reason(path_type: PathType, target: Path) -> OrphanReason:
        if path_type == PathType.DEAD_SYMLINK:
            return OrphanReason.DEAD_LINK

        target_str = str(target)
        if ".cache" in target_str:
            return OrphanReason.STALE_CACHE

        return OrphanReason.NO_PACKAGE_MATCH
