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
        assert "Classify packages" in result.stdout or "headless" in result.stdout.lower()

    def test_advisor_classify_help_shows_options(self) -> None:
        """Advisor classify help shows all available options."""
        result = runner.invoke(app, ["advisor", "classify", "--help"])
        assert "--provider" in result.stdout
        assert "--model" in result.stdout
        assert "--input" in result.stdout

    def test_advisor_session_help(self) -> None:
        """Advisor session subcommand shows help."""
        result = runner.invoke(app, ["advisor", "session", "--help"])
        assert result.exit_code == 0
        assert "--host" in result.stdout
        assert "--provider" in result.stdout


class TestAdvisorClassify:
    """Tests for advisor classify command (always headless)."""

    def test_classify_headless_success(
        self,
        sample_scan_result: ScanResult,
        mock_config: AdvisorConfig,
        tmp_path: Path,
    ) -> None:
        """Classify runs agent headless and reports success."""
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir(parents=True)

        successful_result = AgentResult(
            success=True,
            output="Classification complete",
            decisions_path=workspace_dir / "output" / "decisions.toml",
            workspace_path=workspace_dir,
        )

        with (
            patch("popctl.cli.commands.advisor.is_running_in_container", return_value=False),
            patch("popctl.cli.commands.advisor.load_advisor_config", return_value=mock_config),
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch("popctl.cli.commands.advisor._scan_system", return_value=sample_scan_result),
            patch(
                "popctl.cli.commands.advisor.ensure_advisor_sessions_dir",
                return_value=tmp_path / "sessions",
            ),
            patch(
                "popctl.cli.commands.advisor.get_manifest_path",
                return_value=tmp_path / "nonexistent" / "manifest.toml",
            ),
            patch(
                "popctl.cli.commands.advisor.create_session_workspace",
                return_value=workspace_dir,
            ),
            patch.object(
                __import__("popctl.advisor.runner", fromlist=["AgentRunner"]).AgentRunner,
                "run_headless",
                return_value=successful_result,
            ),
        ):
            result = runner.invoke(app, ["advisor", "classify"])

        assert result.exit_code == 0
        assert "successfully" in result.stdout.lower() or "success" in result.stdout.lower()

    def test_classify_headless_failure(
        self,
        sample_scan_result: ScanResult,
        mock_config: AdvisorConfig,
        tmp_path: Path,
    ) -> None:
        """Classify reports failure when agent fails."""
        workspace_dir = tmp_path / "workspace"

        failed_result = AgentResult(
            success=False,
            output="",
            error="Agent timed out",
            workspace_path=workspace_dir,
        )

        with (
            patch("popctl.cli.commands.advisor.is_running_in_container", return_value=False),
            patch("popctl.cli.commands.advisor.load_advisor_config", return_value=mock_config),
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch("popctl.cli.commands.advisor._scan_system", return_value=sample_scan_result),
            patch(
                "popctl.cli.commands.advisor.ensure_advisor_sessions_dir",
                return_value=tmp_path / "sessions",
            ),
            patch(
                "popctl.cli.commands.advisor.get_manifest_path",
                return_value=tmp_path / "nonexistent" / "manifest.toml",
            ),
            patch(
                "popctl.cli.commands.advisor.create_session_workspace",
                return_value=workspace_dir,
            ),
            patch.object(
                __import__("popctl.advisor.runner", fromlist=["AgentRunner"]).AgentRunner,
                "run_headless",
                return_value=failed_result,
            ),
        ):
            result = runner.invoke(app, ["advisor", "classify"])

        assert result.exit_code == 1
        combined_output = result.stdout + (result.stderr or "")
        assert "failed" in combined_output.lower() or "error" in combined_output.lower()

    def test_classify_shows_container_warning(
        self,
        sample_scan_result: ScanResult,
        mock_config: AdvisorConfig,
        tmp_path: Path,
    ) -> None:
        """Classify shows warning when running in container."""
        workspace_dir = tmp_path / "workspace"

        successful_result = AgentResult(
            success=True,
            output="",
            decisions_path=workspace_dir / "output" / "decisions.toml",
            workspace_path=workspace_dir,
        )

        with (
            patch("popctl.cli.commands.advisor.is_running_in_container", return_value=True),
            patch("popctl.cli.commands.advisor.load_advisor_config", return_value=mock_config),
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch("popctl.cli.commands.advisor._scan_system", return_value=sample_scan_result),
            patch(
                "popctl.cli.commands.advisor.ensure_advisor_sessions_dir",
                return_value=tmp_path / "sessions",
            ),
            patch(
                "popctl.cli.commands.advisor.get_manifest_path",
                return_value=tmp_path / "nonexistent" / "manifest.toml",
            ),
            patch(
                "popctl.cli.commands.advisor.create_session_workspace",
                return_value=workspace_dir,
            ),
            patch.object(
                __import__("popctl.advisor.runner", fromlist=["AgentRunner"]).AgentRunner,
                "run_headless",
                return_value=successful_result,
            ),
        ):
            result = runner.invoke(app, ["advisor", "classify"])

        assert "container" in (result.stdout + (result.stderr or "")).lower()


class TestAdvisorClassifyOptions:
    """Tests for advisor classify command options."""

    def test_classify_with_provider_option(
        self,
        sample_scan_result: ScanResult,
        tmp_path: Path,
    ) -> None:
        """Classify --provider overrides config provider."""
        workspace_dir = tmp_path / "workspace"

        successful_result = AgentResult(
            success=True,
            output="",
            decisions_path=workspace_dir / "output" / "decisions.toml",
            workspace_path=workspace_dir,
        )

        with (
            patch("popctl.cli.commands.advisor.is_running_in_container", return_value=False),
            patch(
                "popctl.cli.commands.advisor.load_advisor_config",
                return_value=AdvisorConfig(provider="claude"),
            ),
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch("popctl.cli.commands.advisor._scan_system", return_value=sample_scan_result),
            patch(
                "popctl.cli.commands.advisor.ensure_advisor_sessions_dir",
                return_value=tmp_path / "sessions",
            ),
            patch(
                "popctl.cli.commands.advisor.get_manifest_path",
                return_value=tmp_path / "nonexistent" / "manifest.toml",
            ),
            patch(
                "popctl.cli.commands.advisor.create_session_workspace",
                return_value=workspace_dir,
            ),
            patch.object(
                __import__("popctl.advisor.runner", fromlist=["AgentRunner"]).AgentRunner,
                "run_headless",
                return_value=successful_result,
            ),
        ):
            result = runner.invoke(app, ["advisor", "classify", "--provider", "gemini"])

        assert result.exit_code == 0
        assert "gemini" in result.stdout.lower()

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


class TestAdvisorSession:
    """Tests for advisor session command."""

    def test_session_manual_mode(
        self,
        sample_scan_result: ScanResult,
        mock_config: AdvisorConfig,
        tmp_path: Path,
    ) -> None:
        """Session returns manual instructions when launch fails."""
        workspace_dir = tmp_path / "workspace"

        manual_result = AgentResult(
            success=False,
            output=(
                f"Workspace prepared: {workspace_dir}\n\nTo start manually:\n  cd {workspace_dir}\n"
            ),
            error="manual_mode",
            workspace_path=workspace_dir,
        )

        with (
            patch("popctl.cli.commands.advisor.is_running_in_container", return_value=False),
            patch("popctl.cli.commands.advisor.load_advisor_config", return_value=mock_config),
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch("popctl.cli.commands.advisor._scan_system", return_value=sample_scan_result),
            patch(
                "popctl.cli.commands.advisor.ensure_advisor_sessions_dir",
                return_value=tmp_path / "sessions",
            ),
            patch(
                "popctl.cli.commands.advisor.get_manifest_path",
                return_value=tmp_path / "nonexistent" / "manifest.toml",
            ),
            patch(
                "popctl.cli.commands.advisor.create_session_workspace",
                return_value=workspace_dir,
            ),
            patch.object(
                __import__("popctl.advisor.runner", fromlist=["AgentRunner"]).AgentRunner,
                "launch_interactive",
                return_value=manual_result,
            ),
        ):
            result = runner.invoke(app, ["advisor", "session"])

        assert result.exit_code == 0
        assert "Workspace prepared" in result.stdout

    def test_session_success(
        self,
        sample_scan_result: ScanResult,
        mock_config: AdvisorConfig,
        tmp_path: Path,
    ) -> None:
        """Session reports success when agent completes."""
        workspace_dir = tmp_path / "workspace"

        successful_result = AgentResult(
            success=True,
            output="",
            decisions_path=workspace_dir / "output" / "decisions.toml",
            workspace_path=workspace_dir,
        )

        with (
            patch("popctl.cli.commands.advisor.is_running_in_container", return_value=False),
            patch("popctl.cli.commands.advisor.load_advisor_config", return_value=mock_config),
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch("popctl.cli.commands.advisor._scan_system", return_value=sample_scan_result),
            patch(
                "popctl.cli.commands.advisor.ensure_advisor_sessions_dir",
                return_value=tmp_path / "sessions",
            ),
            patch(
                "popctl.cli.commands.advisor.get_manifest_path",
                return_value=tmp_path / "nonexistent" / "manifest.toml",
            ),
            patch(
                "popctl.cli.commands.advisor.create_session_workspace",
                return_value=workspace_dir,
            ),
            patch.object(
                __import__("popctl.advisor.runner", fromlist=["AgentRunner"]).AgentRunner,
                "launch_interactive",
                return_value=successful_result,
            ),
        ):
            result = runner.invoke(app, ["advisor", "session"])

        assert result.exit_code == 0
        assert "completed" in result.stdout.lower()

    def test_session_failure(
        self,
        sample_scan_result: ScanResult,
        mock_config: AdvisorConfig,
        tmp_path: Path,
    ) -> None:
        """Session reports failure when agent fails."""
        workspace_dir = tmp_path / "workspace"

        failed_result = AgentResult(
            success=False,
            output="",
            error="Container crashed",
            workspace_path=workspace_dir,
        )

        with (
            patch("popctl.cli.commands.advisor.is_running_in_container", return_value=False),
            patch("popctl.cli.commands.advisor.load_advisor_config", return_value=mock_config),
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch("popctl.cli.commands.advisor._scan_system", return_value=sample_scan_result),
            patch(
                "popctl.cli.commands.advisor.ensure_advisor_sessions_dir",
                return_value=tmp_path / "sessions",
            ),
            patch(
                "popctl.cli.commands.advisor.get_manifest_path",
                return_value=tmp_path / "nonexistent" / "manifest.toml",
            ),
            patch(
                "popctl.cli.commands.advisor.create_session_workspace",
                return_value=workspace_dir,
            ),
            patch.object(
                __import__("popctl.advisor.runner", fromlist=["AgentRunner"]).AgentRunner,
                "launch_interactive",
                return_value=failed_result,
            ),
        ):
            result = runner.invoke(app, ["advisor", "session"])

        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "failed" in combined.lower() or "crashed" in combined.lower()


class TestAdvisorConfigHandling:
    """Tests for advisor config loading and creation."""

    def test_classify_creates_default_config_if_missing(
        self,
        sample_scan_result: ScanResult,
        tmp_path: Path,
    ) -> None:
        """Classify creates default config if none exists."""
        from popctl.advisor.config import AdvisorConfigNotFoundError

        workspace_dir = tmp_path / "workspace"

        successful_result = AgentResult(
            success=True,
            output="",
            decisions_path=workspace_dir / "output" / "decisions.toml",
            workspace_path=workspace_dir,
        )

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
            patch("popctl.cli.commands.advisor._scan_system", return_value=sample_scan_result),
            patch(
                "popctl.cli.commands.advisor.ensure_advisor_sessions_dir",
                return_value=tmp_path / "sessions",
            ),
            patch(
                "popctl.cli.commands.advisor.get_manifest_path",
                return_value=tmp_path / "nonexistent" / "manifest.toml",
            ),
            patch(
                "popctl.cli.commands.advisor.create_session_workspace",
                return_value=workspace_dir,
            ),
            patch.object(
                __import__("popctl.advisor.runner", fromlist=["AgentRunner"]).AgentRunner,
                "run_headless",
                return_value=successful_result,
            ),
        ):
            result = runner.invoke(app, ["advisor", "classify"])

        assert result.exit_code == 0
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
                "popctl.cli.commands.advisor.ensure_advisor_sessions_dir",
                return_value=tmp_path / "sessions",
            ),
            patch(
                "popctl.cli.commands.advisor.find_latest_decisions",
                return_value=None,
            ),
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
                "popctl.cli.commands.advisor.ensure_advisor_sessions_dir",
                return_value=tmp_path / "sessions",
            ),
            patch(
                "popctl.cli.commands.advisor.find_latest_decisions",
                return_value=None,
            ),
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
        assert "would update" in result.stdout.lower()
        mock_save.assert_not_called()


class TestAdvisorApplyErrors:
    """Tests for advisor apply error handling."""

    def test_apply_without_decisions_toml(self, tmp_path: Path) -> None:
        """Apply fails when decisions.toml is not found."""
        with (
            patch(
                "popctl.cli.commands.advisor.ensure_advisor_sessions_dir",
                return_value=tmp_path / "sessions",
            ),
            patch(
                "popctl.cli.commands.advisor.find_latest_decisions",
                return_value=None,
            ),
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
                "popctl.cli.commands.advisor.ensure_advisor_sessions_dir",
                return_value=tmp_path / "sessions",
            ),
            patch(
                "popctl.cli.commands.advisor.find_latest_decisions",
                return_value=None,
            ),
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
                "popctl.cli.commands.advisor.ensure_advisor_sessions_dir",
                return_value=tmp_path / "sessions",
            ),
            patch(
                "popctl.cli.commands.advisor.find_latest_decisions",
                return_value=None,
            ),
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


class TestAdvisorApplyFromSession:
    """Tests for advisor apply resolving decisions from sessions."""

    def test_apply_uses_latest_session(
        self,
        tmp_path: Path,
        sample_manifest: Path,
    ) -> None:
        """Apply resolves decisions from latest session workspace."""
        from popctl.advisor import DecisionsResult, PackageDecision, SourceDecisions

        decisions_path = tmp_path / "session" / "output" / "decisions.toml"
        decisions_path.parent.mkdir(parents=True)
        decisions_path.touch()

        mock_decisions = DecisionsResult(
            packages={
                "apt": SourceDecisions(
                    keep=[
                        PackageDecision(
                            name="firefox",
                            reason="Browser",
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
                "popctl.cli.commands.advisor.ensure_advisor_sessions_dir",
                return_value=tmp_path / "sessions",
            ),
            patch(
                "popctl.cli.commands.advisor.find_latest_decisions",
                return_value=decisions_path,
            ),
            patch(
                "popctl.advisor.import_decisions",
                return_value=mock_decisions,
            ),
            patch(
                "popctl.core.manifest.load_manifest",
                return_value=mock_manifest,
            ),
            patch("popctl.core.manifest.save_manifest"),
            patch(
                "popctl.core.paths.get_manifest_path",
                return_value=sample_manifest,
            ),
        ):
            result = runner.invoke(app, ["advisor", "apply"])

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
                "popctl.cli.commands.advisor.ensure_advisor_sessions_dir",
                return_value=tmp_path / "sessions",
            ),
            patch(
                "popctl.cli.commands.advisor.find_latest_decisions",
                return_value=None,
            ),
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
                "popctl.cli.commands.advisor.ensure_advisor_sessions_dir",
                return_value=tmp_path / "sessions",
            ),
            patch(
                "popctl.cli.commands.advisor.find_latest_decisions",
                return_value=None,
            ),
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
        mock_record.assert_called_once()
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
                "popctl.cli.commands.advisor.ensure_advisor_sessions_dir",
                return_value=tmp_path / "sessions",
            ),
            patch(
                "popctl.cli.commands.advisor.find_latest_decisions",
                return_value=None,
            ),
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
        mock_record.assert_not_called()
        mock_save.assert_not_called()
        assert "would update" in result.stdout.lower()
