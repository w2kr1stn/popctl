"""Unit tests for history command.

Tests for the CLI history command implementation.
"""

import json
from unittest.mock import patch

import pytest
from popctl.cli.main import app
from popctl.models.history import (
    HistoryActionType,
    HistoryEntry,
    HistoryItem,
)
from popctl.models.package import PackageSource
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def sample_history_entries() -> list[HistoryEntry]:
    """Create sample history entries for testing."""
    return [
        HistoryEntry(
            id="abc123456789",
            timestamp="2026-01-26T14:30:00+00:00",
            action_type=HistoryActionType.INSTALL,
            items=(
                HistoryItem(name="vim", source=PackageSource.APT, version="9.0"),
                HistoryItem(name="htop", source=PackageSource.APT, version="3.3.0"),
                HistoryItem(name="curl", source=PackageSource.APT, version="8.5.0"),
            ),
            reversible=True,
        ),
        HistoryEntry(
            id="def678901234",
            timestamp="2026-01-26T14:25:00+00:00",
            action_type=HistoryActionType.REMOVE,
            items=(HistoryItem(name="nano", source=PackageSource.APT, version="7.2"),),
            reversible=True,
        ),
        HistoryEntry(
            id="ghi112233445",
            timestamp="2026-01-25T10:00:00+00:00",
            action_type=HistoryActionType.PURGE,
            items=(
                HistoryItem(name="firefox", source=PackageSource.APT),
                HistoryItem(name="thunderbird", source=PackageSource.APT),
                HistoryItem(name="libreoffice", source=PackageSource.APT),
                HistoryItem(name="gimp", source=PackageSource.APT),
                HistoryItem(name="inkscape", source=PackageSource.APT),
                HistoryItem(name="blender", source=PackageSource.APT),
            ),
            reversible=False,
        ),
    ]


class TestHistoryCommand:
    """Tests for popctl history command."""

    def test_history_help(self) -> None:
        """History command shows help."""
        result = runner.invoke(app, ["history", "--help"])
        assert result.exit_code == 0
        assert "View history of package changes" in result.stdout
        assert "--limit" in result.stdout
        assert "--since" in result.stdout
        assert "--json" in result.stdout

    def test_history_empty(self) -> None:
        """History shows message when no entries exist."""
        with patch("popctl.cli.commands.history.StateManager") as mock_state:
            mock_state.return_value.get_history.return_value = []

            result = runner.invoke(app, ["history"])

        assert result.exit_code == 0
        assert "No history entries found" in result.stdout

    def test_history_with_entries(self, sample_history_entries: list[HistoryEntry]) -> None:
        """History shows entries in table format."""
        with patch("popctl.cli.commands.history.StateManager") as mock_state:
            mock_state.return_value.get_history.return_value = sample_history_entries

            result = runner.invoke(app, ["history"])

        assert result.exit_code == 0
        assert "Package History" in result.stdout
        assert "abc12345" in result.stdout  # First 8 chars of ID
        assert "def67890" in result.stdout
        assert "ghi11223" in result.stdout
        assert "install" in result.stdout
        assert "remove" in result.stdout
        assert "purge" in result.stdout
        assert "vim, htop, curl" in result.stdout
        assert "nano" in result.stdout
        # Check for more packages indicator
        assert "(+3 more)" in result.stdout

    def test_history_limit_option(self, sample_history_entries: list[HistoryEntry]) -> None:
        """History --limit option limits entries."""
        with patch("popctl.cli.commands.history.StateManager") as mock_state:
            # Simulate limit being applied by StateManager
            mock_state.return_value.get_history.return_value = sample_history_entries[:2]

            result = runner.invoke(app, ["history", "--limit", "2"])

        assert result.exit_code == 0
        mock_state.return_value.get_history.assert_called_once_with(limit=2)

    def test_history_json_output(self, sample_history_entries: list[HistoryEntry]) -> None:
        """History --json outputs valid JSON."""
        with patch("popctl.cli.commands.history.StateManager") as mock_state:
            mock_state.return_value.get_history.return_value = sample_history_entries[:1]

            result = runner.invoke(app, ["history", "--json"])

        assert result.exit_code == 0

        # Parse JSON output - type ignored for json.loads dynamic return
        data = json.loads(result.stdout)  # pyright: ignore[reportUnknownVariableType]
        assert isinstance(data, list)
        assert len(data) == 1  # pyright: ignore[reportUnknownArgumentType]
        first_entry = data[0]  # pyright: ignore[reportUnknownVariableType]
        assert first_entry["id"] == "abc123456789"
        assert first_entry["action_type"] == "install"
        items = first_entry["items"]  # pyright: ignore[reportUnknownVariableType]
        assert isinstance(items, list)
        assert len(items) == 3  # pyright: ignore[reportUnknownArgumentType]

    def test_history_since_filter(self, sample_history_entries: list[HistoryEntry]) -> None:
        """History --since filters by date."""
        with patch("popctl.cli.commands.history.StateManager") as mock_state:
            mock_state.return_value.get_history.return_value = sample_history_entries

            # Filter to only include entries from 2026-01-26
            result = runner.invoke(app, ["history", "--since", "2026-01-26"])

        assert result.exit_code == 0
        # Should only show 2 entries from Jan 26
        assert "abc12345" in result.stdout
        assert "def67890" in result.stdout
        # Entry from Jan 25 should not appear
        assert "ghi11223" not in result.stdout

    def test_history_since_invalid_date(self) -> None:
        """History --since with invalid date shows error."""
        with patch("popctl.cli.commands.history.StateManager") as mock_state:
            mock_state.return_value.get_history.return_value = []

            result = runner.invoke(app, ["history", "--since", "invalid-date"])

        assert result.exit_code == 1
        assert "Invalid date format" in result.stderr

    def test_history_shows_undo_availability(
        self, sample_history_entries: list[HistoryEntry]
    ) -> None:
        """History shows whether entries can be undone."""
        with patch("popctl.cli.commands.history.StateManager") as mock_state:
            mock_state.return_value.get_history.return_value = sample_history_entries

            result = runner.invoke(app, ["history"])

        assert result.exit_code == 0
        # The table should show Yes/No for undo availability
        assert "Yes" in result.stdout  # For reversible entries
        assert "No" in result.stdout  # For non-reversible entry


class TestHistoryTableFormatting:
    """Tests for history table formatting."""

    def test_timestamp_formatting(self) -> None:
        """Timestamps are formatted correctly."""
        entry = HistoryEntry(
            id="test12345678",
            timestamp="2026-01-26T14:30:45.123456+00:00",
            action_type=HistoryActionType.INSTALL,
            items=(HistoryItem(name="vim", source=PackageSource.APT),),
            reversible=True,
        )

        with patch("popctl.cli.commands.history.StateManager") as mock_state:
            mock_state.return_value.get_history.return_value = [entry]

            result = runner.invoke(app, ["history"])

        assert result.exit_code == 0
        # Should show formatted timestamp
        assert "2026-01-26 14:30" in result.stdout

    def test_packages_truncation(self) -> None:
        """Package list is truncated with more indicator."""
        items = tuple(HistoryItem(name=f"pkg{i}", source=PackageSource.APT) for i in range(10))
        entry = HistoryEntry(
            id="test12345678",
            timestamp="2026-01-26T14:30:00+00:00",
            action_type=HistoryActionType.INSTALL,
            items=items,
            reversible=True,
        )

        with patch("popctl.cli.commands.history.StateManager") as mock_state:
            mock_state.return_value.get_history.return_value = [entry]

            result = runner.invoke(app, ["history"])

        assert result.exit_code == 0
        # Should show first 3 packages and count
        assert "pkg0" in result.stdout
        assert "pkg1" in result.stdout
        assert "pkg2" in result.stdout
        assert "(+7 more)" in result.stdout


class TestHistoryJsonOutput:
    """Tests for JSON output format."""

    def test_json_includes_all_fields(self) -> None:
        """JSON output includes all entry fields."""
        entry = HistoryEntry(
            id="test12345678",
            timestamp="2026-01-26T14:30:00+00:00",
            action_type=HistoryActionType.INSTALL,
            items=(HistoryItem(name="vim", source=PackageSource.APT, version="9.0"),),
            reversible=True,
            success=True,
            metadata={"command": "popctl apply"},
        )

        with patch("popctl.cli.commands.history.StateManager") as mock_state:
            mock_state.return_value.get_history.return_value = [entry]

            result = runner.invoke(app, ["history", "--json"])

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert len(data) == 1

        record = data[0]
        assert record["id"] == "test12345678"
        assert record["timestamp"] == "2026-01-26T14:30:00+00:00"
        assert record["action_type"] == "install"
        assert record["reversible"] is True
        assert record["success"] is True
        assert record["metadata"] == {"command": "popctl apply"}
        assert len(record["items"]) == 1
        assert record["items"][0]["name"] == "vim"
        assert record["items"][0]["source"] == "apt"
        assert record["items"][0]["version"] == "9.0"

    def test_json_with_since_filter(self, sample_history_entries: list[HistoryEntry]) -> None:
        """JSON output respects --since filter."""
        with patch("popctl.cli.commands.history.StateManager") as mock_state:
            mock_state.return_value.get_history.return_value = sample_history_entries

            result = runner.invoke(app, ["history", "--json", "--since", "2026-01-26"])

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        # Should only have 2 entries from Jan 26
        assert len(data) == 2
        assert data[0]["id"] == "abc123456789"
        assert data[1]["id"] == "def678901234"
