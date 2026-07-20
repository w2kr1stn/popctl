from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from popctl.models.package import PackageSource


class ReplayMode(StrEnum):
    REPORT_ONLY = "report-only"
    REPLAY = "replay"
    BLOCKED = "blocked"


class AptSourceFormat(StrEnum):
    LEGACY = "legacy"
    DEB822 = "deb822"


class FlatpakScope(StrEnum):
    USER = "user"
    SYSTEM = "system"


@dataclass(frozen=True, slots=True)
class SourceLocator:
    manager: PackageSource
    parts: tuple[str, ...]


class SourceModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SourcePlatform(SourceModel):
    distro_id: str
    codename: str


class SignedByBinding(SourceModel):
    key_paths: tuple[str, ...] = ()
    fingerprint_selectors: tuple[str, ...] = ()
    embedded_armor: str | None = None


class AptSource(SourceModel):
    id: str
    capture_path: str
    format: AptSourceFormat
    ordinal: int = Field(ge=0)
    managed_target: str
    verbatim_stanza: str
    key_ids: tuple[str, ...]
    signed_by: SignedByBinding
    replay_mode: ReplayMode
    ppa_display: str | None = None

    @property
    def capture_locator(self) -> SourceLocator:
        return SourceLocator(
            manager=PackageSource.APT,
            parts=(self.capture_path, str(self.ordinal)),
        )

    @property
    def managed_target_locator(self) -> SourceLocator:
        return SourceLocator(
            manager=PackageSource.APT,
            parts=(self.managed_target,),
        )


class AptKey(SourceModel):
    id: str
    target_path: str
    armor: str
    fingerprints: tuple[str, ...]


class FlatpakRemote(SourceModel):
    name: str
    scope: FlatpakScope
    url: str
    gpg_verify: bool
    gpg_key_armor: str
    gpg_fingerprints: tuple[str, ...]
    replay_mode: ReplayMode

    @property
    def locator(self) -> SourceLocator:
        return SourceLocator(
            manager=PackageSource.FLATPAK,
            parts=(self.scope, self.name),
        )


class FlatpakApp(SourceModel):
    id: str
    origin: str
    scope: FlatpakScope
    arch: str
    branch: str

    @property
    def locator(self) -> SourceLocator:
        return SourceLocator(
            manager=PackageSource.FLATPAK,
            parts=(self.scope, self.id, self.arch, self.branch),
        )


class SnapChannel(SourceModel):
    name: str
    channel: str
    replay_mode: ReplayMode

    @property
    def locator(self) -> SourceLocator:
        return SourceLocator(manager=PackageSource.SNAP, parts=(self.name,))


class AptSources(SourceModel):
    entries: tuple[AptSource, ...] = ()
    keys: tuple[AptKey, ...] = ()


class FlatpakSources(SourceModel):
    remotes: tuple[FlatpakRemote, ...] = ()
    apps: tuple[FlatpakApp, ...] = ()


class SnapSources(SourceModel):
    packages: tuple[SnapChannel, ...] = ()


class SourcesConfig(SourceModel):
    platform: SourcePlatform
    apt: AptSources = Field(default_factory=AptSources)
    flatpak: FlatpakSources = Field(default_factory=FlatpakSources)
    snap: SnapSources = Field(default_factory=SnapSources)
