"""APT package operator implementation.

Executes package installation and removal using apt-get.
"""

import logging

from popctl.models.action import Action, ActionResult, ActionType
from popctl.models.package import PackageSource
from popctl.operators.base import Operator
from popctl.utils.shell import command_exists, run_command

logger = logging.getLogger(__name__)


class AptOperator(Operator):
    """Operator for APT/dpkg packages.

    Uses apt-get to install, remove, and purge packages. Requires sudo
    privileges for actual execution.

    Attributes:
        dry_run: If True, uses apt-get --dry-run to simulate actions.
    """

    source = PackageSource.APT

    def is_available(self) -> bool:
        """Check if apt-get is available."""
        return command_exists("apt-get")

    def install(self, packages: list[str]) -> list[ActionResult]:
        """Install packages using apt-get install.

        Args:
            packages: List of package names to install.

        Returns:
            List of ActionResult for each package.

        """
        if not packages:
            return []

        return self._execute_apt_command(ActionType.INSTALL, packages)

    def remove(self, packages: list[str], purge: bool = False) -> list[ActionResult]:
        """Remove packages using apt-get remove or purge.

        Args:
            packages: List of package names to remove.
            purge: If True, use purge to also remove configuration files.

        Returns:
            List of ActionResult for each package.

        """
        if not packages:
            return []

        return self._execute_apt_command(ActionType.PURGE if purge else ActionType.REMOVE, packages)

    def _execute_apt_command(
        self,
        action_type: ActionType,
        packages: list[str],
    ) -> list[ActionResult]:
        """Execute an apt-get command for a list of packages.

        Tries a batch command first for efficiency. If the batch fails and
        there are multiple packages, falls back to single-package operations
        so that one resolver conflict doesn't block all packages.

        Args:
            action_type: Type of action (INSTALL, REMOVE, PURGE).
            packages: List of package names.

        Returns:
            List of ActionResult for each package.
        """
        command = action_type.value

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

        result = run_command(args, timeout=self._TIMEOUT)

        if result.success:
            message = "Dry-run completed" if self.dry_run else "Operation completed"
            return [
                ActionResult(
                    action=Action(action_type=action_type, package=pkg, source=PackageSource.APT),
                    success=True,
                    detail=message,
                )
                for pkg in packages
            ]

        # Batch failed with multiple packages — fall back to single-package ops
        if len(packages) > 1:
            logger.warning(
                "Batch APT %s failed, falling back to single-package operations",
                command,
            )
            results: list[ActionResult] = []
            for pkg in packages:
                results.append(self._execute_apt_single(action_type, pkg))
            return results

        # Single package failed
        error_msg = result.stderr.strip() or "apt-get command failed"
        return [
            ActionResult(
                action=Action(
                    action_type=action_type, package=packages[0], source=PackageSource.APT
                ),
                success=False,
                detail=error_msg,
            ),
        ]

    def _execute_apt_single(
        self,
        action_type: ActionType,
        package: str,
    ) -> ActionResult:
        """Execute an apt-get command for a single package.

        Args:
            action_type: Type of action (INSTALL, REMOVE, PURGE).
            package: Package name.

        Returns:
            ActionResult for the package.
        """
        command = action_type.value
        args = ["sudo", "apt-get", command, "-y"]
        if self.dry_run:
            args.append("--dry-run")
        args.append(package)

        logger.info("APT %s (single): %s", command, package)
        result = run_command(args, timeout=self._TIMEOUT)

        action = Action(action_type=action_type, package=package, source=PackageSource.APT)
        if result.success:
            message = "Dry-run completed" if self.dry_run else "Operation completed"
            return ActionResult(action=action, success=True, detail=message)

        error_msg = result.stderr.strip() or "apt-get command failed"
        return ActionResult(action=action, success=False, detail=error_msg)
