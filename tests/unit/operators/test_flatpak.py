"""Unit tests for FlatpakOperator.

Tests for the Flatpak package operator implementation.
"""

from unittest.mock import patch

import pytest
from popctl.models.action import ActionType
from popctl.models.package import PackageSource
from popctl.operators.flatpak import FlatpakOperator
from popctl.utils.shell import CommandResult


class TestFlatpakOperator:
    """Tests for FlatpakOperator class."""

    @pytest.fixture
    def operator(self) -> FlatpakOperator:
        """Create FlatpakOperator instance."""
        return FlatpakOperator()

    @pytest.fixture
    def dry_run_operator(self) -> FlatpakOperator:
        """Create FlatpakOperator in dry-run mode."""
        return FlatpakOperator(dry_run=True)

    def test_source_is_flatpak(self, operator: FlatpakOperator) -> None:
        """Operator returns FLATPAK as source."""
        assert operator.source == PackageSource.FLATPAK

    def test_is_available_when_flatpak_exists(self, operator: FlatpakOperator) -> None:
        """is_available returns True when flatpak exists."""
        with patch("popctl.operators.flatpak.command_exists", return_value=True):
            assert operator.is_available() is True

    def test_is_available_when_flatpak_missing(self, operator: FlatpakOperator) -> None:
        """is_available returns False when flatpak is missing."""
        with patch("popctl.operators.flatpak.command_exists", return_value=False):
            assert operator.is_available() is False

    def test_install_success(self, operator: FlatpakOperator) -> None:
        """install() returns success results on flatpak success."""
        with (
            patch("popctl.operators.flatpak.command_exists", return_value=True),
            patch("popctl.operators.flatpak.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(stdout="", stderr="", returncode=0)

            results = operator.install(["com.spotify.Client"])

        assert len(results) == 1
        assert results[0].success is True
        assert results[0].action.package == "com.spotify.Client"
        assert results[0].action.action_type == ActionType.INSTALL

        # Verify correct command was called
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert "flatpak" in args
        assert "install" in args
        assert "-y" in args
        assert "com.spotify.Client" in args

    def test_install_multiple_packages(self, operator: FlatpakOperator) -> None:
        """install() installs packages one at a time."""
        with (
            patch("popctl.operators.flatpak.command_exists", return_value=True),
            patch("popctl.operators.flatpak.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(stdout="", stderr="", returncode=0)

            results = operator.install(["com.spotify.Client", "org.mozilla.firefox"])

        assert len(results) == 2
        assert all(r.success for r in results)
        # Should have been called twice (once per package)
        assert mock_run.call_count == 2

    def test_install_failure(self, operator: FlatpakOperator) -> None:
        """install() returns failure results on flatpak failure."""
        with (
            patch("popctl.operators.flatpak.command_exists", return_value=True),
            patch("popctl.operators.flatpak.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(
                stdout="", stderr="error: com.invalid.App not found", returncode=1
            )

            results = operator.install(["com.invalid.App"])

        assert len(results) == 1
        assert results[0].success is False
        assert "not found" in results[0].error.lower()

    def test_install_dry_run(self, dry_run_operator: FlatpakOperator) -> None:
        """install() in dry-run mode does not execute commands."""
        with (
            patch("popctl.operators.flatpak.command_exists", return_value=True),
            patch("popctl.operators.flatpak.run_command") as mock_run,
        ):
            results = dry_run_operator.install(["com.spotify.Client"])

        assert len(results) == 1
        assert results[0].success is True
        assert "Dry-run" in results[0].message

        # No actual command should have been run
        mock_run.assert_not_called()

    def test_install_raises_when_unavailable(self, operator: FlatpakOperator) -> None:
        """install() raises RuntimeError when Flatpak unavailable."""
        with (
            patch("popctl.operators.flatpak.command_exists", return_value=False),
            pytest.raises(RuntimeError, match="not available"),
        ):
            operator.install(["com.spotify.Client"])

    def test_install_empty_list(self, operator: FlatpakOperator) -> None:
        """install() with empty list returns empty results."""
        with patch("popctl.operators.flatpak.command_exists", return_value=True):
            results = operator.install([])

        assert results == []

    def test_remove_success(self, operator: FlatpakOperator) -> None:
        """remove() returns success results on flatpak success."""
        with (
            patch("popctl.operators.flatpak.command_exists", return_value=True),
            patch("popctl.operators.flatpak.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(stdout="", stderr="", returncode=0)

            results = operator.remove(["com.spotify.Client"])

        assert len(results) == 1
        assert results[0].success is True
        assert results[0].action.action_type == ActionType.REMOVE

        # Verify correct command was called
        args = mock_run.call_args[0][0]
        assert "uninstall" in args

    def test_remove_ignores_purge_flag(self, operator: FlatpakOperator) -> None:
        """remove() ignores purge flag (Flatpak has no purge)."""
        with (
            patch("popctl.operators.flatpak.command_exists", return_value=True),
            patch("popctl.operators.flatpak.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(stdout="", stderr="", returncode=0)

            results = operator.remove(["com.spotify.Client"], purge=True)

        # Should still work, just ignore the flag
        assert len(results) == 1
        assert results[0].success is True
        # Action type is still REMOVE (not PURGE) because Flatpak doesn't support purge
        assert results[0].action.action_type == ActionType.REMOVE

    def test_remove_failure(self, operator: FlatpakOperator) -> None:
        """remove() returns failure results on flatpak failure."""
        with (
            patch("popctl.operators.flatpak.command_exists", return_value=True),
            patch("popctl.operators.flatpak.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(
                stdout="", stderr="error: com.spotify.Client not installed", returncode=1
            )

            results = operator.remove(["com.spotify.Client"])

        assert len(results) == 1
        assert results[0].success is False

    def test_remove_dry_run(self, dry_run_operator: FlatpakOperator) -> None:
        """remove() in dry-run mode does not execute commands."""
        with (
            patch("popctl.operators.flatpak.command_exists", return_value=True),
            patch("popctl.operators.flatpak.run_command") as mock_run,
        ):
            results = dry_run_operator.remove(["com.spotify.Client"])

        assert len(results) == 1
        assert results[0].success is True
        assert "Dry-run" in results[0].message

        # No actual command should have been run
        mock_run.assert_not_called()

    def test_remove_raises_when_unavailable(self, operator: FlatpakOperator) -> None:
        """remove() raises RuntimeError when Flatpak unavailable."""
        with (
            patch("popctl.operators.flatpak.command_exists", return_value=False),
            pytest.raises(RuntimeError, match="not available"),
        ):
            operator.remove(["com.spotify.Client"])

    def test_remove_empty_list(self, operator: FlatpakOperator) -> None:
        """remove() with empty list returns empty results."""
        with patch("popctl.operators.flatpak.command_exists", return_value=True):
            results = operator.remove([])

        assert results == []
