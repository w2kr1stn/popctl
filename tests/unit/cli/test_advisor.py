"""Unit tests for advisor command.

Tests for the CLI advisor command implementation.
"""

from pathlib import Path
from unittest.mock import patch

import pytest
from popctl.advisor import AdvisorConfig, AgentResult
from popctl.cli.main import app
from popctl.models.package import PackageSource, PackageStatus, ScannedPackage
from popctl.models.scan_result import ScanResult
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def sample_packages() -> list[ScannedPackage]:
    """Create sample packages for testing."""
    return [
        ScannedPackage(
            name="firefox",
            source=PackageSource.APT,
            version="120.0",
            status=PackageStatus.MANUAL,
            description="Web browser",
        ),
        ScannedPackage(
            name="vim",
            source=PackageSource.APT,
            version="9.0",
            status=PackageStatus.MANUAL,
            description="Text editor",
        ),
    ]


@pytest.fixture
def sample_scan_result(sample_packages: list[ScannedPackage]) -> ScanResult:
    """Create a sample scan result for testing."""
    return ScanResult.create(sample_packages, ["apt"])


@pytest.fixture
def mock_config() -> AdvisorConfig:
    """Create a mock advisor config."""
    return AdvisorConfig(provider="claude", model="sonnet")


class TestAdvisorCommandHelp:
    """Tests for advisor command help."""

    def test_advisor_help(self) -> None:
        """Advisor command shows help."""
        result = runner.invoke(app, ["advisor", "--help"])
        assert result.exit_code == 0
        assert "AI-assisted" in result.stdout

    def test_advisor_classify_help(self) -> None:
        """Advisor classify subcommand shows help."""
        result = runner.invoke(app, ["advisor", "classify", "--help"])
        assert result.exit_code == 0
        assert "Classify packages" in result.stdout

    def test_advisor_classify_help_shows_options(self) -> None:
        """Advisor classify help shows all available options."""
        result = runner.invoke(app, ["advisor", "classify", "--help"])
        assert "--auto" in result.stdout
        assert "--provider" in result.stdout
        assert "--model" in result.stdout
        assert "--input" in result.stdout


class TestAdvisorClassifyInteractive:
    """Tests for advisor classify interactive mode (default)."""

    def test_classify_interactive_mode(
        self,
        sample_scan_result: ScanResult,
        mock_config: AdvisorConfig,
        tmp_path: Path,
    ) -> None:
        """Classify in interactive mode prepares files and shows instructions."""
        with (
            patch("popctl.cli.commands.advisor.is_running_in_container", return_value=False),
            patch("popctl.cli.commands.advisor.load_advisor_config", return_value=mock_config),
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch(
                "popctl.cli.commands.advisor.ensure_exchange_dir",
                return_value=tmp_path / "exchange",
            ),
            patch("popctl.cli.commands.advisor._scan_system", return_value=sample_scan_result),
            patch(
                "popctl.cli.commands.advisor.export_scan_for_advisor",
                return_value=tmp_path / "exchange" / "scan.json",
            ),
            patch(
                "popctl.cli.commands.advisor.export_prompt_files",
                return_value=(tmp_path / "exchange" / "prompt.txt", None),
            ),
            patch.object(
                __import__("popctl.advisor.runner", fromlist=["AgentRunner"]).AgentRunner,
                "prepare_interactive",
                return_value="Test instructions for user",
            ),
        ):
            result = runner.invoke(app, ["advisor", "classify"])

        assert result.exit_code == 0
        # Should show interactive mode output
        assert "Interactive" in result.stdout or "instructions" in result.stdout.lower()

    def test_classify_shows_container_warning(
        self,
        sample_scan_result: ScanResult,
        mock_config: AdvisorConfig,
        tmp_path: Path,
    ) -> None:
        """Classify shows warning when running in container."""
        with (
            patch("popctl.cli.commands.advisor.is_running_in_container", return_value=True),
            patch("popctl.cli.commands.advisor.load_advisor_config", return_value=mock_config),
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch(
                "popctl.cli.commands.advisor.ensure_exchange_dir",
                return_value=tmp_path / "exchange",
            ),
            patch("popctl.cli.commands.advisor._scan_system", return_value=sample_scan_result),
            patch(
                "popctl.cli.commands.advisor.export_scan_for_advisor",
                return_value=tmp_path / "exchange" / "scan.json",
            ),
            patch(
                "popctl.cli.commands.advisor.export_prompt_files",
                return_value=(tmp_path / "exchange" / "prompt.txt", None),
            ),
            patch.object(
                __import__("popctl.advisor.runner", fromlist=["AgentRunner"]).AgentRunner,
                "prepare_interactive",
                return_value="Test instructions",
            ),
        ):
            result = runner.invoke(app, ["advisor", "classify"])

        # Should show container warning in stderr
        assert "container" in (result.stdout + (result.stderr or "")).lower()


class TestAdvisorClassifyHeadless:
    """Tests for advisor classify headless mode (--auto)."""

    def test_classify_headless_success(
        self,
        sample_scan_result: ScanResult,
        mock_config: AdvisorConfig,
        tmp_path: Path,
    ) -> None:
        """Classify --auto runs agent and reports success."""
        exchange_dir = tmp_path / "exchange"
        exchange_dir.mkdir(parents=True)

        successful_result = AgentResult(
            success=True,
            output="Classification complete",
            decisions_path=exchange_dir / "decisions.toml",
        )

        with (
            patch("popctl.cli.commands.advisor.is_running_in_container", return_value=False),
            patch("popctl.cli.commands.advisor.load_advisor_config", return_value=mock_config),
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch(
                "popctl.cli.commands.advisor.ensure_exchange_dir",
                return_value=exchange_dir,
            ),
            patch("popctl.cli.commands.advisor._scan_system", return_value=sample_scan_result),
            patch(
                "popctl.cli.commands.advisor.export_scan_for_advisor",
                return_value=exchange_dir / "scan.json",
            ),
            patch(
                "popctl.cli.commands.advisor.export_prompt_files",
                return_value=(exchange_dir / "prompt.txt", None),
            ),
            patch.object(
                __import__("popctl.advisor.runner", fromlist=["AgentRunner"]).AgentRunner,
                "run_headless",
                return_value=successful_result,
            ),
        ):
            result = runner.invoke(app, ["advisor", "classify", "--auto"])

        assert result.exit_code == 0
        assert "successfully" in result.stdout.lower() or "success" in result.stdout.lower()

    def test_classify_headless_failure(
        self,
        sample_scan_result: ScanResult,
        mock_config: AdvisorConfig,
        tmp_path: Path,
    ) -> None:
        """Classify --auto reports failure when agent fails."""
        exchange_dir = tmp_path / "exchange"
        exchange_dir.mkdir(parents=True)

        failed_result = AgentResult(
            success=False,
            output="",
            error="Agent timed out",
        )

        with (
            patch("popctl.cli.commands.advisor.is_running_in_container", return_value=False),
            patch("popctl.cli.commands.advisor.load_advisor_config", return_value=mock_config),
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch(
                "popctl.cli.commands.advisor.ensure_exchange_dir",
                return_value=exchange_dir,
            ),
            patch("popctl.cli.commands.advisor._scan_system", return_value=sample_scan_result),
            patch(
                "popctl.cli.commands.advisor.export_scan_for_advisor",
                return_value=exchange_dir / "scan.json",
            ),
            patch(
                "popctl.cli.commands.advisor.export_prompt_files",
                return_value=(exchange_dir / "prompt.txt", None),
            ),
            patch.object(
                __import__("popctl.advisor.runner", fromlist=["AgentRunner"]).AgentRunner,
                "run_headless",
                return_value=failed_result,
            ),
        ):
            result = runner.invoke(app, ["advisor", "classify", "--auto"])

        assert result.exit_code == 1
        # Error message goes to stderr via print_error
        combined_output = result.stdout + (result.stderr or "")
        assert "failed" in combined_output.lower() or "error" in combined_output.lower()


class TestAdvisorClassifyOptions:
    """Tests for advisor classify command options."""

    def test_classify_with_provider_option(
        self,
        sample_scan_result: ScanResult,
        tmp_path: Path,
    ) -> None:
        """Classify --provider overrides config provider."""
        exchange_dir = tmp_path / "exchange"

        with (
            patch("popctl.cli.commands.advisor.is_running_in_container", return_value=False),
            patch(
                "popctl.cli.commands.advisor.load_advisor_config",
                return_value=AdvisorConfig(provider="claude"),
            ),
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch(
                "popctl.cli.commands.advisor.ensure_exchange_dir",
                return_value=exchange_dir,
            ),
            patch("popctl.cli.commands.advisor._scan_system", return_value=sample_scan_result),
            patch(
                "popctl.cli.commands.advisor.export_scan_for_advisor",
                return_value=exchange_dir / "scan.json",
            ),
            patch(
                "popctl.cli.commands.advisor.export_prompt_files",
                return_value=(exchange_dir / "prompt.txt", None),
            ),
            patch.object(
                __import__("popctl.advisor.runner", fromlist=["AgentRunner"]).AgentRunner,
                "prepare_interactive",
                return_value="Instructions",
            ),
        ):
            result = runner.invoke(app, ["advisor", "classify", "--provider", "gemini"])

        assert result.exit_code == 0
        # Should mention gemini in output
        assert "gemini" in result.stdout.lower()

    def test_classify_with_model_option(
        self,
        sample_scan_result: ScanResult,
        tmp_path: Path,
    ) -> None:
        """Classify --model overrides config model."""
        exchange_dir = tmp_path / "exchange"

        with (
            patch("popctl.cli.commands.advisor.is_running_in_container", return_value=False),
            patch(
                "popctl.cli.commands.advisor.load_advisor_config",
                return_value=AdvisorConfig(provider="claude"),
            ),
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch(
                "popctl.cli.commands.advisor.ensure_exchange_dir",
                return_value=exchange_dir,
            ),
            patch("popctl.cli.commands.advisor._scan_system", return_value=sample_scan_result),
            patch(
                "popctl.cli.commands.advisor.export_scan_for_advisor",
                return_value=exchange_dir / "scan.json",
            ),
            patch(
                "popctl.cli.commands.advisor.export_prompt_files",
                return_value=(exchange_dir / "prompt.txt", None),
            ),
            patch.object(
                __import__("popctl.advisor.runner", fromlist=["AgentRunner"]).AgentRunner,
                "prepare_interactive",
                return_value="Instructions",
            ),
        ):
            result = runner.invoke(app, ["advisor", "classify", "--model", "opus"])

        assert result.exit_code == 0
        # Should mention opus in output
        assert "opus" in result.stdout.lower()

    def test_classify_with_input_file(
        self,
        tmp_path: Path,
    ) -> None:
        """Classify --input uses existing scan file."""
        exchange_dir = tmp_path / "exchange"
        exchange_dir.mkdir(parents=True)

        # Create a valid scan.json file
        import json

        scan_file = tmp_path / "scan.json"
        scan_data = {
            "metadata": {"timestamp": "2024-01-01T00:00:00Z"},
            "packages": [
                {
                    "name": "test-pkg",
                    "source": "apt",
                    "version": "1.0",
                    "status": "manual",
                }
            ],
            "summary": {"total": 1},
        }
        scan_file.write_text(json.dumps(scan_data))

        with (
            patch("popctl.cli.commands.advisor.is_running_in_container", return_value=False),
            patch(
                "popctl.cli.commands.advisor.load_advisor_config",
                return_value=AdvisorConfig(provider="claude"),
            ),
            patch(
                "popctl.cli.commands.advisor.ensure_exchange_dir",
                return_value=exchange_dir,
            ),
            patch(
                "popctl.cli.commands.advisor.export_scan_for_advisor",
                return_value=exchange_dir / "scan.json",
            ),
            patch(
                "popctl.cli.commands.advisor.export_prompt_files",
                return_value=(exchange_dir / "prompt.txt", None),
            ),
            patch.object(
                __import__("popctl.advisor.runner", fromlist=["AgentRunner"]).AgentRunner,
                "prepare_interactive",
                return_value="Instructions",
            ),
        ):
            result = runner.invoke(app, ["advisor", "classify", "--input", str(scan_file)])

        assert result.exit_code == 0

    def test_classify_with_nonexistent_input_file(self, tmp_path: Path) -> None:
        """Classify --input with nonexistent file shows error."""
        nonexistent = tmp_path / "nonexistent.json"

        with (
            patch("popctl.cli.commands.advisor.is_running_in_container", return_value=False),
            patch(
                "popctl.cli.commands.advisor.load_advisor_config",
                return_value=AdvisorConfig(provider="claude"),
            ),
        ):
            result = runner.invoke(app, ["advisor", "classify", "--input", str(nonexistent)])

        assert result.exit_code == 1
        assert "not found" in (result.stdout + (result.stderr or "")).lower()


class TestAdvisorConfigHandling:
    """Tests for advisor config loading and creation."""

    def test_classify_creates_default_config_if_missing(
        self,
        sample_scan_result: ScanResult,
        tmp_path: Path,
    ) -> None:
        """Classify creates default config if none exists."""
        from popctl.advisor.config import AdvisorConfigNotFoundError

        exchange_dir = tmp_path / "exchange"

        with (
            patch("popctl.cli.commands.advisor.is_running_in_container", return_value=False),
            patch(
                "popctl.cli.commands.advisor.load_advisor_config",
                side_effect=AdvisorConfigNotFoundError("Config not found"),
            ),
            patch(
                "popctl.cli.commands.advisor.save_advisor_config",
                return_value=tmp_path / "advisor.toml",
            ),
            patch(
                "popctl.cli.commands.advisor.get_default_config",
                return_value=AdvisorConfig(provider="claude"),
            ),
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch(
                "popctl.cli.commands.advisor.ensure_exchange_dir",
                return_value=exchange_dir,
            ),
            patch("popctl.cli.commands.advisor._scan_system", return_value=sample_scan_result),
            patch(
                "popctl.cli.commands.advisor.export_scan_for_advisor",
                return_value=exchange_dir / "scan.json",
            ),
            patch(
                "popctl.cli.commands.advisor.export_prompt_files",
                return_value=(exchange_dir / "prompt.txt", None),
            ),
            patch.object(
                __import__("popctl.advisor.runner", fromlist=["AgentRunner"]).AgentRunner,
                "prepare_interactive",
                return_value="Instructions",
            ),
        ):
            result = runner.invoke(app, ["advisor", "classify"])

        assert result.exit_code == 0
        # Should mention creating default config
        assert "default" in result.stdout.lower()


class TestAdvisorScannerAvailability:
    """Tests for scanner availability handling in advisor."""

    def test_classify_no_scanners_available(self) -> None:
        """Classify fails when no scanners are available."""
        with (
            patch("popctl.cli.commands.advisor.is_running_in_container", return_value=False),
            patch(
                "popctl.cli.commands.advisor.load_advisor_config",
                return_value=AdvisorConfig(provider="claude"),
            ),
            patch("popctl.scanners.apt.command_exists", return_value=False),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
        ):
            result = runner.invoke(app, ["advisor", "classify"])

        assert result.exit_code == 1
        assert "not available" in (result.stdout + (result.stderr or "")).lower()
