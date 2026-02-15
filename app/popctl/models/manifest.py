"""Manifest models for declarative system configuration.

This module defines the Pydantic models representing the manifest.toml
structure that describes the desired system state.
"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from popctl.configs.manifest import ConfigEntry, ConfigsConfig
from popctl.filesystem.manifest import FilesystemConfig, FilesystemEntry


class ManifestMeta(BaseModel):
    """Metadata section of the manifest.

    Contains version information and timestamps for tracking
    when the manifest was created and last modified.

    Attributes:
        version: Manifest schema version (e.g., "1.0").
        created: Timestamp when manifest was first created.
        updated: Timestamp when manifest was last modified.
    """

    model_config = ConfigDict(extra="forbid")

    version: Annotated[str, Field(description="Manifest schema version")] = "1.0"
    created: Annotated[datetime, Field(description="Timestamp when manifest was created")]
    updated: Annotated[datetime, Field(description="Timestamp when manifest was last modified")]


class SystemConfig(BaseModel):
    """System configuration section of the manifest.

    Defines the target system properties including machine name
    and base operating system.

    Attributes:
        name: Machine hostname or identifier.
        base: Base OS identifier (e.g., "pop-os-24.04").
        description: Optional description of the machine/configuration.
    """

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(description="Machine hostname or identifier")]
    base: Annotated[str, Field(description="Base OS identifier")] = "pop-os-24.04"
    description: Annotated[str | None, Field(description="Machine description")] = None


# Type alias for package status in manifest
PackageStatusType = Literal["keep", "remove", "optional"]

# Type alias for package source in manifest
PackageSourceType = Literal["apt", "flatpak", "snap"]


class PackageEntry(BaseModel):
    """Entry for a single package in the manifest.

    Describes a package's source and desired state.

    Attributes:
        source: Package manager that provides this package ("apt" or "flatpak").
        status: Desired state of the package ("keep", "remove", or "optional").
        reason: Optional explanation for why this package is tracked.
    """

    model_config = ConfigDict(extra="forbid")

    source: Annotated[PackageSourceType, Field(description="Package manager source")]
    status: Annotated[
        PackageStatusType,
        Field(description="Desired package state"),
    ] = "keep"
    reason: Annotated[str | None, Field(description="Reason for tracking")] = None


class PackageConfig(BaseModel):
    """Package configuration section of the manifest.

    Contains dictionaries of packages organized by their desired state.

    Attributes:
        keep: Packages to keep installed.
        remove: Packages marked for removal.
    """

    model_config = ConfigDict(extra="forbid")

    keep: Annotated[
        dict[str, PackageEntry],
        Field(default_factory=dict, description="Packages to keep installed"),
    ]
    remove: Annotated[
        dict[str, PackageEntry],
        Field(default_factory=dict, description="Packages to remove"),
    ]

    @model_validator(mode="after")
    def validate_no_duplicates(self) -> PackageConfig:
        """Validate that no package appears in both keep and remove lists."""
        duplicates = set(self.keep.keys()) & set(self.remove.keys())
        if duplicates:
            msg = f"Packages cannot be in both keep and remove: {duplicates}"
            raise ValueError(msg)
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

    meta: Annotated[ManifestMeta, Field(description="Manifest metadata")]
    system: Annotated[SystemConfig, Field(description="System configuration")]
    packages: Annotated[PackageConfig, Field(description="Package configuration")]
    filesystem: Annotated[
        FilesystemConfig | None,
        Field(description="Filesystem cleanup configuration"),
    ] = None
    configs: Annotated[
        ConfigsConfig | None,
        Field(description="Config cleanup configuration"),
    ] = None

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

    def get_fs_keep_paths(self) -> dict[str, FilesystemEntry]:
        """Get filesystem paths marked as 'keep'.

        Returns:
            Dictionary of path strings to FilesystemEntry for paths to preserve.
            Returns empty dict if no filesystem section is configured.
        """
        if self.filesystem is None:
            return {}
        return self.filesystem.keep

    def get_fs_remove_paths(self) -> dict[str, FilesystemEntry]:
        """Get filesystem paths marked for removal.

        Returns:
            Dictionary of path strings to FilesystemEntry for paths to delete.
            Returns empty dict if no filesystem section is configured.
        """
        if self.filesystem is None:
            return {}
        return self.filesystem.remove

    def get_config_keep_paths(self) -> dict[str, ConfigEntry]:
        """Get config paths marked as 'keep'.

        Returns:
            Dictionary of path strings to ConfigEntry for configs to preserve.
            Returns empty dict if no configs section is configured.
        """
        if self.configs is None:
            return {}
        return self.configs.keep

    def get_config_remove_paths(self) -> dict[str, ConfigEntry]:
        """Get config paths marked for removal.

        Returns:
            Dictionary of path strings to ConfigEntry for configs to delete.
            Returns empty dict if no configs section is configured.
        """
        if self.configs is None:
            return {}
        return self.configs.remove

    @property
    def package_count(self) -> int:
        """Total number of packages tracked in the manifest."""
        return len(self.packages.keep) + len(self.packages.remove)
