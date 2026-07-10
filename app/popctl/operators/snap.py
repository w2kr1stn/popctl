import logging

from popctl.models.action import Action, ActionResult, ActionType
from popctl.models.package import PackageSource
from popctl.operators.base import Operator
from popctl.utils.shell import command_exists

logger = logging.getLogger(__name__)


class SnapOperator(Operator):
    source = PackageSource.SNAP

    def is_available(self) -> bool:
        return command_exists("snap")

    def install(self, packages: list[str]) -> list[ActionResult]:
        if not packages:
            return []
        results: list[ActionResult] = []
        for pkg in packages:
            action = Action(ActionType.INSTALL, pkg, PackageSource.SNAP)
            logger.info("Installing Snap: %s", pkg)
            results.append(self._run_single(action, ["sudo", "snap", "install", "--", pkg]))
        return results

    def remove(self, packages: list[str], purge: bool = False) -> list[ActionResult]:
        if not packages:
            return []
        results: list[ActionResult] = []
        for pkg in packages:
            action_type = ActionType.PURGE if purge else ActionType.REMOVE
            action = Action(action_type, pkg, PackageSource.SNAP)
            args = ["sudo", "snap", "remove"]
            if purge:
                args.append("--purge")
            args.append("--")
            args.append(pkg)
            logger.info("Removing Snap: %s (purge=%s)", pkg, purge)
            results.append(self._run_single(action, args))
        return results
