import logging
from datetime import UTC, datetime
from pathlib import Path

from popctl.domain.models import PathType
from popctl.utils.shell import run_command

logger = logging.getLogger(__name__)


def classify_path_type(path: Path) -> PathType:
    """Falls back to FILE for special files (sockets, FIFOs, device nodes)."""
    if path.is_symlink():
        return PathType.DEAD_SYMLINK if not path.exists() else PathType.SYMLINK
    if path.is_dir():
        return PathType.DIRECTORY
    return PathType.FILE


def dpkg_owns_path(path: Path, cache: dict[str, bool]) -> bool:
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
    try:
        result = run_command(
            ["dpkg-query", "-f", "${Package}\n", "-W"],
            timeout=30.0,
        )
        if result.success:
            return {line.strip() for line in result.stdout.strip().split("\n") if line.strip()}
        logger.warning(
            "dpkg-query failed — ownership checks will be incomplete: %s",
            result.stderr.strip(),
        )
    except (FileNotFoundError, OSError) as exc:
        logger.warning("Cannot query installed packages: %s", exc)

    return set()


def get_installed_apps() -> set[str]:
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
        logger.warning("flatpak not available: %s", exc)

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
        logger.warning("snap not available: %s", exc)

    return apps


def app_name_matches(name: str, apps: set[str]) -> bool:
    """Case-insensitive match including reverse-DNS component matching.

    E.g. "firefox" matches "org.mozilla.firefox".
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
    try:
        stat = path.lstat()
        dt = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
        return dt.isoformat()
    except OSError:
        return None
