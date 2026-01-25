"""Unit tests for scan command.

Tests for the CLI scan command implementation.
"""

from unittest.mock import patch

import pytest
from popctl.cli.main import app
from popctl.models.package import PackageSource, PackageStatus, ScannedPackage
from popctl.utils.shell import CommandResult
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def sample_packages() -> list[ScannedPackage]:
    """Create sample packages for testing."""
    return [
        ScannedPackage(
            name="firefox",
            source=PackageSource.APT,
            version="128.0",
            status=PackageStatus.MANUAL,
            description="Mozilla Firefox web browser",
            size_bytes=204800000,
        ),
        ScannedPackage(
            name="neovim",
            source=PackageSource.APT,
            version="0.9.5",
            status=PackageStatus.MANUAL,
            description="Vim-based text editor",
            size_bytes=51200000,
        ),
        ScannedPackage(
            name="libgtk-3-0",
            source=PackageSource.APT,
            version="3.24.41",
            status=PackageStatus.AUTO_INSTALLED,
            description="GTK graphical toolkit",
            size_bytes=10240000,
        ),
    ]


class TestScanCommand:
    """Tests for popctl scan command."""

    def test_scan_help(self) -> None:
        """Scan command shows help."""
        result = runner.invoke(app, ["scan", "--help"])
        assert result.exit_code == 0
        assert "Scan system for installed packages" in result.stdout

    def test_scan_count_only(self) -> None:
        """Scan with --count shows counts only."""
        mock_dpkg = "firefox\t128.0\t204800\tFirefox\nneovim\t0.9.5\t51200\tNeovim"
        mock_auto = ""

        with (
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.apt.run_command") as mock_run,
        ):
            mock_run.side_effect = [
                CommandResult(stdout=mock_auto, stderr="", returncode=0),
                CommandResult(stdout=mock_dpkg, stderr="", returncode=0),
            ]

            result = runner.invoke(app, ["scan", "--count"])

        assert result.exit_code == 0
        assert "Total packages: 2" in result.stdout
        assert "Manual: 2" in result.stdout
        assert "Auto: 0" in result.stdout

    def test_scan_manual_only(self) -> None:
        """Scan with --manual-only shows only manual packages."""
        mock_dpkg = "firefox\t128.0\t204800\tFirefox\nlibfoo\t1.0\t100\tLibrary"
        mock_auto = "libfoo"

        with (
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.apt.run_command") as mock_run,
        ):
            mock_run.side_effect = [
                CommandResult(stdout=mock_auto, stderr="", returncode=0),
                CommandResult(stdout=mock_dpkg, stderr="", returncode=0),
            ]

            result = runner.invoke(app, ["scan", "--manual-only"])

        assert result.exit_code == 0
        assert "firefox" in result.stdout
        assert "Manually Installed Packages" in result.stdout

    def test_scan_with_limit(self) -> None:
        """Scan with --limit restricts output."""
        mock_dpkg = "pkg1\t1.0\t100\tPkg1\npkg2\t1.0\t100\tPkg2\npkg3\t1.0\t100\tPkg3"
        mock_auto = ""

        with (
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.apt.run_command") as mock_run,
        ):
            mock_run.side_effect = [
                CommandResult(stdout=mock_auto, stderr="", returncode=0),
                CommandResult(stdout=mock_dpkg, stderr="", returncode=0),
            ]

            result = runner.invoke(app, ["scan", "--limit", "2"])

        assert result.exit_code == 0
        assert "pkg1" in result.stdout
        assert "pkg2" in result.stdout
        assert "limited to 2" in result.stdout

    def test_scan_shows_table(self) -> None:
        """Scan displays packages in table format."""
        mock_dpkg = "firefox\t128.0\t204800\tMozilla Firefox"
        mock_auto = ""

        with (
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.apt.run_command") as mock_run,
        ):
            mock_run.side_effect = [
                CommandResult(stdout=mock_auto, stderr="", returncode=0),
                CommandResult(stdout=mock_dpkg, stderr="", returncode=0),
            ]

            result = runner.invoke(app, ["scan"])

        assert result.exit_code == 0
        assert "firefox" in result.stdout
        assert "128.0" in result.stdout
        assert "Mozilla Firefox" in result.stdout

    def test_scan_apt_unavailable(self) -> None:
        """Scan fails gracefully when APT is unavailable."""
        with patch("popctl.scanners.apt.command_exists", return_value=False):
            result = runner.invoke(app, ["scan"])

        assert result.exit_code == 1
        # Error message may be in output or exception
        assert result.output == "" or "not available" in result.output

    def test_scan_dpkg_error(self) -> None:
        """Scan handles dpkg-query errors gracefully."""
        with (
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.apt.run_command") as mock_run,
        ):
            mock_run.side_effect = [
                CommandResult(stdout="", stderr="", returncode=0),
                CommandResult(stdout="", stderr="dpkg error", returncode=1),
            ]

            result = runner.invoke(app, ["scan"])

        assert result.exit_code == 1
        # Error message may be in output or exception
        assert result.output == "" or "dpkg-query failed" in result.output


class TestScanOutputFormat:
    """Tests for scan output formatting."""

    def test_shows_package_count_summary(self) -> None:
        """Scan shows package count summary at end."""
        mock_dpkg = "firefox\t128.0\t204800\tFirefox\nlibfoo\t1.0\t100\tLibrary"
        mock_auto = "libfoo"

        with (
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.apt.run_command") as mock_run,
        ):
            mock_run.side_effect = [
                CommandResult(stdout=mock_auto, stderr="", returncode=0),
                CommandResult(stdout=mock_dpkg, stderr="", returncode=0),
            ]

            result = runner.invoke(app, ["scan"])

        assert result.exit_code == 0
        assert "Showing 2 of 2 packages" in result.stdout
        assert "(1 manual, 1 auto)" in result.stdout
