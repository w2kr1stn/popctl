from dataclasses import dataclass
from enum import Enum
from typing import Self

from popctl.models.package import PackageSource
from popctl.sources.models import FlatpakApp, FlatpakScope, SnapChannel


class ActionType(Enum):
    INSTALL = "install"
    REMOVE = "remove"
    PURGE = "purge"


_PURGE_SOURCES: frozenset[PackageSource] = frozenset({PackageSource.APT, PackageSource.SNAP})


@dataclass(frozen=True, slots=True)
class SourceInstallContext:
    flatpak_remote: str | None = None
    flatpak_scope: FlatpakScope | None = None
    flatpak_arch: str | None = None
    flatpak_branch: str | None = None
    snap_channel: str | None = None

    def __post_init__(self) -> None:
        flatpak_values = (
            self.flatpak_remote,
            self.flatpak_scope,
            self.flatpak_arch,
            self.flatpak_branch,
        )
        has_flatpak = any(value is not None for value in flatpak_values)
        if has_flatpak and not all(value is not None for value in flatpak_values):
            msg = "Flatpak install context requires remote, scope, arch, and branch"
            raise ValueError(msg)
        if has_flatpak and self.snap_channel is not None:
            msg = "Source install context cannot mix Flatpak and Snap settings"
            raise ValueError(msg)
        if not has_flatpak and self.snap_channel is None:
            msg = "Source install context cannot be empty"
            raise ValueError(msg)

    @classmethod
    def for_flatpak(cls, app: FlatpakApp) -> Self:
        return cls(
            flatpak_remote=app.origin,
            flatpak_scope=app.scope,
            flatpak_arch=app.arch,
            flatpak_branch=app.branch,
        )

    @classmethod
    def for_snap(cls, channel: SnapChannel) -> Self:
        return cls(snap_channel=channel.channel)

    @property
    def is_flatpak(self) -> bool:
        return self.flatpak_remote is not None


@dataclass(frozen=True, slots=True)
class Action:
    action_type: ActionType
    package: str
    source: PackageSource
    source_install_context: SourceInstallContext | None = None

    def __post_init__(self) -> None:
        if not self.package:
            msg = "Package name cannot be empty"
            raise ValueError(msg)
        if self.action_type == ActionType.PURGE and self.source not in _PURGE_SOURCES:
            msg = "PURGE action is only valid for APT and SNAP packages"
            raise ValueError(msg)
        if self.source_install_context is None:
            return
        if self.action_type is not ActionType.INSTALL:
            msg = "Source install context is only valid for install actions"
            raise ValueError(msg)
        if self.source is PackageSource.FLATPAK and self.source_install_context.is_flatpak:
            return
        if self.source is PackageSource.SNAP and not self.source_install_context.is_flatpak:
            return
        msg = "Source install context does not match the package source"
        raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class ActionResult:
    action: Action
    success: bool
    detail: str | None = None

    @property
    def failed(self) -> bool:
        return not self.success
