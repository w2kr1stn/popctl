"""Action models for package operations.

This module defines data structures for representing package management
actions (install, remove, purge) and their execution results.
"""

from dataclasses import dataclass
from enum import Enum

from popctl.models.package import PackageSource


class ActionType(Enum):
    """Type of package management action.

    Attributes:
        INSTALL: Install a package that is not currently installed.
        REMOVE: Remove a package but keep configuration files.
        PURGE: Remove a package including all configuration files (APT only).
    """

    INSTALL = "install"
    REMOVE = "remove"
    PURGE = "purge"


@dataclass(frozen=True, slots=True)
class Action:
    """Represents a single package management action to be executed.

    This is an immutable data structure that describes what operation
    should be performed on which package.

    Attributes:
        action_type: The type of action (install, remove, or purge).
        package: Name of the package to operate on.
        source: Package manager that handles this package.
        reason: Optional explanation for why this action is being taken.
    """

    action_type: ActionType
    package: str
    source: PackageSource
    reason: str | None = None

    def __post_init__(self) -> None:
        """Validate action data after initialization."""
        if not self.package:
            msg = "Package name cannot be empty"
            raise ValueError(msg)

    @property
    def is_install(self) -> bool:
        """Check if this is an install action."""
        return self.action_type == ActionType.INSTALL

    @property
    def is_remove(self) -> bool:
        """Check if this is a remove action."""
        return self.action_type == ActionType.REMOVE

    @property
    def is_purge(self) -> bool:
        """Check if this is a purge action."""
        return self.action_type == ActionType.PURGE

    @property
    def is_destructive(self) -> bool:
        """Check if this action removes a package (remove or purge)."""
        return self.action_type in (ActionType.REMOVE, ActionType.PURGE)


@dataclass(frozen=True, slots=True)
class ActionResult:
    """Result of executing a package management action.

    This immutable data structure captures the outcome of an action,
    including success status and any error information.

    Attributes:
        action: The action that was executed.
        success: Whether the action completed successfully.
        message: Optional success message or additional information.
        error: Optional error message if the action failed.
    """

    action: Action
    success: bool
    message: str | None = None
    error: str | None = None

    @property
    def failed(self) -> bool:
        """Check if the action failed."""
        return not self.success


def create_install_action(
    package: str,
    source: PackageSource,
    reason: str | None = None,
) -> Action:
    """Create an install action for a package.

    Args:
        package: Name of the package to install.
        source: Package manager that handles this package.
        reason: Optional explanation for the installation.

    Returns:
        Action configured for installation.
    """
    return Action(
        action_type=ActionType.INSTALL,
        package=package,
        source=source,
        reason=reason,
    )


def create_remove_action(
    package: str,
    source: PackageSource,
    reason: str | None = None,
    purge: bool = False,
) -> Action:
    """Create a remove action for a package.

    Args:
        package: Name of the package to remove.
        source: Package manager that handles this package.
        reason: Optional explanation for the removal.
        purge: If True, create a purge action instead of remove.

    Returns:
        Action configured for removal or purge.
    """
    action_type = ActionType.PURGE if purge else ActionType.REMOVE
    return Action(
        action_type=action_type,
        package=package,
        source=source,
        reason=reason,
    )
