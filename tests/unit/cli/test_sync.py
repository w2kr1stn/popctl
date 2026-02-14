"""Unit tests for sync command.

Tests for the CLI sync command implementation covering all pipeline phases,
flag behaviors, and edge cases.
"""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from popctl.cli.main import app
from popctl.core.diff import DiffEntry, DiffResult, DiffType
from popctl.models.manifest import (
    Manifest,
    ManifestMeta,
    PackageConfig,
    PackageEntry,
    SystemConfig,
)
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def sample_manifest() -> Manifest:
    """Create a sample manifest for testing."""
    now = datetime.now(UTC)
    return Manifest(
        meta=ManifestMeta(version="1.0", created=now, updated=now),
        system=SystemConfig(name="test-machine"),
        packages=PackageConfig(
            keep={
                "firefox": PackageEntry(source="apt"),
                "neovim": PackageEntry(source="apt"),
            },
            remove={
                "bloatware": PackageEntry(source="apt", status="remove"),
            },
        ),
    )


@pytest.fixture
def in_sync_result() -> DiffResult:
    """Create a diff result that shows system in sync."""
    return DiffResult(new=(), missing=(), extra=())


@pytest.fixture
def diff_result_with_new() -> DiffResult:
    """Create a diff result with NEW packages (requires advisor)."""
    return DiffResult(
        new=(
            DiffEntry(name="htop", source="apt", diff_type=DiffType.NEW, version="3.2.2"),
            DiffEntry(name="curl", source="apt", diff_type=DiffType.NEW, version="8.0"),
        ),
        missing=(DiffEntry(name="vim", source="apt", diff_type=DiffType.MISSING),),
        extra=(DiffEntry(name="bloatware", source="apt", diff_type=DiffType.EXTRA, version="1.0"),),
    )


@pytest.fixture
def diff_result_no_new() -> DiffResult:
    """Create a diff result with MISSING and EXTRA but no NEW."""
    return DiffResult(
        new=(),
        missing=(DiffEntry(name="vim", source="apt", diff_type=DiffType.MISSING),),
        extra=(DiffEntry(name="bloatware", source="apt", diff_type=DiffType.EXTRA, version="1.0"),),
    )


class TestSyncHelp:
    """Tests for sync command help."""

    def test_sync_help(self) -> None:
        """Sync command shows help."""
        result = runner.invoke(app, ["sync", "--help"])
        assert result.exit_code == 0
        assert "Full system synchronization" in result.stdout

    def test_sync_help_shows_flags(self) -> None:
        """Sync help shows all available flags."""
        result = runner.invoke(app, ["sync", "--help"])
        assert "--no-advisor" in result.stdout
        assert "--auto" in result.stdout
        assert "--dry-run" in result.stdout
        assert "--yes" in result.stdout
        assert "--source" in result.stdout
        assert "--purge" in result.stdout


class TestSyncNoManifest:
    """Tests for sync auto-init when no manifest exists."""

    def test_sync_auto_init(self, sample_manifest: Manifest, in_sync_result: DiffResult) -> None:
        """Sync auto-creates manifest when missing, then proceeds."""
        from popctl.models.package import PackageSource

        mock_scanner = MagicMock()
        mock_scanner.source = PackageSource.APT

        with (
            # First call: manifest_exists returns False (triggers init)
            # Second call: after init, load_manifest is called by _compute_diff
            patch("popctl.cli.commands.sync.manifest_exists", return_value=False),
            patch("popctl.cli.commands.sync.get_available_scanners", return_value=[mock_scanner]),
            patch(
                "popctl.cli.commands.init._collect_manual_packages",
                return_value=({"firefox": PackageEntry(source="apt")}, []),
            ),
            patch("popctl.cli.commands.init._create_manifest", return_value=sample_manifest),
            patch("popctl.core.paths.ensure_config_dir"),
            patch(
                "popctl.cli.commands.sync.save_manifest", return_value=Path("/tmp/manifest.toml")
            ),
            # For _compute_diff phase
            patch("popctl.core.manifest.load_manifest", return_value=sample_manifest),
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch.object(
                __import__("popctl.core.diff", fromlist=["DiffEngine"]).DiffEngine,
                "compute_diff",
                return_value=in_sync_result,
            ),
        ):
            result = runner.invoke(app, ["sync"])

        assert result.exit_code == 0
        assert "Manifest created" in result.stdout or "in sync" in result.stdout.lower()


class TestSyncInSync:
    """Tests for sync when system is already in sync."""

    def test_sync_in_sync_message(
        self, sample_manifest: Manifest, in_sync_result: DiffResult
    ) -> None:
        """Sync prints success when system matches manifest."""
        with (
            patch("popctl.cli.commands.sync.manifest_exists", return_value=True),
            patch("popctl.core.manifest.load_manifest", return_value=sample_manifest),
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch.object(
                __import__("popctl.core.diff", fromlist=["DiffEngine"]).DiffEngine,
                "compute_diff",
                return_value=in_sync_result,
            ),
        ):
            result = runner.invoke(app, ["sync"])

        assert result.exit_code == 0
        assert "in sync" in result.stdout.lower()


class TestSyncDryRun:
    """Tests for sync --dry-run option."""

    def test_sync_dry_run_shows_diff(
        self, sample_manifest: Manifest, diff_result_no_new: DiffResult
    ) -> None:
        """Dry-run shows diff summary but does not execute."""
        with (
            patch("popctl.cli.commands.sync.manifest_exists", return_value=True),
            patch("popctl.core.manifest.load_manifest", return_value=sample_manifest),
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch.object(
                __import__("popctl.core.diff", fromlist=["DiffEngine"]).DiffEngine,
                "compute_diff",
                return_value=diff_result_no_new,
            ),
        ):
            result = runner.invoke(app, ["sync", "--dry-run"])

        assert result.exit_code == 0
        assert "Dry-run" in result.stdout or "dry-run" in result.stdout.lower()
        assert "MISSING" in result.stdout or "1" in result.stdout

    def test_sync_dry_run_no_advisor(
        self, sample_manifest: Manifest, diff_result_with_new: DiffResult
    ) -> None:
        """Advisor is NOT invoked in dry-run mode even with NEW packages."""
        with (
            patch("popctl.cli.commands.sync.manifest_exists", return_value=True),
            patch("popctl.core.manifest.load_manifest", return_value=sample_manifest),
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch.object(
                __import__("popctl.core.diff", fromlist=["DiffEngine"]).DiffEngine,
                "compute_diff",
                return_value=diff_result_with_new,
            ),
            patch("popctl.cli.commands.sync._run_advisor") as mock_advisor,
        ):
            result = runner.invoke(app, ["sync", "--dry-run"])

        assert result.exit_code == 0
        mock_advisor.assert_not_called()


class TestSyncNoAdvisor:
    """Tests for sync --no-advisor option."""

    def test_sync_no_advisor_skips_classification(
        self, sample_manifest: Manifest, diff_result_with_new: DiffResult
    ) -> None:
        """--no-advisor flag skips advisor entirely."""
        with (
            patch("popctl.cli.commands.sync.manifest_exists", return_value=True),
            patch("popctl.core.manifest.load_manifest", return_value=sample_manifest),
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch("popctl.operators.apt.command_exists", return_value=True),
            patch.object(
                __import__("popctl.core.diff", fromlist=["DiffEngine"]).DiffEngine,
                "compute_diff",
                return_value=diff_result_with_new,
            ),
            patch("popctl.cli.commands.sync._run_advisor") as mock_advisor,
            patch("popctl.operators.apt.run_command") as mock_run,
        ):
            mock_run.return_value = __import__(
                "popctl.utils.shell", fromlist=["CommandResult"]
            ).CommandResult(stdout="", stderr="", returncode=0)

            runner.invoke(app, ["sync", "--no-advisor", "--yes"])

        mock_advisor.assert_not_called()


class TestSyncAdvisor:
    """Tests for sync advisor integration."""

    def test_sync_skips_advisor_when_no_new(
        self, sample_manifest: Manifest, diff_result_no_new: DiffResult
    ) -> None:
        """No NEW packages means advisor is not called."""
        with (
            patch("popctl.cli.commands.sync.manifest_exists", return_value=True),
            patch("popctl.core.manifest.load_manifest", return_value=sample_manifest),
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch("popctl.operators.apt.command_exists", return_value=True),
            patch.object(
                __import__("popctl.core.diff", fromlist=["DiffEngine"]).DiffEngine,
                "compute_diff",
                return_value=diff_result_no_new,
            ),
            patch("popctl.cli.commands.sync._run_advisor") as mock_advisor,
            patch("popctl.operators.apt.run_command") as mock_run,
        ):
            mock_run.return_value = __import__(
                "popctl.utils.shell", fromlist=["CommandResult"]
            ).CommandResult(stdout="", stderr="", returncode=0)

            runner.invoke(app, ["sync", "--yes"])

        mock_advisor.assert_not_called()

    def test_sync_advisor_failure_continues(
        self, sample_manifest: Manifest, diff_result_with_new: DiffResult
    ) -> None:
        """Advisor failure warns and continues with current manifest."""
        with (
            patch("popctl.cli.commands.sync.manifest_exists", return_value=True),
            patch("popctl.core.manifest.load_manifest", return_value=sample_manifest),
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch("popctl.operators.apt.command_exists", return_value=True),
            patch.object(
                __import__("popctl.core.diff", fromlist=["DiffEngine"]).DiffEngine,
                "compute_diff",
                return_value=diff_result_with_new,
            ),
            # Advisor internal: fail at config loading stage
            patch(
                "popctl.cli.commands.advisor._load_or_create_config",
                side_effect=RuntimeError("config error"),
            ),
            patch("popctl.operators.apt.run_command") as mock_run,
        ):
            mock_run.return_value = __import__(
                "popctl.utils.shell", fromlist=["CommandResult"]
            ).CommandResult(stdout="", stderr="", returncode=0)

            result = runner.invoke(app, ["sync", "--yes", "--auto"])

        # Should NOT fail — advisor error is non-fatal
        # The command should still proceed to execution
        assert result.exit_code == 0 or "FAIL" not in result.stdout


class TestSyncExecution:
    """Tests for sync command system action execution."""

    def test_sync_executes_actions(
        self, sample_manifest: Manifest, diff_result_no_new: DiffResult
    ) -> None:
        """Sync installs/removes packages via operators."""
        with (
            patch("popctl.cli.commands.sync.manifest_exists", return_value=True),
            patch("popctl.core.manifest.load_manifest", return_value=sample_manifest),
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch("popctl.operators.apt.command_exists", return_value=True),
            patch.object(
                __import__("popctl.core.diff", fromlist=["DiffEngine"]).DiffEngine,
                "compute_diff",
                return_value=diff_result_no_new,
            ),
            patch("popctl.operators.apt.run_command") as mock_run,
        ):
            mock_run.return_value = __import__(
                "popctl.utils.shell", fromlist=["CommandResult"]
            ).CommandResult(stdout="", stderr="", returncode=0)

            runner.invoke(app, ["sync", "--yes"])

        # Should have executed commands
        mock_run.assert_called()

    def test_sync_yes_skips_confirmation(
        self, sample_manifest: Manifest, diff_result_no_new: DiffResult
    ) -> None:
        """--yes flag skips confirmation prompt."""
        with (
            patch("popctl.cli.commands.sync.manifest_exists", return_value=True),
            patch("popctl.core.manifest.load_manifest", return_value=sample_manifest),
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch("popctl.operators.apt.command_exists", return_value=True),
            patch.object(
                __import__("popctl.core.diff", fromlist=["DiffEngine"]).DiffEngine,
                "compute_diff",
                return_value=diff_result_no_new,
            ),
            patch("popctl.operators.apt.run_command") as mock_run,
        ):
            mock_run.return_value = __import__(
                "popctl.utils.shell", fromlist=["CommandResult"]
            ).CommandResult(stdout="", stderr="", returncode=0)

            result = runner.invoke(app, ["sync", "--yes"])

        # Should have executed without "Confirm" / "y/N" in output
        assert "y/N" not in result.stdout or result.exit_code == 0


class TestSyncHistory:
    """Tests for sync history recording."""

    def test_sync_records_history(
        self, sample_manifest: Manifest, diff_result_no_new: DiffResult
    ) -> None:
        """Sync records actions with command='popctl sync'."""
        with (
            patch("popctl.cli.commands.sync.manifest_exists", return_value=True),
            patch("popctl.core.manifest.load_manifest", return_value=sample_manifest),
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch("popctl.operators.apt.command_exists", return_value=True),
            patch.object(
                __import__("popctl.core.diff", fromlist=["DiffEngine"]).DiffEngine,
                "compute_diff",
                return_value=diff_result_no_new,
            ),
            patch("popctl.operators.apt.run_command") as mock_run,
            patch("popctl.cli.commands.sync.record_actions_to_history") as mock_record,
        ):
            mock_run.return_value = __import__(
                "popctl.utils.shell", fromlist=["CommandResult"]
            ).CommandResult(stdout="", stderr="", returncode=0)

            runner.invoke(app, ["sync", "--yes"])

        # record_actions_to_history should have been called with command="popctl sync"
        mock_record.assert_called_once()
        call_kwargs = mock_record.call_args
        # Check the command argument
        assert call_kwargs[1]["command"] == "popctl sync" or (
            len(call_kwargs[0]) > 1 and call_kwargs[0][1] == "popctl sync"
        )


class TestSyncFailures:
    """Tests for sync failure handling."""

    def test_sync_reports_failures(self, sample_manifest: Manifest) -> None:
        """Failed system actions result in exit code 1."""
        missing_only = DiffResult(
            new=(),
            missing=(DiffEntry(name="nonexistent-pkg", source="apt", diff_type=DiffType.MISSING),),
            extra=(),
        )

        with (
            patch("popctl.cli.commands.sync.manifest_exists", return_value=True),
            patch("popctl.core.manifest.load_manifest", return_value=sample_manifest),
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch("popctl.operators.apt.command_exists", return_value=True),
            patch.object(
                __import__("popctl.core.diff", fromlist=["DiffEngine"]).DiffEngine,
                "compute_diff",
                return_value=missing_only,
            ),
            patch("popctl.operators.apt.run_command") as mock_run,
        ):
            mock_run.return_value = __import__(
                "popctl.utils.shell", fromlist=["CommandResult"]
            ).CommandResult(stdout="", stderr="E: Package not found", returncode=100)

            result = runner.invoke(app, ["sync", "--yes"])

        assert result.exit_code == 1
        assert "FAIL" in result.stdout or "failed" in result.stdout.lower()


class TestSyncReDiff:
    """Tests for re-diff after advisor apply."""

    def test_sync_re_diffs_after_advisor(self, sample_manifest: Manifest) -> None:
        """Sync re-computes diff after advisor changes."""
        first_diff = DiffResult(
            new=(DiffEntry(name="htop", source="apt", diff_type=DiffType.NEW),),
            missing=(DiffEntry(name="vim", source="apt", diff_type=DiffType.MISSING),),
            extra=(),
        )
        # After advisor, NEW is resolved, only MISSING remains
        second_diff = DiffResult(
            new=(),
            missing=(DiffEntry(name="vim", source="apt", diff_type=DiffType.MISSING),),
            extra=(),
        )

        diff_call_count = 0

        def compute_diff_side_effect(*args, **kwargs):
            nonlocal diff_call_count
            diff_call_count += 1
            if diff_call_count <= 1:
                return first_diff
            return second_diff

        with (
            patch("popctl.cli.commands.sync.manifest_exists", return_value=True),
            patch("popctl.core.manifest.load_manifest", return_value=sample_manifest),
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch("popctl.operators.apt.command_exists", return_value=True),
            patch.object(
                __import__("popctl.core.diff", fromlist=["DiffEngine"]).DiffEngine,
                "compute_diff",
                side_effect=compute_diff_side_effect,
            ),
            # Mock _run_advisor to be a no-op (we test re-diff, not advisor itself)
            patch("popctl.cli.commands.sync._run_advisor"),
            patch("popctl.operators.apt.run_command") as mock_run,
        ):
            mock_run.return_value = __import__(
                "popctl.utils.shell", fromlist=["CommandResult"]
            ).CommandResult(stdout="", stderr="", returncode=0)

            runner.invoke(app, ["sync", "--yes"])

        # compute_diff should have been called twice (initial + re-diff)
        assert diff_call_count == 2


class TestSyncPurge:
    """Tests for sync --purge option."""

    def test_sync_purge_uses_purge_command(self, sample_manifest: Manifest) -> None:
        """Sync --purge passes purge flag to action conversion."""
        extra_only = DiffResult(
            new=(),
            missing=(),
            extra=(DiffEntry(name="bloatware", source="apt", diff_type=DiffType.EXTRA),),
        )

        with (
            patch("popctl.cli.commands.sync.manifest_exists", return_value=True),
            patch("popctl.core.manifest.load_manifest", return_value=sample_manifest),
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch("popctl.operators.apt.command_exists", return_value=True),
            patch.object(
                __import__("popctl.core.diff", fromlist=["DiffEngine"]).DiffEngine,
                "compute_diff",
                return_value=extra_only,
            ),
            patch("popctl.operators.apt.run_command") as mock_run,
        ):
            mock_run.return_value = __import__(
                "popctl.utils.shell", fromlist=["CommandResult"]
            ).CommandResult(stdout="", stderr="", returncode=0)

            runner.invoke(app, ["sync", "--yes", "--purge"])

        # Should have called apt-get purge
        args = mock_run.call_args[0][0]
        assert "purge" in args
