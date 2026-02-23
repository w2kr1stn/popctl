"""Integration tests for Claude Advisor.

These tests verify the end-to-end workflow of the advisor feature,
including file exchange, classification, and manifest updates.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import tomli_w
from popctl.advisor import (
    AdvisorConfig,
    AgentResult,
    DecisionsResult,
    PackageDecision,
    SourceDecisions,
)
from popctl.cli.main import app
from popctl.models.manifest import (
    Manifest,
    ManifestMeta,
    PackageConfig,
    SystemConfig,
)
from popctl.models.package import PackageSource, PackageStatus, ScannedPackage
from popctl.models.scan_result import ScanResult
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def sample_packages() -> list[ScannedPackage]:
    """Create sample packages for integration testing."""
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
        ScannedPackage(
            name="bloatware",
            source=PackageSource.APT,
            version="1.0",
            status=PackageStatus.MANUAL,
            description="Unused application",
        ),
        ScannedPackage(
            name="com.spotify.Client",
            source=PackageSource.FLATPAK,
            version="1.2.3",
            status=PackageStatus.MANUAL,
            description="Music streaming",
        ),
    ]


@pytest.fixture
def sample_scan_result(sample_packages: list[ScannedPackage]) -> ScanResult:
    """Create a sample scan result for integration testing."""
    return ScanResult.create(sample_packages, ["apt", "flatpak"])


@pytest.fixture
def sample_manifest(tmp_path: Path) -> tuple[Path, Manifest]:
    """Create a sample manifest file for testing."""
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

    manifest = Manifest(
        meta=ManifestMeta(
            version="1.0",
            created=datetime.now(UTC),
            updated=datetime.now(UTC),
        ),
        system=SystemConfig(name="test-machine", base="pop-os-24.04"),
        packages=PackageConfig(keep={}, remove={}),
    )
    return manifest_path, manifest


@pytest.fixture
def mock_config() -> AdvisorConfig:
    """Create a mock advisor config."""
    return AdvisorConfig(provider="claude", model="sonnet")


@pytest.fixture
def mock_decisions() -> DecisionsResult:
    """Create mock classification decisions."""
    return DecisionsResult(
        packages={
            "apt": SourceDecisions(
                keep=[
                    PackageDecision(
                        name="firefox",
                        reason="Essential web browser",
                        confidence=0.95,
                        category="desktop",
                    ),
                    PackageDecision(
                        name="vim",
                        reason="Essential text editor",
                        confidence=0.92,
                        category="development",
                    ),
                ],
                remove=[
                    PackageDecision(
                        name="bloatware",
                        reason="Unused application",
                        confidence=0.88,
                        category="other",
                    ),
                ],
                ask=[],
            ),
            "flatpak": SourceDecisions(
                keep=[
                    PackageDecision(
                        name="com.spotify.Client",
                        reason="Music streaming application",
                        confidence=0.90,
                        category="media",
                    ),
                ],
                remove=[],
                ask=[],
            ),
        }
    )


class TestAdvisorIntegration:
    """End-to-end tests for advisor workflow."""

    def test_classify_then_apply_workflow(
        self,
        tmp_path: Path,
        sample_scan_result: ScanResult,
        sample_manifest: tuple[Path, Manifest],
        mock_config: AdvisorConfig,
        mock_decisions: DecisionsResult,
    ) -> None:
        """Test complete classify -> apply workflow with mocks."""
        exchange_dir = tmp_path / "exchange"
        exchange_dir.mkdir(parents=True)
        manifest_path, manifest = sample_manifest

        # Create decisions.toml in exchange dir
        decisions_toml = exchange_dir / "decisions.toml"
        decisions_toml.write_text("""
[packages.apt]
[[packages.apt.keep]]
name = "firefox"
reason = "Essential web browser"
confidence = 0.95
category = "desktop"

[[packages.apt.remove]]
name = "bloatware"
reason = "Unused application"
confidence = 0.88
category = "other"

[packages.flatpak]
[[packages.flatpak.keep]]
name = "com.spotify.Client"
reason = "Music streaming"
confidence = 0.90
category = "media"
""")

        successful_result = AgentResult(
            success=True,
            output="Classification complete",
            decisions_path=decisions_toml,
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
            # Step 1: Run classify in headless mode
            classify_result = runner.invoke(app, ["advisor", "classify", "--auto"])
            assert classify_result.exit_code == 0
            assert (
                "successfully" in classify_result.stdout.lower()
                or "success" in classify_result.stdout.lower()
            )

        # Step 2: Apply the decisions
        with (
            patch(
                "popctl.core.paths.get_exchange_dir",
                return_value=exchange_dir,
            ),
            patch(
                "popctl.advisor.import_decisions",
                return_value=mock_decisions,
            ),
            patch(
                "popctl.core.manifest.load_manifest",
                return_value=manifest,
            ),
            patch(
                "popctl.core.manifest.save_manifest",
            ) as mock_save,
            patch(
                "popctl.core.paths.get_manifest_path",
                return_value=manifest_path,
            ),
        ):
            apply_result = runner.invoke(app, ["advisor", "apply"])

        assert apply_result.exit_code == 0
        mock_save.assert_called_once()

        # Verify manifest was updated
        saved_manifest = mock_save.call_args[0][0]
        assert "firefox" in saved_manifest.packages.keep
        assert "bloatware" in saved_manifest.packages.remove

    def test_headless_mode_creates_decisions(
        self,
        tmp_path: Path,
        sample_scan_result: ScanResult,
        mock_config: AdvisorConfig,
    ) -> None:
        """Headless mode creates decisions.toml."""
        exchange_dir = tmp_path / "exchange"
        exchange_dir.mkdir(parents=True)

        decisions_path = exchange_dir / "decisions.toml"

        successful_result = AgentResult(
            success=True,
            output="Classification complete",
            decisions_path=decisions_path,
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
        # Output should mention the decisions path
        assert "decisions" in result.stdout.lower() or "successfully" in result.stdout.lower()

    def test_apply_updates_manifest_correctly(
        self,
        tmp_path: Path,
        sample_manifest: tuple[Path, Manifest],
        mock_decisions: DecisionsResult,
    ) -> None:
        """Apply correctly updates manifest with decisions."""
        exchange_dir = tmp_path / "exchange"
        exchange_dir.mkdir(parents=True)
        manifest_path, manifest = sample_manifest

        # Create a minimal decisions.toml
        decisions_toml = exchange_dir / "decisions.toml"
        decisions_toml.touch()

        saved_manifest: Manifest | None = None

        def capture_save(m: Manifest, path: Path | None = None) -> Path:
            nonlocal saved_manifest
            saved_manifest = m
            return manifest_path

        with (
            patch(
                "popctl.core.paths.get_exchange_dir",
                return_value=exchange_dir,
            ),
            patch(
                "popctl.advisor.import_decisions",
                return_value=mock_decisions,
            ),
            patch(
                "popctl.core.manifest.load_manifest",
                return_value=manifest,
            ),
            patch(
                "popctl.core.manifest.save_manifest",
                side_effect=capture_save,
            ),
            patch(
                "popctl.core.paths.get_manifest_path",
                return_value=manifest_path,
            ),
        ):
            result = runner.invoke(app, ["advisor", "apply"])

        assert result.exit_code == 0
        assert saved_manifest is not None

        # Verify keep packages
        assert "firefox" in saved_manifest.packages.keep
        assert "vim" in saved_manifest.packages.keep
        assert "com.spotify.Client" in saved_manifest.packages.keep

        # Verify remove packages
        assert "bloatware" in saved_manifest.packages.remove


class TestAdvisorProviderSelection:
    """Tests for AI provider selection."""

    def test_classify_with_gemini_provider(
        self,
        tmp_path: Path,
        sample_scan_result: ScanResult,
    ) -> None:
        """Classify uses Gemini provider when specified."""
        exchange_dir = tmp_path / "exchange"
        exchange_dir.mkdir(parents=True)

        # Track which provider config was used
        captured_config: AdvisorConfig | None = None

        def mock_prepare_interactive(self: Any, path: Path) -> str:
            nonlocal captured_config
            captured_config = self.config
            return "Test instructions"

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
                mock_prepare_interactive,
            ),
        ):
            result = runner.invoke(app, ["advisor", "classify", "--provider", "gemini"])

        assert result.exit_code == 0
        assert "gemini" in result.stdout.lower()

    def test_classify_with_custom_model(
        self,
        tmp_path: Path,
        sample_scan_result: ScanResult,
    ) -> None:
        """Classify uses custom model when specified."""
        exchange_dir = tmp_path / "exchange"
        exchange_dir.mkdir(parents=True)

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
        assert "opus" in result.stdout.lower()


class TestAdvisorApplyDryRun:
    """Tests for advisor apply dry-run functionality."""

    def test_apply_dry_run_shows_changes_without_saving(
        self,
        tmp_path: Path,
        sample_manifest: tuple[Path, Manifest],
        mock_decisions: DecisionsResult,
    ) -> None:
        """Apply --dry-run shows what would change without modifying manifest."""
        exchange_dir = tmp_path / "exchange"
        exchange_dir.mkdir(parents=True)
        manifest_path, manifest = sample_manifest

        decisions_toml = exchange_dir / "decisions.toml"
        decisions_toml.touch()

        with (
            patch(
                "popctl.core.paths.get_exchange_dir",
                return_value=exchange_dir,
            ),
            patch(
                "popctl.advisor.import_decisions",
                return_value=mock_decisions,
            ),
            patch(
                "popctl.core.manifest.load_manifest",
                return_value=manifest,
            ),
            patch(
                "popctl.core.manifest.save_manifest",
            ) as mock_save,
            patch(
                "popctl.core.paths.get_manifest_path",
                return_value=manifest_path,
            ),
        ):
            result = runner.invoke(app, ["advisor", "apply", "--dry-run"])

        assert result.exit_code == 0
        # Should show summary table
        assert "keep" in result.stdout.lower() or "summary" in result.stdout.lower()
        # Should NOT save manifest
        mock_save.assert_not_called()
        # Should indicate dry-run mode
        assert "would" in result.stdout.lower() or "dry" in result.stdout.lower()


class TestAdvisorErrorHandling:
    """Tests for advisor error handling."""

    def test_classify_fails_gracefully_on_agent_error(
        self,
        tmp_path: Path,
        sample_scan_result: ScanResult,
        mock_config: AdvisorConfig,
    ) -> None:
        """Classify reports error when AI agent fails."""
        exchange_dir = tmp_path / "exchange"
        exchange_dir.mkdir(parents=True)

        failed_result = AgentResult(
            success=False,
            output="Agent failed to respond",
            error="Timeout after 600 seconds",
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
        combined = result.stdout + (result.stderr or "")
        assert (
            "failed" in combined.lower()
            or "error" in combined.lower()
            or "timeout" in combined.lower()
        )

    def test_apply_fails_when_no_manifest_exists(self, tmp_path: Path) -> None:
        """Apply fails gracefully when no manifest exists."""
        from popctl.core.manifest import ManifestNotFoundError

        exchange_dir = tmp_path / "exchange"
        exchange_dir.mkdir(parents=True)

        mock_decisions = DecisionsResult(
            packages={
                "apt": SourceDecisions(keep=[], remove=[], ask=[]),
                "flatpak": SourceDecisions(keep=[], remove=[], ask=[]),
            }
        )

        with (
            patch(
                "popctl.core.paths.get_exchange_dir",
                return_value=exchange_dir,
            ),
            patch(
                "popctl.advisor.import_decisions",
                return_value=mock_decisions,
            ),
            patch(
                "popctl.core.manifest.load_manifest",
                side_effect=ManifestNotFoundError("No manifest found"),
            ),
        ):
            result = runner.invoke(app, ["advisor", "apply"])

        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "manifest" in combined.lower()
        assert "init" in combined.lower() or "not found" in combined.lower()
