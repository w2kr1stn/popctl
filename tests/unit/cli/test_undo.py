"""Unit tests for undo command.

Tests for the CLI undo command implementation.
"""

from unittest.mock import MagicMock, patch

import pytest
from popctl.cli.main import app
from popctl.models.action import ActionResult
from popctl.models.history import (
    HistoryActionType,
    HistoryEntry,
    HistoryItem,
)
from popctl.models.package import PackageSource
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def reversible_install_entry() -> HistoryEntry:
    """Create a reversible install entry for testing."""
    return HistoryEntry(
        id="abc123456789",
        timestamp="2026-01-26T14:30:00+00:00",
        action_type=HistoryActionType.INSTALL,
        items=(
            HistoryItem(name="vim", source=PackageSource.APT, version="9.0"),
            HistoryItem(name="htop", source=PackageSource.APT, version="3.3.0"),
        ),
        reversible=True,
    )


@pytest.fixture
def reversible_remove_entry() -> HistoryEntry:
    """Create a reversible remove entry for testing."""
    return HistoryEntry(
        id="def678901234",
        timestamp="2026-01-26T14:25:00+00:00",
        action_type=HistoryActionType.REMOVE,
        items=(HistoryItem(name="nano", source=PackageSource.APT, version="7.2"),),
        reversible=True,
    )


@pytest.fixture
def mixed_source_entry() -> HistoryEntry:
    """Create an entry with both APT and Flatpak items."""
    return HistoryEntry(
        id="mix123456789",
        timestamp="2026-01-26T14:00:00+00:00",
        action_type=HistoryActionType.INSTALL,
        items=(
            HistoryItem(name="vim", source=PackageSource.APT),
            HistoryItem(name="com.spotify.Client", source=PackageSource.FLATPAK),
        ),
        reversible=True,
    )


class TestUndoCommandHelp:
    """Tests for undo command help."""

    def test_undo_help(self) -> None:
        """Undo command shows help."""
        result = runner.invoke(app, ["undo", "--help"])
        assert result.exit_code == 0
        assert "Undo the last reversible action" in result.stdout
        assert "--dry-run" in result.stdout
        assert "--yes" in result.stdout

    def test_undo_help_shows_short_options(self) -> None:
        """Undo help shows short option forms."""
        result = runner.invoke(app, ["undo", "--help"])
        assert "-n" in result.stdout
        assert "-y" in result.stdout


class TestUndoNoHistory:
    """Tests for undo command when no reversible actions exist."""

    def test_undo_no_reversible_actions(self) -> None:
        """Undo shows message when no reversible actions exist."""
        with patch("popctl.cli.commands.undo.StateManager") as mock_state:
            mock_state.return_value.get_last_reversible.return_value = None

            result = runner.invoke(app, ["undo"])

        assert result.exit_code == 0
        assert "No reversible actions" in result.stdout


class TestUndoDryRun:
    """Tests for undo --dry-run option."""

    def test_undo_dry_run_shows_preview(self, reversible_install_entry: HistoryEntry) -> None:
        """Dry-run shows preview without executing."""
        with patch("popctl.cli.commands.undo.StateManager") as mock_state:
            mock_state.return_value.get_last_reversible.return_value = reversible_install_entry

            result = runner.invoke(app, ["undo", "--dry-run"])

        assert result.exit_code == 0
        # Should show what would be undone
        assert "install" in result.stdout.lower()
        assert "remove" in result.stdout.lower()
        assert "vim" in result.stdout
        assert "htop" in result.stdout
        # Should indicate no changes made
        assert "No changes made" in result.stdout

    def test_undo_dry_run_does_not_execute(self, reversible_install_entry: HistoryEntry) -> None:
        """Dry-run does not call operators or mark entry reversed."""
        with patch("popctl.cli.commands.undo.StateManager") as mock_state:
            mock_state.return_value.get_last_reversible.return_value = reversible_install_entry

            result = runner.invoke(app, ["undo", "--dry-run"])

        assert result.exit_code == 0
        # mark_entry_reversed should NOT be called
        mock_state.return_value.mark_entry_reversed.assert_not_called()


class TestUndoConfirmation:
    """Tests for undo confirmation prompt."""

    def test_undo_prompts_for_confirmation(self, reversible_install_entry: HistoryEntry) -> None:
        """Undo prompts for confirmation by default."""
        with patch("popctl.cli.commands.undo.StateManager") as mock_state:
            mock_state.return_value.get_last_reversible.return_value = reversible_install_entry

            # Simulate user declining
            result = runner.invoke(app, ["undo"], input="n\n")

        assert result.exit_code == 0
        assert "Cancelled" in result.stdout

    def test_undo_yes_skips_confirmation(self, reversible_install_entry: HistoryEntry) -> None:
        """Undo --yes skips confirmation prompt."""
        with (
            patch("popctl.cli.commands.undo.StateManager") as mock_state,
            patch("popctl.cli.commands.undo.AptOperator") as mock_apt,
        ):
            mock_state.return_value.get_last_reversible.return_value = reversible_install_entry
            mock_state.return_value.mark_entry_reversed.return_value = True

            # Create mock action results
            mock_result = MagicMock(spec=ActionResult)
            mock_result.success = True
            mock_apt.return_value.execute.return_value = [mock_result, mock_result]

            result = runner.invoke(app, ["undo", "--yes"])

        assert result.exit_code == 0
        assert "Cancelled" not in result.stdout
        # Operator should have been called
        mock_apt.return_value.execute.assert_called_once()


class TestUndoExecution:
    """Tests for undo command execution."""

    def test_undo_install_executes_remove(self, reversible_install_entry: HistoryEntry) -> None:
        """Undoing install executes remove."""
        with (
            patch("popctl.cli.commands.undo.StateManager") as mock_state,
            patch("popctl.cli.commands.undo.AptOperator") as mock_apt,
        ):
            mock_state.return_value.get_last_reversible.return_value = reversible_install_entry
            mock_state.return_value.mark_entry_reversed.return_value = True

            mock_result = MagicMock(spec=ActionResult)
            mock_result.success = True
            mock_apt.return_value.execute.return_value = [mock_result, mock_result]

            result = runner.invoke(app, ["undo", "--yes"])

        assert result.exit_code == 0
        assert "undone successfully" in result.stdout

        # Verify remove was called
        mock_apt.assert_called_once_with(dry_run=False)
        actions = mock_apt.return_value.execute.call_args[0][0]
        assert len(actions) == 2
        assert all(a.action_type.value == "remove" for a in actions)

    def test_undo_remove_executes_install(self, reversible_remove_entry: HistoryEntry) -> None:
        """Undoing remove executes install."""
        with (
            patch("popctl.cli.commands.undo.StateManager") as mock_state,
            patch("popctl.cli.commands.undo.AptOperator") as mock_apt,
        ):
            mock_state.return_value.get_last_reversible.return_value = reversible_remove_entry
            mock_state.return_value.mark_entry_reversed.return_value = True

            mock_result = MagicMock(spec=ActionResult)
            mock_result.success = True
            mock_apt.return_value.execute.return_value = [mock_result]

            result = runner.invoke(app, ["undo", "--yes"])

        assert result.exit_code == 0
        assert "undone successfully" in result.stdout

        # Verify install was called
        actions = mock_apt.return_value.execute.call_args[0][0]
        assert len(actions) == 1
        assert actions[0].action_type.value == "install"
        assert actions[0].package == "nano"

    def test_undo_marks_entry_reversed_on_success(
        self, reversible_install_entry: HistoryEntry
    ) -> None:
        """Undo marks entry as reversed after successful execution."""
        with (
            patch("popctl.cli.commands.undo.StateManager") as mock_state,
            patch("popctl.cli.commands.undo.AptOperator") as mock_apt,
        ):
            mock_state.return_value.get_last_reversible.return_value = reversible_install_entry
            mock_state.return_value.mark_entry_reversed.return_value = True

            mock_result = MagicMock(spec=ActionResult)
            mock_result.success = True
            mock_apt.return_value.execute.return_value = [mock_result, mock_result]

            result = runner.invoke(app, ["undo", "--yes"])

        assert result.exit_code == 0
        mock_state.return_value.mark_entry_reversed.assert_called_once_with("abc123456789")


class TestUndoMixedSources:
    """Tests for undo with mixed package sources."""

    def test_undo_mixed_sources(self, mixed_source_entry: HistoryEntry) -> None:
        """Undo handles both APT and Flatpak packages."""
        with (
            patch("popctl.cli.commands.undo.StateManager") as mock_state,
            patch("popctl.cli.commands.undo.AptOperator") as mock_apt,
            patch("popctl.cli.commands.undo.FlatpakOperator") as mock_flatpak,
        ):
            mock_state.return_value.get_last_reversible.return_value = mixed_source_entry
            mock_state.return_value.mark_entry_reversed.return_value = True

            mock_result = MagicMock(spec=ActionResult)
            mock_result.success = True
            mock_apt.return_value.execute.return_value = [mock_result]
            mock_flatpak.return_value.execute.return_value = [mock_result]

            result = runner.invoke(app, ["undo", "--yes"])

        assert result.exit_code == 0
        assert "undone successfully" in result.stdout

        # Both operators should have been called
        mock_apt.return_value.execute.assert_called_once()
        mock_flatpak.return_value.execute.assert_called_once()

        # Verify APT action
        apt_actions = mock_apt.return_value.execute.call_args[0][0]
        assert len(apt_actions) == 1
        assert apt_actions[0].package == "vim"

        # Verify Flatpak action
        flatpak_actions = mock_flatpak.return_value.execute.call_args[0][0]
        assert len(flatpak_actions) == 1
        assert flatpak_actions[0].package == "com.spotify.Client"


class TestUndoFailure:
    """Tests for undo command failure handling."""

    def test_undo_failure_reports_error(self, reversible_install_entry: HistoryEntry) -> None:
        """Undo reports error when operator fails."""
        with (
            patch("popctl.cli.commands.undo.StateManager") as mock_state,
            patch("popctl.cli.commands.undo.AptOperator") as mock_apt,
        ):
            mock_state.return_value.get_last_reversible.return_value = reversible_install_entry

            # Simulate failure
            mock_result = MagicMock(spec=ActionResult)
            mock_result.success = False
            mock_apt.return_value.execute.return_value = [mock_result, mock_result]

            result = runner.invoke(app, ["undo", "--yes"], catch_exceptions=False)

        assert result.exit_code == 1
        # Error message goes to stderr via err_console
        output = result.stdout + (result.stderr or "")
        assert "Failed to undo" in output

    def test_undo_failure_does_not_mark_reversed(
        self, reversible_install_entry: HistoryEntry
    ) -> None:
        """Failed undo does not mark entry as reversed."""
        with (
            patch("popctl.cli.commands.undo.StateManager") as mock_state,
            patch("popctl.cli.commands.undo.AptOperator") as mock_apt,
        ):
            mock_state.return_value.get_last_reversible.return_value = reversible_install_entry

            mock_result = MagicMock(spec=ActionResult)
            mock_result.success = False
            mock_apt.return_value.execute.return_value = [mock_result]

            runner.invoke(app, ["undo", "--yes"])

        # mark_entry_reversed should NOT be called on failure
        mock_state.return_value.mark_entry_reversed.assert_not_called()

    def test_undo_partial_failure(self, mixed_source_entry: HistoryEntry) -> None:
        """Undo fails if any operator fails."""
        with (
            patch("popctl.cli.commands.undo.StateManager") as mock_state,
            patch("popctl.cli.commands.undo.AptOperator") as mock_apt,
            patch("popctl.cli.commands.undo.FlatpakOperator") as mock_flatpak,
        ):
            mock_state.return_value.get_last_reversible.return_value = mixed_source_entry

            # APT succeeds
            apt_result = MagicMock(spec=ActionResult)
            apt_result.success = True
            mock_apt.return_value.execute.return_value = [apt_result]

            # Flatpak fails
            flatpak_result = MagicMock(spec=ActionResult)
            flatpak_result.success = False
            mock_flatpak.return_value.execute.return_value = [flatpak_result]

            result = runner.invoke(app, ["undo", "--yes"], catch_exceptions=False)

        assert result.exit_code == 1
        # Error message goes to stderr via err_console
        output = result.stdout + (result.stderr or "")
        assert "Failed to undo" in output


class TestUndoPreview:
    """Tests for undo preview display."""

    def test_undo_preview_shows_entry_info(self, reversible_install_entry: HistoryEntry) -> None:
        """Preview shows entry ID, timestamp, and packages."""
        with patch("popctl.cli.commands.undo.StateManager") as mock_state:
            mock_state.return_value.get_last_reversible.return_value = reversible_install_entry

            result = runner.invoke(app, ["undo", "--dry-run"])

        assert result.exit_code == 0
        # Should show truncated ID
        assert "abc12345" in result.stdout
        # Should show timestamp
        assert "2026-01-26" in result.stdout
        # Should show packages
        assert "vim" in result.stdout
        assert "htop" in result.stdout
        # Should show action transformation
        assert "install" in result.stdout.lower()
        assert "remove" in result.stdout.lower()

    def test_undo_preview_truncates_many_packages(self) -> None:
        """Preview truncates list when many packages."""
        entry = HistoryEntry(
            id="many12345678",
            timestamp="2026-01-26T14:30:00+00:00",
            action_type=HistoryActionType.INSTALL,
            items=tuple(HistoryItem(name=f"pkg{i}", source=PackageSource.APT) for i in range(15)),
            reversible=True,
        )

        with patch("popctl.cli.commands.undo.StateManager") as mock_state:
            mock_state.return_value.get_last_reversible.return_value = entry

            result = runner.invoke(app, ["undo", "--dry-run"])

        assert result.exit_code == 0
        # Should show first 10 packages
        for i in range(10):
            assert f"pkg{i}" in result.stdout
        # Should indicate more packages
        assert "5 more" in result.stdout


class TestUndoPurge:
    """Tests for undoing purge actions."""

    def test_undo_purge_executes_install(self) -> None:
        """Undoing purge executes install (config is lost)."""
        purge_entry = HistoryEntry(
            id="purge1234567",
            timestamp="2026-01-26T14:30:00+00:00",
            action_type=HistoryActionType.PURGE,
            items=(HistoryItem(name="nginx", source=PackageSource.APT),),
            reversible=True,
        )

        with (
            patch("popctl.cli.commands.undo.StateManager") as mock_state,
            patch("popctl.cli.commands.undo.AptOperator") as mock_apt,
        ):
            mock_state.return_value.get_last_reversible.return_value = purge_entry
            mock_state.return_value.mark_entry_reversed.return_value = True

            mock_result = MagicMock(spec=ActionResult)
            mock_result.success = True
            mock_apt.return_value.execute.return_value = [mock_result]

            result = runner.invoke(app, ["undo", "--yes"])

        assert result.exit_code == 0

        # Verify install was called (not purge reversal)
        actions = mock_apt.return_value.execute.call_args[0][0]
        assert len(actions) == 1
        assert actions[0].action_type.value == "install"
