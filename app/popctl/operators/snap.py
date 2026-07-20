import logging
from collections.abc import Sequence

from popctl.models.action import Action, ActionResult, ActionType
from popctl.models.package import PackageSource
from popctl.operators.base import Operator
from popctl.utils.shell import command_exists

logger = logging.getLogger(__name__)


class SnapOperator(Operator):
    source = PackageSource.SNAP

    def is_available(self) -> bool:
        return command_exists("snap")

    def install(self, items: Sequence[Action | str]) -> list[ActionResult]:
        if not items:
            return []
        results: list[ActionResult] = []
        for item in items:
            action = (
                item
                if isinstance(item, Action)
                else Action(ActionType.INSTALL, item, PackageSource.SNAP)
            )
            if action.source is not PackageSource.SNAP:
                msg = "Snap operator received an action for another source"
                raise ValueError(msg)
            logger.info("Installing Snap: %s", action.package)
            context = action.source_install_context
            cmd = ["sudo", "snap", "install"]
            if context is not None:
                if context.is_flatpak:
                    msg = "Snap operator received a non-Snap source context"
                    raise ValueError(msg)
                cmd.append(f"--channel={context.snap_channel}")
            cmd.extend(("--", action.package))
            results.append(self._run_single(action, cmd))
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
