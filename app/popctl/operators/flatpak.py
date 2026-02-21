"""Flatpak package operator implementation.

Executes package installation and removal using the flatpak CLI.
"""

import logging

from popctl.models.action import Action, ActionResult, ActionType
from popctl.models.package import PackageSource
from popctl.operators.base import Operator
from popctl.utils.shell import command_exists, run_command

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

        """
        if not packages:
            return []

        return [self._install_single(pkg) for pkg in packages]

    def remove(self, packages: list[str], purge: bool = False) -> list[ActionResult]:
        """Remove Flatpak applications.

        Note: Flatpak does not distinguish between remove and purge.
        The purge parameter is accepted for API consistency but ignored.

        Args:
            packages: List of application IDs to remove.
            purge: Ignored for Flatpak (no concept of purge).

        Returns:
            List of ActionResult for each package.

        """
        if not packages:
            return []

        # Flatpak has no purge distinction - ignore the flag
        if purge:
            logger.debug("Flatpak does not support purge, using standard uninstall")

        return [self._uninstall_single(pkg) for pkg in packages]

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
