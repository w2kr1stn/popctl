"""Unit tests for state management functions.

Tests for the module-level functions that handle history persistence.
"""

import json
import logging
from pathlib import Path

import pytest
from popctl.core.state import (
    _get_inverse_action_type,
    _history_path,
    get_history,
    get_last_reversible,
    mark_entry_reversed,
    record_action,
)
from popctl.models.history import (
    HistoryActionType,
    HistoryEntry,
    HistoryItem,
    create_history_entry,
)
from popctl.models.package import PackageSource


def test_history_path_with_custom_dir(tmp_path: Path) -> None:
    """_history_path returns correct path."""
    assert _history_path(tmp_path) == tmp_path / "history.jsonl"


class TestRecordAction:
    """Tests for record_action function."""

    @pytest.fixture
    def sample_entry(self) -> HistoryEntry:
        """Create a sample history entry."""
        return create_history_entry(
            action_type=HistoryActionType.INSTALL,
            items=[HistoryItem(name="vim", source=PackageSource.APT)],
        )

    def test_record_action_creates_file(self, tmp_path: Path, sample_entry: HistoryEntry) -> None:
        """record_action creates history file if it doesn't exist."""
        path = _history_path(tmp_path)
        assert not path.exists()

        record_action(sample_entry, state_dir=tmp_path)

        assert path.exists()

    def test_record_action_creates_directories(
        self, tmp_path: Path, sample_entry: HistoryEntry
    ) -> None:
        """record_action creates parent directories if needed."""
        nested_dir = tmp_path / "deep" / "nested" / "state"

        record_action(sample_entry, state_dir=nested_dir)

        assert nested_dir.exists()
        assert _history_path(nested_dir).exists()

    def test_record_action_writes_valid_jsonl(
        self, tmp_path: Path, sample_entry: HistoryEntry
    ) -> None:
        """record_action writes valid JSON line."""
        record_action(sample_entry, state_dir=tmp_path)

        content = _history_path(tmp_path).read_text(encoding="utf-8")
        lines = content.strip().split("\n")
        assert len(lines) == 1

        # Should be valid JSON
        data = json.loads(lines[0])
        assert data["id"] == sample_entry.id
        assert data["action_type"] == "install"

    def test_record_action_appends_to_file(self, tmp_path: Path) -> None:
        """record_action appends new entries without overwriting."""
        entry1 = create_history_entry(
            action_type=HistoryActionType.INSTALL,
            items=[HistoryItem(name="vim", source=PackageSource.APT)],
        )
        entry2 = create_history_entry(
            action_type=HistoryActionType.REMOVE,
            items=[HistoryItem(name="nano", source=PackageSource.APT)],
        )

        record_action(entry1, state_dir=tmp_path)
        record_action(entry2, state_dir=tmp_path)

        content = _history_path(tmp_path).read_text(encoding="utf-8")
        lines = [line for line in content.strip().split("\n") if line]
        assert len(lines) == 2

        # Verify both entries are present
        data1 = json.loads(lines[0])
        data2 = json.loads(lines[1])
        assert data1["id"] == entry1.id
        assert data2["id"] == entry2.id

    def test_record_action_adds_newline(self, tmp_path: Path, sample_entry: HistoryEntry) -> None:
        """record_action adds newline after each entry."""
        record_action(sample_entry, state_dir=tmp_path)

        content = _history_path(tmp_path).read_text(encoding="utf-8")
        assert content.endswith("\n")


class TestGetHistory:
    """Tests for get_history function."""

    def test_get_history_empty_file(self, tmp_path: Path) -> None:
        """get_history returns empty list when file doesn't exist."""
        result = get_history(state_dir=tmp_path)
        assert result == []

    def test_get_history_returns_entries(self, tmp_path: Path) -> None:
        """get_history returns recorded entries."""
        entry = create_history_entry(
            action_type=HistoryActionType.INSTALL,
            items=[HistoryItem(name="vim", source=PackageSource.APT)],
        )
        record_action(entry, state_dir=tmp_path)

        result = get_history(state_dir=tmp_path)

        assert len(result) == 1
        assert result[0].id == entry.id

    def test_get_history_newest_first(self, tmp_path: Path) -> None:
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

        record_action(entry1, state_dir=tmp_path)
        record_action(entry2, state_dir=tmp_path)
        record_action(entry3, state_dir=tmp_path)

        result = get_history(state_dir=tmp_path)

        # Should be newest first (reverse of insertion order)
        assert len(result) == 3
        assert result[0].id == entry3.id
        assert result[1].id == entry2.id
        assert result[2].id == entry1.id

    def test_get_history_with_limit(self, tmp_path: Path) -> None:
        """get_history respects limit parameter."""
        for i in range(5):
            entry = create_history_entry(
                action_type=HistoryActionType.INSTALL,
                items=[HistoryItem(name=f"pkg{i}", source=PackageSource.APT)],
            )
            record_action(entry, state_dir=tmp_path)

        result = get_history(limit=2, state_dir=tmp_path)

        assert len(result) == 2

    def test_get_history_limit_larger_than_entries(self, tmp_path: Path) -> None:
        """get_history handles limit larger than number of entries."""
        entry = create_history_entry(
            action_type=HistoryActionType.INSTALL,
            items=[HistoryItem(name="vim", source=PackageSource.APT)],
        )
        record_action(entry, state_dir=tmp_path)

        result = get_history(limit=100, state_dir=tmp_path)

        assert len(result) == 1

    def test_get_history_limit_zero(self, tmp_path: Path) -> None:
        """get_history with limit=0 returns empty list."""
        entry = create_history_entry(
            action_type=HistoryActionType.INSTALL,
            items=[HistoryItem(name="vim", source=PackageSource.APT)],
        )
        record_action(entry, state_dir=tmp_path)

        result = get_history(limit=0, state_dir=tmp_path)

        assert result == []


class TestGetHistoryCorruptLines:
    """Tests for handling corrupt lines in history file."""

    def test_get_history_skips_corrupt_lines(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """get_history skips corrupt JSON lines with warning."""
        # Write valid entry
        entry = create_history_entry(
            action_type=HistoryActionType.INSTALL,
            items=[HistoryItem(name="vim", source=PackageSource.APT)],
        )

        # Manually write mix of valid and corrupt lines
        tmp_path.mkdir(parents=True, exist_ok=True)
        with _history_path(tmp_path).open("w", encoding="utf-8") as f:
            f.write(entry.to_json_line() + "\n")
            f.write("not valid json\n")
            f.write('{"incomplete": true}\n')  # Missing required fields

        with caplog.at_level(logging.WARNING):
            result = get_history(state_dir=tmp_path)

        # Should only return the valid entry
        assert len(result) == 1
        assert result[0].id == entry.id

        # Should have logged warnings
        assert "Skipping corrupt history line" in caplog.text

    def test_get_history_skips_empty_lines(self, tmp_path: Path) -> None:
        """get_history skips empty lines without warning."""
        entry = create_history_entry(
            action_type=HistoryActionType.INSTALL,
            items=[HistoryItem(name="vim", source=PackageSource.APT)],
        )

        tmp_path.mkdir(parents=True, exist_ok=True)
        with _history_path(tmp_path).open("w", encoding="utf-8") as f:
            f.write("\n")
            f.write(entry.to_json_line() + "\n")
            f.write("   \n")

        result = get_history(state_dir=tmp_path)

        assert len(result) == 1
        assert result[0].id == entry.id

    def test_get_history_invalid_action_type(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """get_history skips entries with invalid action type."""
        tmp_path.mkdir(parents=True, exist_ok=True)
        invalid_data = {
            "id": "abc123",
            "timestamp": "2026-01-25T14:30:00+00:00",
            "action_type": "unknown_action",
            "items": [{"name": "vim", "source": "apt"}],
        }
        with _history_path(tmp_path).open("w", encoding="utf-8") as f:
            f.write(json.dumps(invalid_data) + "\n")

        with caplog.at_level(logging.WARNING):
            result = get_history(state_dir=tmp_path)

        assert result == []
        assert "Skipping corrupt history line" in caplog.text


class TestGetLastReversible:
    """Tests for get_last_reversible function."""

    def test_get_last_reversible_empty_history(self, tmp_path: Path) -> None:
        """get_last_reversible returns None when history is empty."""
        result = get_last_reversible(state_dir=tmp_path)
        assert result is None

    def test_get_last_reversible_finds_latest(self, tmp_path: Path) -> None:
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

        record_action(entry1, state_dir=tmp_path)
        record_action(entry2, state_dir=tmp_path)

        result = get_last_reversible(state_dir=tmp_path)

        assert result is not None
        assert result.id == entry2.id

    def test_get_last_reversible_skips_non_reversible(self, tmp_path: Path) -> None:
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

        record_action(entry1, state_dir=tmp_path)
        record_action(entry2, state_dir=tmp_path)

        result = get_last_reversible(state_dir=tmp_path)

        assert result is not None
        assert result.id == entry1.id

    def test_get_last_reversible_no_reversible_entries(self, tmp_path: Path) -> None:
        """get_last_reversible returns None when no reversible entries exist."""
        entry = create_history_entry(
            action_type=HistoryActionType.PURGE,
            items=[HistoryItem(name="vim", source=PackageSource.APT)],
            reversible=False,
        )
        record_action(entry, state_dir=tmp_path)

        result = get_last_reversible(state_dir=tmp_path)

        assert result is None

    def test_get_last_reversible_skips_already_reversed(self, tmp_path: Path) -> None:
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

        record_action(entry1, state_dir=tmp_path)
        record_action(entry2, state_dir=tmp_path)

        # Mark entry2 as reversed
        mark_entry_reversed(entry2, state_dir=tmp_path)

        result = get_last_reversible(state_dir=tmp_path)

        assert result is not None
        assert result.id == entry1.id


class TestMarkEntryReversed:
    """Tests for mark_entry_reversed function."""

    def test_mark_entry_reversed_returns_none(self, tmp_path: Path) -> None:
        """mark_entry_reversed returns None."""
        entry = create_history_entry(
            action_type=HistoryActionType.INSTALL,
            items=[HistoryItem(name="vim", source=PackageSource.APT)],
        )
        record_action(entry, state_dir=tmp_path)

        result = mark_entry_reversed(entry, state_dir=tmp_path)

        assert result is None

    def test_mark_entry_reversed_creates_reversal_entry(self, tmp_path: Path) -> None:
        """mark_entry_reversed creates a new reversal entry."""
        entry = create_history_entry(
            action_type=HistoryActionType.INSTALL,
            items=[HistoryItem(name="vim", source=PackageSource.APT)],
        )
        record_action(entry, state_dir=tmp_path)

        mark_entry_reversed(entry, state_dir=tmp_path)

        history = get_history(state_dir=tmp_path)
        assert len(history) == 2

        # Most recent entry should be the reversal marker
        reversal = history[0]
        assert reversal.metadata.get("reversed_entry_id") == entry.id
        assert reversal.reversible is False

    def test_mark_entry_reversed_uses_inverse_action_type(self, tmp_path: Path) -> None:
        """mark_entry_reversed uses inverse action type for reversal."""
        entry = create_history_entry(
            action_type=HistoryActionType.INSTALL,
            items=[HistoryItem(name="vim", source=PackageSource.APT)],
        )
        record_action(entry, state_dir=tmp_path)

        mark_entry_reversed(entry, state_dir=tmp_path)

        history = get_history(state_dir=tmp_path)
        reversal = history[0]

        # INSTALL reversed should be REMOVE
        assert reversal.action_type == HistoryActionType.REMOVE

    def test_mark_entry_reversed_remove_becomes_install(self, tmp_path: Path) -> None:
        """mark_entry_reversed: REMOVE action reversed becomes INSTALL."""
        entry = create_history_entry(
            action_type=HistoryActionType.REMOVE,
            items=[HistoryItem(name="vim", source=PackageSource.APT)],
        )
        record_action(entry, state_dir=tmp_path)

        mark_entry_reversed(entry, state_dir=tmp_path)

        history = get_history(state_dir=tmp_path)
        reversal = history[0]

        assert reversal.action_type == HistoryActionType.INSTALL

    def test_mark_entry_reversed_preserves_items(self, tmp_path: Path) -> None:
        """mark_entry_reversed preserves the items from original entry."""
        items = [
            HistoryItem(name="vim", source=PackageSource.APT),
            HistoryItem(name="htop", source=PackageSource.APT),
        ]
        entry = create_history_entry(
            action_type=HistoryActionType.INSTALL,
            items=items,
        )
        record_action(entry, state_dir=tmp_path)

        mark_entry_reversed(entry, state_dir=tmp_path)

        history = get_history(state_dir=tmp_path)
        reversal = history[0]

        assert len(reversal.items) == 2
        assert reversal.items[0].name == "vim"
        assert reversal.items[0].version == "9.0"
        assert reversal.items[1].name == "htop"


class TestInverseActionType:
    """Tests for _get_inverse_action_type function."""

    def test_install_inverse_is_remove(self) -> None:
        """INSTALL inverse is REMOVE."""
        result = _get_inverse_action_type(HistoryActionType.INSTALL)
        assert result == HistoryActionType.REMOVE

    def test_remove_inverse_is_install(self) -> None:
        """REMOVE inverse is INSTALL."""
        result = _get_inverse_action_type(HistoryActionType.REMOVE)
        assert result == HistoryActionType.INSTALL

    def test_purge_inverse_is_install(self) -> None:
        """PURGE inverse is INSTALL (reinstall)."""
        result = _get_inverse_action_type(HistoryActionType.PURGE)
        assert result == HistoryActionType.INSTALL

    def test_advisor_apply_inverse_is_advisor_apply(self) -> None:
        """ADVISOR_APPLY inverse is ADVISOR_APPLY."""
        result = _get_inverse_action_type(HistoryActionType.ADVISOR_APPLY)
        assert result == HistoryActionType.ADVISOR_APPLY
