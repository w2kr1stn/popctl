"""Unit tests for init command.

Tests for the CLI init command implementation.
"""

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from popctl.cli.main import app
from popctl.models.manifest import Manifest, ManifestMeta, PackageConfig, PackageEntry, SystemConfig
from popctl.models.package import PackageSource
from popctl.sources.models import (
    AptKey,
    AptSource,
    AptSourceFormat,
    AptSources,
    ReplayMode,
    SignedByBinding,
    SourcePlatform,
    SourcesConfig,
)
from popctl.sources.phase import (
    SourceCaptureTrustResult,
    SourceInteractionPolicy,
    capture_and_trust_sources,
)
from popctl.utils.shell import CommandResult
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture(autouse=True)
def _source_capture_without_replay() -> Iterator[None]:
    """Keep package-focused init tests independent of source-capture commands."""
    sources = SourcesConfig(platform=SourcePlatform(distro_id="ubuntu", codename="noble"))
    with (
        patch("popctl.scanners.snap.command_exists", return_value=False),
        patch(
            "popctl.cli.commands.init.capture_and_trust_sources",
            return_value=SourceCaptureTrustResult(success=True, sources=sources),
        ),
    ):
        yield


@pytest.fixture
def mock_apt_packages() -> str:
    """Sample APT packages for testing."""
    return """installed\tfirefox\t128.0\t204800\tMozilla Firefox
installed\tneovim\t0.9.5\t51200\tText editor
installed\tgit\t2.43.0\t10240\tVersion control
installed\tsystemd\t255\t8000\tSystem daemon"""


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
        mock_dpkg = """installed\tfirefox\t128.0\t100\tBrowser
installed\tsystemd\t255\t8000\tSystem daemon
installed\tlinux-image-generic\t6.5\t500000\tKernel
installed\tapt-utils\t2.7\t10000\tPackage manager utilities"""

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
        mock_dpkg = """installed\tfirefox\t128.0\t100\tBrowser
installed\tlibfoo\t1.0\t50\tLibrary"""
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

    def test_init_dry_run_forwards_to_source_capture_without_mutation(self) -> None:
        source = AptSource(
            id="vendor",
            capture_path="/etc/apt/sources.list.d/vendor.sources",
            format=AptSourceFormat.DEB822,
            ordinal=0,
            managed_target="popctl-vendor",
            verbatim_stanza="Types: deb\nURIs: https://vendor.example/apt\nSuites: stable\n",
            key_ids=("vendor",),
            signed_by=SignedByBinding(key_paths=("/etc/apt/keyrings/vendor.asc",)),
            replay_mode=ReplayMode.REPLAY,
        )
        sources = SourcesConfig(
            platform=SourcePlatform(distro_id="ubuntu", codename="noble"),
            apt=AptSources(
                entries=(source,),
                keys=(
                    AptKey(
                        id="vendor",
                        target_path="/etc/apt/keyrings/vendor.asc",
                        armor="vendor-key",
                        fingerprints=("A" * 40,),
                    ),
                ),
            ),
        )
        manifest = Manifest(
            meta=ManifestMeta(created=datetime.now(UTC), updated=datetime.now(UTC)),
            system=SystemConfig(name="test-machine"),
            packages=PackageConfig(keep={"vim": PackageEntry(source="apt")}),
        )
        scanner = MagicMock()
        scanner.source = PackageSource.APT

        with (
            patch("popctl.cli.commands.init.get_available_scanners", return_value=[scanner]),
            patch(
                "popctl.cli.commands.init.scan_and_create_manifest",
                return_value=(manifest, manifest.packages.keep, []),
            ),
            patch(
                "popctl.cli.commands.init.capture_and_trust_sources",
                return_value=SourceCaptureTrustResult(success=True, sources=sources),
            ) as capture,
            patch("popctl.cli.commands.init.save_manifest") as save,
            patch("popctl.sources.phase.typer.confirm") as confirm,
            patch("popctl.sources.phase.provision_sources") as provision,
            patch("popctl.core.executor.record_actions_to_history") as history,
        ):
            result = runner.invoke(app, ["init", "--dry-run"])

        assert result.exit_code == 0
        assert capture.call_args.kwargs["dry_run"] is True
        save.assert_not_called()
        confirm.assert_not_called()
        provision.assert_not_called()
        history.assert_not_called()

    def test_init_persists_an_approved_source_capture_once(self) -> None:
        source = AptSource(
            id="vendor",
            capture_path="/etc/apt/sources.list.d/vendor.sources",
            format=AptSourceFormat.DEB822,
            ordinal=0,
            managed_target="popctl-vendor",
            verbatim_stanza="Types: deb\nURIs: https://vendor.example/apt\nSuites: stable\n",
            key_ids=("vendor",),
            signed_by=SignedByBinding(key_paths=("/etc/apt/keyrings/vendor.asc",)),
            replay_mode=ReplayMode.REPLAY,
        )
        sources = SourcesConfig(
            platform=SourcePlatform(distro_id="ubuntu", codename="noble"),
            apt=AptSources(
                entries=(source,),
                keys=(
                    AptKey(
                        id="vendor",
                        target_path="/etc/apt/keyrings/vendor.asc",
                        armor="vendor-key",
                        fingerprints=("A" * 40,),
                    ),
                ),
            ),
        )
        manifest = Manifest(
            meta=ManifestMeta(created=datetime.now(UTC), updated=datetime.now(UTC)),
            system=SystemConfig(name="test-machine"),
            packages=PackageConfig(keep={"vim": PackageEntry(source="apt")}),
        )
        scanner = MagicMock()
        scanner.source = PackageSource.APT

        with (
            patch("popctl.cli.commands.init.get_available_scanners", return_value=[scanner]),
            patch(
                "popctl.cli.commands.init.scan_and_create_manifest",
                return_value=(manifest, manifest.packages.keep, []),
            ),
            patch(
                "popctl.cli.commands.init.capture_and_trust_sources",
                wraps=capture_and_trust_sources,
            ),
            patch("popctl.sources.phase.capture_sources", return_value=sources),
            patch("popctl.sources.phase.typer.confirm", return_value=True) as confirm,
            patch(
                "popctl.cli.commands.init.SourceInteractionPolicy",
                return_value=SourceInteractionPolicy(interactive=True),
            ),
            patch("popctl.cli.commands.init.save_manifest") as save,
        ):
            result = runner.invoke(app, ["init"])

        assert result.exit_code == 0
        confirm.assert_called_once()
        saved_manifest = save.call_args.args[0]
        assert saved_manifest.sources == sources
        save.assert_called_once()


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
        assert "created" in data["meta"]
        assert "updated" in data["meta"]

        # Check system
        assert "name" in data["system"]

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
