"""Abstract base class for package operators.

This module defines the Operator interface that all package management
operators must implement.
"""

from abc import ABC, abstractmethod

from popctl.models.action import Action, ActionResult
from popctl.models.package import PackageSource


class Operator(ABC):
    """Abstract base class for all package operators.

    Operators are responsible for executing package management actions
    (install, remove, purge) for a specific package manager.

    Attributes:
        dry_run: If True, only simulate actions without executing them.

    Example:
        >>> operator = AptOperator(dry_run=True)
        >>> if operator.is_available():
        ...     results = operator.install(["htop", "neovim"])
        ...     for result in results:
        ...         print(f"{result.action.package}: {result.success}")
    """

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

    @property
    @abstractmethod
    def source(self) -> PackageSource:
        """Return the package source this operator handles.

        Returns:
            PackageSource enum value (APT, FLATPAK, or SNAP).
        """

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

    def execute(self, actions: list[Action]) -> list[ActionResult]:
        """Execute a list of actions.

        This is a convenience method that dispatches actions to the
        appropriate install/remove method based on action type.

        Args:
            actions: List of Action objects to execute.

        Returns:
            List of ActionResult for each action.

        Raises:
            RuntimeError: If the package manager is not available.
            ValueError: If an action's source doesn't match this operator.
        """
        if not self.is_available():
            msg = f"{self.source.value.upper()} package manager is not available"
            raise RuntimeError(msg)

        # Validate all actions belong to this operator
        for action in actions:
            if action.source != self.source:
                msg = (
                    f"Action source {action.source.value} doesn't match "
                    f"operator source {self.source.value}"
                )
                raise ValueError(msg)

        # Group actions by type for batch processing
        install_packages: list[str] = []
        remove_packages: list[str] = []
        purge_packages: list[str] = []

        for action in actions:
            if action.is_install:
                install_packages.append(action.package)
            elif action.is_purge:
                purge_packages.append(action.package)
            elif action.is_remove:
                remove_packages.append(action.package)

        results: list[ActionResult] = []

        if install_packages:
            results.extend(self.install(install_packages))

        if remove_packages:
            results.extend(self.remove(remove_packages, purge=False))

        if purge_packages:
            results.extend(self.remove(purge_packages, purge=True))

        return results
