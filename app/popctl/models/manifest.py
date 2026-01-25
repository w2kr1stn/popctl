"""Manifest models for declarative system configuration.

This module defines the Pydantic models representing the manifest.toml
structure that describes the desired system state.
"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


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
PackageSourceType = Literal["apt", "flatpak"]


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


class Manifest(BaseModel):
    """Complete manifest representing desired system state.

    The manifest is the central configuration file that describes
    which packages should be installed or removed from the system.

    Attributes:
        meta: Metadata section with version and timestamps.
        system: System configuration with machine details.
        packages: Package configuration with keep/remove lists.
    """

    model_config = ConfigDict(extra="forbid")

    meta: Annotated[ManifestMeta, Field(description="Manifest metadata")]
    system: Annotated[SystemConfig, Field(description="System configuration")]
    packages: Annotated[PackageConfig, Field(description="Package configuration")]

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

    @property
    def package_count(self) -> int:
        """Total number of packages tracked in the manifest."""
        return len(self.packages.keep) + len(self.packages.remove)
