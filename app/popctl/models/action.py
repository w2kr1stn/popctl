from dataclasses import dataclass
from enum import Enum

from popctl.models.package import PackageSource


class ActionType(Enum):
    INSTALL = "install"
    REMOVE = "remove"
    PURGE = "purge"


_PURGE_SOURCES: frozenset[PackageSource] = frozenset({PackageSource.APT, PackageSource.SNAP})


@dataclass(frozen=True, slots=True)
class Action:
    action_type: ActionType
    package: str
    source: PackageSource

    def __post_init__(self) -> None:
        if not self.package:
            msg = "Package name cannot be empty"
            raise ValueError(msg)
        if self.action_type == ActionType.PURGE and self.source not in _PURGE_SOURCES:
            msg = "PURGE action is only valid for APT and SNAP packages"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class ActionResult:
    action: Action
    success: bool
    detail: str | None = None

    @property
    def failed(self) -> bool:
        return not self.success
