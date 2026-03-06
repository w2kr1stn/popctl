"""Backup restore: fetch, decrypt, extract, restore files and packages.

Decrypts an age-encrypted tar.zst archive, extracts files to their
original home-relative locations, and installs/removes packages
using existing popctl operators.
"""

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from popctl.backup.backup import BackupError, is_rclone_remote
from popctl.core.manifest import load_manifest
from popctl.core.paths import get_backups_dir, get_config_dir, get_state_dir
from popctl.models.backup import BackupMetadata
from popctl.utils.shell import command_exists, run_command

logger = logging.getLogger(__name__)


def _fetch_backup(source: str) -> Path:
    """Fetch backup file to a local path.

    For local sources, validates the file exists.
    For rclone remotes, downloads to a temporary directory.

    Returns:
        Path to the local backup file.

    Raises:
        BackupError: If the source cannot be accessed.
    """
    if is_rclone_remote(source):
        if not command_exists("rclone"):
            raise BackupError(
                "rclone is not installed. Install it via 'sudo apt install rclone' "
                "or from https://rclone.org/"
            )
        tmpdir = Path(tempfile.mkdtemp(prefix="popctl-restore-"))
        filename = source.rsplit("/", 1)[-1] if "/" in source else source.rsplit(":", 1)[-1]
        dest = tmpdir / filename
        result = run_command(
            ["rclone", "copyto", source, str(dest)],
            timeout=1800.0,
        )
        if not result.success:
            raise BackupError(f"rclone download failed: {result.stderr.strip()}")
        return dest

    path = Path(source).expanduser().resolve()
    if not path.exists():
        raise BackupError(f"Backup file not found: {path}")
    return path


def _decrypt_and_decompress(
    backup_path: Path,
    output_dir: Path,
    identity: str | None,
) -> None:
    """Decrypt with age and decompress with zstd, extract tar to output_dir.

    Pipeline: age -d -i <identity> <file> | zstd -d | tar xf - -C <output_dir>

    Raises:
        BackupError: If decryption or decompression fails.
    """
    if not command_exists("age"):
        raise BackupError("age is not installed.")
    if not command_exists("zstd"):
        raise BackupError("zstd is not installed.")

    # Resolve identity file
    identity_args: list[str] = []
    if identity:
        identity_path = str(Path(identity).expanduser())
        identity_args = ["-i", identity_path]

    age_proc: subprocess.Popen[bytes] | None = None
    zstd_proc: subprocess.Popen[bytes] | None = None
    tar_proc: subprocess.Popen[bytes] | None = None
    try:
        age_proc = subprocess.Popen(
            ["age", "-d", *identity_args, str(backup_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        zstd_proc = subprocess.Popen(
            ["zstd", "-d", "-c"],
            stdin=age_proc.stdout,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        tar_proc = subprocess.Popen(
            ["tar", "xf", "-", "-C", str(output_dir)],
            stdin=zstd_proc.stdout,
            stderr=subprocess.PIPE,
        )

        # Allow upstream processes to receive SIGPIPE
        if age_proc.stdout:
            age_proc.stdout.close()
        if zstd_proc.stdout:
            zstd_proc.stdout.close()

        _, tar_stderr = tar_proc.communicate(timeout=600)
        zstd_proc.wait(timeout=600)
        age_proc.wait(timeout=600)

        if age_proc.returncode != 0:
            raise BackupError("age decryption failed — wrong identity key?")
        if zstd_proc.returncode != 0:
            raise BackupError("zstd decompression failed")
        if tar_proc.returncode != 0:
            raise BackupError(f"tar extraction failed: {tar_stderr.decode().strip()}")

    except subprocess.TimeoutExpired as e:
        for proc in (age_proc, zstd_proc, tar_proc):
            if proc:
                proc.kill()
        raise BackupError("Restore pipeline timed out") from e
    except FileNotFoundError as e:
        raise BackupError(f"Required binary not found: {e}") from e


def read_backup_metadata(
    source: str,
    identity: str | None = None,
) -> BackupMetadata:
    """Read only metadata.json from a backup archive.

    Decrypts and extracts the full archive to a temp dir, then reads
    metadata.json. This is simpler than partial extraction and the
    temp dir is cleaned up automatically.

    Args:
        source: Local path or rclone remote to backup file.
        identity: Path to age identity (private key) file.

    Returns:
        BackupMetadata from the archive.

    Raises:
        BackupError: If the backup cannot be read.
    """
    backup_path = _fetch_backup(source)

    with tempfile.TemporaryDirectory(prefix="popctl-info-") as tmpdir:
        output_dir = Path(tmpdir)
        _decrypt_and_decompress(backup_path, output_dir, identity)

        metadata_file = output_dir / "metadata.json"
        if not metadata_file.exists():
            raise BackupError("Invalid backup: metadata.json not found in archive")

        try:
            return BackupMetadata.from_json(metadata_file.read_text())
        except (KeyError, ValueError) as e:
            raise BackupError(f"Invalid metadata.json: {e}") from e


def _restore_popctl_state(staging_dir: Path) -> int:
    """Restore popctl state files from staging directory.

    Returns:
        Number of files restored.
    """
    config_dir = get_config_dir()
    state_dir = get_state_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)

    popctl_dir = staging_dir / "files" / "popctl"
    if not popctl_dir.exists():
        return 0

    count = 0
    mapping = {
        "manifest.toml": config_dir / "manifest.toml",
        "advisor.toml": config_dir / "advisor.toml",
        "history.jsonl": state_dir / "history.jsonl",
        "advisor-memory.md": state_dir / "advisor" / "memory.md",
    }

    for filename, dest in mapping.items():
        src = popctl_dir / filename
        if src.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(src), str(dest))
            count += 1
            logger.debug("Restored popctl state: %s", dest)

    return count


def _restore_home_files(staging_dir: Path) -> int:
    """Restore home directory files from staging, rebasing to current $HOME.

    Preserves file permissions from the archive. Backs up existing files
    before overwriting.

    Returns:
        Number of files restored.
    """
    home_dir = staging_dir / "files" / "home"
    if not home_dir.exists():
        return 0

    current_home = Path.home()
    count = 0

    for src in sorted(home_dir.rglob("*")):
        if not src.is_file():
            continue

        rel = src.relative_to(home_dir)
        dest = current_home / rel

        # Create parent directories
        dest.parent.mkdir(parents=True, exist_ok=True)

        try:
            shutil.copy2(str(src), str(dest))
            count += 1
        except (OSError, PermissionError) as e:
            logger.warning("Could not restore %s: %s", dest, e)

    return count


def _fix_sensitive_permissions() -> None:
    """Fix permissions on sensitive directories after restore."""
    home = Path.home()

    ssh_dir = home / ".ssh"
    if ssh_dir.exists():
        ssh_dir.chmod(0o700)
        for child in ssh_dir.iterdir():
            if child.is_file():
                child.chmod(0o600)

    gnupg_dir = home / ".gnupg"
    if gnupg_dir.exists():
        gnupg_dir.chmod(0o700)


def _install_packages() -> tuple[int, int]:
    """Install packages from the restored manifest.

    Uses existing popctl operators to install packages.keep and
    remove packages.remove.

    Returns:
        Tuple of (installed_count, failed_count).
    """
    from popctl.core.diff import DiffEntry, DiffResult, DiffType, diff_to_actions
    from popctl.core.executor import execute_actions, record_actions_to_history
    from popctl.core.manifest import ManifestError
    from popctl.operators import get_available_operators

    try:
        manifest = load_manifest()
    except ManifestError as e:
        logger.warning("Could not load restored manifest: %s", e)
        return 0, 0

    # Build MISSING entries for all packages.keep (they're all missing on fresh install)
    missing: list[DiffEntry] = []
    for name, entry in manifest.packages.keep.items():
        missing.append(DiffEntry(
            name=name,
            source=entry.source,
            diff_type=DiffType.MISSING,
        ))

    # Build EXTRA entries for all packages.remove (they might be pre-installed)
    extra: list[DiffEntry] = []
    for name, entry in manifest.packages.remove.items():
        extra.append(DiffEntry(
            name=name,
            source=entry.source,
            diff_type=DiffType.EXTRA,
        ))

    diff_result = DiffResult(
        new=(),
        missing=tuple(missing),
        extra=tuple(extra),
    )

    actions = diff_to_actions(diff_result)
    if not actions:
        return 0, 0

    operators = get_available_operators()
    results = execute_actions(actions, operators)

    # Record to history
    if results:
        record_actions_to_history(results, command="popctl backup restore")

    installed = sum(1 for r in results if r.success)
    failed = sum(1 for r in results if r.failed)
    return installed, failed


def list_backups(target: str = "") -> list[str]:
    """List available backup files at the given target.

    Args:
        target: Local directory or rclone remote. Empty uses default.

    Returns:
        Sorted list of backup filenames.
    """
    if target and is_rclone_remote(target):
        if not command_exists("rclone"):
            raise BackupError("rclone is not installed.")
        result = run_command(
            ["rclone", "lsf", target, "--include", "popctl-backup-*.tar.zst.age"],
            timeout=60.0,
        )
        if not result.success:
            raise BackupError(f"rclone list failed: {result.stderr.strip()}")
        return sorted(result.stdout.strip().splitlines()) if result.stdout.strip() else []

    target_dir = Path(target) if target else get_backups_dir()
    if not target_dir.exists():
        return []
    return sorted(
        p.name for p in target_dir.glob("popctl-backup-*.tar.zst.age")
    )


def restore_backup(
    source: str,
    identity: str | None = None,
    *,
    files_only: bool = False,
    packages_only: bool = False,
) -> dict[str, int]:
    """Restore a backup archive to the current system.

    Args:
        source: Local path or rclone remote to backup file.
        identity: Path to age identity (private key) file.
        files_only: Only restore files, skip package installation.
        packages_only: Only install packages, skip file restoration.

    Returns:
        Dict with counts: popctl_state, home_files, packages_installed, packages_failed.

    Raises:
        BackupError: If restore fails.
    """
    backup_path = _fetch_backup(source)

    with tempfile.TemporaryDirectory(prefix="popctl-restore-") as tmpdir:
        staging_dir = Path(tmpdir)
        _decrypt_and_decompress(backup_path, staging_dir, identity)

        # Verify archive contains metadata
        if not (staging_dir / "metadata.json").exists():
            raise BackupError("Invalid backup: metadata.json not found")

        counts: dict[str, int] = {
            "popctl_state": 0,
            "home_files": 0,
            "packages_installed": 0,
            "packages_failed": 0,
        }

        if not packages_only:
            # Always restore popctl state first (manifest needed for package install)
            counts["popctl_state"] = _restore_popctl_state(staging_dir)

            # Restore home directory files
            counts["home_files"] = _restore_home_files(staging_dir)

            # Fix sensitive file permissions
            _fix_sensitive_permissions()

        if not files_only:
            # Ensure manifest is available (restore it if packages_only)
            if packages_only:
                counts["popctl_state"] = _restore_popctl_state(staging_dir)

            installed, failed = _install_packages()
            counts["packages_installed"] = installed
            counts["packages_failed"] = failed

    return counts
