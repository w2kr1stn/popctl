import logging
import re
from collections.abc import Sequence

from popctl.core.baseline import is_package_protected
from popctl.models.action import Action, ActionResult, ActionType
from popctl.models.package import PackageSource
from popctl.operators.base import Operator
from popctl.utils.shell import command_exists, run_command

logger = logging.getLogger(__name__)


class AptOperator(Operator):
    source = PackageSource.APT

    def is_available(self) -> bool:
        return command_exists("apt-get")

    def install(self, items: Sequence[Action | str]) -> list[ActionResult]:
        packages = self._package_names(items)
        if not packages:
            return []

        return self._execute_apt_command(ActionType.INSTALL, packages)

    @staticmethod
    def _package_names(items: Sequence[Action | str]) -> list[str]:
        packages: list[str] = []
        for item in items:
            if isinstance(item, Action):
                if item.source is not PackageSource.APT:
                    msg = "APT operator received an action for another source"
                    raise ValueError(msg)
                packages.append(item.package)
            else:
                packages.append(item)
        return packages

    def remove(self, packages: list[str], purge: bool = False) -> list[ActionResult]:
        if not packages:
            return []

        return self._execute_apt_command(ActionType.PURGE if purge else ActionType.REMOVE, packages)

    def _execute_apt_command(
        self,
        action_type: ActionType,
        packages: list[str],
    ) -> list[ActionResult]:
        """Batch first, then fall back to single-package ops on failure.

        This prevents one resolver conflict from blocking all packages.
        """
        refusal_results = self._guard_removal_transaction(action_type, packages)
        if refusal_results is not None:
            return refusal_results

        command = action_type.value

        args = ["sudo", "apt-get", command, "-y"]
        if self.dry_run:
            args.append("--dry-run")
        args.append("--")
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
            batch_err = result.stderr.strip()
            logger.warning(
                "Batch APT %s failed (rc=%d), falling back to single-package operations: %s",
                command,
                result.returncode,
                batch_err or "(no stderr)",
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
        refusal_results = self._guard_removal_transaction(action_type, [package])
        if refusal_results is not None:
            return refusal_results[0]

        command = action_type.value
        args = ["sudo", "apt-get", command, "-y"]
        if self.dry_run:
            args.append("--dry-run")
        args.append("--")
        args.append(package)

        logger.info("APT %s (single): %s", command, package)
        result = run_command(args, timeout=self._TIMEOUT)

        action = Action(action_type=action_type, package=package, source=PackageSource.APT)
        if result.success:
            message = "Dry-run completed" if self.dry_run else "Operation completed"
            return ActionResult(action=action, success=True, detail=message)

        error_msg = result.stderr.strip() or "apt-get command failed"
        return ActionResult(action=action, success=False, detail=error_msg)

    def _guard_removal_transaction(
        self,
        action_type: ActionType,
        packages: list[str],
    ) -> list[ActionResult] | None:
        if action_type not in (ActionType.REMOVE, ActionType.PURGE):
            return None

        simulation = run_command(
            ["apt-get", "-s", action_type.value, "--", *packages], timeout=self._TIMEOUT
        )
        if not simulation.success:
            error_msg = (
                simulation.stderr.strip()
                or simulation.stdout.strip()
                or "apt-get simulation failed"
            )
            return self._removal_refusal_results(
                action_type,
                packages,
                f"refused: apt-get simulation failed: {error_msg}",
            )

        removed_packages = self._parse_simulated_removals(simulation.stdout, simulation.stderr)
        if removed_packages is None:
            return self._removal_refusal_results(
                action_type,
                packages,
                "refused: apt-get simulation output could not be parsed",
            )

        protected_packages = sorted(
            package for package in removed_packages if is_package_protected(package)
        )
        if protected_packages:
            return self._removal_refusal_results(
                action_type,
                packages,
                "refused: removing "
                f"{', '.join(packages)} would also remove protected "
                f"{', '.join(protected_packages)}",
            )

        return None

    @staticmethod
    def _parse_simulated_removals(stdout: str, stderr: str) -> set[str] | None:
        removed_packages: set[str] = set()
        action_line_count = 0
        for line in f"{stdout}\n{stderr}".splitlines():
            fields = line.split()
            action_index = next(
                (
                    index
                    for index, field in enumerate(fields)
                    if field.casefold() in {"remv", "purg"}
                ),
                None,
            )
            if action_index is None:
                continue

            if action_index + 1 >= len(fields):
                return None

            package = fields[action_index + 1].split(":", 1)[0]
            if not package:
                return None
            action_line_count += 1
            removed_packages.add(package)

        has_removal_summary = False
        for line in f"{stdout}\n{stderr}".splitlines():
            match = re.search(r"\b(\d+)\s+to\s+remove\b", line, flags=re.IGNORECASE)
            if match is None:
                continue

            has_removal_summary = True
            if action_line_count < int(match.group(1)):
                return None

        if removed_packages or has_removal_summary:
            return removed_packages
        return None

    def _removal_refusal_results(
        self,
        action_type: ActionType,
        packages: list[str],
        detail: str,
    ) -> list[ActionResult]:
        return [
            ActionResult(
                action=Action(action_type=action_type, package=package, source=PackageSource.APT),
                success=False,
                detail=detail,
            )
            for package in packages
        ]
