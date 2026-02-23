"""Unit tests for SnapOperator.

Tests for the Snap package operator implementation.
"""

from unittest.mock import patch

import pytest
from popctl.models.action import ActionType
from popctl.models.package import PackageSource
from popctl.operators.snap import SnapOperator
from popctl.utils.shell import CommandResult


class TestSnapOperator:
    """Tests for SnapOperator class."""

    @pytest.fixture
    def operator(self) -> SnapOperator:
        """Create SnapOperator instance."""
        return SnapOperator()

    @pytest.fixture
    def dry_run_operator(self) -> SnapOperator:
        """Create SnapOperator in dry-run mode."""
        return SnapOperator(dry_run=True)

    def test_source_is_snap(self, operator: SnapOperator) -> None:
        """Operator returns SNAP as source."""
        assert operator.source == PackageSource.SNAP

    def test_is_available_with_snap(self, operator: SnapOperator) -> None:
        """is_available returns True when snap exists."""
        with patch("popctl.operators.snap.command_exists", return_value=True):
            assert operator.is_available() is True

    def test_is_available_without_snap(self, operator: SnapOperator) -> None:
        """is_available returns False when snap is missing."""
        with patch("popctl.operators.snap.command_exists", return_value=False):
            assert operator.is_available() is False

    def test_install_success(self, operator: SnapOperator) -> None:
        """install() returns success results on snap success."""
        with (
            patch("popctl.operators.snap.command_exists", return_value=True),
            patch("popctl.operators.snap.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(stdout="", stderr="", returncode=0)

            results = operator.install(["firefox"])

        assert len(results) == 1
        assert results[0].success is True
        assert results[0].action.package == "firefox"
        assert results[0].action.action_type == ActionType.INSTALL

        # Verify correct command was called
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert "sudo" in args
        assert "snap" in args
        assert "install" in args
        assert "firefox" in args

    def test_install_failure(self, operator: SnapOperator) -> None:
        """install() returns failure results on snap failure."""
        with (
            patch("popctl.operators.snap.command_exists", return_value=True),
            patch("popctl.operators.snap.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(
                stdout="", stderr='error: snap "nonexistent" not found', returncode=1
            )

            results = operator.install(["nonexistent"])

        assert len(results) == 1
        assert results[0].success is False
        assert "not found" in results[0].error.lower()

    def test_install_dry_run(self, dry_run_operator: SnapOperator) -> None:
        """install() in dry-run mode does not execute commands."""
        with (
            patch("popctl.operators.snap.command_exists", return_value=True),
            patch("popctl.operators.snap.run_command") as mock_run,
        ):
            results = dry_run_operator.install(["firefox"])

        assert len(results) == 1
        assert results[0].success is True
        assert "Dry-run" in results[0].message

        # No actual command should have been run
        mock_run.assert_not_called()

    def test_install_empty_list(self, operator: SnapOperator) -> None:
        """install() with empty list returns empty results."""
        with patch("popctl.operators.snap.command_exists", return_value=True):
            results = operator.install([])

        assert results == []

    def test_install_raises_when_unavailable(self, operator: SnapOperator) -> None:
        """install() raises RuntimeError when snap unavailable."""
        with (
            patch("popctl.operators.snap.command_exists", return_value=False),
            pytest.raises(RuntimeError, match="not available"),
        ):
            operator.install(["firefox"])

    def test_remove_success(self, operator: SnapOperator) -> None:
        """remove() returns success results on snap success."""
        with (
            patch("popctl.operators.snap.command_exists", return_value=True),
            patch("popctl.operators.snap.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(stdout="", stderr="", returncode=0)

            results = operator.remove(["firefox"])

        assert len(results) == 1
        assert results[0].success is True
        assert results[0].action.action_type == ActionType.REMOVE

        # Verify correct command was called
        args = mock_run.call_args[0][0]
        assert "sudo" in args
        assert "snap" in args
        assert "remove" in args
        assert "firefox" in args
        assert "--purge" not in args

    def test_remove_with_purge(self, operator: SnapOperator) -> None:
        """remove() with purge=True uses --purge flag and ActionType.PURGE."""
        with (
            patch("popctl.operators.snap.command_exists", return_value=True),
            patch("popctl.operators.snap.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(stdout="", stderr="", returncode=0)

            results = operator.remove(["firefox"], purge=True)

        assert len(results) == 1
        assert results[0].success is True
        assert results[0].action.action_type == ActionType.PURGE

        # Verify --purge flag was used
        args = mock_run.call_args[0][0]
        assert "sudo" in args
        assert "snap" in args
        assert "remove" in args
        assert "--purge" in args
        assert "firefox" in args

    def test_remove_failure(self, operator: SnapOperator) -> None:
        """remove() returns failure results on snap failure."""
        with (
            patch("popctl.operators.snap.command_exists", return_value=True),
            patch("popctl.operators.snap.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(
                stdout="", stderr='error: snap "firefox" is not installed', returncode=1
            )

            results = operator.remove(["firefox"])

        assert len(results) == 1
        assert results[0].success is False

    def test_remove_dry_run(self, dry_run_operator: SnapOperator) -> None:
        """remove() in dry-run mode does not execute commands."""
        with (
            patch("popctl.operators.snap.command_exists", return_value=True),
            patch("popctl.operators.snap.run_command") as mock_run,
        ):
            results = dry_run_operator.remove(["firefox"])

        assert len(results) == 1
        assert results[0].success is True
        assert "Dry-run" in results[0].message

        # No actual command should have been run
        mock_run.assert_not_called()

    def test_remove_empty_list(self, operator: SnapOperator) -> None:
        """remove() with empty list returns empty results."""
        with patch("popctl.operators.snap.command_exists", return_value=True):
            results = operator.remove([])

        assert results == []

    def test_remove_raises_when_unavailable(self, operator: SnapOperator) -> None:
        """remove() raises RuntimeError when snap unavailable."""
        with (
            patch("popctl.operators.snap.command_exists", return_value=False),
            pytest.raises(RuntimeError, match="not available"),
        ):
            operator.remove(["firefox"])
