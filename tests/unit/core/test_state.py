"""Unit tests for StateManager.

Tests for the StateManager class that handles history persistence.
"""

import json
import logging
from pathlib import Path

import pytest
from popctl.core.state import StateManager
from popctl.models.history import (
    HistoryActionType,
    HistoryEntry,
    HistoryItem,
    create_history_entry,
)
from popctl.models.package import PackageSource


class TestStateManagerInit:
    """Tests for StateManager initialization."""

    def test_init_with_default_state_dir(self) -> None:
        """StateManager uses default state directory when none provided."""
        manager = StateManager()
        # Should not raise, just use default
        assert manager._state_dir is not None

    def test_init_with_custom_state_dir(self, tmp_path: Path) -> None:
        """StateManager uses custom state directory when provided."""
        manager = StateManager(state_dir=tmp_path)
        assert manager._state_dir == tmp_path

    def test_history_path_property(self, tmp_path: Path) -> None:
        """history_path returns correct path."""
        manager = StateManager(state_dir=tmp_path)
        assert manager.history_path == tmp_path / "history.jsonl"


class TestRecordAction:
    """Tests for StateManager.record_action method."""

    @pytest.fixture
    def manager(self, tmp_path: Path) -> StateManager:
        """Create a StateManager with temporary directory."""
        return StateManager(state_dir=tmp_path)

    @pytest.fixture
    def sample_entry(self) -> HistoryEntry:
        """Create a sample history entry."""
        return create_history_entry(
            action_type=HistoryActionType.INSTALL,
            items=[HistoryItem(name="vim", source=PackageSource.APT)],
        )

    def test_record_action_creates_file(
        self, manager: StateManager, sample_entry: HistoryEntry
    ) -> None:
        """record_action creates history file if it doesn't exist."""
        assert not manager.history_path.exists()

        manager.record_action(sample_entry)

        assert manager.history_path.exists()

    def test_record_action_creates_directories(
        self, tmp_path: Path, sample_entry: HistoryEntry
    ) -> None:
        """record_action creates parent directories if needed."""
        nested_dir = tmp_path / "deep" / "nested" / "state"
        manager = StateManager(state_dir=nested_dir)

        manager.record_action(sample_entry)

        assert nested_dir.exists()
        assert manager.history_path.exists()

    def test_record_action_writes_valid_jsonl(
        self, manager: StateManager, sample_entry: HistoryEntry
    ) -> None:
        """record_action writes valid JSON line."""
        manager.record_action(sample_entry)

        content = manager.history_path.read_text(encoding="utf-8")
        lines = content.strip().split("\n")
        assert len(lines) == 1

        # Should be valid JSON
        data = json.loads(lines[0])
        assert data["id"] == sample_entry.id
        assert data["action_type"] == "install"

    def test_record_action_appends_to_file(self, manager: StateManager) -> None:
        """record_action appends new entries without overwriting."""
        entry1 = create_history_entry(
            action_type=HistoryActionType.INSTALL,
            items=[HistoryItem(name="vim", source=PackageSource.APT)],
        )
        entry2 = create_history_entry(
            action_type=HistoryActionType.REMOVE,
            items=[HistoryItem(name="nano", source=PackageSource.APT)],
        )

        manager.record_action(entry1)
        manager.record_action(entry2)

        content = manager.history_path.read_text(encoding="utf-8")
        lines = [line for line in content.strip().split("\n") if line]
        assert len(lines) == 2

        # Verify both entries are present
        data1 = json.loads(lines[0])
        data2 = json.loads(lines[1])
        assert data1["id"] == entry1.id
        assert data2["id"] == entry2.id

    def test_record_action_adds_newline(
        self, manager: StateManager, sample_entry: HistoryEntry
    ) -> None:
        """record_action adds newline after each entry."""
        manager.record_action(sample_entry)

        content = manager.history_path.read_text(encoding="utf-8")
        assert content.endswith("\n")


class TestGetHistory:
    """Tests for StateManager.get_history method."""

    @pytest.fixture
    def manager(self, tmp_path: Path) -> StateManager:
        """Create a StateManager with temporary directory."""
        return StateManager(state_dir=tmp_path)

    def test_get_history_empty_file(self, manager: StateManager) -> None:
        """get_history returns empty list when file doesn't exist."""
        result = manager.get_history()
        assert result == []

    def test_get_history_returns_entries(self, manager: StateManager) -> None:
        """get_history returns recorded entries."""
        entry = create_history_entry(
            action_type=HistoryActionType.INSTALL,
            items=[HistoryItem(name="vim", source=PackageSource.APT)],
        )
        manager.record_action(entry)

        result = manager.get_history()

        assert len(result) == 1
        assert result[0].id == entry.id

    def test_get_history_newest_first(self, manager: StateManager) -> None:
        """get_history returns entries in reverse order (newest first)."""
        entry1 = create_history_entry(
            action_type=HistoryActionType.INSTALL,
            items=[HistoryItem(name="vim", source=PackageSource.APT)],
        )
        entry2 = create_history_entry(
            action_type=HistoryActionType.REMOVE,
            items=[HistoryItem(name="nano", source=PackageSource.APT)],
        )
        entry3 = create_history_entry(
            action_type=HistoryActionType.INSTALL,
            items=[HistoryItem(name="htop", source=PackageSource.APT)],
        )

        manager.record_action(entry1)
        manager.record_action(entry2)
        manager.record_action(entry3)

        result = manager.get_history()

        # Should be newest first (reverse of insertion order)
        assert len(result) == 3
        assert result[0].id == entry3.id
        assert result[1].id == entry2.id
        assert result[2].id == entry1.id

    def test_get_history_with_limit(self, manager: StateManager) -> None:
        """get_history respects limit parameter."""
        for i in range(5):
            entry = create_history_entry(
                action_type=HistoryActionType.INSTALL,
                items=[HistoryItem(name=f"pkg{i}", source=PackageSource.APT)],
            )
            manager.record_action(entry)

        result = manager.get_history(limit=2)

        assert len(result) == 2

    def test_get_history_limit_larger_than_entries(self, manager: StateManager) -> None:
        """get_history handles limit larger than number of entries."""
        entry = create_history_entry(
            action_type=HistoryActionType.INSTALL,
            items=[HistoryItem(name="vim", source=PackageSource.APT)],
        )
        manager.record_action(entry)

        result = manager.get_history(limit=100)

        assert len(result) == 1

    def test_get_history_limit_zero(self, manager: StateManager) -> None:
        """get_history with limit=0 returns empty list."""
        entry = create_history_entry(
            action_type=HistoryActionType.INSTALL,
            items=[HistoryItem(name="vim", source=PackageSource.APT)],
        )
        manager.record_action(entry)

        result = manager.get_history(limit=0)

        assert result == []


class TestGetHistoryCorruptLines:
    """Tests for handling corrupt lines in history file."""

    @pytest.fixture
    def manager(self, tmp_path: Path) -> StateManager:
        """Create a StateManager with temporary directory."""
        return StateManager(state_dir=tmp_path)

    def test_get_history_skips_corrupt_lines(
        self, manager: StateManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        """get_history skips corrupt JSON lines with warning."""
        # Write valid entry
        entry = create_history_entry(
            action_type=HistoryActionType.INSTALL,
            items=[HistoryItem(name="vim", source=PackageSource.APT)],
        )

        # Manually write mix of valid and corrupt lines
        manager._state_dir.mkdir(parents=True, exist_ok=True)
        with manager.history_path.open("w", encoding="utf-8") as f:
            f.write(entry.to_json_line() + "\n")
            f.write("not valid json\n")
            f.write('{"incomplete": true}\n')  # Missing required fields

        with caplog.at_level(logging.WARNING):
            result = manager.get_history()

        # Should only return the valid entry
        assert len(result) == 1
        assert result[0].id == entry.id

        # Should have logged warnings
        assert "Skipping corrupt history line" in caplog.text

    def test_get_history_skips_empty_lines(self, manager: StateManager) -> None:
        """get_history skips empty lines without warning."""
        entry = create_history_entry(
            action_type=HistoryActionType.INSTALL,
            items=[HistoryItem(name="vim", source=PackageSource.APT)],
        )

        manager._state_dir.mkdir(parents=True, exist_ok=True)
        with manager.history_path.open("w", encoding="utf-8") as f:
            f.write("\n")
            f.write(entry.to_json_line() + "\n")
            f.write("   \n")

        result = manager.get_history()

        assert len(result) == 1
        assert result[0].id == entry.id

    def test_get_history_invalid_action_type(
        self, manager: StateManager, caplog: pytest.LogCaptureFixture
    ) -> None:
        """get_history skips entries with invalid action type."""
        manager._state_dir.mkdir(parents=True, exist_ok=True)
        invalid_data = {
            "id": "abc123",
            "timestamp": "2026-01-25T14:30:00+00:00",
            "action_type": "unknown_action",
            "items": [{"name": "vim", "source": "apt"}],
        }
        with manager.history_path.open("w", encoding="utf-8") as f:
            f.write(json.dumps(invalid_data) + "\n")

        with caplog.at_level(logging.WARNING):
            result = manager.get_history()

        assert result == []
        assert "Skipping corrupt history line" in caplog.text


class TestGetLastReversible:
    """Tests for StateManager.get_last_reversible method."""

    @pytest.fixture
    def manager(self, tmp_path: Path) -> StateManager:
        """Create a StateManager with temporary directory."""
        return StateManager(state_dir=tmp_path)

    def test_get_last_reversible_empty_history(self, manager: StateManager) -> None:
        """get_last_reversible returns None when history is empty."""
        result = manager.get_last_reversible()
        assert result is None

    def test_get_last_reversible_finds_latest(self, manager: StateManager) -> None:
        """get_last_reversible returns the most recent reversible entry."""
        entry1 = create_history_entry(
            action_type=HistoryActionType.INSTALL,
            items=[HistoryItem(name="vim", source=PackageSource.APT)],
            reversible=True,
        )
        entry2 = create_history_entry(
            action_type=HistoryActionType.REMOVE,
            items=[HistoryItem(name="nano", source=PackageSource.APT)],
            reversible=True,
        )

        manager.record_action(entry1)
        manager.record_action(entry2)

        result = manager.get_last_reversible()

        assert result is not None
        assert result.id == entry2.id

    def test_get_last_reversible_skips_non_reversible(self, manager: StateManager) -> None:
        """get_last_reversible skips non-reversible entries."""
        entry1 = create_history_entry(
            action_type=HistoryActionType.INSTALL,
            items=[HistoryItem(name="vim", source=PackageSource.APT)],
            reversible=True,
        )
        entry2 = create_history_entry(
            action_type=HistoryActionType.PURGE,
            items=[HistoryItem(name="nano", source=PackageSource.APT)],
            reversible=False,
        )

        manager.record_action(entry1)
        manager.record_action(entry2)

        result = manager.get_last_reversible()

        assert result is not None
        assert result.id == entry1.id

    def test_get_last_reversible_no_reversible_entries(self, manager: StateManager) -> None:
        """get_last_reversible returns None when no reversible entries exist."""
        entry = create_history_entry(
            action_type=HistoryActionType.PURGE,
            items=[HistoryItem(name="vim", source=PackageSource.APT)],
            reversible=False,
        )
        manager.record_action(entry)

        result = manager.get_last_reversible()

        assert result is None

    def test_get_last_reversible_skips_already_reversed(self, manager: StateManager) -> None:
        """get_last_reversible skips entries that have been reversed."""
        entry1 = create_history_entry(
            action_type=HistoryActionType.INSTALL,
            items=[HistoryItem(name="vim", source=PackageSource.APT)],
            reversible=True,
        )
        entry2 = create_history_entry(
            action_type=HistoryActionType.INSTALL,
            items=[HistoryItem(name="nano", source=PackageSource.APT)],
            reversible=True,
        )

        manager.record_action(entry1)
        manager.record_action(entry2)

        # Mark entry2 as reversed
        manager.mark_entry_reversed(entry2.id)

        result = manager.get_last_reversible()

        assert result is not None
        assert result.id == entry1.id


class TestGetEntryById:
    """Tests for StateManager.get_entry_by_id method."""

    @pytest.fixture
    def manager(self, tmp_path: Path) -> StateManager:
        """Create a StateManager with temporary directory."""
        return StateManager(state_dir=tmp_path)

    def test_get_entry_by_id_empty_history(self, manager: StateManager) -> None:
        """get_entry_by_id returns None when history is empty."""
        result = manager.get_entry_by_id("nonexistent")
        assert result is None

    def test_get_entry_by_id_finds_entry(self, manager: StateManager) -> None:
        """get_entry_by_id returns entry when found."""
        entry = create_history_entry(
            action_type=HistoryActionType.INSTALL,
            items=[HistoryItem(name="vim", source=PackageSource.APT)],
        )
        manager.record_action(entry)

        result = manager.get_entry_by_id(entry.id)

        assert result is not None
        assert result.id == entry.id
        assert result.items[0].name == "vim"

    def test_get_entry_by_id_not_found(self, manager: StateManager) -> None:
        """get_entry_by_id returns None when ID not found."""
        entry = create_history_entry(
            action_type=HistoryActionType.INSTALL,
            items=[HistoryItem(name="vim", source=PackageSource.APT)],
        )
        manager.record_action(entry)

        result = manager.get_entry_by_id("nonexistent_id")

        assert result is None

    def test_get_entry_by_id_multiple_entries(self, manager: StateManager) -> None:
        """get_entry_by_id finds correct entry among multiple."""
        entry1 = create_history_entry(
            action_type=HistoryActionType.INSTALL,
            items=[HistoryItem(name="vim", source=PackageSource.APT)],
        )
        entry2 = create_history_entry(
            action_type=HistoryActionType.REMOVE,
            items=[HistoryItem(name="nano", source=PackageSource.APT)],
        )
        entry3 = create_history_entry(
            action_type=HistoryActionType.INSTALL,
            items=[HistoryItem(name="htop", source=PackageSource.APT)],
        )

        manager.record_action(entry1)
        manager.record_action(entry2)
        manager.record_action(entry3)

        result = manager.get_entry_by_id(entry2.id)

        assert result is not None
        assert result.id == entry2.id
        assert result.items[0].name == "nano"


class TestMarkEntryReversed:
    """Tests for StateManager.mark_entry_reversed method."""

    @pytest.fixture
    def manager(self, tmp_path: Path) -> StateManager:
        """Create a StateManager with temporary directory."""
        return StateManager(state_dir=tmp_path)

    def test_mark_entry_reversed_returns_true_on_success(self, manager: StateManager) -> None:
        """mark_entry_reversed returns True when entry is found."""
        entry = create_history_entry(
            action_type=HistoryActionType.INSTALL,
            items=[HistoryItem(name="vim", source=PackageSource.APT)],
        )
        manager.record_action(entry)

        result = manager.mark_entry_reversed(entry.id)

        assert result is True

    def test_mark_entry_reversed_returns_false_when_not_found(self, manager: StateManager) -> None:
        """mark_entry_reversed returns False when entry not found."""
        result = manager.mark_entry_reversed("nonexistent_id")
        assert result is False

    def test_mark_entry_reversed_creates_reversal_entry(self, manager: StateManager) -> None:
        """mark_entry_reversed creates a new reversal entry."""
        entry = create_history_entry(
            action_type=HistoryActionType.INSTALL,
            items=[HistoryItem(name="vim", source=PackageSource.APT)],
        )
        manager.record_action(entry)

        manager.mark_entry_reversed(entry.id)

        history = manager.get_history()
        assert len(history) == 2

        # Most recent entry should be the reversal marker
        reversal = history[0]
        assert reversal.metadata.get("reversed_entry_id") == entry.id
        assert reversal.reversible is False

    def test_mark_entry_reversed_uses_inverse_action_type(self, manager: StateManager) -> None:
        """mark_entry_reversed uses inverse action type for reversal."""
        entry = create_history_entry(
            action_type=HistoryActionType.INSTALL,
            items=[HistoryItem(name="vim", source=PackageSource.APT)],
        )
        manager.record_action(entry)

        manager.mark_entry_reversed(entry.id)

        history = manager.get_history()
        reversal = history[0]

        # INSTALL reversed should be REMOVE
        assert reversal.action_type == HistoryActionType.REMOVE

    def test_mark_entry_reversed_remove_becomes_install(self, manager: StateManager) -> None:
        """mark_entry_reversed: REMOVE action reversed becomes INSTALL."""
        entry = create_history_entry(
            action_type=HistoryActionType.REMOVE,
            items=[HistoryItem(name="vim", source=PackageSource.APT)],
        )
        manager.record_action(entry)

        manager.mark_entry_reversed(entry.id)

        history = manager.get_history()
        reversal = history[0]

        assert reversal.action_type == HistoryActionType.INSTALL

    def test_mark_entry_reversed_preserves_items(self, manager: StateManager) -> None:
        """mark_entry_reversed preserves the items from original entry."""
        items = [
            HistoryItem(name="vim", source=PackageSource.APT, version="9.0"),
            HistoryItem(name="htop", source=PackageSource.APT),
        ]
        entry = create_history_entry(
            action_type=HistoryActionType.INSTALL,
            items=items,
        )
        manager.record_action(entry)

        manager.mark_entry_reversed(entry.id)

        history = manager.get_history()
        reversal = history[0]

        assert len(reversal.items) == 2
        assert reversal.items[0].name == "vim"
        assert reversal.items[0].version == "9.0"
        assert reversal.items[1].name == "htop"


class TestInverseActionType:
    """Tests for _get_inverse_action_type method."""

    @pytest.fixture
    def manager(self, tmp_path: Path) -> StateManager:
        """Create a StateManager with temporary directory."""
        return StateManager(state_dir=tmp_path)

    def test_install_inverse_is_remove(self, manager: StateManager) -> None:
        """INSTALL inverse is REMOVE."""
        result = manager._get_inverse_action_type(HistoryActionType.INSTALL)
        assert result == HistoryActionType.REMOVE

    def test_remove_inverse_is_install(self, manager: StateManager) -> None:
        """REMOVE inverse is INSTALL."""
        result = manager._get_inverse_action_type(HistoryActionType.REMOVE)
        assert result == HistoryActionType.INSTALL

    def test_purge_inverse_is_install(self, manager: StateManager) -> None:
        """PURGE inverse is INSTALL (reinstall)."""
        result = manager._get_inverse_action_type(HistoryActionType.PURGE)
        assert result == HistoryActionType.INSTALL

    def test_apply_inverse_is_apply(self, manager: StateManager) -> None:
        """APPLY inverse is APPLY (batch operation)."""
        result = manager._get_inverse_action_type(HistoryActionType.APPLY)
        assert result == HistoryActionType.APPLY

    def test_advisor_apply_inverse_is_advisor_apply(self, manager: StateManager) -> None:
        """ADVISOR_APPLY inverse is ADVISOR_APPLY."""
        result = manager._get_inverse_action_type(HistoryActionType.ADVISOR_APPLY)
        assert result == HistoryActionType.ADVISOR_APPLY
