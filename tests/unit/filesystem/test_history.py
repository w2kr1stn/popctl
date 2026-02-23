"""Unit tests for filesystem history recording.

Tests that filesystem deletion operations are correctly recorded
to the shared history file with proper metadata and action types.
"""

from unittest.mock import MagicMock, patch

from popctl.filesystem.history import record_fs_deletions
from popctl.models.history import HistoryActionType, HistoryEntry


class TestRecordFsDeletions:
    """Tests for record_fs_deletions function."""

    @patch("popctl.filesystem.history.StateManager")
    def test_record_fs_deletions(self, mock_sm_cls: MagicMock) -> None:
        """Happy path: entry is written to history."""
        mock_sm = MagicMock()
        mock_sm_cls.return_value = mock_sm

        record_fs_deletions(["/home/user/.config/old_app"])

        mock_sm.record_action.assert_called_once()
        entry: HistoryEntry = mock_sm.record_action.call_args[0][0]
        assert len(entry.items) == 1
        assert entry.items[0].name == "/home/user/.config/old_app"

    @patch("popctl.filesystem.history.StateManager")
    def test_record_fs_deletions_metadata(self, mock_sm_cls: MagicMock) -> None:
        """Metadata contains domain and command."""
        mock_sm = MagicMock()
        mock_sm_cls.return_value = mock_sm

        record_fs_deletions(
            ["/home/user/.cache/stale"],
            command="popctl sync",
        )

        entry: HistoryEntry = mock_sm.record_action.call_args[0][0]
        assert entry.metadata["domain"] == "filesystem"
        assert entry.metadata["command"] == "popctl sync"

    @patch("popctl.filesystem.history.StateManager")
    def test_record_fs_deletions_default_command(self, mock_sm_cls: MagicMock) -> None:
        """Default command is 'popctl fs clean'."""
        mock_sm = MagicMock()
        mock_sm_cls.return_value = mock_sm

        record_fs_deletions(["/home/user/.config/old_app"])

        entry: HistoryEntry = mock_sm.record_action.call_args[0][0]
        assert entry.metadata["command"] == "popctl fs clean"

    @patch("popctl.filesystem.history.StateManager")
    def test_record_fs_deletions_not_reversible(self, mock_sm_cls: MagicMock) -> None:
        """Filesystem deletions are not reversible."""
        mock_sm = MagicMock()
        mock_sm_cls.return_value = mock_sm

        record_fs_deletions(["/home/user/.config/old_app"])

        entry: HistoryEntry = mock_sm.record_action.call_args[0][0]
        assert entry.reversible is False

    @patch("popctl.filesystem.history.StateManager")
    def test_record_fs_deletions_action_type(self, mock_sm_cls: MagicMock) -> None:
        """Action type is FS_DELETE."""
        mock_sm = MagicMock()
        mock_sm_cls.return_value = mock_sm

        record_fs_deletions(["/home/user/.config/old_app"])

        entry: HistoryEntry = mock_sm.record_action.call_args[0][0]
        assert entry.action_type == HistoryActionType.FS_DELETE

    @patch("popctl.filesystem.history.StateManager")
    def test_record_fs_deletions_multiple_paths(self, mock_sm_cls: MagicMock) -> None:
        """Multiple deleted paths create multiple history items."""
        mock_sm = MagicMock()
        mock_sm_cls.return_value = mock_sm

        paths = [
            "/home/user/.config/app1",
            "/home/user/.cache/app2",
            "/home/user/.local/share/app3",
        ]
        record_fs_deletions(paths)

        entry: HistoryEntry = mock_sm.record_action.call_args[0][0]
        assert len(entry.items) == 3
        assert entry.items[0].name == "/home/user/.config/app1"
        assert entry.items[1].name == "/home/user/.cache/app2"
        assert entry.items[2].name == "/home/user/.local/share/app3"

    @patch("popctl.filesystem.history.StateManager")
    def test_record_fs_deletions_uses_apt_placeholder(self, mock_sm_cls: MagicMock) -> None:
        """History items use PackageSource.APT as placeholder source."""
        from popctl.models.package import PackageSource

        mock_sm = MagicMock()
        mock_sm_cls.return_value = mock_sm

        record_fs_deletions(["/home/user/.config/old_app"])

        entry: HistoryEntry = mock_sm.record_action.call_args[0][0]
        assert entry.items[0].source == PackageSource.APT
