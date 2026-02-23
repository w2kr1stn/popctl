"""Unit tests for init command.

Tests for the CLI init command implementation.
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from popctl.cli.main import app
from popctl.utils.shell import CommandResult
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def mock_apt_packages() -> str:
    """Sample APT packages for testing."""
    return """firefox\t128.0\t204800\tMozilla Firefox
neovim\t0.9.5\t51200\tText editor
git\t2.43.0\t10240\tVersion control
systemd\t255\t8000\tSystem daemon"""


@pytest.fixture
def mock_flatpak_packages() -> str:
    """Sample Flatpak packages for testing."""
    return """com.spotify.Client\t1.2.31\t1.2 GB\tMusic streaming
org.mozilla.firefox\t128.0\t500 MB\tWeb browser"""


class TestInitCommand:
    """Tests for popctl init command."""

    def test_init_help(self) -> None:
        """Init command shows help."""
        result = runner.invoke(app, ["init", "--help"])
        assert result.exit_code == 0
        assert "Initialize" in result.stdout

    def test_init_dry_run(
        self, tmp_path: Path, mock_apt_packages: str, mock_flatpak_packages: str
    ) -> None:
        """Init --dry-run shows what would be created without writing files."""
        with (
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=True),
            patch("popctl.scanners.apt.run_command") as mock_apt_run,
            patch("popctl.scanners.flatpak.run_command") as mock_flatpak_run,
            patch.dict(os.environ, {"XDG_CONFIG_HOME": str(tmp_path / "config")}),
        ):
            mock_apt_run.side_effect = [
                CommandResult(stdout="", stderr="", returncode=0),  # apt-mark
                CommandResult(stdout=mock_apt_packages, stderr="", returncode=0),
            ]
            mock_flatpak_run.return_value = CommandResult(
                stdout=mock_flatpak_packages, stderr="", returncode=0
            )

            result = runner.invoke(app, ["init", "--dry-run"])

        assert result.exit_code == 0
        assert "DRY-RUN" in result.stdout
        # Manifest should not be created
        assert not (tmp_path / "config" / "popctl" / "manifest.toml").exists()

    def test_init_creates_manifest(
        self, tmp_path: Path, mock_apt_packages: str, mock_flatpak_packages: str
    ) -> None:
        """Init creates manifest.toml file."""
        manifest_path = tmp_path / "manifest.toml"

        with (
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=True),
            patch("popctl.scanners.apt.run_command") as mock_apt_run,
            patch("popctl.scanners.flatpak.run_command") as mock_flatpak_run,
        ):
            mock_apt_run.side_effect = [
                CommandResult(stdout="", stderr="", returncode=0),
                CommandResult(stdout=mock_apt_packages, stderr="", returncode=0),
            ]
            mock_flatpak_run.return_value = CommandResult(
                stdout=mock_flatpak_packages, stderr="", returncode=0
            )

            result = runner.invoke(app, ["init", "--output", str(manifest_path)])

        assert result.exit_code == 0
        assert manifest_path.exists()
        assert "Manifest created" in result.stdout

    def test_init_excludes_protected_packages(self, tmp_path: Path) -> None:
        """Init excludes protected system packages from manifest."""
        mock_dpkg = """firefox\t128.0\t100\tBrowser
systemd\t255\t8000\tSystem daemon
linux-image-generic\t6.5\t500000\tKernel
apt-utils\t2.7\t10000\tPackage manager utilities"""

        manifest_path = tmp_path / "manifest.toml"

        with (
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch("popctl.scanners.apt.run_command") as mock_run,
        ):
            mock_run.side_effect = [
                CommandResult(stdout="", stderr="", returncode=0),
                CommandResult(stdout=mock_dpkg, stderr="", returncode=0),
            ]

            result = runner.invoke(app, ["init", "--output", str(manifest_path)])

        assert result.exit_code == 0

        # Read and check manifest content - check packages.keep section
        import tomllib

        with open(manifest_path, "rb") as f:
            data = tomllib.load(f)

        keep_packages = data["packages"]["keep"]
        assert "firefox" in keep_packages
        assert "systemd" not in keep_packages
        assert "linux-image-generic" not in keep_packages
        assert "apt-utils" not in keep_packages

    def test_init_excludes_auto_installed_packages(self, tmp_path: Path) -> None:
        """Init excludes auto-installed packages from manifest."""
        mock_dpkg = """firefox\t128.0\t100\tBrowser
libfoo\t1.0\t50\tLibrary"""
        mock_auto = "libfoo"

        manifest_path = tmp_path / "manifest.toml"

        with (
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch("popctl.scanners.apt.run_command") as mock_run,
        ):
            mock_run.side_effect = [
                CommandResult(stdout=mock_auto, stderr="", returncode=0),
                CommandResult(stdout=mock_dpkg, stderr="", returncode=0),
            ]

            result = runner.invoke(app, ["init", "--output", str(manifest_path)])

        assert result.exit_code == 0

        content = manifest_path.read_text()
        assert "firefox" in content
        assert "libfoo" not in content

    def test_init_fails_if_manifest_exists(self, tmp_path: Path) -> None:
        """Init fails if manifest already exists without --force."""
        manifest_path = tmp_path / "manifest.toml"
        manifest_path.write_text("[meta]\nversion = '1.0'\n")

        result = runner.invoke(app, ["init", "--output", str(manifest_path)])

        assert result.exit_code == 1
        assert "already exists" in (result.stdout + result.stderr)

    def test_init_force_overwrites_existing(self, tmp_path: Path, mock_apt_packages: str) -> None:
        """Init --force overwrites existing manifest."""
        manifest_path = tmp_path / "manifest.toml"
        manifest_path.write_text("[meta]\nversion = 'old'\n")

        with (
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch("popctl.scanners.apt.run_command") as mock_run,
        ):
            mock_run.side_effect = [
                CommandResult(stdout="", stderr="", returncode=0),
                CommandResult(stdout=mock_apt_packages, stderr="", returncode=0),
            ]

            result = runner.invoke(app, ["init", "--force", "--output", str(manifest_path)])

        assert result.exit_code == 0
        content = manifest_path.read_text()
        assert "firefox" in content

    def test_init_fails_without_package_managers(self) -> None:
        """Init fails when no package managers are available."""
        with (
            patch("popctl.scanners.apt.command_exists", return_value=False),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
        ):
            result = runner.invoke(app, ["init", "--dry-run"])

        assert result.exit_code == 1
        assert "No package managers available" in (result.stdout + result.stderr)


class TestInitManifestContent:
    """Tests for generated manifest content."""

    def test_manifest_has_correct_structure(self, tmp_path: Path, mock_apt_packages: str) -> None:
        """Generated manifest has correct TOML structure."""
        import tomllib

        manifest_path = tmp_path / "manifest.toml"

        with (
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch("popctl.scanners.apt.run_command") as mock_run,
        ):
            mock_run.side_effect = [
                CommandResult(stdout="", stderr="", returncode=0),
                CommandResult(stdout=mock_apt_packages, stderr="", returncode=0),
            ]

            runner.invoke(app, ["init", "--output", str(manifest_path)])

        with open(manifest_path, "rb") as f:
            data = tomllib.load(f)

        # Check structure
        assert "meta" in data
        assert "system" in data
        assert "packages" in data

        # Check meta
        assert data["meta"]["version"] == "1.0"
        assert "created" in data["meta"]
        assert "updated" in data["meta"]

        # Check system
        assert "name" in data["system"]
        assert data["system"]["base"] == "pop-os-24.04"

        # Check packages
        assert "keep" in data["packages"]
        assert "remove" in data["packages"]

    def test_manifest_packages_have_correct_format(
        self, tmp_path: Path, mock_apt_packages: str, mock_flatpak_packages: str
    ) -> None:
        """Package entries have correct source attribute."""
        import tomllib

        manifest_path = tmp_path / "manifest.toml"

        with (
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=True),
            patch("popctl.scanners.apt.run_command") as mock_apt_run,
            patch("popctl.scanners.flatpak.run_command") as mock_flatpak_run,
        ):
            mock_apt_run.side_effect = [
                CommandResult(stdout="", stderr="", returncode=0),
                CommandResult(stdout=mock_apt_packages, stderr="", returncode=0),
            ]
            mock_flatpak_run.return_value = CommandResult(
                stdout=mock_flatpak_packages, stderr="", returncode=0
            )

            runner.invoke(app, ["init", "--output", str(manifest_path)])

        with open(manifest_path, "rb") as f:
            data = tomllib.load(f)

        keep_packages = data["packages"]["keep"]

        # Check APT package format
        if "firefox" in keep_packages:
            assert keep_packages["firefox"]["source"] == "apt"

        # Check Flatpak package format
        if "com.spotify.Client" in keep_packages:
            assert keep_packages["com.spotify.Client"]["source"] == "flatpak"


class TestInitSummary:
    """Tests for init command summary output."""

    def test_shows_package_counts(
        self, tmp_path: Path, mock_apt_packages: str, mock_flatpak_packages: str
    ) -> None:
        """Init shows package count summary."""
        manifest_path = tmp_path / "manifest.toml"

        with (
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=True),
            patch("popctl.scanners.apt.run_command") as mock_apt_run,
            patch("popctl.scanners.flatpak.run_command") as mock_flatpak_run,
        ):
            mock_apt_run.side_effect = [
                CommandResult(stdout="", stderr="", returncode=0),
                CommandResult(stdout=mock_apt_packages, stderr="", returncode=0),
            ]
            mock_flatpak_run.return_value = CommandResult(
                stdout=mock_flatpak_packages, stderr="", returncode=0
            )

            result = runner.invoke(app, ["init", "--output", str(manifest_path)])

        assert result.exit_code == 0
        # Should show some count information
        assert "Total packages:" in result.stdout or "packages" in result.stdout.lower()
