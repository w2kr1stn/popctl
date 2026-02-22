"""Unit tests for config history recording.

Tests that config deletion operations are correctly recorded
to the shared history file with proper metadata and action types.
"""

from unittest.mock import MagicMock, patch

from popctl.domain.history import record_domain_deletions
from popctl.models.history import HistoryActionType, HistoryEntry


class TestRecordConfigDeletions:
    """Tests for record_config_deletions function."""

    @patch("popctl.domain.history.record_action")
    def test_record_config_deletions(self, mock_record: MagicMock) -> None:
        """Happy path: entry is written to history."""
        record_domain_deletions("configs", ["/home/user/.config/old_app"])

        mock_record.assert_called_once()
        entry: HistoryEntry = mock_record.call_args[0][0]
        assert len(entry.items) == 1
        assert entry.items[0].name == "/home/user/.config/old_app"

    @patch("popctl.domain.history.record_action")
    def test_record_config_deletions_metadata(self, mock_record: MagicMock) -> None:
        """Metadata contains domain=configs and command."""
        record_domain_deletions(
            "configs",
            ["/home/user/.config/stale"],
            command="popctl sync",
        )

        entry: HistoryEntry = mock_record.call_args[0][0]
        assert entry.metadata["domain"] == "configs"
        assert entry.metadata["command"] == "popctl sync"

    @patch("popctl.domain.history.record_action")
    def test_record_config_deletions_not_reversible(self, mock_record: MagicMock) -> None:
        """Config deletions are not reversible."""
        record_domain_deletions("configs", ["/home/user/.config/old_app"])

        entry: HistoryEntry = mock_record.call_args[0][0]
        assert entry.reversible is False

    @patch("popctl.domain.history.record_action")
    def test_record_config_deletions_action_type(self, mock_record: MagicMock) -> None:
        """Action type is CONFIG_DELETE."""
        record_domain_deletions("configs", ["/home/user/.config/old_app"])

        entry: HistoryEntry = mock_record.call_args[0][0]
        assert entry.action_type == HistoryActionType.CONFIG_DELETE

    @patch("popctl.domain.history.record_action")
    def test_record_config_deletions_default_command(self, mock_record: MagicMock) -> None:
        """Default command is 'popctl config clean'."""
        record_domain_deletions("configs", ["/home/user/.config/old_app"])

        entry: HistoryEntry = mock_record.call_args[0][0]
        assert entry.metadata["command"] == "popctl config clean"

    @patch("popctl.domain.history.record_action")
    def test_record_config_deletions_multiple_paths(self, mock_record: MagicMock) -> None:
        """Multiple deleted paths create multiple history items."""
        paths = [
            "/home/user/.config/app1",
            "/home/user/.config/app2",
            "/home/user/.config/app3",
        ]
        record_domain_deletions("configs", paths)

        entry: HistoryEntry = mock_record.call_args[0][0]
        assert len(entry.items) == 3
        assert entry.items[0].name == "/home/user/.config/app1"
        assert entry.items[1].name == "/home/user/.config/app2"
        assert entry.items[2].name == "/home/user/.config/app3"

    @patch("popctl.domain.history.record_action")
    def test_record_config_deletions_without_source(self, mock_record: MagicMock) -> None:
        """Domain deletions have no package source (source is None)."""
        record_domain_deletions("configs", ["/home/user/.config/old_app"])

        entry: HistoryEntry = mock_record.call_args[0][0]
        assert entry.items[0].source is None
