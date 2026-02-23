"""Flatpak package operator implementation.

Executes package installation and removal using the flatpak CLI.
"""

import logging

from popctl.models.action import Action, ActionResult, ActionType
from popctl.models.package import PackageSource
from popctl.operators.base import Operator
from popctl.utils.shell import command_exists

logger = logging.getLogger(__name__)


class FlatpakOperator(Operator):
    """Operator for Flatpak applications.

    Uses the flatpak CLI to install and uninstall applications.

    Attributes:
        dry_run: If True, only print what would be done without executing.
    """

    source = PackageSource.FLATPAK

    def is_available(self) -> bool:
        """Check if flatpak CLI is available."""
        return command_exists("flatpak")

    def install(self, packages: list[str]) -> list[ActionResult]:
        """Install Flatpak applications."""
        if not packages:
            return []
        results: list[ActionResult] = []
        for pkg in packages:
            action = Action(ActionType.INSTALL, pkg, PackageSource.FLATPAK)
            logger.info("Installing Flatpak: %s", pkg)
            results.append(self._run_single(action, ["flatpak", "install", "-y", "--user", pkg]))
        return results

    def remove(self, packages: list[str], purge: bool = False) -> list[ActionResult]:
        """Remove Flatpak applications."""
        if not packages:
            return []
        if purge:
            logger.debug("Flatpak does not support purge, using standard uninstall")
        results: list[ActionResult] = []
        for pkg in packages:
            action = Action(ActionType.REMOVE, pkg, PackageSource.FLATPAK)
            logger.info("Uninstalling Flatpak: %s", pkg)
            results.append(self._run_single(action, ["flatpak", "uninstall", "-y", pkg]))
        return results
