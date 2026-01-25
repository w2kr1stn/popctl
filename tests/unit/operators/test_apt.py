"""Unit tests for AptOperator.

Tests for the APT package operator implementation.
"""

from unittest.mock import patch

import pytest

from popctl.models.action import ActionType
from popctl.models.package import PackageSource
from popctl.operators.apt import AptOperator
from popctl.utils.shell import CommandResult


class TestAptOperator:
    """Tests for AptOperator class."""

    @pytest.fixture
    def operator(self) -> AptOperator:
        """Create AptOperator instance."""
        return AptOperator()

    @pytest.fixture
    def dry_run_operator(self) -> AptOperator:
        """Create AptOperator in dry-run mode."""
        return AptOperator(dry_run=True)

    def test_source_is_apt(self, operator: AptOperator) -> None:
        """Operator returns APT as source."""
        assert operator.source == PackageSource.APT

    def test_is_available_when_apt_exists(self, operator: AptOperator) -> None:
        """is_available returns True when apt-get exists."""
        with patch("popctl.operators.apt.command_exists", return_value=True):
            assert operator.is_available() is True

    def test_is_available_when_apt_missing(self, operator: AptOperator) -> None:
        """is_available returns False when apt-get is missing."""
        with patch("popctl.operators.apt.command_exists", return_value=False):
            assert operator.is_available() is False

    def test_install_success(self, operator: AptOperator) -> None:
        """install() returns success results on apt-get success."""
        with (
            patch("popctl.operators.apt.command_exists", return_value=True),
            patch("popctl.operators.apt.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(stdout="", stderr="", returncode=0)

            results = operator.install(["htop", "neovim"])

        assert len(results) == 2
        assert all(r.success for r in results)
        assert results[0].action.package == "htop"
        assert results[1].action.package == "neovim"
        assert all(r.action.action_type == ActionType.INSTALL for r in results)

        # Verify correct command was called
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert "sudo" in args
        assert "apt-get" in args
        assert "install" in args
        assert "-y" in args
        assert "htop" in args
        assert "neovim" in args

    def test_install_failure(self, operator: AptOperator) -> None:
        """install() returns failure results on apt-get failure."""
        with (
            patch("popctl.operators.apt.command_exists", return_value=True),
            patch("popctl.operators.apt.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(
                stdout="", stderr="E: Package htop not found", returncode=100
            )

            results = operator.install(["htop"])

        assert len(results) == 1
        assert results[0].success is False
        assert "not found" in results[0].error.lower()

    def test_install_dry_run(self, dry_run_operator: AptOperator) -> None:
        """install() in dry-run mode uses --dry-run flag."""
        with (
            patch("popctl.operators.apt.command_exists", return_value=True),
            patch("popctl.operators.apt.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(stdout="", stderr="", returncode=0)

            results = dry_run_operator.install(["htop"])

        assert len(results) == 1
        assert results[0].success is True
        assert "Dry-run" in results[0].message

        # Verify --dry-run was in command
        args = mock_run.call_args[0][0]
        assert "--dry-run" in args

    def test_install_raises_when_unavailable(self, operator: AptOperator) -> None:
        """install() raises RuntimeError when APT unavailable."""
        with (
            patch("popctl.operators.apt.command_exists", return_value=False),
            pytest.raises(RuntimeError, match="not available"),
        ):
            operator.install(["htop"])

    def test_install_empty_list(self, operator: AptOperator) -> None:
        """install() with empty list returns empty results."""
        with patch("popctl.operators.apt.command_exists", return_value=True):
            results = operator.install([])

        assert results == []

    def test_remove_success(self, operator: AptOperator) -> None:
        """remove() returns success results on apt-get success."""
        with (
            patch("popctl.operators.apt.command_exists", return_value=True),
            patch("popctl.operators.apt.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(stdout="", stderr="", returncode=0)

            results = operator.remove(["bloatware"])

        assert len(results) == 1
        assert results[0].success is True
        assert results[0].action.action_type == ActionType.REMOVE

        # Verify correct command was called
        args = mock_run.call_args[0][0]
        assert "remove" in args
        assert "bloatware" in args

    def test_remove_with_purge(self, operator: AptOperator) -> None:
        """remove() with purge=True uses purge command."""
        with (
            patch("popctl.operators.apt.command_exists", return_value=True),
            patch("popctl.operators.apt.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(stdout="", stderr="", returncode=0)

            results = operator.remove(["bloatware"], purge=True)

        assert len(results) == 1
        assert results[0].action.action_type == ActionType.PURGE

        # Verify purge command was used
        args = mock_run.call_args[0][0]
        assert "purge" in args
        assert "remove" not in args

    def test_remove_failure(self, operator: AptOperator) -> None:
        """remove() returns failure results on apt-get failure."""
        with (
            patch("popctl.operators.apt.command_exists", return_value=True),
            patch("popctl.operators.apt.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(
                stdout="", stderr="E: Package bloatware is not installed", returncode=100
            )

            results = operator.remove(["bloatware"])

        assert len(results) == 1
        assert results[0].success is False

    def test_remove_dry_run(self, dry_run_operator: AptOperator) -> None:
        """remove() in dry-run mode uses --dry-run flag."""
        with (
            patch("popctl.operators.apt.command_exists", return_value=True),
            patch("popctl.operators.apt.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(stdout="", stderr="", returncode=0)

            results = dry_run_operator.remove(["bloatware"])

        args = mock_run.call_args[0][0]
        assert "--dry-run" in args

    def test_remove_raises_when_unavailable(self, operator: AptOperator) -> None:
        """remove() raises RuntimeError when APT unavailable."""
        with (
            patch("popctl.operators.apt.command_exists", return_value=False),
            pytest.raises(RuntimeError, match="not available"),
        ):
            operator.remove(["bloatware"])

    def test_remove_empty_list(self, operator: AptOperator) -> None:
        """remove() with empty list returns empty results."""
        with patch("popctl.operators.apt.command_exists", return_value=True):
            results = operator.remove([])

        assert results == []
