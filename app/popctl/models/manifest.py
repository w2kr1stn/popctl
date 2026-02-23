"""Manifest models for declarative system configuration.

This module defines the Pydantic models representing the manifest.toml
structure that describes the desired system state.
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ManifestMeta(BaseModel):
    """Metadata section of the manifest.

    Contains timestamps for tracking when the manifest was created
    and last modified.

    Attributes:
        created: Timestamp when manifest was first created.
        updated: Timestamp when manifest was last modified.
    """

    model_config = ConfigDict(extra="ignore")

    created: datetime
    updated: datetime


class SystemConfig(BaseModel):
    """System configuration section of the manifest.

    Defines the target system properties including machine name
    and base operating system.

    Attributes:
        name: Machine hostname or identifier.
        base: Base OS identifier (e.g., "pop-os-24.04").
    """

    model_config = ConfigDict(extra="ignore")

    name: str
    base: str = "pop-os-24.04"


# Type alias for package source in manifest
PackageSourceType = Literal["apt", "flatpak", "snap"]


def _validate_keep_remove_disjoint(
    keep: Mapping[str, object], remove: Mapping[str, object], noun: str
) -> None:
    """Raise ValueError if any key appears in both keep and remove."""
    duplicates = set(keep.keys()) & set(remove.keys())
    if duplicates:
        msg = f"{noun} cannot be in both keep and remove: {duplicates}"
        raise ValueError(msg)


class DomainEntry(BaseModel):
    """Entry for a single path in the manifest (filesystem or config).

    Describes a path's classification reason and category.

    Attributes:
        reason: Human-readable explanation for the classification.
        category: Optional grouping category (e.g., "config", "cache", "editor").
    """

    model_config = ConfigDict(extra="forbid")

    reason: str | None = None
    category: str | None = None


class DomainConfig(BaseModel):
    """Keep/remove configuration for a domain section.

    Contains dictionaries of paths organized by their desired state
    (keep or remove). Used for both [filesystem] and [configs] manifest sections.

    Attributes:
        keep: Paths to preserve (not delete during cleanup).
        remove: Paths marked for deletion during cleanup.
    """

    model_config = ConfigDict(extra="forbid")

    keep: dict[str, DomainEntry] = Field(default_factory=dict)
    remove: dict[str, DomainEntry] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_no_duplicates(self) -> Self:
        """Validate that no path appears in both keep and remove lists."""
        _validate_keep_remove_disjoint(self.keep, self.remove, "Paths")
        return self


class PackageEntry(BaseModel):
    """Entry for a single package in the manifest.

    Describes a package's source. The keep/remove state is determined
    by which dict the entry belongs to (``packages.keep`` vs ``packages.remove``).

    Attributes:
        source: Package manager that provides this package ("apt" or "flatpak").
        reason: Optional explanation for why this package is tracked.
    """

    model_config = ConfigDict(extra="ignore")

    source: PackageSourceType
    reason: str | None = None


class PackageConfig(BaseModel):
    """Package configuration section of the manifest.

    Contains dictionaries of packages organized by their desired state.

    Attributes:
        keep: Packages to keep installed.
        remove: Packages marked for removal.
    """

    model_config = ConfigDict(extra="forbid")

    keep: dict[str, PackageEntry] = Field(default_factory=dict)
    remove: dict[str, PackageEntry] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_no_duplicates(self) -> PackageConfig:
        """Validate that no package appears in both keep and remove lists."""
        _validate_keep_remove_disjoint(self.keep, self.remove, "Packages")
        return self


class Manifest(BaseModel):
    """Complete manifest representing desired system state.

    The manifest is the central configuration file that describes
    which packages should be installed or removed from the system.

    Attributes:
        meta: Metadata section with version and timestamps.
        system: System configuration with machine details.
        packages: Package configuration with keep/remove lists.
        filesystem: Optional filesystem cleanup configuration.
        configs: Optional config cleanup configuration.
    """

    model_config = ConfigDict(extra="forbid")

    meta: ManifestMeta
    system: SystemConfig
    packages: PackageConfig
    filesystem: DomainConfig | None = None
    configs: DomainConfig | None = None

    def get_keep_packages(self, source: PackageSourceType | None = None) -> dict[str, PackageEntry]:
        """Get packages marked as 'keep', optionally filtered by source.

        Args:
            source: Filter by package source ("apt" or "flatpak"). If None, returns all.

        Returns:
            Dictionary of package names to PackageEntry for packages to keep.
        """
        if source is None:
            return self.packages.keep
        return {name: entry for name, entry in self.packages.keep.items() if entry.source == source}

    def get_remove_packages(
        self, source: PackageSourceType | None = None
    ) -> dict[str, PackageEntry]:
        """Get packages marked for removal, optionally filtered by source.

        Args:
            source: Filter by package source ("apt" or "flatpak"). If None, returns all.

        Returns:
            Dictionary of package names to PackageEntry for packages to remove.
        """
        if source is None:
            return self.packages.remove
        return {
            name: entry for name, entry in self.packages.remove.items() if entry.source == source
        }

    def _get_domain_remove(
        self, domain: Literal["filesystem", "configs"]
    ) -> dict[str, DomainEntry]:
        """Get remove paths for the specified domain section."""
        section = self.filesystem if domain == "filesystem" else self.configs
        return section.remove if section is not None else {}

    def get_fs_remove_paths(self) -> dict[str, DomainEntry]:
        """Get filesystem paths marked for removal.

        Returns:
            Dictionary of path strings to DomainEntry for paths to delete.
            Returns empty dict if no filesystem section is configured.
        """
        return self._get_domain_remove("filesystem")

    def get_config_remove_paths(self) -> dict[str, DomainEntry]:
        """Get config paths marked for removal.

        Returns:
            Dictionary of path strings to DomainEntry for configs to delete.
            Returns empty dict if no configs section is configured.
        """
        return self._get_domain_remove("configs")
