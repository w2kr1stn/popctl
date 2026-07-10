import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from popctl.models.package import PackageSource


class HistoryActionType(Enum):
    INSTALL = "install"
    REMOVE = "remove"
    PURGE = "purge"
    ADVISOR_APPLY = "advisor_apply"
    FS_DELETE = "fs_delete"
    CONFIG_DELETE = "config_delete"


@dataclass(frozen=True, slots=True)
class HistoryItem:
    name: str
    source: PackageSource | None = None

    def __post_init__(self) -> None:
        if not self.name:
            msg = "Package name cannot be empty"
            raise ValueError(msg)

    def to_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"name": self.name}
        if self.source is not None:
            result["source"] = self.source.value
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HistoryItem:
        raw_source = data.get("source")
        return cls(
            name=data["name"],
            source=PackageSource(raw_source) if raw_source is not None else None,
        )


@dataclass(frozen=True, slots=True)
class HistoryEntry:
    id: str
    timestamp: str
    action_type: HistoryActionType
    items: tuple[HistoryItem, ...]
    reversible: bool = True
    metadata: dict[str, Any] = field(default_factory=lambda: {})

    def __post_init__(self) -> None:
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
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "action_type": self.action_type.value,
            "items": [item.to_dict() for item in self.items],
            "reversible": self.reversible,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HistoryEntry:
        items = tuple(HistoryItem.from_dict(item) for item in data["items"])
        return cls(
            id=data["id"],
            timestamp=data["timestamp"],
            action_type=HistoryActionType(data["action_type"]),
            items=items,
            reversible=data.get("reversible", True),
            metadata=dict(data.get("metadata", {})),
        )

    def to_json_line(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_json_line(cls, line: str) -> HistoryEntry:
        data = json.loads(line.strip())
        return cls.from_dict(data)


def create_history_entry(
    action_type: HistoryActionType,
    items: list[HistoryItem],
    reversible: bool = True,
    metadata: dict[str, str] | None = None,
) -> HistoryEntry:
    return HistoryEntry(
        id=uuid.uuid4().hex[:12],
        timestamp=datetime.now(UTC).isoformat(),
        action_type=action_type,
        items=tuple(items),
        reversible=reversible,
        metadata=dict(metadata) if metadata else {},
    )
