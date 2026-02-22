"""Unit tests for core/executor.py.

Tests operator factory, action execution dispatch, and history recording.
"""

from unittest.mock import MagicMock, patch

from popctl.core.executor import (
    execute_actions,
    get_available_operators,
    record_actions_to_history,
)
from popctl.models.action import Action, ActionResult, ActionType
from popctl.models.history import HistoryActionType
from popctl.models.package import PackageSource
from popctl.operators.apt import AptOperator
from popctl.operators.flatpak import FlatpakOperator
from popctl.operators.snap import SnapOperator

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_action(
    package: str = "test-pkg",
    action_type: ActionType = ActionType.INSTALL,
    source: PackageSource = PackageSource.APT,
) -> Action:
    """Create a test Action."""
    return Action(
        action_type=action_type,
        package=package,
        source=source,
    )


def _make_result(
    action: Action | None = None,
    success: bool = True,
) -> ActionResult:
    """Create a test ActionResult."""
    if action is None:
        action = _make_action()
    return ActionResult(
        action=action,
        success=success,
        detail="ok" if success else "failed",
    )


# ---------------------------------------------------------------------------
# get_available_operators
# ---------------------------------------------------------------------------


class TestGetAvailableOperators:
    """Tests for the get_available_operators filter function."""

    def test_get_available_operators_filters(self) -> None:
        """Operators that are not available are excluded."""
        with (
            patch.object(AptOperator, "is_available", return_value=True),
            patch.object(FlatpakOperator, "is_available", return_value=False),
            patch.object(SnapOperator, "is_available", return_value=False),
        ):
            ops = get_available_operators()

        assert len(ops) == 1
        assert isinstance(ops[0], AptOperator)


# ---------------------------------------------------------------------------
# execute_actions
# ---------------------------------------------------------------------------


class TestExecuteActions:
    """Tests for the execute_actions dispatcher."""

    def test_execute_actions_dispatches_by_source(self) -> None:
        """Actions are dispatched to operators matching their source."""
        apt_action = _make_action(package="vim", source=PackageSource.APT)
        flatpak_action = _make_action(
            package="com.example.App",
            source=PackageSource.FLATPAK,
        )

        apt_result = _make_result(apt_action)
        flatpak_result = _make_result(flatpak_action)

        apt_op = MagicMock(spec=AptOperator)
        apt_op.source = PackageSource.APT
        apt_op.execute.return_value = [apt_result]

        flatpak_op = MagicMock(spec=FlatpakOperator)
        flatpak_op.source = PackageSource.FLATPAK
        flatpak_op.execute.return_value = [flatpak_result]

        results = execute_actions(
            [apt_action, flatpak_action],
            [apt_op, flatpak_op],
        )

        apt_op.execute.assert_called_once_with([apt_action])
        flatpak_op.execute.assert_called_once_with([flatpak_action])
        assert results == [apt_result, flatpak_result]

    def test_execute_actions_empty_list(self) -> None:
        """Empty action list returns empty results."""
        op = MagicMock(spec=AptOperator)
        op.source = PackageSource.APT

        results = execute_actions([], [op])

        assert results == []
        op.execute.assert_not_called()


# ---------------------------------------------------------------------------
# record_actions_to_history
# ---------------------------------------------------------------------------


class TestRecordActionsToHistory:
    """Tests for the record_actions_to_history function."""

    def test_record_actions_to_history_success(self) -> None:
        """Successful actions are recorded via record_action."""
        action = _make_action(package="vim", action_type=ActionType.INSTALL)
        result = _make_result(action, success=True)

        with patch("popctl.core.executor.record_action") as mock_record:
            record_actions_to_history([result])

        mock_record.assert_called_once()

        entry = mock_record.call_args[0][0]
        assert entry.action_type == HistoryActionType.INSTALL
        assert len(entry.items) == 1
        assert entry.items[0].name == "vim"

    def test_record_actions_to_history_groups_by_type(self) -> None:
        """Separate history entries are created per ActionType."""
        install = _make_action("vim", ActionType.INSTALL, PackageSource.APT)
        remove = _make_action("bloat", ActionType.REMOVE, PackageSource.APT)

        results = [
            _make_result(install, success=True),
            _make_result(remove, success=True),
        ]

        with patch("popctl.core.executor.record_action") as mock_record:
            record_actions_to_history(results)

        assert mock_record.call_count == 2

        recorded_types = {call.args[0].action_type for call in mock_record.call_args_list}
        assert recorded_types == {HistoryActionType.INSTALL, HistoryActionType.REMOVE}

    def test_record_actions_to_history_custom_command(self) -> None:
        """The command parameter appears in the history metadata."""
        action = _make_action()
        result = _make_result(action, success=True)

        with patch("popctl.core.executor.record_action") as mock_record:
            record_actions_to_history([result], command="popctl sync")

        entry = mock_record.call_args[0][0]
        assert entry.metadata["command"] == "popctl sync"

    def test_record_actions_to_history_handles_os_error(self) -> None:
        """OSError is caught and reported via print_warning, no crash."""
        action = _make_action()
        result = _make_result(action, success=True)

        with (
            patch(
                "popctl.core.executor.record_action",
                side_effect=OSError("disk full"),
            ),
            patch("popctl.core.executor.print_warning") as mock_warn,
        ):
            # Should NOT raise
            record_actions_to_history([result])

        mock_warn.assert_called_once()
        assert "disk full" in mock_warn.call_args[0][0]

    def test_record_actions_to_history_skips_failed(self) -> None:
        """Failed results are not recorded in history."""
        ok_action = _make_action("vim", ActionType.INSTALL, PackageSource.APT)
        fail_action = _make_action("bad", ActionType.INSTALL, PackageSource.APT)

        results = [
            _make_result(ok_action, success=True),
            _make_result(fail_action, success=False),
        ]

        with patch("popctl.core.executor.record_action") as mock_record:
            record_actions_to_history(results)

        mock_record.assert_called_once()

        entry = mock_record.call_args[0][0]
        names = [item.name for item in entry.items]
        assert "vim" in names
        assert "bad" not in names
