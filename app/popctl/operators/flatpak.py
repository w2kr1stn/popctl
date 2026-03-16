import logging

from popctl.models.action import Action, ActionResult, ActionType
from popctl.models.package import PackageSource
from popctl.operators.base import Operator
from popctl.utils.shell import command_exists

logger = logging.getLogger(__name__)


class FlatpakOperator(Operator):
    source = PackageSource.FLATPAK

    def is_available(self) -> bool:
        return command_exists("flatpak")

    def install(self, packages: list[str]) -> list[ActionResult]:
        if not packages:
            return []
        results: list[ActionResult] = []
        for pkg in packages:
            action = Action(ActionType.INSTALL, pkg, PackageSource.FLATPAK)
            logger.info("Installing Flatpak: %s", pkg)
            cmd = ["flatpak", "install", "-y", "--user", "--", pkg]
            results.append(self._run_single(action, cmd))
        return results

    def remove(self, packages: list[str], purge: bool = False) -> list[ActionResult]:
        if not packages:
            return []
        if purge:
            logger.debug("Flatpak does not support purge, using standard uninstall")
        results: list[ActionResult] = []
        for pkg in packages:
            action = Action(ActionType.REMOVE, pkg, PackageSource.FLATPAK)
            logger.info("Uninstalling Flatpak: %s", pkg)
            results.append(self._run_single(action, ["flatpak", "uninstall", "-y", "--", pkg]))
        return results
