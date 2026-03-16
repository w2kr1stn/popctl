from abc import ABC, abstractmethod

from popctl.models.action import Action, ActionResult
from popctl.models.package import PackageSource
from popctl.utils.shell import CommandResult, run_command


class Operator(ABC):
    _TIMEOUT: float = 300.0

    def __init__(self, dry_run: bool = False) -> None:
        self._dry_run = dry_run

    @property
    def dry_run(self) -> bool:
        return self._dry_run

    source: PackageSource

    @abstractmethod
    def install(self, packages: list[str]) -> list[ActionResult]: ...

    @abstractmethod
    def remove(self, packages: list[str], purge: bool = False) -> list[ActionResult]: ...

    @abstractmethod
    def is_available(self) -> bool: ...

    def _run_single(self, action: Action, args: list[str]) -> ActionResult:
        if self.dry_run:
            return self._dry_run_result(action)
        result = run_command(args, timeout=self._TIMEOUT)
        return self._create_result(action, result)

    def _dry_run_result(self, action: Action) -> ActionResult:
        return ActionResult(
            action=action,
            success=True,
            detail=f"Dry-run: would {action.action_type.value}",
        )

    def _create_result(self, action: Action, result: CommandResult) -> ActionResult:
        if result.success:
            return ActionResult(action=action, success=True, detail="Operation completed")

        error_msg = (
            result.stderr.strip() or result.stdout.strip() or f"{self.source.value} command failed"
        )
        return ActionResult(action=action, success=False, detail=error_msg)
