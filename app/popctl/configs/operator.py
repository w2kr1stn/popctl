import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path

from popctl.core.paths import ensure_dir, get_state_dir
from popctl.domain.models import DomainActionResult
from popctl.domain.protected import is_protected
from popctl.utils.shell import safe_resolve

logger = logging.getLogger(__name__)


class ConfigOperator:
    def __init__(self, *, dry_run: bool = False) -> None:
        self._dry_run = dry_run

    def delete(self, paths: list[str]) -> list[DomainActionResult]:
        if not paths:
            return []

        # Create timestamped backup directory for this batch
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup_base = ensure_dir(get_state_dir() / "config-backups", "config backup")
        backup_dir = backup_base / timestamp

        if not self._dry_run:
            try:
                backup_dir.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                logger.warning("Could not create backup directory %s: %s", backup_dir, e)
                return [
                    DomainActionResult(
                        path=p, success=False, error=f"Backup directory creation failed: {e}"
                    )
                    for p in paths
                ]

        results: list[DomainActionResult] = []
        for path in paths:
            results.append(self._delete_single(path, backup_dir))

        return results

    def _backup_path(self, path: str, backup_dir: Path) -> str | None:
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

    def _delete_single(self, path: str, backup_dir: Path) -> DomainActionResult:
        """Protected check -> existence check -> backup (non-fatal) -> delete."""
        path = safe_resolve(path)

        # 1. Check protected
        if is_protected(path, "configs"):
            return DomainActionResult(
                path=path,
                success=False,
                error=f"Protected config cannot be deleted: {path}",
            )

        target = Path(path)

        # 2. Check existence — idempotent success (like rm -f)
        if not target.exists() and not target.is_symlink():
            return DomainActionResult(path=path, success=True)

        # Dry-run: skip actual backup+delete
        if self._dry_run:
            logger.info("Dry-run: would back up and delete %s", path)
            return DomainActionResult(
                path=path,
                success=True,
                dry_run=True,
            )

        # 3. Backup (fatal — abort deletion if backup fails)
        backup_result = self._backup_path(path, backup_dir)
        if backup_result is None:
            return DomainActionResult(
                path=path,
                success=False,
                error="Backup failed — deletion aborted to preserve original",
            )

        # 4. Delete
        try:
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(path)
            else:
                target.unlink()

            return DomainActionResult(
                path=path,
                success=True,
                backup_path=backup_result,
            )

        except OSError as e:
            return DomainActionResult(
                path=path,
                success=False,
                error=str(e),
                backup_path=backup_result,
            )
