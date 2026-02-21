"""Snap package operator implementation.

Executes package installation and removal using the snap CLI.
"""

import logging

from popctl.models.action import Action, ActionResult, ActionType
from popctl.models.package import PackageSource
from popctl.operators.base import Operator
from popctl.utils.shell import command_exists, run_command

logger = logging.getLogger(__name__)


class SnapOperator(Operator):
    """Operator for Snap packages.

    Uses the snap CLI to install, remove, and purge packages.
    Requires sudo privileges for all operations.

    Attributes:
        dry_run: If True, only simulate actions without executing them.
    """

    # Timeout for snap operations (5 minutes)
    _SNAP_TIMEOUT: float = 300.0

    @property
    def source(self) -> PackageSource:
        """Return SNAP as the package source."""
        return PackageSource.SNAP

    def is_available(self) -> bool:
        """Check if snap CLI is available."""
        return command_exists("snap")

    def install(self, packages: list[str]) -> list[ActionResult]:
        """Install Snap packages.

        Args:
            packages: List of snap package names to install.

        Returns:
            List of ActionResult for each package.

        """
        if not packages:
            return []

        return [self._install_single(pkg) for pkg in packages]

    def remove(self, packages: list[str], purge: bool = False) -> list[ActionResult]:
        """Remove Snap packages.

        Args:
            packages: List of snap package names to remove.
            purge: If True, use --purge to remove all data as well.

        Returns:
            List of ActionResult for each package.

        """
        if not packages:
            return []

        return [self._remove_single(pkg, purge=purge) for pkg in packages]

    def _install_single(self, package: str) -> ActionResult:
        """Install a single Snap package.

        Args:
            package: Snap package name to install.

        Returns:
            ActionResult for this package.
        """
        action = Action(
            action_type=ActionType.INSTALL,
            package=package,
            source=PackageSource.SNAP,
        )

        if self.dry_run:
            logger.info("Dry-run: Would install snap %s", package)
            return ActionResult(
                action=action,
                success=True,
                message="Dry-run: would install",
            )

        args = ["sudo", "snap", "install", package]

        logger.info("Installing Snap: %s", package)
        result = run_command(args, timeout=self._SNAP_TIMEOUT)

        return self._create_result(action, result)

    def _remove_single(self, package: str, *, purge: bool) -> ActionResult:
        """Remove a single Snap package.

        Args:
            package: Snap package name to remove.
            purge: If True, use --purge flag to remove all data.

        Returns:
            ActionResult for this package.
        """
        action_type = ActionType.PURGE if purge else ActionType.REMOVE
        action = Action(
            action_type=action_type,
            package=package,
            source=PackageSource.SNAP,
        )

        if self.dry_run:
            verb = "purge" if purge else "remove"
            logger.info("Dry-run: Would %s snap %s", verb, package)
            return ActionResult(
                action=action,
                success=True,
                message=f"Dry-run: would {verb}",
            )

        args = ["sudo", "snap", "remove"]
        if purge:
            args.append("--purge")
        args.append(package)

        logger.info("Removing Snap: %s (purge=%s)", package, purge)
        result = run_command(args, timeout=self._SNAP_TIMEOUT)

        return self._create_result(action, result)
