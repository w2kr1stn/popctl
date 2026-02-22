"""Unit tests for filesystem history recording.

Tests that filesystem deletion operations are correctly recorded
to the shared history file with proper metadata and action types.
"""

from unittest.mock import MagicMock, patch

from popctl.domain.history import record_domain_deletions
from popctl.models.history import HistoryActionType, HistoryEntry


class TestRecordFsDeletions:
    """Tests for record_fs_deletions function."""

    @patch("popctl.domain.history.record_action")
    def test_record_fs_deletions(self, mock_record: MagicMock) -> None:
        """Happy path: entry is written to history."""
        record_domain_deletions("filesystem", ["/home/user/.config/old_app"])

        mock_record.assert_called_once()
        entry: HistoryEntry = mock_record.call_args[0][0]
        assert len(entry.items) == 1
        assert entry.items[0].name == "/home/user/.config/old_app"

    @patch("popctl.domain.history.record_action")
    def test_record_fs_deletions_metadata(self, mock_record: MagicMock) -> None:
        """Metadata contains domain and command."""
        record_domain_deletions(
            "filesystem",
            ["/home/user/.cache/stale"],
            command="popctl sync",
        )

        entry: HistoryEntry = mock_record.call_args[0][0]
        assert entry.metadata["domain"] == "filesystem"
        assert entry.metadata["command"] == "popctl sync"

    @patch("popctl.domain.history.record_action")
    def test_record_fs_deletions_default_command(self, mock_record: MagicMock) -> None:
        """Default command is 'popctl fs clean'."""
        record_domain_deletions("filesystem", ["/home/user/.config/old_app"])

        entry: HistoryEntry = mock_record.call_args[0][0]
        assert entry.metadata["command"] == "popctl fs clean"

    @patch("popctl.domain.history.record_action")
    def test_record_fs_deletions_not_reversible(self, mock_record: MagicMock) -> None:
        """Filesystem deletions are not reversible."""
        record_domain_deletions("filesystem", ["/home/user/.config/old_app"])

        entry: HistoryEntry = mock_record.call_args[0][0]
        assert entry.reversible is False

    @patch("popctl.domain.history.record_action")
    def test_record_fs_deletions_action_type(self, mock_record: MagicMock) -> None:
        """Action type is FS_DELETE."""
        record_domain_deletions("filesystem", ["/home/user/.config/old_app"])

        entry: HistoryEntry = mock_record.call_args[0][0]
        assert entry.action_type == HistoryActionType.FS_DELETE

    @patch("popctl.domain.history.record_action")
    def test_record_fs_deletions_multiple_paths(self, mock_record: MagicMock) -> None:
        """Multiple deleted paths create multiple history items."""
        paths = [
            "/home/user/.config/app1",
            "/home/user/.cache/app2",
            "/home/user/.local/share/app3",
        ]
        record_domain_deletions("filesystem", paths)

        entry: HistoryEntry = mock_record.call_args[0][0]
        assert len(entry.items) == 3
        assert entry.items[0].name == "/home/user/.config/app1"
        assert entry.items[1].name == "/home/user/.cache/app2"
        assert entry.items[2].name == "/home/user/.local/share/app3"

    @patch("popctl.domain.history.record_action")
    def test_record_fs_deletions_without_source(self, mock_record: MagicMock) -> None:
        """Domain deletions have no package source (source is None)."""
        record_domain_deletions("filesystem", ["/home/user/.config/old_app"])

        entry: HistoryEntry = mock_record.call_args[0][0]
        assert entry.items[0].source is None
