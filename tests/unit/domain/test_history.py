"""Tests for domain history recording.

Consolidated from filesystem/test_history.py and configs/test_history.py.
Both domains use the same record_domain_deletions function with different
domain names and action types.
"""

from unittest.mock import MagicMock, patch

import pytest
from popctl.core.state import record_domain_deletions
from popctl.models.history import HistoryActionType, HistoryEntry

_DOMAIN_PARAMS = [
    pytest.param("filesystem", HistoryActionType.FS_DELETE, id="filesystem"),
    pytest.param("configs", HistoryActionType.CONFIG_DELETE, id="configs"),
]


class TestRecordDomainDeletions:
    """Tests for record_domain_deletions function."""

    @pytest.mark.parametrize(("domain", "action_type"), _DOMAIN_PARAMS)
    @patch("popctl.core.state.record_action")
    def test_record_deletions(
        self,
        mock_record: MagicMock,
        domain: str,
        action_type: HistoryActionType,
    ) -> None:
        """Happy path: entry is written to history."""
        record_domain_deletions(domain, ["/home/user/.config/old_app"], command="popctl test")

        mock_record.assert_called_once()
        entry: HistoryEntry = mock_record.call_args[0][0]
        assert len(entry.items) == 1
        assert entry.items[0].name == "/home/user/.config/old_app"

    @pytest.mark.parametrize(("domain", "action_type"), _DOMAIN_PARAMS)
    @patch("popctl.core.state.record_action")
    def test_record_deletions_metadata(
        self,
        mock_record: MagicMock,
        domain: str,
        action_type: HistoryActionType,
    ) -> None:
        """Metadata contains domain and command."""
        record_domain_deletions(domain, ["/home/user/.config/stale"], command="popctl sync")

        entry: HistoryEntry = mock_record.call_args[0][0]
        assert entry.metadata["domain"] == domain
        assert entry.metadata["command"] == "popctl sync"

    @pytest.mark.parametrize(("domain", "action_type"), _DOMAIN_PARAMS)
    @patch("popctl.core.state.record_action")
    def test_record_deletions_not_reversible(
        self,
        mock_record: MagicMock,
        domain: str,
        action_type: HistoryActionType,
    ) -> None:
        """Domain deletions are not reversible."""
        record_domain_deletions(domain, ["/home/user/.config/old_app"], command="popctl test")

        entry: HistoryEntry = mock_record.call_args[0][0]
        assert entry.reversible is False

    @pytest.mark.parametrize(("domain", "action_type"), _DOMAIN_PARAMS)
    @patch("popctl.core.state.record_action")
    def test_record_deletions_action_type(
        self,
        mock_record: MagicMock,
        domain: str,
        action_type: HistoryActionType,
    ) -> None:
        """Action type matches the domain."""
        record_domain_deletions(domain, ["/home/user/.config/old_app"], command="popctl test")

        entry: HistoryEntry = mock_record.call_args[0][0]
        assert entry.action_type == action_type

    @pytest.mark.parametrize(("domain", "action_type"), _DOMAIN_PARAMS)
    @patch("popctl.core.state.record_action")
    def test_record_deletions_multiple_paths(
        self,
        mock_record: MagicMock,
        domain: str,
        action_type: HistoryActionType,
    ) -> None:
        """Multiple deleted paths create multiple history items."""
        paths = [
            "/home/user/.config/app1",
            "/home/user/.config/app2",
            "/home/user/.config/app3",
        ]
        record_domain_deletions(domain, paths, command="popctl test")

        entry: HistoryEntry = mock_record.call_args[0][0]
        assert len(entry.items) == 3

    @pytest.mark.parametrize(("domain", "action_type"), _DOMAIN_PARAMS)
    @patch("popctl.core.state.record_action")
    def test_record_deletions_without_source(
        self,
        mock_record: MagicMock,
        domain: str,
        action_type: HistoryActionType,
    ) -> None:
        """Domain deletions have no package source (source is None)."""
        record_domain_deletions(domain, ["/home/user/.config/old_app"], command="popctl test")

        entry: HistoryEntry = mock_record.call_args[0][0]
        assert entry.items[0].source is None
