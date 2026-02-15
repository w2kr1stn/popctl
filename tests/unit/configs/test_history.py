"""Unit tests for config history recording.

Tests that config deletion operations are correctly recorded
to the shared history file with proper metadata and action types.
"""

from unittest.mock import MagicMock, patch

from popctl.configs.history import record_config_deletions
from popctl.models.history import HistoryActionType, HistoryEntry
from popctl.models.package import PackageSource


class TestRecordConfigDeletions:
    """Tests for record_config_deletions function."""

    @patch("popctl.configs.history.StateManager")
    def test_record_config_deletions(self, mock_sm_cls: MagicMock) -> None:
        """Happy path: entry is written to history."""
        mock_sm = MagicMock()
        mock_sm_cls.return_value = mock_sm

        record_config_deletions(["/home/user/.config/old_app"])

        mock_sm.record_action.assert_called_once()
        entry: HistoryEntry = mock_sm.record_action.call_args[0][0]
        assert len(entry.items) == 1
        assert entry.items[0].name == "/home/user/.config/old_app"

    @patch("popctl.configs.history.StateManager")
    def test_record_config_deletions_metadata(self, mock_sm_cls: MagicMock) -> None:
        """Metadata contains domain=configs and command."""
        mock_sm = MagicMock()
        mock_sm_cls.return_value = mock_sm

        record_config_deletions(
            ["/home/user/.config/stale"],
            command="popctl sync",
        )

        entry: HistoryEntry = mock_sm.record_action.call_args[0][0]
        assert entry.metadata["domain"] == "configs"
        assert entry.metadata["command"] == "popctl sync"

    @patch("popctl.configs.history.StateManager")
    def test_record_config_deletions_not_reversible(self, mock_sm_cls: MagicMock) -> None:
        """Config deletions are not reversible."""
        mock_sm = MagicMock()
        mock_sm_cls.return_value = mock_sm

        record_config_deletions(["/home/user/.config/old_app"])

        entry: HistoryEntry = mock_sm.record_action.call_args[0][0]
        assert entry.reversible is False

    @patch("popctl.configs.history.StateManager")
    def test_record_config_deletions_action_type(self, mock_sm_cls: MagicMock) -> None:
        """Action type is CONFIG_DELETE."""
        mock_sm = MagicMock()
        mock_sm_cls.return_value = mock_sm

        record_config_deletions(["/home/user/.config/old_app"])

        entry: HistoryEntry = mock_sm.record_action.call_args[0][0]
        assert entry.action_type == HistoryActionType.CONFIG_DELETE

    @patch("popctl.configs.history.StateManager")
    def test_record_config_deletions_default_command(self, mock_sm_cls: MagicMock) -> None:
        """Default command is 'popctl config clean'."""
        mock_sm = MagicMock()
        mock_sm_cls.return_value = mock_sm

        record_config_deletions(["/home/user/.config/old_app"])

        entry: HistoryEntry = mock_sm.record_action.call_args[0][0]
        assert entry.metadata["command"] == "popctl config clean"

    @patch("popctl.configs.history.StateManager")
    def test_record_config_deletions_multiple_paths(self, mock_sm_cls: MagicMock) -> None:
        """Multiple deleted paths create multiple history items."""
        mock_sm = MagicMock()
        mock_sm_cls.return_value = mock_sm

        paths = [
            "/home/user/.config/app1",
            "/home/user/.config/app2",
            "/home/user/.config/app3",
        ]
        record_config_deletions(paths)

        entry: HistoryEntry = mock_sm.record_action.call_args[0][0]
        assert len(entry.items) == 3
        assert entry.items[0].name == "/home/user/.config/app1"
        assert entry.items[1].name == "/home/user/.config/app2"
        assert entry.items[2].name == "/home/user/.config/app3"

    @patch("popctl.configs.history.StateManager")
    def test_record_config_deletions_uses_apt_placeholder(self, mock_sm_cls: MagicMock) -> None:
        """History items use PackageSource.APT as placeholder source."""
        mock_sm = MagicMock()
        mock_sm_cls.return_value = mock_sm

        record_config_deletions(["/home/user/.config/old_app"])

        entry: HistoryEntry = mock_sm.record_action.call_args[0][0]
        assert entry.items[0].source == PackageSource.APT
