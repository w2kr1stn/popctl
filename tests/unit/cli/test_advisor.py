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


# =============================================================================
# Tests for advisor apply command
# =============================================================================


@pytest.fixture
def sample_manifest(tmp_path: Path) -> Path:
    """Create a sample manifest file for testing."""
    from datetime import UTC, datetime
    from typing import Any

    import tomli_w

    manifest_data: dict[str, Any] = {
        "meta": {
            "version": "1.0",
            "created": datetime.now(UTC).isoformat(),
            "updated": datetime.now(UTC).isoformat(),
        },
        "system": {
            "name": "test-machine",
            "base": "pop-os-24.04",
        },
        "packages": {
            "keep": {},
            "remove": {},
        },
    }
    manifest_path = tmp_path / "manifest.toml"
    with manifest_path.open("wb") as f:
        tomli_w.dump(manifest_data, f)
    return manifest_path


@pytest.fixture
def sample_decisions_toml(tmp_path: Path) -> Path:
    """Create a sample decisions.toml file for testing."""
    decisions_content = """\
[packages.apt.keep]
[[packages.apt.keep]]
name = "firefox"
reason = "Essential web browser"
confidence = 0.95
category = "browser"

[[packages.apt.keep]]
name = "vim"
reason = "Essential editor"
confidence = 0.90
category = "development"

[packages.apt.remove]
[[packages.apt.remove]]
name = "bloatware"
reason = "Unused application"
confidence = 0.85
category = "other"

[packages.apt.ask]
[[packages.apt.ask]]
name = "unknown-tool"
reason = "Unclear if needed"
confidence = 0.45
category = "other"

[packages.flatpak.keep]
[[packages.flatpak.keep]]
name = "com.spotify.Client"
reason = "Music streaming app"
confidence = 0.88
category = "media"
"""
    decisions_path = tmp_path / "decisions.toml"
    decisions_path.write_text(decisions_content)
    return decisions_path


class TestAdvisorApplyHelp:
    """Tests for advisor apply command help."""

    def test_apply_help(self) -> None:
        """Apply command shows help."""
        result = runner.invoke(app, ["advisor", "apply", "--help"])
        assert result.exit_code == 0
        assert "Apply" in result.stdout
        assert "--dry-run" in result.stdout
        assert "--input" in result.stdout

    def test_apply_help_shows_examples(self) -> None:
        """Apply help shows usage examples."""
        result = runner.invoke(app, ["advisor", "apply", "--help"])
        assert "popctl advisor apply" in result.stdout


class TestAdvisorApplyWithValidDecisions:
    """Tests for advisor apply with valid decisions.toml."""

    def test_apply_with_valid_decisions(
        self,
        tmp_path: Path,
        sample_manifest: Path,
        sample_decisions_toml: Path,
    ) -> None:
        """Apply updates manifest with decisions."""
        from popctl.advisor import DecisionsResult, PackageDecision, SourceDecisions

        mock_decisions = DecisionsResult(
            packages={
                "apt": SourceDecisions(
                    keep=[
                        PackageDecision(
                            name="firefox",
                            reason="Essential desktop application",
                            confidence=0.95,
                            category="desktop",
                        ),
                    ],
                    remove=[
                        PackageDecision(
                            name="bloatware",
                            reason="Unused",
                            confidence=0.85,
                            category="other",
                        ),
                    ],
                    ask=[],
                ),
                "flatpak": SourceDecisions(keep=[], remove=[], ask=[]),
            }
        )

        from datetime import UTC, datetime

        from popctl.models.manifest import (
            Manifest,
            ManifestMeta,
            PackageConfig,
            SystemConfig,
        )

        mock_manifest = Manifest(
            meta=ManifestMeta(
                version="1.0",
                created=datetime.now(UTC),
                updated=datetime.now(UTC),
            ),
            system=SystemConfig(name="test", base="pop-os-24.04"),
            packages=PackageConfig(keep={}, remove={}),
        )

        with (
            patch(
                "popctl.advisor.import_decisions",
                return_value=mock_decisions,
            ),
            patch(
                "popctl.core.paths.get_exchange_dir",
                return_value=tmp_path,
            ),
            patch(
                "popctl.core.manifest.load_manifest",
                return_value=mock_manifest,
            ),
            patch(
                "popctl.core.manifest.save_manifest",
            ) as mock_save,
            patch(
                "popctl.core.paths.get_manifest_path",
                return_value=sample_manifest,
            ),
        ):
            result = runner.invoke(app, ["advisor", "apply"])

        assert result.exit_code == 0
        assert "updated" in result.stdout.lower() or "summary" in result.stdout.lower()
        mock_save.assert_called_once()


class TestAdvisorApplyDryRun:
    """Tests for advisor apply --dry-run option."""

    def test_apply_dry_run_does_not_modify_manifest(
        self,
        tmp_path: Path,
        sample_manifest: Path,
    ) -> None:
        """Apply --dry-run shows changes without modifying manifest."""
        from popctl.advisor import DecisionsResult, PackageDecision, SourceDecisions

        mock_decisions = DecisionsResult(
            packages={
                "apt": SourceDecisions(
                    keep=[
                        PackageDecision(
                            name="firefox",
                            reason="Desktop application",
                            confidence=0.95,
                            category="desktop",
                        ),
                    ],
                    remove=[],
                    ask=[],
                ),
                "flatpak": SourceDecisions(keep=[], remove=[], ask=[]),
            }
        )

        from datetime import UTC, datetime

        from popctl.models.manifest import (
            Manifest,
            ManifestMeta,
            PackageConfig,
            SystemConfig,
        )

        mock_manifest = Manifest(
            meta=ManifestMeta(
                version="1.0",
                created=datetime.now(UTC),
                updated=datetime.now(UTC),
            ),
            system=SystemConfig(name="test", base="pop-os-24.04"),
            packages=PackageConfig(keep={}, remove={}),
        )

        with (
            patch(
                "popctl.advisor.import_decisions",
                return_value=mock_decisions,
            ),
            patch(
                "popctl.core.paths.get_exchange_dir",
                return_value=tmp_path,
            ),
            patch(
                "popctl.core.manifest.load_manifest",
                return_value=mock_manifest,
            ),
            patch(
                "popctl.core.manifest.save_manifest",
            ) as mock_save,
            patch(
                "popctl.core.paths.get_manifest_path",
                return_value=sample_manifest,
            ),
        ):
            result = runner.invoke(app, ["advisor", "apply", "--dry-run"])

        assert result.exit_code == 0
        # dry-run mode shows "Would update" instead of "updated"
        assert "would update" in result.stdout.lower()
        # save_manifest should NOT be called in dry-run mode
        mock_save.assert_not_called()


class TestAdvisorApplyErrors:
    """Tests for advisor apply error handling."""

    def test_apply_without_decisions_toml(self, tmp_path: Path) -> None:
        """Apply fails when decisions.toml is not found."""
        with (
            patch(
                "popctl.core.paths.get_exchange_dir",
                return_value=tmp_path,
            ),
            patch(
                "popctl.advisor.import_decisions",
                side_effect=FileNotFoundError("decisions.toml not found"),
            ),
        ):
            result = runner.invoke(app, ["advisor", "apply"])

        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "not found" in combined.lower() or "decisions.toml" in combined.lower()

    def test_apply_without_manifest(self, tmp_path: Path) -> None:
        """Apply fails when manifest is not found."""
        from popctl.advisor import DecisionsResult, SourceDecisions
        from popctl.core.manifest import ManifestNotFoundError

        mock_decisions = DecisionsResult(
            packages={
                "apt": SourceDecisions(keep=[], remove=[], ask=[]),
                "flatpak": SourceDecisions(keep=[], remove=[], ask=[]),
            }
        )

        with (
            patch(
                "popctl.core.paths.get_exchange_dir",
                return_value=tmp_path,
            ),
            patch(
                "popctl.advisor.import_decisions",
                return_value=mock_decisions,
            ),
            patch(
                "popctl.core.manifest.load_manifest",
                side_effect=ManifestNotFoundError("Manifest not found"),
            ),
        ):
            result = runner.invoke(app, ["advisor", "apply"])

        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "manifest" in combined.lower() and (
            "not found" in combined.lower() or "init" in combined.lower()
        )

    def test_apply_with_invalid_decisions_toml(self, tmp_path: Path) -> None:
        """Apply fails when decisions.toml is invalid."""
        with (
            patch(
                "popctl.core.paths.get_exchange_dir",
                return_value=tmp_path,
            ),
            patch(
                "popctl.advisor.import_decisions",
                side_effect=ValueError("Invalid TOML syntax"),
            ),
        ):
            result = runner.invoke(app, ["advisor", "apply"])

        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "invalid" in combined.lower()


class TestAdvisorApplyWithInputFile:
    """Tests for advisor apply with custom input file."""

    def test_apply_with_custom_input_path(
        self,
        tmp_path: Path,
        sample_manifest: Path,
    ) -> None:
        """Apply --input uses specified decisions file."""
        from popctl.advisor import DecisionsResult, SourceDecisions

        custom_decisions_path = tmp_path / "custom" / "decisions.toml"
        custom_decisions_path.parent.mkdir(parents=True)
        custom_decisions_path.touch()

        mock_decisions = DecisionsResult(
            packages={
                "apt": SourceDecisions(keep=[], remove=[], ask=[]),
                "flatpak": SourceDecisions(keep=[], remove=[], ask=[]),
            }
        )

        from datetime import UTC, datetime

        from popctl.models.manifest import (
            Manifest,
            ManifestMeta,
            PackageConfig,
            SystemConfig,
        )

        mock_manifest = Manifest(
            meta=ManifestMeta(
                version="1.0",
                created=datetime.now(UTC),
                updated=datetime.now(UTC),
            ),
            system=SystemConfig(name="test", base="pop-os-24.04"),
            packages=PackageConfig(keep={}, remove={}),
        )

        with (
            patch(
                "popctl.advisor.import_decisions",
                return_value=mock_decisions,
            ),
            patch(
                "popctl.core.manifest.load_manifest",
                return_value=mock_manifest,
            ),
            patch(
                "popctl.core.manifest.save_manifest",
            ),
            patch(
                "popctl.core.paths.get_manifest_path",
                return_value=sample_manifest,
            ),
        ):
            result = runner.invoke(app, ["advisor", "apply", "--input", str(custom_decisions_path)])

        assert result.exit_code == 0


class TestAdvisorApplyAskPackages:
    """Tests for advisor apply handling of 'ask' packages."""

    def test_apply_shows_ask_packages(
        self,
        tmp_path: Path,
        sample_manifest: Path,
    ) -> None:
        """Apply displays packages that need manual decision."""
        from popctl.advisor import DecisionsResult, PackageDecision, SourceDecisions

        mock_decisions = DecisionsResult(
            packages={
                "apt": SourceDecisions(
                    keep=[],
                    remove=[],
                    ask=[
                        PackageDecision(
                            name="unknown-tool",
                            reason="Unclear if needed",
                            confidence=0.45,
                            category="other",
                        ),
                        PackageDecision(
                            name="maybe-useful",
                            reason="Optional dependency",
                            confidence=0.55,
                            category="development",
                        ),
                    ],
                ),
                "flatpak": SourceDecisions(keep=[], remove=[], ask=[]),
            }
        )

        from datetime import UTC, datetime

        from popctl.models.manifest import (
            Manifest,
            ManifestMeta,
            PackageConfig,
            SystemConfig,
        )

        mock_manifest = Manifest(
            meta=ManifestMeta(
                version="1.0",
                created=datetime.now(UTC),
                updated=datetime.now(UTC),
            ),
            system=SystemConfig(name="test", base="pop-os-24.04"),
            packages=PackageConfig(keep={}, remove={}),
        )

        with (
            patch(
                "popctl.advisor.import_decisions",
                return_value=mock_decisions,
            ),
            patch(
                "popctl.core.paths.get_exchange_dir",
                return_value=tmp_path,
            ),
            patch(
                "popctl.core.manifest.load_manifest",
                return_value=mock_manifest,
            ),
            patch(
                "popctl.core.manifest.save_manifest",
            ),
            patch(
                "popctl.core.paths.get_manifest_path",
                return_value=sample_manifest,
            ),
        ):
            result = runner.invoke(app, ["advisor", "apply", "--dry-run"])

        assert result.exit_code == 0
        # Should show packages requiring manual decision
        assert "manual" in result.stdout.lower() or "ask" in result.stdout.lower()


class TestAdvisorApplyHistory:
    """Tests for advisor apply history tracking."""

    def test_apply_records_history_on_success(
        self,
        tmp_path: Path,
        sample_manifest: Path,
    ) -> None:
        """Advisor apply records classifications to history."""
        from popctl.advisor import DecisionsResult, PackageDecision, SourceDecisions

        mock_decisions = DecisionsResult(
            packages={
                "apt": SourceDecisions(
                    keep=[
                        PackageDecision(
                            name="firefox",
                            reason="Desktop application",
                            confidence=0.95,
                            category="desktop",
                        ),
                    ],
                    remove=[
                        PackageDecision(
                            name="bloatware",
                            reason="Unused",
                            confidence=0.85,
                            category="other",
                        ),
                    ],
                    ask=[],
                ),
                "flatpak": SourceDecisions(keep=[], remove=[], ask=[]),
            }
        )

        from datetime import UTC, datetime

        from popctl.models.manifest import (
            Manifest,
            ManifestMeta,
            PackageConfig,
            SystemConfig,
        )

        mock_manifest = Manifest(
            meta=ManifestMeta(
                version="1.0",
                created=datetime.now(UTC),
                updated=datetime.now(UTC),
            ),
            system=SystemConfig(name="test", base="pop-os-24.04"),
            packages=PackageConfig(keep={}, remove={}),
        )

        with (
            patch(
                "popctl.advisor.import_decisions",
                return_value=mock_decisions,
            ),
            patch(
                "popctl.core.paths.get_exchange_dir",
                return_value=tmp_path,
            ),
            patch(
                "popctl.core.manifest.load_manifest",
                return_value=mock_manifest,
            ),
            patch(
                "popctl.core.manifest.save_manifest",
            ),
            patch(
                "popctl.core.paths.get_manifest_path",
                return_value=sample_manifest,
            ),
            patch("popctl.cli.commands.advisor._record_advisor_apply_to_history") as mock_record,
        ):
            result = runner.invoke(app, ["advisor", "apply"])

        assert result.exit_code == 0
        # _record_advisor_apply_to_history should have been called
        mock_record.assert_called_once()
        # History message should appear in output
        assert "history" in result.stdout.lower()

    def test_apply_does_not_record_history_on_dry_run(
        self,
        tmp_path: Path,
        sample_manifest: Path,
    ) -> None:
        """Advisor apply --dry-run does NOT record history."""
        from popctl.advisor import DecisionsResult, PackageDecision, SourceDecisions

        mock_decisions = DecisionsResult(
            packages={
                "apt": SourceDecisions(
                    keep=[
                        PackageDecision(
                            name="firefox",
                            reason="Desktop application",
                            confidence=0.95,
                            category="desktop",
                        ),
                    ],
                    remove=[],
                    ask=[],
                ),
                "flatpak": SourceDecisions(keep=[], remove=[], ask=[]),
            }
        )

        from datetime import UTC, datetime

        from popctl.models.manifest import (
            Manifest,
            ManifestMeta,
            PackageConfig,
            SystemConfig,
        )

        mock_manifest = Manifest(
            meta=ManifestMeta(
                version="1.0",
                created=datetime.now(UTC),
                updated=datetime.now(UTC),
            ),
            system=SystemConfig(name="test", base="pop-os-24.04"),
            packages=PackageConfig(keep={}, remove={}),
        )

        with (
            patch(
                "popctl.advisor.import_decisions",
                return_value=mock_decisions,
            ),
            patch(
                "popctl.core.paths.get_exchange_dir",
                return_value=tmp_path,
            ),
            patch(
                "popctl.core.manifest.load_manifest",
                return_value=mock_manifest,
            ),
            patch(
                "popctl.core.manifest.save_manifest",
            ) as mock_save,
            patch(
                "popctl.core.paths.get_manifest_path",
                return_value=sample_manifest,
            ),
            patch("popctl.cli.commands.advisor._record_advisor_apply_to_history") as mock_record,
        ):
            result = runner.invoke(app, ["advisor", "apply", "--dry-run"])

        assert result.exit_code == 0
        # _record_advisor_apply_to_history should NOT be called in dry-run mode
        mock_record.assert_not_called()
        # save_manifest should also NOT be called
        mock_save.assert_not_called()
        # dry-run message should appear
        assert "would update" in result.stdout.lower()
