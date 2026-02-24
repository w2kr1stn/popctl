"""Filesystem deletion operator.

Handles safe deletion of orphaned filesystem paths with dry-run
support, protected path checking, and sudo escalation for system paths.
"""

import logging
import shutil
from pathlib import Path

from popctl.domain.models import DomainActionResult
from popctl.domain.protected import is_protected
from popctl.utils.shell import run_command

logger = logging.getLogger(__name__)


class FilesystemOperator:
    """Handles deletion of orphaned filesystem paths.

    Supports dry-run mode, protected path rejection, and automatic
    sudo escalation for paths under /etc.

    Attributes:
        _dry_run: If True, simulate deletions without modifying the filesystem.
    """

    def __init__(self, *, dry_run: bool = False) -> None:
        """Initialize the FilesystemOperator.

        Args:
            dry_run: If True, report what would be deleted without deleting.
        """
        self._dry_run = dry_run

    def delete(self, paths: list[str]) -> list[DomainActionResult]:
        """Delete multiple filesystem paths and return results.

        Each path is checked against protected patterns before deletion.
        Protected paths are skipped with an error result. Non-protected
        paths are deleted individually, with failures isolated per path.

        Args:
            paths: List of absolute filesystem paths to delete.

        Returns:
            List of DomainActionResult, one per input path.
        """
        results: list[DomainActionResult] = []

        for path in paths:
            path = str(Path(path).expanduser())
            if is_protected(path, "filesystem"):
                results.append(
                    DomainActionResult(
                        path=path,
                        success=False,
                        error=f"Protected path cannot be deleted: {path}",
                    )
                )
                continue

            results.append(self._delete_single(path))

        return results

    def _delete_single(self, path: str) -> DomainActionResult:
        """Delete a single filesystem path.

        Dispatches to the appropriate deletion strategy based on
        the path location and type:
        - /etc paths: sudo rm -rf via run_command
        - Directories: shutil.rmtree
        - Files and symlinks: Path.unlink

        Args:
            path: Absolute filesystem path to delete.

        Returns:
            DomainActionResult indicating success or failure.
        """
        if self._dry_run:
            logger.info("Dry-run: would delete %s", path)
            return DomainActionResult(
                path=path,
                success=True,
                dry_run=True,
            )

        try:
            target = Path(path)

            # /etc paths require sudo
            if path.startswith("/etc/"):
                result = run_command(["sudo", "rm", "-rf", path])
                if not result.success:
                    return DomainActionResult(
                        path=path,
                        success=False,
                        error=result.stderr.strip() or "sudo rm failed",
                    )
                return DomainActionResult(path=path, success=True)

            # Directories (but not symlinks to directories)
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(path)
                return DomainActionResult(path=path, success=True)

            # Files, symlinks, and dead symlinks
            if target.exists() or target.is_symlink():
                target.unlink()
                return DomainActionResult(path=path, success=True)

            # Path does not exist — idempotent success (like rm -f)
            return DomainActionResult(path=path, success=True)

        except OSError as e:
            return DomainActionResult(
                path=path,
                success=False,
                error=str(e),
            )
