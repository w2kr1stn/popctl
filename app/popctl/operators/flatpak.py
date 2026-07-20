import logging
from collections.abc import Sequence

from popctl.models.action import Action, ActionResult, ActionType
from popctl.models.package import PackageSource
from popctl.operators.base import Operator
from popctl.utils.shell import command_exists

logger = logging.getLogger(__name__)


class FlatpakOperator(Operator):
    source = PackageSource.FLATPAK

    def is_available(self) -> bool:
        return command_exists("flatpak")

    def install(self, items: Sequence[Action | str]) -> list[ActionResult]:
        if not items:
            return []
        results: list[ActionResult] = []
        for item in items:
            action = (
                item
                if isinstance(item, Action)
                else Action(ActionType.INSTALL, item, PackageSource.FLATPAK)
            )
            if action.source is not PackageSource.FLATPAK:
                msg = "Flatpak operator received an action for another source"
                raise ValueError(msg)
            logger.info("Installing Flatpak: %s", action.package)
            context = action.source_install_context
            if context is None:
                cmd = ["flatpak", "install", "-y", "--user", "--", action.package]
            else:
                if not context.is_flatpak:
                    msg = "Flatpak operator received a non-Flatpak source context"
                    raise ValueError(msg)
                if (
                    context.flatpak_scope is None
                    or context.flatpak_arch is None
                    or context.flatpak_branch is None
                    or context.flatpak_remote is None
                ):
                    msg = "Flatpak operator received an incomplete source context"
                    raise ValueError(msg)
                scope = "--user" if context.flatpak_scope.value == "user" else "--system"
                cmd = (["sudo"] if context.flatpak_scope.value == "system" else []) + [
                    "flatpak",
                    "install",
                    "-y",
                    scope,
                    f"--arch={context.flatpak_arch}",
                    f"--branch={context.flatpak_branch}",
                    context.flatpak_remote,
                    "--",
                    action.package,
                ]
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
