"""Backup creation: collect files via home walk, build archive, store.

Walks the entire home directory (excluding symlink dirs, caches, and
build artifacts), plus popctl state files. Creates a tar.zst archive
encrypted with age.
"""

import io
import logging
import socket
import subprocess
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from popctl import __version__
from popctl.core.paths import get_backups_dir, get_config_dir, get_state_dir
from popctl.models.backup import BackupMetadata
from popctl.utils.shell import command_exists, run_command

logger = logging.getLogger(__name__)

# Directory names to exclude during home walk (case-sensitive)
_EXCLUDED_DIRS: frozenset[str] = frozenset({
    "__pycache__",
    ".cache",
    ".local/share/Trash",
    "node_modules",
    ".venv",
    ".env",
    ".tox",
    ".nox",
    ".ruff_cache",
    ".pytest_cache",
    ".mypy_cache",
    ".pyright",
    ".uv",
    ".cargo/registry",
    ".npm",
    ".yarn",
})

# Top-level dir names under ~/ to always exclude
_EXCLUDED_TOPLEVEL: frozenset[str] = frozenset({
    "snap",  # managed by snapd
    ".local/share/flatpak",  # managed by flatpak
})


class BackupError(Exception):
    """Base exception for backup operations."""


def _should_exclude_dir(rel_path: Path) -> bool:
    """Check if a directory should be excluded from the home walk.

    Excludes symlink directories (external drive mounts), known cache/build
    directories, and .git/objects (large, reproducible).
    """
    rel_str = str(rel_path)

    # Exact matches against excluded set
    if rel_str in _EXCLUDED_DIRS or rel_str in _EXCLUDED_TOPLEVEL:
        return True

    # Any path component matching an excluded dir name
    for part in rel_path.parts:
        if part in _EXCLUDED_DIRS:
            return True

    # .git/objects and .git/modules/*/objects are large and reproducible
    return (".git/" in rel_str or rel_str.startswith(".git/")) and "/objects" in rel_str


def _walk_home() -> list[tuple[Path, str]]:
    """Walk the home directory and collect files for backup.

    Skips symlink directories (external drive mounts), cache/build dirs,
    and other excludable content. Follows symlink files but not symlink
    directories.

    Returns:
        List of (absolute_path, archive_path) tuples.
    """
    home = Path.home()
    files: list[tuple[Path, str]] = []

    for item in sorted(home.iterdir()):
        rel = item.relative_to(home)

        # Skip symlink directories (external drive mounts)
        if item.is_symlink() and item.is_dir():
            logger.debug("Skipping symlink directory: %s", item)
            continue

        if item.is_file() or (item.is_symlink() and not item.is_dir()):
            # Top-level file (dotfile, etc.)
            files.append((item, f"files/home/{rel}"))
        elif item.is_dir():
            # Check exclusion before recursing
            if _should_exclude_dir(rel):
                logger.debug("Excluding directory: %s", rel)
                continue
            _walk_dir(item, home, files)

    return files


def _walk_dir(
    directory: Path,
    home: Path,
    files: list[tuple[Path, str]],
) -> None:
    """Recursively walk a directory, adding files to the collection."""
    try:
        entries = sorted(directory.iterdir())
    except PermissionError:
        logger.debug("Permission denied: %s", directory)
        return

    for item in entries:
        rel = item.relative_to(home)

        if item.is_symlink() and item.is_dir():
            # Skip symlink directories
            continue

        if item.is_dir():
            if _should_exclude_dir(rel):
                logger.debug("Excluding directory: %s", rel)
                continue
            _walk_dir(item, home, files)
        elif item.is_file() or item.is_symlink():
            files.append((item, f"files/home/{rel}"))


def collect_backup_files() -> list[tuple[Path, str]]:
    """Collect all files to include in the backup.

    Combines popctl state files with a full home directory walk.
    Deduplicates by resolved path.

    Returns:
        Deduplicated list of (source_path, archive_path) tuples.
    """
    seen: set[Path] = set()
    files: list[tuple[Path, str]] = []

    def _add(absolute: Path, archive_path: str) -> None:
        resolved = absolute.resolve()
        if resolved in seen or not resolved.exists():
            return
        seen.add(resolved)
        files.append((resolved, archive_path))

    # 1. popctl state files (separate category for easy restore)
    config_dir = get_config_dir()
    state_dir = get_state_dir()

    for name in ("manifest.toml", "advisor.toml"):
        _add(config_dir / name, f"files/popctl/{name}")

    _add(state_dir / "history.jsonl", "files/popctl/history.jsonl")
    _add(state_dir / "advisor" / "memory.md", "files/popctl/advisor-memory.md")

    # 2. Home directory walk
    for abs_path, archive_path in _walk_home():
        resolved = abs_path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            files.append((abs_path, archive_path))

    return files


def _build_metadata() -> BackupMetadata:
    """Create backup metadata from current system state."""
    return BackupMetadata(
        created=datetime.now(UTC).isoformat(),
        hostname=socket.gethostname(),
        popctl_version=__version__,
    )



def _stream_backup(
    files: list[tuple[Path, str]],
    metadata: BackupMetadata,
    output_path: Path,
    recipient: str,
) -> None:
    """Stream tar | zstd | age directly to output without intermediate files.

    Writes tar data into a zstd | age subprocess pipeline, avoiding
    the need to store the full uncompressed tar on disk.

    Raises:
        BackupError: If any stage of the pipeline fails.
    """
    recipient_expanded = str(Path(recipient).expanduser())
    if Path(recipient_expanded).is_file():
        age_args = ["age", "-R", recipient_expanded]
    else:
        age_args = ["age", "-r", recipient]

    zstd_proc: subprocess.Popen[bytes] | None = None
    age_proc: subprocess.Popen[bytes] | None = None
    try:
        zstd_proc = subprocess.Popen(
            ["zstd", "-3", "-c"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        age_proc = subprocess.Popen(
            [*age_args, "-o", str(output_path)],
            stdin=zstd_proc.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Allow zstd to receive SIGPIPE if age exits early
        if zstd_proc.stdout:
            zstd_proc.stdout.close()

        # Stream tar data into zstd's stdin
        assert zstd_proc.stdin is not None  # noqa: S101
        with tarfile.open(fileobj=zstd_proc.stdin, mode="w|") as tar:
            # metadata.json as first entry
            metadata_bytes = metadata.to_json().encode()
            info = tarfile.TarInfo(name="metadata.json")
            info.size = len(metadata_bytes)
            tar.addfile(info, io.BytesIO(metadata_bytes))

            for source_path, archive_path in files:
                try:
                    tar.add(str(source_path), arcname=archive_path)
                except (OSError, PermissionError) as e:
                    logger.warning("Could not add %s: %s", source_path, e)

        # Close zstd stdin to signal EOF
        zstd_proc.stdin.close()

        _, age_stderr = age_proc.communicate(timeout=3600)
        zstd_proc.wait(timeout=3600)

        if zstd_proc.returncode != 0:
            zstd_err = zstd_proc.stderr.read().decode().strip() if zstd_proc.stderr else ""
            raise BackupError(f"zstd compression failed: {zstd_err or '(no details)'}")
        if age_proc.returncode != 0:
            raise BackupError(f"age encryption failed: {age_stderr.decode().strip()}")

    except subprocess.TimeoutExpired as e:
        if zstd_proc:
            zstd_proc.kill()
        if age_proc:
            age_proc.kill()
        raise BackupError("Backup pipeline timed out (1h limit)") from e
    except FileNotFoundError as e:
        raise BackupError(f"Required binary not found: {e}") from e


def is_rclone_remote(target: str) -> bool:
    """Check if target looks like an rclone remote (contains ':' not at start)."""
    return ":" in target and not target.startswith("/")



def _store_rclone(archive_path: Path, remote: str) -> str:
    """Upload archive to rclone remote.

    Raises:
        BackupError: If rclone is not available or upload fails.
    """
    if not command_exists("rclone"):
        raise BackupError(
            "rclone is not installed. Install it via 'sudo apt install rclone' "
            "or from https://rclone.org/"
        )
    remote_path = remote.rstrip("/") + "/" + archive_path.name
    result = run_command(
        ["rclone", "copyto", str(archive_path), remote_path],
        timeout=1800.0,
    )
    if not result.success:
        raise BackupError(f"rclone upload failed: {result.stderr.strip()}")
    return remote_path


def _prune_old_backups(
    target_dir: Path, max_backups: int, *, progress: bool = True
) -> None:
    """Delete oldest backups exceeding max_backups in a local directory."""
    from popctl.utils.formatting import print_info

    pattern = "popctl-backup-*.tar.zst.age"
    backups = sorted(target_dir.glob(pattern))

    if len(backups) <= max_backups:
        return

    to_delete = backups[: len(backups) - max_backups]
    for old in to_delete:
        try:
            old.unlink()
            if progress:
                print_info(f"Pruned old backup: {old.name}")
        except OSError as e:
            logger.warning("Could not delete old backup %s: %s", old, e)


def create_backup(
    target: str = "",
    recipient: str | None = None,
    *,
    progress: bool = True,
) -> str:
    """Create an encrypted backup archive.

    Resolves defaults from ``~/.config/popctl/backup.toml`` when CLI
    arguments are not provided.

    Args:
        target: Destination path or rclone remote. Empty string uses config/default.
        recipient: age public key or recipients file path.
            Falls back to backup.toml ``recipients``, then
            ``~/.config/popctl/backup.age-recipients``.
        progress: If True, print status messages during each phase.

    Returns:
        Final storage path (local path or rclone remote path).

    Raises:
        BackupError: If prerequisites are missing or backup creation fails.
    """
    from popctl.backup.config import load_backup_config
    from popctl.utils.formatting import print_info

    if not command_exists("age"):
        raise BackupError(
            "age is not installed. Install it via 'sudo apt install age' "
            "or from https://filippo.io/age"
        )
    if not command_exists("zstd"):
        raise BackupError("zstd is not installed. Install it via 'sudo apt install zstd'")

    # Load config defaults
    config = load_backup_config()

    # Resolve target: CLI flag → config → built-in default
    if not target:
        target = config.target

    # Resolve recipient: CLI flag → config → well-known path
    if recipient is None:
        if config.recipients:
            recipient = config.recipients
        else:
            default_recipients = get_config_dir() / "backup.age-recipients"
            if default_recipients.exists():
                recipient = str(default_recipients)
            else:
                raise BackupError(
                    "No age recipient specified. Use --recipient, configure "
                    "backup.toml, or create " + str(default_recipients)
                )

    # Collect files
    if progress:
        print_info("Collecting files...")
    files = collect_backup_files()
    total_size = sum(p.stat().st_size for p, _ in files if p.exists())
    if progress:
        print_info(
            f"Collected {len(files)} files ({total_size / 1024 / 1024:.1f} MB)"
        )

    metadata = _build_metadata()

    hostname = socket.gethostname()
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    archive_name = f"popctl-backup-{hostname}-{timestamp}.tar.zst.age"

    # For local targets, write directly to destination (no temp copy).
    # For rclone, write to a temp dir first, then upload.
    use_rclone = bool(target) and is_rclone_remote(target)
    target_dir = Path(target) if target and not use_rclone else get_backups_dir()

    if use_rclone:
        tmpdir_ctx = tempfile.TemporaryDirectory(prefix="popctl-backup-")
        tmpdir = tmpdir_ctx.__enter__()
        output_path = Path(tmpdir) / archive_name
    else:
        tmpdir_ctx = None
        target_dir.mkdir(parents=True, exist_ok=True)
        output_path = target_dir / archive_name

    try:
        if progress:
            dest_label = target if use_rclone else str(target_dir)
            print_info(f"Archiving, compressing, and encrypting to {dest_label}...")
        _stream_backup(files, metadata, output_path, recipient)

        if use_rclone:
            if progress:
                print_info(f"Uploading to {target}...")
            return _store_rclone(output_path, target)

        if config.max_backups > 0:
            _prune_old_backups(target_dir, config.max_backups, progress=progress)
        return str(output_path)
    finally:
        if tmpdir_ctx is not None:
            tmpdir_ctx.__exit__(None, None, None)
