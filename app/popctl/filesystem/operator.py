"""Filesystem deletion operator.

Handles safe deletion of orphaned filesystem paths with dry-run
support, protected path checking, and sudo escalation for system paths.
"""

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from popctl.filesystem.protected import is_protected_path
from popctl.utils.shell import run_command

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class FilesystemActionResult:
    """Result of a single filesystem deletion operation.

    Attributes:
        path: Absolute path that was operated on.
        success: Whether the operation completed successfully.
        error: Error message if the operation failed, None otherwise.
        dry_run: Whether this was a dry-run (no actual deletion).
    """

    path: str
    success: bool
    error: str | None = None
    dry_run: bool = False


class FilesystemOperator:
    """Handles deletion of orphaned filesystem paths.

    Supports dry-run mode, protected path rejection, and automatic
    sudo escalation for paths under /etc.

    Attributes:
        _dry_run: If True, simulate deletions without modifying the filesystem.
    """

    def __init__(self, dry_run: bool = False) -> None:
        """Initialize the FilesystemOperator.

        Args:
            dry_run: If True, report what would be deleted without deleting.
        """
        self._dry_run = dry_run

    def delete(self, paths: list[str]) -> list[FilesystemActionResult]:
        """Delete multiple filesystem paths and return results.

        Each path is checked against protected patterns before deletion.
        Protected paths are skipped with an error result. Non-protected
        paths are deleted individually, with failures isolated per path.

        Args:
            paths: List of absolute filesystem paths to delete.

        Returns:
            List of FilesystemActionResult, one per input path.
        """
        results: list[FilesystemActionResult] = []

        for path in paths:
            if self._is_protected(path):
                results.append(
                    FilesystemActionResult(
                        path=path,
                        success=False,
                        error=f"Protected path cannot be deleted: {path}",
                    )
                )
                continue

            results.append(self._delete_single(path))

        return results

    def is_available(self) -> bool:
        """Check if the operator is available.

        The filesystem operator is always available since it uses
        standard OS operations.

        Returns:
            Always True.
        """
        return True

    def _delete_single(self, path: str) -> FilesystemActionResult:
        """Delete a single filesystem path.

        Dispatches to the appropriate deletion strategy based on
        the path location and type:
        - /etc paths: sudo rm -rf via run_command
        - Directories: shutil.rmtree
        - Files and symlinks: Path.unlink

        Args:
            path: Absolute filesystem path to delete.

        Returns:
            FilesystemActionResult indicating success or failure.
        """
        if self._dry_run:
            logger.info("Dry-run: would delete %s", path)
            return FilesystemActionResult(
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
                    return FilesystemActionResult(
                        path=path,
                        success=False,
                        error=result.stderr.strip() or "sudo rm failed",
                    )
                return FilesystemActionResult(path=path, success=True)

            # Directories (but not symlinks to directories)
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(path)
                return FilesystemActionResult(path=path, success=True)

            # Files, symlinks, and dead symlinks
            if target.exists() or target.is_symlink():
                target.unlink()
                return FilesystemActionResult(path=path, success=True)

            # Path does not exist
            return FilesystemActionResult(
                path=path,
                success=False,
                error=f"Path does not exist: {path}",
            )

        except OSError as e:
            return FilesystemActionResult(
                path=path,
                success=False,
                error=str(e),
            )

    def _is_protected(self, path: str) -> bool:
        """Check if a path is protected from deletion.

        Args:
            path: Absolute filesystem path to check.

        Returns:
            True if the path matches a protected pattern.
        """
        return is_protected_path(path)
