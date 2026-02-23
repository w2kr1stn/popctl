"""Abstract base class for package operators.

This module defines the Operator interface that all package management
operators must implement.
"""

from abc import ABC, abstractmethod

from popctl.models.action import Action, ActionResult
from popctl.models.package import PackageSource
from popctl.utils.shell import CommandResult, run_command


class Operator(ABC):
    """Abstract base class for all package operators.

    Operators are responsible for executing package management actions
    (install, remove, purge) for a specific package manager.

    Two execution strategies exist:
    - **Batch** (APT): Passes all packages to a single command invocation
      via custom ``install``/``remove`` implementations. APT's transactional
      semantics make this the correct approach.
    - **Single-action** (Flatpak, Snap): Iterates packages one-by-one using
      the ``_run_single`` / ``_dry_run_result`` / ``_create_result`` helpers
      provided by this base class.

    Attributes:
        dry_run: If True, only simulate actions without executing them.

    Example:
        >>> operator = AptOperator(dry_run=True)
        >>> if operator.is_available():
        ...     results = operator.install(["htop", "neovim"])
        ...     for result in results:
        ...         print(f"{result.action.package}: {result.success}")
    """

    _TIMEOUT: float = 300.0

    def __init__(self, dry_run: bool = False) -> None:
        """Initialize the operator.

        Args:
            dry_run: If True, only simulate actions without executing them.
        """
        self._dry_run = dry_run

    @property
    def dry_run(self) -> bool:
        """Check if operator is in dry-run mode."""
        return self._dry_run

    source: PackageSource

    @abstractmethod
    def install(self, packages: list[str]) -> list[ActionResult]:
        """Install one or more packages.

        Args:
            packages: List of package names to install.

        Returns:
            List of ActionResult for each package.

        Raises:
            RuntimeError: If the package manager is not available.
        """

    @abstractmethod
    def remove(self, packages: list[str], purge: bool = False) -> list[ActionResult]:
        """Remove one or more packages.

        Args:
            packages: List of package names to remove.
            purge: If True, remove configuration files as well (APT only).

        Returns:
            List of ActionResult for each package.

        Raises:
            RuntimeError: If the package manager is not available.
        """

    @abstractmethod
    def is_available(self) -> bool:
        """Check if this package manager is available on the system.

        Returns:
            True if the package manager can be used, False otherwise.
        """

    def _run_single(self, action: Action, args: list[str]) -> ActionResult:
        """Execute a single action or return a dry-run result.

        Convenience helper for single-action operators (Flatpak, Snap).
        Batch operators like APT implement their own execution strategy.

        Args:
            action: The action to execute.
            args: Command-line arguments for the package manager.

        Returns:
            ActionResult from command execution or dry-run simulation.
        """
        if self.dry_run:
            return self._dry_run_result(action)
        result = run_command(args, timeout=self._TIMEOUT)
        return self._create_result(action, result)

    def _dry_run_result(self, action: Action) -> ActionResult:
        """Create a success result for dry-run mode.

        Args:
            action: The action that would be executed.

        Returns:
            ActionResult indicating dry-run success.
        """
        return ActionResult(
            action=action,
            success=True,
            detail=f"Dry-run: would {action.action_type.value}",
        )

    def _create_result(self, action: Action, result: CommandResult) -> ActionResult:
        """Create an ActionResult from a CommandResult.

        Args:
            action: The action that was executed.
            result: The command execution result.

        Returns:
            ActionResult with appropriate success/detail info.
        """
        if result.success:
            return ActionResult(action=action, success=True, detail="Operation completed")

        error_msg = (
            result.stderr.strip() or result.stdout.strip() or f"{self.source.value} command failed"
        )
        return ActionResult(action=action, success=False, detail=error_msg)
