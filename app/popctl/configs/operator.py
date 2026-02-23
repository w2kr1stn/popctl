"""Config backup and deletion operator.

Handles safe deletion of orphaned configuration paths with automatic
backup before deletion, dry-run support, and protected path checking.
Only operates on user home paths (no /etc, no sudo).
"""

import logging
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from popctl.configs.protected import is_protected_config
from popctl.core.paths import ensure_config_backup_dir

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ConfigActionResult:
    """Result of a single config deletion operation.

    Attributes:
        path: Absolute path that was operated on.
        success: Whether the operation completed successfully.
        error: Error message if the operation failed, None otherwise.
        dry_run: Whether this was a dry-run (no actual deletion).
        backup_path: Path to backup copy, None if backup was skipped or failed.
    """

    path: str
    success: bool
    error: str | None = None
    dry_run: bool = False
    backup_path: str | None = None


class ConfigOperator:
    """Backs up and deletes orphaned config paths.

    Creates timestamped backups before deletion, preserving the relative
    directory structure from the user's home directory. Only handles
    user home paths -- no /etc paths, no sudo escalation.

    Attributes:
        _dry_run: If True, simulate operations without modifying the filesystem.
    """

    def __init__(self, *, dry_run: bool = False) -> None:
        """Initialize the ConfigOperator.

        Args:
            dry_run: If True, report what would be done without doing it.
        """
        self._dry_run = dry_run

    def delete(self, paths: list[str]) -> list[ConfigActionResult]:
        """Delete multiple config paths with backup and return results.

        Creates a single timestamped backup directory for all paths in
        this batch. Each path is checked against protected patterns
        before deletion. Protected paths are rejected with an error result.

        Args:
            paths: List of absolute config paths to delete.

        Returns:
            List of ConfigActionResult, one per input path.
        """
        if not paths:
            return []

        # Create timestamped backup directory for this batch
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup_base = ensure_config_backup_dir()
        backup_dir = backup_base / timestamp

        if not self._dry_run:
            try:
                backup_dir.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                logger.warning("Could not create backup directory %s: %s", backup_dir, e)

        results: list[ConfigActionResult] = []

        for path in paths:
            results.append(self._delete_single(path, backup_dir))

        return results

    def _backup_path(self, path: str, backup_dir: Path) -> str | None:
        """Backup a config path before deletion.

        Calculates the relative path from the user's home directory and
        copies the config to the backup directory preserving that structure.

        Args:
            path: Absolute config path to back up.
            backup_dir: Timestamped backup directory for this batch.

        Returns:
            Backup path as string if successful, None on failure.
        """
        try:
            source = Path(path)
            home = Path.home()

            try:
                relative = source.relative_to(home)
            except ValueError:
                # Path is not under home -- use the full path structure
                relative = Path(path.lstrip("/"))

            dest = backup_dir / relative

            # Ensure parent directories exist
            dest.parent.mkdir(parents=True, exist_ok=True)

            if source.is_dir() and not source.is_symlink():
                shutil.copytree(str(source), str(dest))
            else:
                shutil.copy2(str(source), str(dest))

            return str(dest)

        except OSError as e:
            logger.warning("Backup failed for %s: %s", path, e)
            return None

    def _delete_single(self, path: str, backup_dir: Path) -> ConfigActionResult:
        """Delete a single config path with backup.

        Steps:
        1. Check is_protected_config() -- reject if protected
        2. Check path exists -- error if not
        3. Backup first (failure is non-fatal)
        4. Delete: shutil.rmtree() for dirs, Path.unlink() for files
        5. Return ConfigActionResult

        Args:
            path: Absolute config path to delete.
            backup_dir: Timestamped backup directory for this batch.

        Returns:
            ConfigActionResult indicating success or failure.
        """
        # 1. Check protected
        if is_protected_config(path):
            return ConfigActionResult(
                path=path,
                success=False,
                error=f"Protected config cannot be deleted: {path}",
            )

        target = Path(path)

        # 2. Check existence
        if not target.exists() and not target.is_symlink():
            return ConfigActionResult(
                path=path,
                success=False,
                error=f"Path does not exist: {path}",
            )

        # Dry-run: skip actual backup+delete
        if self._dry_run:
            logger.info("Dry-run: would back up and delete %s", path)
            return ConfigActionResult(
                path=path,
                success=True,
                dry_run=True,
            )

        # 3. Backup (non-fatal)
        backup_result = self._backup_path(path, backup_dir)

        # 4. Delete
        try:
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(path)
            else:
                target.unlink()

            return ConfigActionResult(
                path=path,
                success=True,
                backup_path=backup_result,
            )

        except OSError as e:
            return ConfigActionResult(
                path=path,
                success=False,
                error=str(e),
                backup_path=backup_result,
            )
