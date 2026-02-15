"""Unit tests for History models.

Tests for the HistoryEntry, HistoryItem, and HistoryActionType data structures.
"""

import json
from datetime import UTC, datetime
from typing import Any

import pytest
from popctl.models.history import (
    HistoryActionType,
    HistoryEntry,
    HistoryItem,
    create_history_entry,
)
from popctl.models.package import PackageSource


class TestHistoryActionType:
    """Tests for HistoryActionType enum."""

    def test_action_type_values(self) -> None:
        """HistoryActionType has expected values."""
        assert HistoryActionType.INSTALL.value == "install"
        assert HistoryActionType.REMOVE.value == "remove"
        assert HistoryActionType.PURGE.value == "purge"
        assert HistoryActionType.APPLY.value == "apply"
        assert HistoryActionType.ADVISOR_APPLY.value == "advisor_apply"

    def test_action_type_count(self) -> None:
        """HistoryActionType has exactly 6 members."""
        assert len(HistoryActionType) == 6

    def test_fs_delete_action_type(self) -> None:
        """FS_DELETE is a valid HistoryActionType value."""
        assert HistoryActionType.FS_DELETE.value == "fs_delete"
        assert HistoryActionType("fs_delete") == HistoryActionType.FS_DELETE

    def test_action_type_is_str_enum(self) -> None:
        """HistoryActionType inherits from str for JSON serialization."""
        assert isinstance(HistoryActionType.INSTALL, str)
        assert HistoryActionType.INSTALL == "install"


class TestHistoryItem:
    """Tests for HistoryItem dataclass."""

    def test_create_history_item(self) -> None:
        """Can create a history item with required fields."""
        item = HistoryItem(
            name="vim",
            source=PackageSource.APT,
        )
        assert item.name == "vim"
        assert item.source == PackageSource.APT
        assert item.version is None

    def test_create_history_item_with_version(self) -> None:
        """Can create a history item with version."""
        item = HistoryItem(
            name="vim",
            source=PackageSource.APT,
            version="9.0.1234",
        )
        assert item.version == "9.0.1234"

    def test_history_item_is_frozen(self) -> None:
        """HistoryItem is immutable."""
        item = HistoryItem(name="vim", source=PackageSource.APT)
        with pytest.raises(AttributeError):
            item.name = "nano"  # type: ignore[misc]

    def test_history_item_empty_name_raises(self) -> None:
        """HistoryItem with empty name raises ValueError."""
        with pytest.raises(ValueError, match="Package name cannot be empty"):
            HistoryItem(name="", source=PackageSource.APT)

    def test_history_item_to_dict(self) -> None:
        """to_dict serializes item correctly."""
        item = HistoryItem(
            name="vim",
            source=PackageSource.APT,
            version="9.0",
        )
        result = item.to_dict()
        assert result == {
            "name": "vim",
            "source": "apt",
            "version": "9.0",
        }

    def test_history_item_to_dict_without_version(self) -> None:
        """to_dict omits version if None."""
        item = HistoryItem(name="vim", source=PackageSource.APT)
        result = item.to_dict()
        assert "version" not in result
        assert result == {"name": "vim", "source": "apt"}

    def test_history_item_from_dict(self) -> None:
        """from_dict deserializes item correctly."""
        data = {"name": "vim", "source": "apt", "version": "9.0"}
        item = HistoryItem.from_dict(data)
        assert item.name == "vim"
        assert item.source == PackageSource.APT
        assert item.version == "9.0"

    def test_history_item_from_dict_without_version(self) -> None:
        """from_dict handles missing version."""
        data = {"name": "vim", "source": "apt"}
        item = HistoryItem.from_dict(data)
        assert item.version is None

    def test_history_item_flatpak(self) -> None:
        """Can create history item for Flatpak packages."""
        item = HistoryItem(
            name="com.spotify.Client",
            source=PackageSource.FLATPAK,
            version="1.2.3",
        )
        assert item.source == PackageSource.FLATPAK


class TestHistoryEntry:
    """Tests for HistoryEntry dataclass."""

    @pytest.fixture
    def sample_items(self) -> tuple[HistoryItem, ...]:
        """Create sample history items for testing."""
        return (
            HistoryItem(name="vim", source=PackageSource.APT, version="9.0"),
            HistoryItem(name="htop", source=PackageSource.APT),
        )

    @pytest.fixture
    def sample_entry(self, sample_items: tuple[HistoryItem, ...]) -> HistoryEntry:
        """Create a sample history entry for testing."""
        return HistoryEntry(
            id="abc123def456",
            timestamp="2026-01-25T14:30:00+00:00",
            action_type=HistoryActionType.INSTALL,
            items=sample_items,
            reversible=True,
            success=True,
            metadata={"command": "popctl apply"},
        )

    def test_create_history_entry(self, sample_items: tuple[HistoryItem, ...]) -> None:
        """Can create a history entry with all fields."""
        entry = HistoryEntry(
            id="abc123",
            timestamp="2026-01-25T14:30:00+00:00",
            action_type=HistoryActionType.INSTALL,
            items=sample_items,
        )
        assert entry.id == "abc123"
        assert entry.timestamp == "2026-01-25T14:30:00+00:00"
        assert entry.action_type == HistoryActionType.INSTALL
        assert len(entry.items) == 2
        assert entry.reversible is True
        assert entry.success is True
        assert entry.metadata == {}

    def test_history_entry_is_frozen(self, sample_items: tuple[HistoryItem, ...]) -> None:
        """HistoryEntry is immutable."""
        entry = HistoryEntry(
            id="abc123",
            timestamp="2026-01-25T14:30:00+00:00",
            action_type=HistoryActionType.INSTALL,
            items=sample_items,
        )
        with pytest.raises(AttributeError):
            entry.id = "other"  # type: ignore[misc]

    def test_history_entry_empty_id_raises(self, sample_items: tuple[HistoryItem, ...]) -> None:
        """HistoryEntry with empty ID raises ValueError."""
        with pytest.raises(ValueError, match="History entry ID cannot be empty"):
            HistoryEntry(
                id="",
                timestamp="2026-01-25T14:30:00+00:00",
                action_type=HistoryActionType.INSTALL,
                items=sample_items,
            )

    def test_history_entry_empty_timestamp_raises(
        self, sample_items: tuple[HistoryItem, ...]
    ) -> None:
        """HistoryEntry with empty timestamp raises ValueError."""
        with pytest.raises(ValueError, match="Timestamp cannot be empty"):
            HistoryEntry(
                id="abc123",
                timestamp="",
                action_type=HistoryActionType.INSTALL,
                items=sample_items,
            )

    def test_history_entry_empty_items_raises(self) -> None:
        """HistoryEntry with no items raises ValueError."""
        with pytest.raises(ValueError, match="History entry must have at least one item"):
            HistoryEntry(
                id="abc123",
                timestamp="2026-01-25T14:30:00+00:00",
                action_type=HistoryActionType.INSTALL,
                items=(),
            )

    def test_history_entry_to_dict(self, sample_entry: HistoryEntry) -> None:
        """to_dict serializes entry correctly."""
        result = sample_entry.to_dict()
        assert result == {
            "id": "abc123def456",
            "timestamp": "2026-01-25T14:30:00+00:00",
            "action_type": "install",
            "items": [
                {"name": "vim", "source": "apt", "version": "9.0"},
                {"name": "htop", "source": "apt"},
            ],
            "reversible": True,
            "success": True,
            "metadata": {"command": "popctl apply"},
        }

    def test_history_entry_from_dict(self) -> None:
        """from_dict deserializes entry correctly."""
        data: dict[str, Any] = {
            "id": "abc123",
            "timestamp": "2026-01-25T14:30:00+00:00",
            "action_type": "remove",
            "items": [{"name": "nano", "source": "apt"}],
            "reversible": True,
            "success": True,
            "metadata": {},
        }
        entry = HistoryEntry.from_dict(data)
        assert entry.id == "abc123"
        assert entry.action_type == HistoryActionType.REMOVE
        assert len(entry.items) == 1
        assert entry.items[0].name == "nano"

    def test_history_entry_from_dict_defaults(self) -> None:
        """from_dict uses defaults for optional fields."""
        data = {
            "id": "abc123",
            "timestamp": "2026-01-25T14:30:00+00:00",
            "action_type": "install",
            "items": [{"name": "vim", "source": "apt"}],
        }
        entry = HistoryEntry.from_dict(data)
        assert entry.reversible is True
        assert entry.success is True
        assert entry.metadata == {}

    def test_history_entry_roundtrip_dict(self, sample_entry: HistoryEntry) -> None:
        """to_dict/from_dict roundtrip preserves data."""
        data = sample_entry.to_dict()
        restored = HistoryEntry.from_dict(data)
        assert restored.id == sample_entry.id
        assert restored.timestamp == sample_entry.timestamp
        assert restored.action_type == sample_entry.action_type
        assert len(restored.items) == len(sample_entry.items)
        assert restored.items[0].name == sample_entry.items[0].name
        assert restored.reversible == sample_entry.reversible
        assert restored.success == sample_entry.success
        assert restored.metadata == sample_entry.metadata


class TestHistoryEntryJsonLine:
    """Tests for JSONL serialization."""

    @pytest.fixture
    def sample_entry(self) -> HistoryEntry:
        """Create a sample history entry for testing."""
        return HistoryEntry(
            id="abc123",
            timestamp="2026-01-25T14:30:00+00:00",
            action_type=HistoryActionType.INSTALL,
            items=(HistoryItem(name="vim", source=PackageSource.APT, version="9.0"),),
            reversible=True,
            success=True,
            metadata={"command": "popctl apply"},
        )

    def test_to_json_line(self, sample_entry: HistoryEntry) -> None:
        """to_json_line produces valid JSON."""
        line = sample_entry.to_json_line()
        # Should be valid JSON
        data = json.loads(line)
        assert data["id"] == "abc123"
        assert data["action_type"] == "install"

    def test_to_json_line_no_trailing_newline(self, sample_entry: HistoryEntry) -> None:
        """to_json_line does not include trailing newline."""
        line = sample_entry.to_json_line()
        assert not line.endswith("\n")

    def test_to_json_line_compact(self, sample_entry: HistoryEntry) -> None:
        """to_json_line produces compact JSON without spaces."""
        line = sample_entry.to_json_line()
        # Should use compact separators
        assert ": " not in line
        assert ", " not in line

    def test_from_json_line(self) -> None:
        """from_json_line parses valid JSON line."""
        line = (
            '{"id":"abc123","timestamp":"2026-01-25T14:30:00+00:00",'
            '"action_type":"install","items":[{"name":"vim","source":"apt"}],'
            '"reversible":true,"success":true,"metadata":{}}'
        )
        entry = HistoryEntry.from_json_line(line)
        assert entry.id == "abc123"
        assert entry.action_type == HistoryActionType.INSTALL

    def test_from_json_line_with_whitespace(self) -> None:
        """from_json_line handles trailing whitespace."""
        line = (
            '{"id":"abc123","timestamp":"2026-01-25T14:30:00+00:00",'
            '"action_type":"install","items":[{"name":"vim","source":"apt"}],'
            '"reversible":true,"success":true,"metadata":{}}\n'
        )
        entry = HistoryEntry.from_json_line(line)
        assert entry.id == "abc123"

    def test_json_line_roundtrip(self, sample_entry: HistoryEntry) -> None:
        """to_json_line/from_json_line roundtrip preserves data."""
        line = sample_entry.to_json_line()
        restored = HistoryEntry.from_json_line(line)
        assert restored.id == sample_entry.id
        assert restored.timestamp == sample_entry.timestamp
        assert restored.action_type == sample_entry.action_type
        assert len(restored.items) == len(sample_entry.items)
        assert restored.items[0].name == sample_entry.items[0].name
        assert restored.metadata == sample_entry.metadata

    def test_from_json_line_invalid_json(self) -> None:
        """from_json_line raises on invalid JSON."""
        with pytest.raises(json.JSONDecodeError):
            HistoryEntry.from_json_line("not valid json")

    def test_from_json_line_missing_field(self) -> None:
        """from_json_line raises on missing required field."""
        line = '{"id":"abc123","timestamp":"2026-01-25T14:30:00+00:00"}'
        with pytest.raises(KeyError):
            HistoryEntry.from_json_line(line)


class TestCreateHistoryEntry:
    """Tests for create_history_entry factory function."""

    def test_create_history_entry_basic(self) -> None:
        """create_history_entry creates entry with auto-generated ID and timestamp."""
        items = [HistoryItem(name="vim", source=PackageSource.APT)]
        entry = create_history_entry(
            action_type=HistoryActionType.INSTALL,
            items=items,
        )
        # ID should be 12 hex characters
        assert len(entry.id) == 12
        assert all(c in "0123456789abcdef" for c in entry.id)
        # Timestamp should be valid ISO format
        datetime.fromisoformat(entry.timestamp)
        assert entry.action_type == HistoryActionType.INSTALL
        assert len(entry.items) == 1
        assert entry.reversible is True
        assert entry.success is True
        assert entry.metadata == {}

    def test_create_history_entry_with_metadata(self) -> None:
        """create_history_entry accepts metadata."""
        items = [HistoryItem(name="vim", source=PackageSource.APT)]
        entry = create_history_entry(
            action_type=HistoryActionType.APPLY,
            items=items,
            metadata={"command": "popctl apply", "dry_run": False},
        )
        assert entry.metadata == {"command": "popctl apply", "dry_run": False}

    def test_create_history_entry_not_reversible(self) -> None:
        """create_history_entry accepts reversible flag."""
        items = [HistoryItem(name="vim", source=PackageSource.APT)]
        entry = create_history_entry(
            action_type=HistoryActionType.PURGE,
            items=items,
            reversible=False,
        )
        assert entry.reversible is False

    def test_create_history_entry_empty_items_raises(self) -> None:
        """create_history_entry raises on empty items list."""
        with pytest.raises(ValueError, match="Cannot create history entry with no items"):
            create_history_entry(
                action_type=HistoryActionType.INSTALL,
                items=[],
            )

    def test_create_history_entry_multiple_items(self) -> None:
        """create_history_entry handles multiple items."""
        items = [
            HistoryItem(name="vim", source=PackageSource.APT),
            HistoryItem(name="htop", source=PackageSource.APT),
            HistoryItem(name="com.spotify.Client", source=PackageSource.FLATPAK),
        ]
        entry = create_history_entry(
            action_type=HistoryActionType.INSTALL,
            items=items,
        )
        assert len(entry.items) == 3
        assert entry.items[2].source == PackageSource.FLATPAK

    def test_create_history_entry_items_become_tuple(self) -> None:
        """create_history_entry converts list to tuple."""
        items = [HistoryItem(name="vim", source=PackageSource.APT)]
        entry = create_history_entry(
            action_type=HistoryActionType.INSTALL,
            items=items,
        )
        assert isinstance(entry.items, tuple)

    def test_create_history_entry_unique_ids(self) -> None:
        """create_history_entry generates unique IDs."""
        items = [HistoryItem(name="vim", source=PackageSource.APT)]
        entry1 = create_history_entry(
            action_type=HistoryActionType.INSTALL,
            items=items,
        )
        entry2 = create_history_entry(
            action_type=HistoryActionType.INSTALL,
            items=items,
        )
        assert entry1.id != entry2.id

    def test_create_history_entry_utc_timestamp(self) -> None:
        """create_history_entry uses UTC timezone."""
        items = [HistoryItem(name="vim", source=PackageSource.APT)]
        entry = create_history_entry(
            action_type=HistoryActionType.INSTALL,
            items=items,
        )
        # Parse the timestamp and verify it has timezone info
        ts = datetime.fromisoformat(entry.timestamp)
        assert ts.tzinfo is not None
        # Should be close to current UTC time
        now = datetime.now(UTC)
        diff = abs((now - ts).total_seconds())
        assert diff < 5  # Within 5 seconds
