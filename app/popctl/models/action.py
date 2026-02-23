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
        PURGE: Remove a package including all configuration files (APT and SNAP).
    """

    INSTALL = "install"
    REMOVE = "remove"
    PURGE = "purge"


_PURGE_SOURCES: frozenset[PackageSource] = frozenset({PackageSource.APT, PackageSource.SNAP})


@dataclass(frozen=True, slots=True)
class Action:
    """Represents a single package management action to be executed.

    This is an immutable data structure that describes what operation
    should be performed on which package.

    Attributes:
        action_type: The type of action (install, remove, or purge).
        package: Name of the package to operate on.
        source: Package manager that handles this package.
    """

    action_type: ActionType
    package: str
    source: PackageSource

    def __post_init__(self) -> None:
        """Validate action data after initialization."""
        if not self.package:
            msg = "Package name cannot be empty"
            raise ValueError(msg)
        if self.action_type == ActionType.PURGE and self.source not in _PURGE_SOURCES:
            msg = "PURGE action is only valid for APT and SNAP packages"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class ActionResult:
    """Result of executing a package management action.

    This immutable data structure captures the outcome of an action,
    including success status and any detail information.

    Attributes:
        action: The action that was executed.
        success: Whether the action completed successfully.
        detail: Optional detail message (success info or error description).
    """

    action: Action
    success: bool
    detail: str | None = None

    @property
    def failed(self) -> bool:
        """Check if the action failed."""
        return not self.success
