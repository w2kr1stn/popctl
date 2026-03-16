import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from popctl.backup.backup import BackupError, is_rclone_remote
from popctl.core.manifest import load_manifest
from popctl.core.paths import get_backups_dir, get_config_dir, get_state_dir
from popctl.models.backup import BackupMetadata
from popctl.utils.formatting import print_warning
from popctl.utils.shell import command_exists, run_command

logger = logging.getLogger(__name__)


def _fetch_backup(source: str, target_dir: Path) -> Path:
    if is_rclone_remote(source):
        if not command_exists("rclone"):
            raise BackupError(
                "rclone is not installed. Install it via 'sudo apt install rclone' "
                "or from https://rclone.org/"
            )
        filename = source.rsplit("/", 1)[-1] if "/" in source else source.rsplit(":", 1)[-1]
        dest = target_dir / filename
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
            ["tar", "xf", "-", "-C", str(output_dir), "--no-same-owner", "--no-absolute-filenames"],
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
    with tempfile.TemporaryDirectory(prefix="popctl-info-") as tmpdir:
        output_dir = Path(tmpdir)
        backup_path = _fetch_backup(source, output_dir)
        _decrypt_and_decompress(backup_path, output_dir, identity)

        metadata_file = output_dir / "metadata.json"
        if not metadata_file.exists():
            raise BackupError("Invalid backup: metadata.json not found in archive")

        try:
            return BackupMetadata.from_json(metadata_file.read_text())
        except (KeyError, ValueError) as e:
            raise BackupError(f"Invalid metadata.json: {e}") from e


def _restore_popctl_state(staging_dir: Path) -> int:
    config_dir = get_config_dir()
    state_dir = get_state_dir()
    config_dir.mkdir(parents=True, exist_ok=True)
    state_dir.mkdir(parents=True, exist_ok=True)

    popctl_dir = staging_dir / "files" / "popctl"
    if not popctl_dir.exists():
        return 0

    resolved_config = config_dir.resolve()
    resolved_state = state_dir.resolve()

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
            resolved_dest = dest.resolve()
            if not (
                resolved_dest.is_relative_to(resolved_config)
                or resolved_dest.is_relative_to(resolved_state)
            ):
                logger.warning("Skipping path traversal attempt: %s", filename)
                continue
            dest.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(str(src), str(dest))
                count += 1
                logger.debug("Restored popctl state: %s", dest)
            except OSError as e:
                logger.warning("Could not restore %s: %s", dest, e)

    return count


def _restore_home_files(staging_dir: Path) -> int:
    home_dir = staging_dir / "files" / "home"
    if not home_dir.exists():
        return 0

    current_home = Path.home()
    count = 0

    resolved_home = current_home.resolve()

    for src in sorted(home_dir.rglob("*")):
        if not src.is_file():
            continue

        rel = src.relative_to(home_dir)
        dest = (current_home / rel).resolve()

        if not dest.is_relative_to(resolved_home):
            logger.warning("Skipping path traversal attempt: %s", rel)
            continue

        # Create parent directories
        dest.parent.mkdir(parents=True, exist_ok=True)

        try:
            shutil.copy2(str(src), str(dest))
            count += 1
        except (OSError, PermissionError) as e:
            logger.warning("Could not restore %s: %s", dest, e)

    return count


def _fix_sensitive_permissions() -> None:
    home = Path.home()

    ssh_dir = home / ".ssh"
    if ssh_dir.exists():
        try:
            ssh_dir.chmod(0o700)
            for child in ssh_dir.iterdir():
                if child.is_file():
                    child.chmod(0o600)
        except OSError as e:
            logger.warning("Could not fix .ssh permissions: %s", e)

    gnupg_dir = home / ".gnupg"
    if gnupg_dir.exists():
        try:
            gnupg_dir.chmod(0o700)
        except OSError as e:
            logger.warning("Could not fix .gnupg permissions: %s", e)


def _install_packages() -> tuple[int, int]:
    from popctl.core.diff import DiffEntry, DiffResult, DiffType, diff_to_actions
    from popctl.core.executor import execute_actions, record_actions_to_history
    from popctl.core.manifest import ManifestError
    from popctl.models.package import PackageSource
    from popctl.operators import get_available_operators

    try:
        manifest = load_manifest()
    except ManifestError as e:
        logger.warning("Could not load restored manifest: %s", e)
        print_warning(f"Could not load restored manifest: {e}")
        return 0, 0

    # Build MISSING entries for all packages.keep (they're all missing on fresh install)
    missing: list[DiffEntry] = []
    for name, entry in manifest.packages.keep.items():
        missing.append(DiffEntry(
            name=name,
            source=PackageSource(entry.source),
            diff_type=DiffType.MISSING,
        ))

    # Build EXTRA entries for all packages.remove (they might be pre-installed)
    extra: list[DiffEntry] = []
    for name, entry in manifest.packages.remove.items():
        extra.append(DiffEntry(
            name=name,
            source=PackageSource(entry.source),
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
    with tempfile.TemporaryDirectory(prefix="popctl-restore-") as tmpdir:
        staging_dir = Path(tmpdir)
        backup_path = _fetch_backup(source, staging_dir)
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
