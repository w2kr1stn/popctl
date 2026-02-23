"""Flatpak package operator implementation.

Executes package installation and removal using the flatpak CLI.
"""

import logging

from popctl.models.action import Action, ActionResult, ActionType
from popctl.models.package import PackageSource
from popctl.operators.base import Operator
from popctl.utils.shell import CommandResult, command_exists, run_command

logger = logging.getLogger(__name__)


class FlatpakOperator(Operator):
    """Operator for Flatpak applications.

    Uses the flatpak CLI to install and uninstall applications.

    Attributes:
        dry_run: If True, only print what would be done without executing.
    """

    # Timeout for flatpak operations (5 minutes)
    _FLATPAK_TIMEOUT: float = 300.0

    @property
    def source(self) -> PackageSource:
        """Return FLATPAK as the package source."""
        return PackageSource.FLATPAK

    def is_available(self) -> bool:
        """Check if flatpak CLI is available."""
        return command_exists("flatpak")

    def install(self, packages: list[str]) -> list[ActionResult]:
        """Install Flatpak applications.

        Args:
            packages: List of application IDs to install (e.g., 'com.spotify.Client').

        Returns:
            List of ActionResult for each package.

        Raises:
            RuntimeError: If flatpak is not available.
        """
        if not self.is_available():
            msg = "Flatpak is not available on this system"
            raise RuntimeError(msg)

        if not packages:
            return []

        return self._execute_flatpak_install(packages)

    def remove(self, packages: list[str], purge: bool = False) -> list[ActionResult]:
        """Remove Flatpak applications.

        Note: Flatpak does not distinguish between remove and purge.
        The purge parameter is accepted for API consistency but ignored.

        Args:
            packages: List of application IDs to remove.
            purge: Ignored for Flatpak (no concept of purge).

        Returns:
            List of ActionResult for each package.

        Raises:
            RuntimeError: If flatpak is not available.
        """
        if not self.is_available():
            msg = "Flatpak is not available on this system"
            raise RuntimeError(msg)

        if not packages:
            return []

        # Flatpak has no purge distinction - ignore the flag
        if purge:
            logger.debug("Flatpak does not support purge, using standard uninstall")

        return self._execute_flatpak_uninstall(packages)

    def _execute_flatpak_install(self, packages: list[str]) -> list[ActionResult]:
        """Execute flatpak install for a list of applications.

        Args:
            packages: List of application IDs to install.

        Returns:
            List of ActionResult for each package.
        """
        results: list[ActionResult] = []

        # Flatpak install works best one app at a time for better error reporting
        for package in packages:
            result = self._install_single(package)
            results.append(result)

        return results

    def _install_single(self, package: str) -> ActionResult:
        """Install a single Flatpak application.

        Args:
            package: Application ID to install.

        Returns:
            ActionResult for this package.
        """
        action = Action(
            action_type=ActionType.INSTALL,
            package=package,
            source=PackageSource.FLATPAK,
        )

        if self.dry_run:
            logger.info("Dry-run: Would install flatpak %s", package)
            return ActionResult(
                action=action,
                success=True,
                message="Dry-run: would install",
            )

        # Build the flatpak install command
        # -y: non-interactive, --user: install to user scope
        args = ["flatpak", "install", "-y", "--user", package]

        logger.info("Installing Flatpak: %s", package)
        result = run_command(args, timeout=self._FLATPAK_TIMEOUT)

        return self._create_result(action, result)

    def _execute_flatpak_uninstall(self, packages: list[str]) -> list[ActionResult]:
        """Execute flatpak uninstall for a list of applications.

        Args:
            packages: List of application IDs to uninstall.

        Returns:
            List of ActionResult for each package.
        """
        results: list[ActionResult] = []

        # Flatpak uninstall works best one app at a time for better error reporting
        for package in packages:
            result = self._uninstall_single(package)
            results.append(result)

        return results

    def _uninstall_single(self, package: str) -> ActionResult:
        """Uninstall a single Flatpak application.

        Args:
            package: Application ID to uninstall.

        Returns:
            ActionResult for this package.
        """
        action = Action(
            action_type=ActionType.REMOVE,
            package=package,
            source=PackageSource.FLATPAK,
        )

        if self.dry_run:
            logger.info("Dry-run: Would uninstall flatpak %s", package)
            return ActionResult(
                action=action,
                success=True,
                message="Dry-run: would uninstall",
            )

        # Build the flatpak uninstall command
        # -y: non-interactive
        args = ["flatpak", "uninstall", "-y", package]

        logger.info("Uninstalling Flatpak: %s", package)
        result = run_command(args, timeout=self._FLATPAK_TIMEOUT)

        return self._create_result(action, result)

    def _create_result(self, action: Action, result: CommandResult) -> ActionResult:
        """Create an ActionResult from a CommandResult.

        Args:
            action: The action that was executed.
            result: The command execution result.

        Returns:
            ActionResult with appropriate success/error info.
        """
        if result.success:
            return ActionResult(
                action=action,
                success=True,
                message="Operation completed",
            )

        error_msg = result.stderr.strip() or result.stdout.strip() or "flatpak command failed"
        return ActionResult(
            action=action,
            success=False,
            error=error_msg,
        )
