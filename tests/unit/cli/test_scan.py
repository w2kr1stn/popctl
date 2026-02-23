"""Unit tests for scan command.

Tests for the CLI scan command implementation.
"""

import json
import tempfile
from pathlib import Path
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
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch("popctl.scanners.apt.run_command") as mock_run,
        ):
            mock_run.side_effect = [
                CommandResult(stdout=mock_auto, stderr="", returncode=0),
                CommandResult(stdout=mock_dpkg, stderr="", returncode=0),
            ]

            result = runner.invoke(app, ["scan", "--count", "--source", "apt"])

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
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch("popctl.scanners.apt.run_command") as mock_run,
        ):
            mock_run.side_effect = [
                CommandResult(stdout=mock_auto, stderr="", returncode=0),
                CommandResult(stdout=mock_dpkg, stderr="", returncode=0),
            ]

            result = runner.invoke(app, ["scan", "--manual-only", "--source", "apt"])

        assert result.exit_code == 0
        assert "firefox" in result.stdout
        assert "Manually Installed Packages" in result.stdout

    def test_scan_with_limit(self) -> None:
        """Scan with --limit restricts output."""
        mock_dpkg = "pkg1\t1.0\t100\tPkg1\npkg2\t1.0\t100\tPkg2\npkg3\t1.0\t100\tPkg3"
        mock_auto = ""

        with (
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch("popctl.scanners.apt.run_command") as mock_run,
        ):
            mock_run.side_effect = [
                CommandResult(stdout=mock_auto, stderr="", returncode=0),
                CommandResult(stdout=mock_dpkg, stderr="", returncode=0),
            ]

            result = runner.invoke(app, ["scan", "--limit", "2", "--source", "apt"])

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
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch("popctl.scanners.apt.run_command") as mock_run,
        ):
            mock_run.side_effect = [
                CommandResult(stdout=mock_auto, stderr="", returncode=0),
                CommandResult(stdout=mock_dpkg, stderr="", returncode=0),
            ]

            result = runner.invoke(app, ["scan", "--source", "apt"])

        assert result.exit_code == 0
        assert "firefox" in result.stdout
        assert "128.0" in result.stdout
        assert "Mozilla Firefox" in result.stdout

    def test_scan_apt_unavailable(self) -> None:
        """Scan fails gracefully when APT is unavailable (when APT explicitly requested)."""
        with patch("popctl.scanners.apt.command_exists", return_value=False):
            result = runner.invoke(app, ["scan", "--source", "apt"])

        assert result.exit_code == 1
        # Error message about no available package managers
        assert "not available" in (result.stdout + result.stderr)

    def test_scan_dpkg_error(self) -> None:
        """Scan handles dpkg-query errors gracefully."""
        with (
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch("popctl.scanners.apt.run_command") as mock_run,
        ):
            mock_run.side_effect = [
                CommandResult(stdout="", stderr="", returncode=0),
                CommandResult(stdout="", stderr="dpkg error", returncode=1),
            ]

            result = runner.invoke(app, ["scan", "--source", "apt"])

        assert result.exit_code == 1
        # Error message may be in output or stderr
        assert "dpkg-query failed" in (result.stdout + result.stderr)


class TestScanOutputFormat:
    """Tests for scan output formatting."""

    def test_shows_package_count_summary(self) -> None:
        """Scan shows package count summary at end."""
        mock_dpkg = "firefox\t128.0\t204800\tFirefox\nlibfoo\t1.0\t100\tLibrary"
        mock_auto = "libfoo"

        with (
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch("popctl.scanners.apt.run_command") as mock_run,
        ):
            mock_run.side_effect = [
                CommandResult(stdout=mock_auto, stderr="", returncode=0),
                CommandResult(stdout=mock_dpkg, stderr="", returncode=0),
            ]

            result = runner.invoke(app, ["scan", "--source", "apt"])

        assert result.exit_code == 0
        assert "Showing 2 of 2 packages" in result.stdout
        assert "(1 manual, 1 auto)" in result.stdout


class TestScanSourceOption:
    """Tests for --source option."""

    def test_scan_source_apt_only(self) -> None:
        """Scan --source apt scans only APT packages."""
        mock_dpkg = "firefox\t128.0\t204800\tFirefox"
        mock_auto = ""

        with (
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.apt.run_command") as mock_run,
        ):
            mock_run.side_effect = [
                CommandResult(stdout=mock_auto, stderr="", returncode=0),
                CommandResult(stdout=mock_dpkg, stderr="", returncode=0),
            ]

            result = runner.invoke(app, ["scan", "--source", "apt"])

        assert result.exit_code == 0
        assert "firefox" in result.stdout
        assert "(APT)" in result.stdout

    def test_scan_source_flatpak_only(self) -> None:
        """Scan --source flatpak scans only Flatpak apps."""
        mock_flatpak = "com.spotify.Client\t1.2.31\t1.2 GB\tMusic"

        with (
            patch("popctl.scanners.flatpak.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(
                stdout=mock_flatpak, stderr="", returncode=0
            )

            result = runner.invoke(app, ["scan", "--source", "flatpak"])

        assert result.exit_code == 0
        assert "com.spotify.Client" in result.stdout
        assert "(FLATPAK)" in result.stdout

    def test_scan_source_all(self) -> None:
        """Scan --source all scans both APT and Flatpak."""
        mock_dpkg = "firefox\t128.0\t204800\tFirefox"
        mock_auto = ""
        mock_flatpak = "com.spotify.Client\t1.2.31\t1.2 GB\tMusic"

        with (
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=True),
            patch("popctl.scanners.apt.run_command") as mock_apt_run,
            patch("popctl.scanners.flatpak.run_command") as mock_flatpak_run,
        ):
            mock_apt_run.side_effect = [
                CommandResult(stdout=mock_auto, stderr="", returncode=0),
                CommandResult(stdout=mock_dpkg, stderr="", returncode=0),
            ]
            mock_flatpak_run.return_value = CommandResult(
                stdout=mock_flatpak, stderr="", returncode=0
            )

            result = runner.invoke(app, ["scan", "--source", "all"])

        assert result.exit_code == 0
        assert "firefox" in result.stdout
        assert "com.spotify.Client" in result.stdout

    def test_scan_flatpak_unavailable_warning(self) -> None:
        """Scan shows warning when Flatpak is unavailable but continues with APT."""
        mock_dpkg = "firefox\t128.0\t204800\tFirefox"
        mock_auto = ""

        with (
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch("popctl.scanners.apt.run_command") as mock_run,
        ):
            mock_run.side_effect = [
                CommandResult(stdout=mock_auto, stderr="", returncode=0),
                CommandResult(stdout=mock_dpkg, stderr="", returncode=0),
            ]

            result = runner.invoke(app, ["scan", "--source", "all"])

        assert result.exit_code == 0
        assert "firefox" in result.stdout
        # Warning about Flatpak in stderr
        assert "FLATPAK" in result.stderr and "not available" in result.stderr


class TestScanExportOption:
    """Tests for --export option."""

    def test_scan_export_creates_json_file(self) -> None:
        """Scan --export creates JSON file with scan results."""
        mock_dpkg = "firefox\t128.0\t204800\tFirefox"
        mock_auto = ""

        with tempfile.TemporaryDirectory() as tmpdir:
            export_path = Path(tmpdir) / "scan.json"

            with (
                patch("popctl.scanners.apt.command_exists", return_value=True),
                patch("popctl.scanners.flatpak.command_exists", return_value=False),
                patch("popctl.scanners.apt.run_command") as mock_run,
            ):
                mock_run.side_effect = [
                    CommandResult(stdout=mock_auto, stderr="", returncode=0),
                    CommandResult(stdout=mock_dpkg, stderr="", returncode=0),
                ]

                result = runner.invoke(
                    app, ["scan", "--source", "apt", "--export", str(export_path)]
                )

            assert result.exit_code == 0
            assert export_path.exists()

            # Verify JSON structure
            data = json.loads(export_path.read_text())
            assert "metadata" in data
            assert "packages" in data
            assert "summary" in data
            assert data["metadata"]["sources"] == ["apt"]
            assert len(data["packages"]) == 1
            assert data["packages"][0]["name"] == "firefox"

    def test_scan_export_includes_metadata(self) -> None:
        """Exported JSON includes proper metadata."""
        mock_dpkg = "firefox\t128.0\t204800\tFirefox"
        mock_auto = ""

        with tempfile.TemporaryDirectory() as tmpdir:
            export_path = Path(tmpdir) / "scan.json"

            with (
                patch("popctl.scanners.apt.command_exists", return_value=True),
                patch("popctl.scanners.flatpak.command_exists", return_value=False),
                patch("popctl.scanners.apt.run_command") as mock_run,
            ):
                mock_run.side_effect = [
                    CommandResult(stdout=mock_auto, stderr="", returncode=0),
                    CommandResult(stdout=mock_dpkg, stderr="", returncode=0),
                ]

                result = runner.invoke(
                    app, ["scan", "--source", "apt", "--export", str(export_path)]
                )

            assert result.exit_code == 0
            data = json.loads(export_path.read_text())

            metadata = data["metadata"]
            assert "timestamp" in metadata
            assert "hostname" in metadata
            assert "popctl_version" in metadata
            assert metadata["popctl_version"] == "0.1.0"


class TestScanFormatOption:
    """Tests for --format option."""

    def test_scan_format_json(self) -> None:
        """Scan --format json outputs JSON to stdout."""
        mock_dpkg = "firefox\t128.0\t204800\tFirefox"
        mock_auto = ""

        with (
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch("popctl.scanners.apt.run_command") as mock_run,
        ):
            mock_run.side_effect = [
                CommandResult(stdout=mock_auto, stderr="", returncode=0),
                CommandResult(stdout=mock_dpkg, stderr="", returncode=0),
            ]

            result = runner.invoke(app, ["scan", "--source", "apt", "--format", "json"])

        assert result.exit_code == 0
        # Should be valid JSON
        data = json.loads(result.stdout)
        assert "metadata" in data
        assert "packages" in data
        assert len(data["packages"]) == 1

    def test_scan_format_table_default(self) -> None:
        """Scan defaults to table format."""
        mock_dpkg = "firefox\t128.0\t204800\tFirefox"
        mock_auto = ""

        with (
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch("popctl.scanners.apt.run_command") as mock_run,
        ):
            mock_run.side_effect = [
                CommandResult(stdout=mock_auto, stderr="", returncode=0),
                CommandResult(stdout=mock_dpkg, stderr="", returncode=0),
            ]

            result = runner.invoke(app, ["scan", "--source", "apt"])

        assert result.exit_code == 0
        # Table format contains package names and dividers
        assert "firefox" in result.stdout
        # Should not be pure JSON
        with pytest.raises(json.JSONDecodeError):
            json.loads(result.stdout)
