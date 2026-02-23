"""Package/app ownership checking and path metadata for orphan scanners.

Provides shared functions for determining whether filesystem or config
paths are owned by installed packages (dpkg, flatpak, snap). Caches
are passed explicitly to avoid hidden state.
"""

import logging
from datetime import UTC, datetime
from pathlib import Path

from popctl.domain.models import PathType
from popctl.utils.shell import run_command

logger = logging.getLogger(__name__)


def classify_path_type(path: Path) -> PathType:
    """Classify a filesystem path into a PathType.

    Handles regular files, directories, symlinks (live and dead),
    and falls back to FILE for special files (sockets, FIFOs).

    Args:
        path: Path to classify.

    Returns:
        The PathType classification.
    """
    if path.is_symlink():
        return PathType.DEAD_SYMLINK if not path.exists() else PathType.SYMLINK
    if path.is_dir():
        return PathType.DIRECTORY
    return PathType.FILE


def dpkg_owns_path(path: Path, cache: dict[str, bool]) -> bool:
    """Check if dpkg knows about files in this path (cached).

    Runs ``dpkg -S <path>`` and caches the result. Returns True
    if the return code is 0 (at least one package owns the path).

    Args:
        path: Filesystem path to check.
        cache: Mutable cache dict for storing results.

    Returns:
        True if a package owns the path, False otherwise.
    """
    path_str = str(path)
    if path_str in cache:
        return cache[path_str]

    try:
        result = run_command(["dpkg", "-S", path_str], timeout=10.0)
        owned = result.success
    except (FileNotFoundError, OSError):
        owned = False

    cache[path_str] = owned
    return owned


def get_installed_packages() -> set[str]:
    """Get installed dpkg package names.

    Runs ``dpkg-query -f '${Package}\\n' -W`` and returns the result.

    Returns:
        Set of installed package names.
    """
    try:
        result = run_command(
            ["dpkg-query", "-f", "${Package}\n", "-W"],
            timeout=30.0,
        )
        if result.success:
            return {line.strip() for line in result.stdout.strip().split("\n") if line.strip()}
        logger.warning("dpkg-query failed: %s", result.stderr.strip())
    except (FileNotFoundError, OSError) as exc:
        logger.warning("Cannot query installed packages: %s", exc)

    return set()


def get_installed_apps() -> set[str]:
    """Get installed flatpak + snap app names.

    Queries both flatpak and snap for installed applications.
    If either is unavailable, its contribution is an empty set.

    Returns:
        Set of installed application identifiers.
    """
    apps: set[str] = set()

    # Flatpak apps
    try:
        result = run_command(
            ["flatpak", "list", "--app", "--columns=application"],
            timeout=15.0,
        )
        if result.success:
            apps.update(line.strip() for line in result.stdout.strip().split("\n") if line.strip())
    except (FileNotFoundError, OSError) as exc:
        logger.debug("flatpak not available: %s", exc)

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
    except (FileNotFoundError, OSError) as exc:
        logger.debug("snap not available: %s", exc)

    return apps


def app_name_matches(name: str, apps: set[str]) -> bool:
    """Check if name matches any installed app name.

    Performs case-insensitive comparison against installed app names.
    Also checks if the directory name appears as a component in
    reverse-DNS app IDs (e.g., "org.mozilla.firefox" contains "firefox").

    Args:
        name: Directory or file name to check.
        apps: Set of installed application identifiers.

    Returns:
        True if the name matches an installed app.
    """
    name_lower = name.lower()

    for app in apps:
        app_lower = app.lower()
        # Exact match
        if name_lower == app_lower:
            return True
        # Reverse-DNS component match (e.g., "firefox" in "org.mozilla.firefox")
        if "." in app_lower and name_lower in app_lower.split("."):
            return True

    return False


def get_path_size(path: Path) -> int | None:
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

        return None  # special file (socket, FIFO, device node)
    except OSError:
        return None


def get_path_mtime(path: Path) -> str | None:
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
