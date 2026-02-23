"""APT package operator implementation.

Executes package installation and removal using apt-get.
"""

import logging

from popctl.models.action import Action, ActionResult, ActionType
from popctl.models.package import PackageSource
from popctl.operators.base import Operator
from popctl.utils.shell import CommandResult, command_exists, run_command

logger = logging.getLogger(__name__)


class AptOperator(Operator):
    """Operator for APT/dpkg packages.

    Uses apt-get to install, remove, and purge packages. Requires sudo
    privileges for actual execution.

    Attributes:
        dry_run: If True, uses apt-get --dry-run to simulate actions.
    """

    # Timeout for apt operations (5 minutes)
    _APT_TIMEOUT: float = 300.0

    @property
    def source(self) -> PackageSource:
        """Return APT as the package source."""
        return PackageSource.APT

    def is_available(self) -> bool:
        """Check if apt-get is available."""
        return command_exists("apt-get")

    def install(self, packages: list[str]) -> list[ActionResult]:
        """Install packages using apt-get install.

        Args:
            packages: List of package names to install.

        Returns:
            List of ActionResult for each package.

        Raises:
            RuntimeError: If apt-get is not available.
        """
        if not self.is_available():
            msg = "APT package manager is not available on this system"
            raise RuntimeError(msg)

        if not packages:
            return []

        return self._execute_apt_command("install", packages)

    def remove(self, packages: list[str], purge: bool = False) -> list[ActionResult]:
        """Remove packages using apt-get remove or purge.

        Args:
            packages: List of package names to remove.
            purge: If True, use purge to also remove configuration files.

        Returns:
            List of ActionResult for each package.

        Raises:
            RuntimeError: If apt-get is not available.
        """
        if not self.is_available():
            msg = "APT package manager is not available on this system"
            raise RuntimeError(msg)

        if not packages:
            return []

        command = "purge" if purge else "remove"
        return self._execute_apt_command(command, packages)

    def _execute_apt_command(
        self,
        command: str,
        packages: list[str],
    ) -> list[ActionResult]:
        """Execute an apt-get command for a list of packages.

        Args:
            command: APT command (install, remove, purge).
            packages: List of package names.

        Returns:
            List of ActionResult for each package.
        """
        # Map command to ActionType
        action_type_map = {
            "install": ActionType.INSTALL,
            "remove": ActionType.REMOVE,
            "purge": ActionType.PURGE,
        }
        action_type = action_type_map[command]

        # Build the apt-get command
        args = ["sudo", "apt-get", command, "-y"]

        if self.dry_run:
            args.append("--dry-run")

        args.extend(packages)

        logger.info(
            "Executing APT %s for packages: %s (dry_run=%s)",
            command,
            ", ".join(packages),
            self.dry_run,
        )

        result = run_command(args, timeout=self._APT_TIMEOUT)

        return self._parse_apt_result(result, packages, action_type)

    def _parse_apt_result(
        self,
        result: CommandResult,
        packages: list[str],
        action_type: ActionType,
    ) -> list[ActionResult]:
        """Parse apt-get output and create ActionResult for each package.

        For simplicity, we treat the entire operation as atomic - either
        all packages succeed or all fail. This matches apt-get's behavior
        where a single failure aborts the entire transaction.

        Args:
            result: CommandResult from apt-get execution.
            packages: List of packages that were operated on.
            action_type: Type of action performed.

        Returns:
            List of ActionResult for each package.
        """
        results: list[ActionResult] = []

        if result.success:
            message = "Dry-run completed" if self.dry_run else "Operation completed"
            for package in packages:
                action = Action(
                    action_type=action_type,
                    package=package,
                    source=PackageSource.APT,
                )
                results.append(
                    ActionResult(
                        action=action,
                        success=True,
                        message=message,
                    )
                )
        else:
            # All packages fail if apt-get fails
            error_msg = result.stderr.strip() or "apt-get command failed"
            for package in packages:
                action = Action(
                    action_type=action_type,
                    package=package,
                    source=PackageSource.APT,
                )
                results.append(
                    ActionResult(
                        action=action,
                        success=False,
                        error=error_msg,
                    )
                )

        return results
