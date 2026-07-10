import logging
import shutil
from pathlib import Path

from popctl.domain.models import DomainActionResult
from popctl.domain.protected import is_protected
from popctl.utils.shell import run_command, safe_resolve

logger = logging.getLogger(__name__)


class FilesystemOperator:
    def __init__(self, *, dry_run: bool = False) -> None:
        self._dry_run = dry_run

    def delete(self, paths: list[str]) -> list[DomainActionResult]:
        results: list[DomainActionResult] = []

        for path in paths:
            path = safe_resolve(path)
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
        """/etc paths use sudo rm -rf; dirs use rmtree; files use unlink."""
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
                resolved = str(Path(path).resolve())
                if is_protected(resolved, "filesystem"):
                    return DomainActionResult(
                        path=path,
                        success=False,
                        error=f"Resolved target is protected: {resolved}",
                    )
                result = run_command(["sudo", "rm", "-rf", "--", resolved])
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
