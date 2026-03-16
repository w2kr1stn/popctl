from collections.abc import Mapping
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ManifestMeta(BaseModel):
    model_config = ConfigDict(extra="ignore")

    created: datetime
    updated: datetime


class SystemConfig(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str


# Type alias for package source in manifest
PackageSourceType = Literal["apt", "flatpak", "snap"]


def _validate_keep_remove_disjoint(
    keep: Mapping[str, object], remove: Mapping[str, object], noun: str
) -> None:
    """Raise ValueError if any key appears in both keep and remove."""
    duplicates = keep.keys() & remove.keys()
    if duplicates:
        msg = f"{noun} cannot be in both keep and remove: {duplicates}"
        raise ValueError(msg)


class DomainEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str | None = None
    category: str | None = None


class DomainConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keep: dict[str, DomainEntry] = Field(default_factory=dict)
    remove: dict[str, DomainEntry] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_no_duplicates(self) -> Self:
        """Validate that no path appears in both keep and remove lists."""
        _validate_keep_remove_disjoint(self.keep, self.remove, "Paths")
        return self


class PackageEntry(BaseModel):
    model_config = ConfigDict(extra="ignore")

    source: PackageSourceType
    reason: str | None = None


class PackageConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    keep: dict[str, PackageEntry] = Field(default_factory=dict)
    remove: dict[str, PackageEntry] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_no_duplicates(self) -> PackageConfig:
        """Validate that no package appears in both keep and remove lists."""
        _validate_keep_remove_disjoint(self.keep, self.remove, "Packages")
        return self


class Manifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meta: ManifestMeta
    system: SystemConfig
    packages: PackageConfig
    filesystem: DomainConfig | None = None
    configs: DomainConfig | None = None

    def get_keep_packages(self, source: PackageSourceType | None = None) -> dict[str, PackageEntry]:
        if source is None:
            return self.packages.keep
        return {name: entry for name, entry in self.packages.keep.items() if entry.source == source}

    def get_remove_packages(
        self, source: PackageSourceType | None = None
    ) -> dict[str, PackageEntry]:
        if source is None:
            return self.packages.remove
        return {
            name: entry for name, entry in self.packages.remove.items() if entry.source == source
        }

    def get_domain_remove(
        self, domain: Literal["filesystem", "configs"]
    ) -> dict[str, DomainEntry]:
        section = self.filesystem if domain == "filesystem" else self.configs
        return section.remove if section is not None else {}
