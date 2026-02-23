"""Pydantic models for the manifest [configs] section.

This module defines the configuration models for config entries
in the manifest.toml file, supporting keep/remove lists for
orphaned configuration files and dotfiles.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ConfigEntry(BaseModel):
    """Entry for a single config path in the manifest.

    Describes a config path's classification reason and category.

    Attributes:
        reason: Human-readable explanation for the classification.
        category: Optional grouping category (e.g., "editor", "obsolete").
    """

    model_config = ConfigDict(extra="forbid")

    reason: Annotated[str | None, Field(description="Reason for classification")] = None
    category: Annotated[str | None, Field(description="Grouping category")] = None


class ConfigsConfig(BaseModel):
    """Config configuration section of the manifest.

    Contains dictionaries of config paths organized by their
    desired state (keep or remove).

    Attributes:
        keep: Config paths to preserve (not delete during cleanup).
        remove: Config paths marked for deletion during cleanup.
    """

    model_config = ConfigDict(extra="forbid")

    keep: Annotated[
        dict[str, ConfigEntry],
        Field(default_factory=dict, description="Configs to preserve"),
    ]
    remove: Annotated[
        dict[str, ConfigEntry],
        Field(default_factory=dict, description="Configs to delete"),
    ]

    @model_validator(mode="after")
    def validate_no_duplicates(self) -> ConfigsConfig:
        """Validate that no path appears in both keep and remove lists."""
        duplicates = set(self.keep.keys()) & set(self.remove.keys())
        if duplicates:
            msg = f"Paths cannot be in both keep and remove: {duplicates}"
            raise ValueError(msg)
        return self
