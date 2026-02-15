"""History entry model for tracking changes.

This module defines data structures for recording package management
operations in a history file, enabling undo functionality.
"""

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from popctl.models.package import PackageSource


class HistoryActionType(str, Enum):
    """Type of action recorded in history.

    Attributes:
        INSTALL: Package installation operation.
        REMOVE: Package removal operation (keeps config).
        PURGE: Package purge operation (removes config).
        APPLY: Batch operation from manifest apply.
        ADVISOR_APPLY: Classifications applied from AI advisor.
    """

    INSTALL = "install"
    REMOVE = "remove"
    PURGE = "purge"
    APPLY = "apply"
    ADVISOR_APPLY = "advisor_apply"
    FS_DELETE = "fs_delete"


@dataclass(frozen=True, slots=True)
class HistoryItem:
    """Single item affected by an action.

    Represents a package that was modified during an operation.

    Attributes:
        name: Package name (e.g., 'vim', 'com.spotify.Client').
        source: Package manager that handles this package.
        version: Optional version string of the package.
    """

    name: str
    source: PackageSource
    version: str | None = None

    def __post_init__(self) -> None:
        """Validate item data after initialization."""
        if not self.name:
            msg = "Package name cannot be empty"
            raise ValueError(msg)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for JSON storage.

        Returns:
            Dictionary representation of the history item.
        """
        result: dict[str, Any] = {
            "name": self.name,
            "source": self.source.value,
        }
        if self.version is not None:
            result["version"] = self.version
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HistoryItem:
        """Deserialize from dictionary.

        Args:
            data: Dictionary containing item data.

        Returns:
            HistoryItem instance.

        Raises:
            KeyError: If required fields are missing.
            ValueError: If source is invalid.
        """
        return cls(
            name=data["name"],
            source=PackageSource(data["source"]),
            version=data.get("version"),
        )


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    """Record of a single action in history.

    Immutable data structure representing a completed operation
    that can potentially be undone.

    Attributes:
        id: Unique identifier (12-character hex string from UUID).
        timestamp: When the action occurred (ISO 8601 format with timezone).
        action_type: Type of action (install, remove, etc.).
        items: Tuple of packages affected by this action.
        reversible: Whether this action can be undone.
        success: Whether the action completed successfully.
        metadata: Additional context (command, user, etc.).
    """

    id: str
    timestamp: str
    action_type: HistoryActionType
    items: tuple[HistoryItem, ...]
    reversible: bool = True
    success: bool = True
    metadata: dict[str, Any] = field(default_factory=lambda: {})

    def __post_init__(self) -> None:
        """Validate entry data after initialization."""
        if not self.id:
            msg = "History entry ID cannot be empty"
            raise ValueError(msg)
        if not self.timestamp:
            msg = "Timestamp cannot be empty"
            raise ValueError(msg)
        if not self.items:
            msg = "History entry must have at least one item"
            raise ValueError(msg)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for JSON storage.

        Returns:
            Dictionary representation of the history entry.
        """
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "action_type": self.action_type.value,
            "items": [item.to_dict() for item in self.items],
            "reversible": self.reversible,
            "success": self.success,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HistoryEntry:
        """Deserialize from dictionary.

        Args:
            data: Dictionary containing entry data.

        Returns:
            HistoryEntry instance.

        Raises:
            KeyError: If required fields are missing.
            ValueError: If action_type or item data is invalid.
        """
        items = tuple(HistoryItem.from_dict(item) for item in data["items"])
        return cls(
            id=data["id"],
            timestamp=data["timestamp"],
            action_type=HistoryActionType(data["action_type"]),
            items=items,
            reversible=data.get("reversible", True),
            success=data.get("success", True),
            metadata=data.get("metadata", {}),
        )

    def to_json_line(self) -> str:
        """Serialize to JSON line for JSONL storage.

        Returns:
            Single JSON line (no trailing newline).
        """
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_json_line(cls, line: str) -> HistoryEntry:
        """Deserialize from JSON line.

        Args:
            line: Single JSON line (with or without trailing whitespace).

        Returns:
            HistoryEntry instance.

        Raises:
            json.JSONDecodeError: If line is not valid JSON.
            KeyError: If required fields are missing.
            ValueError: If data is invalid.
        """
        data = json.loads(line.strip())
        return cls.from_dict(data)


def create_history_entry(
    action_type: HistoryActionType,
    items: list[HistoryItem],
    reversible: bool = True,
    metadata: dict[str, Any] | None = None,
) -> HistoryEntry:
    """Factory function to create a new HistoryEntry.

    Automatically generates a unique ID and current timestamp.

    Args:
        action_type: Type of action being recorded.
        items: List of packages affected by this action.
        reversible: Whether this action can be undone (default True).
        metadata: Optional additional context.

    Returns:
        New HistoryEntry with auto-generated ID and timestamp.

    Raises:
        ValueError: If items list is empty.
    """
    if not items:
        msg = "Cannot create history entry with no items"
        raise ValueError(msg)

    return HistoryEntry(
        id=uuid.uuid4().hex[:12],
        timestamp=datetime.now(UTC).isoformat(),
        action_type=action_type,
        items=tuple(items),
        reversible=reversible,
        success=True,
        metadata=metadata or {},
    )
