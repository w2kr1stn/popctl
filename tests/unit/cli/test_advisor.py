"""Unit tests for advisor command.

Tests for the CLI advisor command implementation,
including integration tests for end-to-end advisor workflows.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
import tomli_w
import typer
from popctl.advisor import (
    AdvisorConfig,
    AgentResult,
    AgentRunner,
    DecisionsResult,
    PackageDecision,
    SourceDecisions,
)
from popctl.cli.main import app
from popctl.models.manifest import Manifest, ManifestMeta, PackageConfig, SystemConfig
from popctl.models.package import PackageSource, PackageStatus, ScannedPackage, ScanResult
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
    return tuple(sample_packages)


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
        assert all(provider in result.stdout for provider in ("claude", "gemini", "codex"))
        assert "gpt-5.6-terra" in result.stdout

    def test_advisor_session_help_is_provider_neutral(self) -> None:
        result = runner.invoke(app, ["advisor", "session", "--help"])
        assert result.exit_code == 0
        assert all(provider in result.stdout for provider in ("claude", "gemini", "codex"))
        assert "selected provider" in result.stdout
        assert "CLI interactively" in result.stdout


class TestAdvisorClassify:
    """Tests for advisor classify command (always headless)."""

    @pytest.mark.parametrize(
        ("session", "expected_use_djinn"),
        [(object(), True), (None, False)],
        ids=("djinn-session", "no-djinn-session"),
    )
    def test_prepare_session_selects_sessions_dir_for_session_backend(
        self,
        mock_config: AdvisorConfig,
        sample_scan_result: ScanResult,
        tmp_path: Path,
        session: object | None,
        expected_use_djinn: bool,
    ) -> None:
        """The active session backend determines the advisor sessions directory."""
        from popctl.cli.commands.advisor import _prepare_session

        workspace_dir = tmp_path / "workspace"
        events: list[str] = []

        def get_session_manager() -> object | None:
            events.append("session")
            return session

        def ensure_sessions_dir(*, use_djinn: bool) -> Path:
            events.append("sessions_dir")
            assert use_djinn is expected_use_djinn
            return tmp_path / "sessions"

        def create_workspace(*args: object, **kwargs: object) -> Path:
            events.append("workspace")
            return workspace_dir

        with (
            patch("popctl.cli.commands.advisor.load_or_create_config", return_value=mock_config),
            patch("popctl.cli.commands.advisor.scan_system", return_value=sample_scan_result),
            patch(
                "popctl.cli.commands.advisor.get_session_manager",
                side_effect=get_session_manager,
            ),
            patch(
                "popctl.cli.commands.advisor.ensure_advisor_sessions_dir",
                side_effect=ensure_sessions_dir,
            ),
            patch(
                "popctl.cli.commands.advisor.get_manifest_path",
                return_value=tmp_path / "manifest.toml",
            ),
            patch("popctl.cli.commands.advisor.get_state_dir", return_value=tmp_path),
            patch(
                "popctl.cli.commands.advisor.create_session_workspace",
                side_effect=create_workspace,
            ),
        ):
            _, actual_workspace_dir = _prepare_session(None, None, None)

        assert actual_workspace_dir == workspace_dir
        assert events == ["session", "sessions_dir", "workspace"]

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
            patch("popctl.cli.commands.advisor.load_or_create_config", return_value=mock_config),
            patch("popctl.cli.commands.advisor.scan_system", return_value=sample_scan_result),
            patch(
                "popctl.cli.commands.advisor.create_session_workspace",
                return_value=workspace_dir,
            ),
            patch.object(
                AgentRunner,
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
            patch("popctl.cli.commands.advisor.load_or_create_config", return_value=mock_config),
            patch("popctl.cli.commands.advisor.scan_system", return_value=sample_scan_result),
            patch(
                "popctl.cli.commands.advisor.create_session_workspace",
                return_value=workspace_dir,
            ),
            patch.object(
                AgentRunner,
                "run_headless",
                return_value=failed_result,
            ),
        ):
            result = runner.invoke(app, ["advisor", "classify"])

        assert result.exit_code == 1
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
        workspace_dir = tmp_path / "workspace"

        successful_result = AgentResult(
            success=True,
            output="",
            decisions_path=workspace_dir / "output" / "decisions.toml",
            workspace_path=workspace_dir,
        )

        with (
            patch(
                "popctl.advisor.config.load_advisor_config",
                return_value=AdvisorConfig(provider="claude"),
            ),
            patch("popctl.cli.commands.advisor.scan_system", return_value=sample_scan_result),
            patch(
                "popctl.cli.commands.advisor.create_session_workspace",
                return_value=workspace_dir,
            ),
            patch.object(
                AgentRunner,
                "run_headless",
                return_value=successful_result,
            ),
        ):
            result = runner.invoke(app, ["advisor", "classify", "--provider", "gemini"])

        assert result.exit_code == 0
        assert "gemini" in result.stdout.lower()

    def test_classify_with_custom_model(
        self,
        sample_scan_result: ScanResult,
        tmp_path: Path,
    ) -> None:
        """Classify --model overrides config model."""
        workspace_dir = tmp_path / "workspace"

        successful_result = AgentResult(
            success=True,
            output="",
            decisions_path=workspace_dir / "output" / "decisions.toml",
            workspace_path=workspace_dir,
        )

        with (
            patch(
                "popctl.advisor.config.load_advisor_config",
                return_value=AdvisorConfig(provider="claude"),
            ),
            patch("popctl.cli.commands.advisor.scan_system", return_value=sample_scan_result),
            patch(
                "popctl.cli.commands.advisor.create_session_workspace",
                return_value=workspace_dir,
            ),
            patch.object(
                AgentRunner,
                "run_headless",
                return_value=successful_result,
            ),
        ):
            result = runner.invoke(app, ["advisor", "classify", "--model", "opus"])

        assert result.exit_code == 0
        assert "opus" in result.stdout.lower()

    def test_classify_with_nonexistent_input_file(self, tmp_path: Path) -> None:
        """Classify --input with nonexistent file shows error."""
        nonexistent = tmp_path / "nonexistent.json"

        with patch(
            "popctl.cli.commands.advisor.load_or_create_config",
            return_value=AdvisorConfig(provider="claude"),
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
            patch("popctl.cli.commands.advisor.load_or_create_config", return_value=mock_config),
            patch("popctl.cli.commands.advisor.scan_system", return_value=sample_scan_result),
            patch(
                "popctl.cli.commands.advisor.create_session_workspace",
                return_value=workspace_dir,
            ),
            patch.object(
                AgentRunner,
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
            patch("popctl.cli.commands.advisor.load_or_create_config", return_value=mock_config),
            patch("popctl.cli.commands.advisor.scan_system", return_value=sample_scan_result),
            patch(
                "popctl.cli.commands.advisor.create_session_workspace",
                return_value=workspace_dir,
            ),
            patch.object(
                AgentRunner,
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
            error="Agent crashed",
            workspace_path=workspace_dir,
        )

        with (
            patch("popctl.cli.commands.advisor.load_or_create_config", return_value=mock_config),
            patch("popctl.cli.commands.advisor.scan_system", return_value=sample_scan_result),
            patch(
                "popctl.cli.commands.advisor.create_session_workspace",
                return_value=workspace_dir,
            ),
            patch.object(
                AgentRunner,
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

        workspace_dir = tmp_path / "workspace"

        successful_result = AgentResult(
            success=True,
            output="",
            decisions_path=workspace_dir / "output" / "decisions.toml",
            workspace_path=workspace_dir,
        )

        with (
            patch(
                "popctl.cli.commands.advisor.load_or_create_config",
                return_value=AdvisorConfig(),
            ),
            patch("popctl.cli.commands.advisor.scan_system", return_value=sample_scan_result),
            patch(
                "popctl.cli.commands.advisor.create_session_workspace",
                return_value=workspace_dir,
            ),
            patch.object(
                AgentRunner,
                "run_headless",
                return_value=successful_result,
            ),
        ):
            result = runner.invoke(app, ["advisor", "classify"])

        assert result.exit_code == 0


class TestAdvisorScannerAvailability:
    """Tests for scanner availability handling in advisor."""

    def test_classify_no_scanners_available(self) -> None:
        """Classify fails when no scanners are available."""
        with (
            patch(
                "popctl.cli.commands.advisor.load_or_create_config",
                return_value=AdvisorConfig(provider="claude"),
            ),
            patch("popctl.scanners.apt.command_exists", return_value=False),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch("popctl.scanners.snap.command_exists", return_value=False),
        ):
            result = runner.invoke(app, ["advisor", "classify"])

        assert result.exit_code == 1
        output = (result.stdout + (result.stderr or "")).lower()
        assert "no package managers" in output or "not available" in output


# =============================================================================
# Tests for advisor apply command
# =============================================================================


@pytest.fixture
def sample_manifest_path(tmp_path: Path) -> Path:
    """Create a sample manifest file on disk for testing."""
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
def empty_manifest() -> Manifest:
    """Empty manifest for apply tests."""
    return Manifest(
        meta=ManifestMeta(created=datetime.now(UTC), updated=datetime.now(UTC)),
        system=SystemConfig(name="test"),
        packages=PackageConfig(keep={}, remove={}),
    )


class TestAdvisorApplyWithValidDecisions:
    """Tests for advisor apply with valid decisions.toml."""

    def test_apply_with_valid_decisions(
        self,
        tmp_path: Path,
        sample_manifest_path: Path,
        empty_manifest: Manifest,
    ) -> None:
        """Apply updates manifest with decisions."""
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

        decisions_path = tmp_path / "decisions.toml"
        decisions_path.touch()

        with (
            patch(
                "popctl.cli.commands.advisor.ensure_advisor_sessions_dir",
                return_value=tmp_path / "sessions",
            ),
            patch(
                "popctl.cli.commands.advisor.find_all_unapplied_decisions",
                return_value=[decisions_path],
            ),
            patch("popctl.cli.commands.advisor.cleanup_empty_sessions", return_value=0),
            patch("popctl.cli.commands.advisor.delete_session"),
            patch(
                "popctl.cli.commands.advisor.import_decisions",
                return_value=mock_decisions,
            ),
            patch(
                "popctl.cli.commands.advisor.require_manifest",
                return_value=empty_manifest,
            ),
            patch(
                "popctl.cli.commands.advisor.save_manifest",
            ) as mock_save,
            patch(
                "popctl.cli.commands.advisor.get_manifest_path",
                return_value=sample_manifest_path,
            ),
        ):
            result = runner.invoke(app, ["advisor", "apply"])

        assert result.exit_code == 0
        assert "updated" in result.stdout.lower() or "summary" in result.stdout.lower()
        mock_save.assert_called_once()


class TestAdvisorApplySessionRoots:
    """Deferred advisor decisions are found across both session-root layouts."""

    @staticmethod
    def _decisions() -> DecisionsResult:
        return DecisionsResult(
            packages={
                "apt": SourceDecisions(keep=[], remove=[], ask=[]),
                "flatpak": SourceDecisions(keep=[], remove=[], ask=[]),
            }
        )

    @staticmethod
    def _package_decisions(*, keep: bool) -> DecisionsResult:
        decision = PackageDecision(
            name="firefox",
            reason="Test classification",
            confidence=1.0,
            category="other",
        )
        return DecisionsResult(
            packages={
                "apt": SourceDecisions(
                    keep=[decision] if keep else [],
                    remove=[] if keep else [decision],
                    ask=[],
                )
            }
        )

    def test_djinn_classify_then_apply_uses_djinn_sessions_root(
        self,
        tmp_path: Path,
        sample_scan_result: ScanResult,
        mock_config: AdvisorConfig,
        empty_manifest: Manifest,
    ) -> None:
        """Djinn-backed classification decisions are applied, recorded, and removed."""
        session_backend = object()
        workspace_dir = Path.home() / ".djinn" / "sessions" / "popctl" / "20260101T100000"
        decisions_path = workspace_dir / "output" / "decisions.toml"

        def create_workspace(*args: object, **_kwargs: object) -> Path:
            assert args[1] == workspace_dir.parent
            decisions_path.parent.mkdir(parents=True)
            decisions_path.touch()
            return workspace_dir

        classify_result = AgentResult(
            success=True,
            output="",
            decisions_path=decisions_path,
            workspace_path=workspace_dir,
        )

        with (
            patch("popctl.cli.commands.advisor.get_session_manager", return_value=session_backend),
            patch("popctl.cli.commands.advisor.load_or_create_config", return_value=mock_config),
            patch("popctl.cli.commands.advisor.scan_system", return_value=sample_scan_result),
            patch(
                "popctl.cli.commands.advisor.create_session_workspace",
                side_effect=create_workspace,
            ),
            patch.object(AgentRunner, "run_headless", return_value=classify_result),
        ):
            result = runner.invoke(app, ["advisor", "classify"])

        assert result.exit_code == 0
        assert decisions_path.exists()

        with (
            patch("popctl.cli.commands.advisor.get_session_manager", return_value=session_backend),
            patch("popctl.cli.commands.advisor.import_decisions", return_value=self._decisions()),
            patch("popctl.cli.commands.advisor.require_manifest", return_value=empty_manifest),
            patch("popctl.cli.commands.advisor.save_manifest"),
            patch("popctl.cli.commands.advisor.record_advisor_apply_to_history") as record_history,
        ):
            result = runner.invoke(app, ["advisor", "apply"])

        assert result.exit_code == 0
        record_history.assert_called_once()
        assert not workspace_dir.exists()

    def test_apply_xdg_decisions_without_constructing_session_manager(
        self,
        tmp_path: Path,
        empty_manifest: Manifest,
    ) -> None:
        """Apply reads XDG decisions even when optional Djinn config is broken."""
        decisions_path = (
            tmp_path
            / "xdg-state"
            / "popctl"
            / "sessions"
            / "20260101T100000"
            / "output"
            / "decisions.toml"
        )
        decisions_path.parent.mkdir(parents=True)
        decisions_path.touch()

        with (
            patch(
                "popctl.cli.commands.advisor.get_session_manager",
                side_effect=RuntimeError("broken optional Djinn config"),
            ),
            patch(
                "popctl.cli.commands.advisor.import_decisions",
                return_value=self._package_decisions(keep=True),
            ) as load,
            patch("popctl.cli.commands.advisor.require_manifest", return_value=empty_manifest),
        ):
            result = runner.invoke(app, ["advisor", "apply", "--dry-run"])

        assert result.exit_code == 0
        load.assert_called_once_with(decisions_path)
        assert "firefox" in empty_manifest.packages.keep

    def test_both_roots_are_applied_in_global_chronological_order(
        self,
        tmp_path: Path,
        empty_manifest: Manifest,
    ) -> None:
        """An older Djinn decision applies before a newer XDG decision."""
        xdg_decisions_path = (
            tmp_path
            / "xdg-state"
            / "popctl"
            / "sessions"
            / "20260101T100000"
            / "output"
            / "decisions.toml"
        )
        djinn_decisions_path = (
            tmp_path
            / "isolated-home"
            / ".djinn"
            / "sessions"
            / "popctl"
            / "20250101T100000"
            / "output"
            / "decisions.toml"
        )
        for decisions_path in (xdg_decisions_path, djinn_decisions_path):
            decisions_path.parent.mkdir(parents=True)
            decisions_path.touch()

        with (
            patch("popctl.cli.commands.advisor.get_session_manager", return_value=None),
            patch(
                "popctl.cli.commands.advisor.import_decisions",
                side_effect=[
                    self._package_decisions(keep=False),
                    self._package_decisions(keep=True),
                ],
            ) as load,
            patch("popctl.cli.commands.advisor.require_manifest", return_value=empty_manifest),
        ):
            result = runner.invoke(app, ["advisor", "apply", "--dry-run"])

        assert result.exit_code == 0
        assert [call.args[0] for call in load.call_args_list] == [
            djinn_decisions_path,
            xdg_decisions_path,
        ]
        assert "firefox" in empty_manifest.packages.keep
        assert "firefox" not in empty_manifest.packages.remove

    def test_both_roots_are_ordered_when_djinn_is_primary(
        self,
        tmp_path: Path,
        empty_manifest: Manifest,
    ) -> None:
        """An older XDG decision applies before a newer Djinn decision."""
        xdg_decisions_path = (
            tmp_path
            / "xdg-state"
            / "popctl"
            / "sessions"
            / "20250101T100000"
            / "output"
            / "decisions.toml"
        )
        djinn_decisions_path = (
            tmp_path
            / "isolated-home"
            / ".djinn"
            / "sessions"
            / "popctl"
            / "20260101T100000"
            / "output"
            / "decisions.toml"
        )
        for decisions_path in (xdg_decisions_path, djinn_decisions_path):
            decisions_path.parent.mkdir(parents=True)
            decisions_path.touch()

        with (
            patch("popctl.cli.commands.advisor.get_session_manager", return_value=object()),
            patch(
                "popctl.cli.commands.advisor.import_decisions",
                side_effect=[
                    self._package_decisions(keep=True),
                    self._package_decisions(keep=False),
                ],
            ) as load,
            patch("popctl.cli.commands.advisor.require_manifest", return_value=empty_manifest),
        ):
            result = runner.invoke(app, ["advisor", "apply", "--dry-run"])

        assert result.exit_code == 0
        assert [call.args[0] for call in load.call_args_list] == [
            xdg_decisions_path,
            djinn_decisions_path,
        ]
        assert "firefox" in empty_manifest.packages.remove
        assert "firefox" not in empty_manifest.packages.keep


class TestAdvisorApplyDryRun:
    """Tests for advisor apply --dry-run option."""

    def test_apply_dry_run_does_not_modify_manifest(
        self,
        tmp_path: Path,
        sample_manifest_path: Path,
        empty_manifest: Manifest,
    ) -> None:
        """Apply --dry-run shows changes without modifying manifest."""
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

        decisions_path = tmp_path / "decisions.toml"
        decisions_path.touch()

        with (
            patch(
                "popctl.cli.commands.advisor.ensure_advisor_sessions_dir",
                return_value=tmp_path / "sessions",
            ),
            patch(
                "popctl.cli.commands.advisor.find_all_unapplied_decisions",
                return_value=[decisions_path],
            ),
            patch("popctl.cli.commands.advisor.cleanup_empty_sessions", return_value=0),
            patch("popctl.cli.commands.advisor.delete_session"),
            patch(
                "popctl.cli.commands.advisor.import_decisions",
                return_value=mock_decisions,
            ),
            patch(
                "popctl.cli.commands.advisor.require_manifest",
                return_value=empty_manifest,
            ),
            patch(
                "popctl.cli.commands.advisor.save_manifest",
            ) as mock_save,
            patch(
                "popctl.cli.commands.advisor.get_manifest_path",
                return_value=sample_manifest_path,
            ),
        ):
            result = runner.invoke(app, ["advisor", "apply", "--dry-run"])

        assert result.exit_code == 0
        assert "would update" in result.stdout.lower()
        mock_save.assert_not_called()


class TestAdvisorApplyErrors:
    """Tests for advisor apply error handling."""

    def test_apply_without_decisions_toml(self, tmp_path: Path) -> None:
        """Apply fails when no decisions are found."""
        with (
            patch(
                "popctl.cli.commands.advisor.ensure_advisor_sessions_dir",
                return_value=tmp_path / "sessions",
            ),
            patch(
                "popctl.cli.commands.advisor.find_all_unapplied_decisions",
                return_value=[],
            ),
            patch("popctl.cli.commands.advisor.cleanup_empty_sessions", return_value=0),
        ):
            result = runner.invoke(app, ["advisor", "apply"])

        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "no unapplied advisor decisions found" in combined.lower()

    def test_apply_without_manifest(self, tmp_path: Path) -> None:
        """Apply fails when manifest is not found."""
        decisions_path = tmp_path / "decisions.toml"
        decisions_path.touch()

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
                "popctl.cli.commands.advisor.find_all_unapplied_decisions",
                return_value=[decisions_path],
            ),
            patch("popctl.cli.commands.advisor.cleanup_empty_sessions", return_value=0),
            patch("popctl.cli.commands.advisor.delete_session"),
            patch(
                "popctl.cli.commands.advisor.import_decisions",
                return_value=mock_decisions,
            ),
            patch(
                "popctl.cli.commands.advisor.require_manifest",
                side_effect=typer.Exit(code=1),
            ),
        ):
            result = runner.invoke(app, ["advisor", "apply"])

        assert result.exit_code == 1

    def test_apply_with_invalid_decisions_toml(
        self, tmp_path: Path, empty_manifest: Manifest
    ) -> None:
        """Apply fails when all decisions.toml files are invalid."""
        decisions_path = tmp_path / "decisions.toml"
        decisions_path.touch()

        with (
            patch(
                "popctl.cli.commands.advisor.ensure_advisor_sessions_dir",
                return_value=tmp_path / "sessions",
            ),
            patch(
                "popctl.cli.commands.advisor.find_all_unapplied_decisions",
                return_value=[decisions_path],
            ),
            patch("popctl.cli.commands.advisor.cleanup_empty_sessions", return_value=0),
            patch("popctl.cli.commands.advisor.delete_session"),
            patch(
                "popctl.cli.commands.advisor.import_decisions",
                side_effect=ValueError("Invalid TOML syntax"),
            ),
            patch(
                "popctl.cli.commands.advisor.require_manifest",
                return_value=empty_manifest,
            ),
        ):
            result = runner.invoke(app, ["advisor", "apply"])

        assert result.exit_code == 1
        combined = result.stdout + (result.stderr or "")
        assert "failed to load" in combined.lower()


class TestAdvisorApplyWithInputFile:
    """Tests for advisor apply with custom input file."""

    def test_apply_with_custom_input_path(
        self,
        tmp_path: Path,
        sample_manifest_path: Path,
        empty_manifest: Manifest,
    ) -> None:
        """Apply --input uses specified decisions file."""
        custom_decisions_path = tmp_path / "custom" / "decisions.toml"
        custom_decisions_path.parent.mkdir(parents=True)
        custom_decisions_path.touch()

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
            patch("popctl.cli.commands.advisor.cleanup_empty_sessions", return_value=0),
            patch("popctl.cli.commands.advisor.delete_session"),
            patch(
                "popctl.cli.commands.advisor.import_decisions",
                return_value=mock_decisions,
            ),
            patch(
                "popctl.cli.commands.advisor.require_manifest",
                return_value=empty_manifest,
            ),
            patch(
                "popctl.cli.commands.advisor.save_manifest",
            ),
            patch(
                "popctl.cli.commands.advisor.get_manifest_path",
                return_value=sample_manifest_path,
            ),
        ):
            result = runner.invoke(app, ["advisor", "apply", "--input", str(custom_decisions_path)])

        assert result.exit_code == 0


class TestAdvisorApplyAskPackages:
    """Tests for advisor apply handling of 'ask' packages."""

    def test_apply_shows_ask_packages(
        self,
        tmp_path: Path,
        sample_manifest_path: Path,
        empty_manifest: Manifest,
    ) -> None:
        """Apply displays packages that need manual decision."""
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

        decisions_path = tmp_path / "decisions.toml"
        decisions_path.touch()

        with (
            patch(
                "popctl.cli.commands.advisor.ensure_advisor_sessions_dir",
                return_value=tmp_path / "sessions",
            ),
            patch(
                "popctl.cli.commands.advisor.find_all_unapplied_decisions",
                return_value=[decisions_path],
            ),
            patch("popctl.cli.commands.advisor.cleanup_empty_sessions", return_value=0),
            patch("popctl.cli.commands.advisor.delete_session"),
            patch(
                "popctl.cli.commands.advisor.import_decisions",
                return_value=mock_decisions,
            ),
            patch(
                "popctl.cli.commands.advisor.require_manifest",
                return_value=empty_manifest,
            ),
            patch(
                "popctl.cli.commands.advisor.save_manifest",
            ),
            patch(
                "popctl.cli.commands.advisor.get_manifest_path",
                return_value=sample_manifest_path,
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
        sample_manifest_path: Path,
        empty_manifest: Manifest,
    ) -> None:
        """Advisor apply records classifications to history."""
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

        decisions_path = tmp_path / "decisions.toml"
        decisions_path.touch()

        with (
            patch(
                "popctl.cli.commands.advisor.ensure_advisor_sessions_dir",
                return_value=tmp_path / "sessions",
            ),
            patch(
                "popctl.cli.commands.advisor.find_all_unapplied_decisions",
                return_value=[decisions_path],
            ),
            patch("popctl.cli.commands.advisor.cleanup_empty_sessions", return_value=0),
            patch("popctl.cli.commands.advisor.delete_session"),
            patch(
                "popctl.cli.commands.advisor.import_decisions",
                return_value=mock_decisions,
            ),
            patch(
                "popctl.cli.commands.advisor.require_manifest",
                return_value=empty_manifest,
            ),
            patch(
                "popctl.cli.commands.advisor.save_manifest",
            ),
            patch(
                "popctl.cli.commands.advisor.get_manifest_path",
                return_value=sample_manifest_path,
            ),
            patch("popctl.cli.commands.advisor.record_advisor_apply_to_history") as mock_record,
        ):
            result = runner.invoke(app, ["advisor", "apply"])

        assert result.exit_code == 0
        mock_record.assert_called_once()
        assert "history" in result.stdout.lower()

    def test_apply_does_not_record_history_on_dry_run(
        self,
        tmp_path: Path,
        sample_manifest_path: Path,
        empty_manifest: Manifest,
    ) -> None:
        """Advisor apply --dry-run does NOT record history."""
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

        decisions_path = tmp_path / "decisions.toml"
        decisions_path.touch()

        with (
            patch(
                "popctl.cli.commands.advisor.ensure_advisor_sessions_dir",
                return_value=tmp_path / "sessions",
            ),
            patch(
                "popctl.cli.commands.advisor.find_all_unapplied_decisions",
                return_value=[decisions_path],
            ),
            patch("popctl.cli.commands.advisor.cleanup_empty_sessions", return_value=0),
            patch("popctl.cli.commands.advisor.delete_session"),
            patch(
                "popctl.cli.commands.advisor.import_decisions",
                return_value=mock_decisions,
            ),
            patch(
                "popctl.cli.commands.advisor.require_manifest",
                return_value=empty_manifest,
            ),
            patch(
                "popctl.cli.commands.advisor.save_manifest",
            ) as mock_save,
            patch(
                "popctl.cli.commands.advisor.get_manifest_path",
                return_value=sample_manifest_path,
            ),
            patch("popctl.cli.commands.advisor.record_advisor_apply_to_history") as mock_record,
        ):
            result = runner.invoke(app, ["advisor", "apply", "--dry-run"])

        assert result.exit_code == 0
        mock_record.assert_not_called()
        mock_save.assert_not_called()
        assert "would update" in result.stdout.lower()


# =============================================================================
# Integration tests (merged from test_advisor_workflow.py)
# =============================================================================


class TestAdvisorIntegration:
    """End-to-end tests for advisor workflows spanning classify and apply."""

    def test_classify_then_apply_workflow(
        self,
        tmp_path: Path,
    ) -> None:
        """Test complete classify -> apply workflow with mocks."""
        # -- Setup: manifest on disk + in-memory --
        manifest_data: dict[str, Any] = {
            "meta": {
                "version": "1.0",
                "created": datetime.now(UTC).isoformat(),
                "updated": datetime.now(UTC).isoformat(),
            },
            "system": {"name": "test-machine", "base": "pop-os-24.04"},
            "packages": {"keep": {}, "remove": {}},
        }
        manifest_path = tmp_path / "manifest.toml"
        with manifest_path.open("wb") as f:
            tomli_w.dump(manifest_data, f)

        manifest = Manifest(
            meta=ManifestMeta(created=datetime.now(UTC), updated=datetime.now(UTC)),
            system=SystemConfig(name="test-machine"),
            packages=PackageConfig(keep={}, remove={}),
        )

        scan_result: ScanResult = (
            ScannedPackage(
                name="firefox",
                source=PackageSource.APT,
                version="120.0",
                status=PackageStatus.MANUAL,
                description="Web browser",
            ),
            ScannedPackage(
                name="bloatware",
                source=PackageSource.APT,
                version="1.0",
                status=PackageStatus.MANUAL,
                description="Unused application",
            ),
        )

        decisions = DecisionsResult(
            packages={
                "apt": SourceDecisions(
                    keep=[
                        PackageDecision(
                            name="firefox",
                            reason="Essential web browser",
                            confidence=0.95,
                            category="desktop",
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
                "flatpak": SourceDecisions(keep=[], remove=[], ask=[]),
            }
        )

        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir(parents=True)
        decisions_toml = workspace_dir / "output" / "decisions.toml"
        decisions_toml.parent.mkdir(parents=True)
        decisions_toml.touch()

        config = AdvisorConfig(provider="claude", model="sonnet")

        successful_result = AgentResult(
            success=True,
            output="Classification complete",
            decisions_path=decisions_toml,
            workspace_path=workspace_dir,
        )

        # Step 1: classify
        with (
            patch("popctl.cli.commands.advisor.load_or_create_config", return_value=config),
            patch("popctl.cli.commands.advisor.scan_system", return_value=scan_result),
            patch(
                "popctl.cli.commands.advisor.create_session_workspace",
                return_value=workspace_dir,
            ),
            patch.object(AgentRunner, "run_headless", return_value=successful_result),
        ):
            classify_result = runner.invoke(app, ["advisor", "classify"])
            assert classify_result.exit_code == 0

        # Step 2: apply
        with (
            patch(
                "popctl.cli.commands.advisor.ensure_advisor_sessions_dir",
                return_value=tmp_path / "sessions",
            ),
            patch(
                "popctl.cli.commands.advisor.find_all_unapplied_decisions",
                return_value=[decisions_toml],
            ),
            patch("popctl.cli.commands.advisor.cleanup_empty_sessions", return_value=0),
            patch("popctl.cli.commands.advisor.delete_session"),
            patch("popctl.cli.commands.advisor.import_decisions", return_value=decisions),
            patch("popctl.cli.commands.advisor.require_manifest", return_value=manifest),
            patch("popctl.cli.commands.advisor.save_manifest") as mock_save,
            patch(
                "popctl.cli.commands.advisor.get_manifest_path",
                return_value=manifest_path,
            ),
        ):
            apply_result = runner.invoke(app, ["advisor", "apply"])

        assert apply_result.exit_code == 0
        mock_save.assert_called_once()

        saved_manifest = mock_save.call_args[0][0]
        assert "firefox" in saved_manifest.packages.keep
        assert "bloatware" in saved_manifest.packages.remove

    def test_apply_updates_manifest_content_correctly(
        self,
        tmp_path: Path,
    ) -> None:
        """Apply populates manifest keep/remove with correct multi-source packages."""
        manifest = Manifest(
            meta=ManifestMeta(created=datetime.now(UTC), updated=datetime.now(UTC)),
            system=SystemConfig(name="test-machine"),
            packages=PackageConfig(keep={}, remove={}),
        )
        manifest_path = tmp_path / "manifest.toml"
        manifest_path.touch()

        decisions = DecisionsResult(
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

        decisions_path = tmp_path / "session" / "output" / "decisions.toml"
        decisions_path.parent.mkdir(parents=True)
        decisions_path.touch()

        saved_manifest: Manifest | None = None

        def capture_save(m: Manifest, path: Path | None = None) -> Path:
            nonlocal saved_manifest
            saved_manifest = m
            return manifest_path

        with (
            patch(
                "popctl.cli.commands.advisor.ensure_advisor_sessions_dir",
                return_value=tmp_path / "sessions",
            ),
            patch(
                "popctl.cli.commands.advisor.find_all_unapplied_decisions",
                return_value=[decisions_path],
            ),
            patch("popctl.cli.commands.advisor.cleanup_empty_sessions", return_value=0),
            patch("popctl.cli.commands.advisor.delete_session"),
            patch("popctl.cli.commands.advisor.import_decisions", return_value=decisions),
            patch("popctl.cli.commands.advisor.require_manifest", return_value=manifest),
            patch(
                "popctl.cli.commands.advisor.save_manifest",
                side_effect=capture_save,
            ),
            patch(
                "popctl.cli.commands.advisor.get_manifest_path",
                return_value=manifest_path,
            ),
        ):
            result = runner.invoke(app, ["advisor", "apply"])

        assert result.exit_code == 0
        assert saved_manifest is not None

        # Verify keep packages from both sources
        assert "firefox" in saved_manifest.packages.keep
        assert "vim" in saved_manifest.packages.keep
        assert "com.spotify.Client" in saved_manifest.packages.keep

        # Verify remove packages
        assert "bloatware" in saved_manifest.packages.remove
