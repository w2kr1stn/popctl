"""Pydantic models for the manifest [filesystem] section.

This module defines the configuration models for filesystem entries
in the manifest.toml file, supporting keep/remove lists for
orphaned directories and files.
"""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FilesystemEntry(BaseModel):
    """Entry for a single filesystem path in the manifest.

    Describes a filesystem path's classification reason and category.

    Attributes:
        reason: Human-readable explanation for the classification.
        category: Optional grouping category (e.g., "config", "cache", "data").
    """

    model_config = ConfigDict(extra="forbid")

    reason: Annotated[str | None, Field(description="Reason for classification")] = None
    category: Annotated[str | None, Field(description="Grouping category")] = None


class FilesystemConfig(BaseModel):
    """Filesystem configuration section of the manifest.

    Contains dictionaries of filesystem paths organized by their
    desired state (keep or remove).

    Attributes:
        keep: Paths to preserve (not delete during cleanup).
        remove: Paths marked for deletion during cleanup.
    """

    model_config = ConfigDict(extra="forbid")

    keep: Annotated[
        dict[str, FilesystemEntry],
        Field(default_factory=dict, description="Paths to preserve"),
    ]
    remove: Annotated[
        dict[str, FilesystemEntry],
        Field(default_factory=dict, description="Paths to delete"),
    ]

    @model_validator(mode="after")
    def validate_no_duplicates(self) -> FilesystemConfig:
        """Validate that no path appears in both keep and remove lists."""
        duplicates = set(self.keep.keys()) & set(self.remove.keys())
        if duplicates:
            msg = f"Paths cannot be in both keep and remove: {duplicates}"
            raise ValueError(msg)
        return self
