"""Shared Pydantic models for manifest [filesystem] and [configs] sections.

These models define the configuration structure for domain entries
(filesystem paths and config paths) in the manifest.toml file,
supporting keep/remove lists for orphaned entries.
"""

from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class DomainEntry(BaseModel):
    """Entry for a single path in the manifest (filesystem or config).

    Describes a path's classification reason and category.

    Attributes:
        reason: Human-readable explanation for the classification.
        category: Optional grouping category (e.g., "config", "cache", "editor").
    """

    model_config = ConfigDict(extra="forbid")

    reason: Annotated[str | None, Field(description="Reason for classification")] = None
    category: Annotated[str | None, Field(description="Grouping category")] = None


class DomainConfig(BaseModel):
    """Keep/remove configuration for a domain section.

    Contains dictionaries of paths organized by their desired state
    (keep or remove). Used for both [filesystem] and [configs] manifest sections.

    Attributes:
        keep: Paths to preserve (not delete during cleanup).
        remove: Paths marked for deletion during cleanup.
    """

    model_config = ConfigDict(extra="forbid")

    keep: Annotated[
        dict[str, DomainEntry],
        Field(default_factory=dict, description="Paths to preserve"),
    ]
    remove: Annotated[
        dict[str, DomainEntry],
        Field(default_factory=dict, description="Paths to delete"),
    ]

    @model_validator(mode="after")
    def validate_no_duplicates(self) -> Self:
        """Validate that no path appears in both keep and remove lists."""
        duplicates = set(self.keep.keys()) & set(self.remove.keys())
        if duplicates:
            msg = f"Paths cannot be in both keep and remove: {duplicates}"
            raise ValueError(msg)
        return self
