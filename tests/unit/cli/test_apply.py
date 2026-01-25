"""Unit tests for apply command.

Tests for the CLI apply command implementation.
"""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

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
def diff_result_with_actions() -> DiffResult:
    """Create a diff result with actionable changes."""
    return DiffResult(
        new=(
            # NEW packages are NOT actioned by apply
            DiffEntry(name="htop", source="apt", diff_type=DiffType.NEW, version="3.2.2"),
        ),
        missing=(
            # MISSING packages -> INSTALL
            DiffEntry(name="vim", source="apt", diff_type=DiffType.MISSING),
        ),
        extra=(
            # EXTRA packages -> REMOVE
            DiffEntry(name="bloatware", source="apt", diff_type=DiffType.EXTRA, version="1.0"),
        ),
    )


class TestApplyCommandHelp:
    """Tests for apply command help."""

    def test_apply_help(self) -> None:
        """Apply command shows help."""
        result = runner.invoke(app, ["apply", "--help"])
        assert result.exit_code == 0
        assert "Apply manifest to system" in result.stdout

    def test_apply_help_shows_options(self) -> None:
        """Apply help shows all available options."""
        result = runner.invoke(app, ["apply", "--help"])
        assert "--yes" in result.stdout
        assert "--dry-run" in result.stdout
        assert "--source" in result.stdout
        assert "--purge" in result.stdout


class TestApplyNoManifest:
    """Tests for apply command when no manifest exists."""

    def test_apply_no_manifest_error(self, tmp_path: Path) -> None:
        """Apply shows error when manifest doesn't exist."""
        from popctl.core.manifest import ManifestNotFoundError

        with patch("popctl.cli.commands.apply.get_manifest_path") as mock_path:
            mock_path.return_value = tmp_path / "nonexistent.toml"
            with patch(
                "popctl.cli.commands.apply.load_manifest",
                side_effect=ManifestNotFoundError("Manifest not found"),
            ):
                result = runner.invoke(app, ["apply"])

        assert result.exit_code == 1
        assert "Manifest not found" in (result.stdout + result.stderr)
        assert "popctl init" in (result.stdout + result.stderr)


class TestApplyInSync:
    """Tests for apply command when system is in sync."""

    def test_apply_in_sync_message(
        self, sample_manifest: Manifest, in_sync_result: DiffResult
    ) -> None:
        """Apply shows success message when in sync."""
        with (
            patch("popctl.cli.commands.apply.load_manifest", return_value=sample_manifest),
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch.object(
                __import__("popctl.core.diff", fromlist=["DiffEngine"]).DiffEngine,
                "compute_diff",
                return_value=in_sync_result,
            ),
        ):
            result = runner.invoke(app, ["apply"])

        assert result.exit_code == 0
        assert "in sync" in result.stdout.lower() or "nothing to do" in result.stdout.lower()


class TestApplyDryRun:
    """Tests for apply --dry-run option."""

    def test_apply_dry_run_shows_actions(
        self, sample_manifest: Manifest, diff_result_with_actions: DiffResult
    ) -> None:
        """Dry-run shows planned actions without executing."""
        with (
            patch("popctl.cli.commands.apply.load_manifest", return_value=sample_manifest),
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch.object(
                __import__("popctl.core.diff", fromlist=["DiffEngine"]).DiffEngine,
                "compute_diff",
                return_value=diff_result_with_actions,
            ),
        ):
            result = runner.invoke(app, ["apply", "--dry-run"])

        assert result.exit_code == 0
        # Should show planned actions
        assert "vim" in result.stdout  # MISSING -> install
        assert "bloatware" in result.stdout  # EXTRA -> remove
        # Should indicate dry-run mode
        assert "Dry" in result.stdout or "dry" in result.stdout

    def test_apply_dry_run_does_not_execute(
        self, sample_manifest: Manifest, diff_result_with_actions: DiffResult
    ) -> None:
        """Dry-run does not call operators."""
        with (
            patch("popctl.cli.commands.apply.load_manifest", return_value=sample_manifest),
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch("popctl.operators.apt.command_exists", return_value=True),
            patch.object(
                __import__("popctl.core.diff", fromlist=["DiffEngine"]).DiffEngine,
                "compute_diff",
                return_value=diff_result_with_actions,
            ),
            patch("popctl.operators.apt.run_command") as mock_run,
        ):
            result = runner.invoke(app, ["apply", "--dry-run"])

        # run_command should not be called in dry-run
        mock_run.assert_not_called()
        assert result.exit_code == 0


class TestApplyConfirmation:
    """Tests for apply confirmation prompt."""

    def test_apply_prompts_for_confirmation(
        self, sample_manifest: Manifest, diff_result_with_actions: DiffResult
    ) -> None:
        """Apply prompts for confirmation by default."""
        with (
            patch("popctl.cli.commands.apply.load_manifest", return_value=sample_manifest),
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch.object(
                __import__("popctl.core.diff", fromlist=["DiffEngine"]).DiffEngine,
                "compute_diff",
                return_value=diff_result_with_actions,
            ),
        ):
            # Simulate user declining
            result = runner.invoke(app, ["apply"], input="n\n")

        # Should exit cleanly after user declines
        assert result.exit_code == 0
        assert "Aborted" in result.stdout

    def test_apply_yes_skips_confirmation(
        self, sample_manifest: Manifest, diff_result_with_actions: DiffResult
    ) -> None:
        """Apply --yes skips confirmation prompt."""
        with (
            patch("popctl.cli.commands.apply.load_manifest", return_value=sample_manifest),
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch("popctl.operators.apt.command_exists", return_value=True),
            patch.object(
                __import__("popctl.core.diff", fromlist=["DiffEngine"]).DiffEngine,
                "compute_diff",
                return_value=diff_result_with_actions,
            ),
            patch("popctl.operators.apt.run_command") as mock_run,
        ):
            mock_run.return_value = __import__(
                "popctl.utils.shell", fromlist=["CommandResult"]
            ).CommandResult(stdout="", stderr="", returncode=0)

            result = runner.invoke(app, ["apply", "--yes"])

        # Should have executed without prompting
        assert "Confirm" not in result.stdout or "y/N" not in result.stdout


class TestApplyExecution:
    """Tests for apply command execution."""

    def test_apply_executes_install_actions(self, sample_manifest: Manifest) -> None:
        """Apply executes install actions for missing packages."""
        missing_only = DiffResult(
            new=(),
            missing=(DiffEntry(name="vim", source="apt", diff_type=DiffType.MISSING),),
            extra=(),
        )

        with (
            patch("popctl.cli.commands.apply.load_manifest", return_value=sample_manifest),
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
            ).CommandResult(stdout="", stderr="", returncode=0)

            result = runner.invoke(app, ["apply", "--yes"])

        # Should have called apt-get install
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert "install" in args
        assert "vim" in args

    def test_apply_executes_remove_actions(self, sample_manifest: Manifest) -> None:
        """Apply executes remove actions for extra packages."""
        extra_only = DiffResult(
            new=(),
            missing=(),
            extra=(DiffEntry(name="bloatware", source="apt", diff_type=DiffType.EXTRA),),
        )

        with (
            patch("popctl.cli.commands.apply.load_manifest", return_value=sample_manifest),
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

            result = runner.invoke(app, ["apply", "--yes"])

        # Should have called apt-get remove
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert "remove" in args
        assert "bloatware" in args

    def test_apply_with_purge_uses_purge_command(self, sample_manifest: Manifest) -> None:
        """Apply --purge uses apt-get purge instead of remove."""
        extra_only = DiffResult(
            new=(),
            missing=(),
            extra=(DiffEntry(name="bloatware", source="apt", diff_type=DiffType.EXTRA),),
        )

        with (
            patch("popctl.cli.commands.apply.load_manifest", return_value=sample_manifest),
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

            result = runner.invoke(app, ["apply", "--yes", "--purge"])

        # Should have called apt-get purge
        args = mock_run.call_args[0][0]
        assert "purge" in args


class TestApplyNewPackages:
    """Tests for NEW packages handling."""

    def test_apply_ignores_new_packages(self, sample_manifest: Manifest) -> None:
        """Apply does NOT remove NEW packages (not in manifest)."""
        new_only = DiffResult(
            new=(DiffEntry(name="htop", source="apt", diff_type=DiffType.NEW),),
            missing=(),
            extra=(),
        )

        with (
            patch("popctl.cli.commands.apply.load_manifest", return_value=sample_manifest),
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch.object(
                __import__("popctl.core.diff", fromlist=["DiffEngine"]).DiffEngine,
                "compute_diff",
                return_value=new_only,
            ),
        ):
            result = runner.invoke(app, ["apply"])

        # Should report nothing to do since NEW packages are ignored
        assert result.exit_code == 0
        assert "Nothing to do" in result.stdout or "in sync" in result.stdout.lower()


class TestApplyProtectedPackages:
    """Tests for protected package handling."""

    def test_apply_does_not_remove_protected(self, sample_manifest: Manifest) -> None:
        """Apply does not create remove actions for protected packages."""
        # Even if somehow a protected package ends up in EXTRA, it should be filtered
        protected_extra = DiffResult(
            new=(),
            missing=(),
            extra=(
                DiffEntry(name="systemd", source="apt", diff_type=DiffType.EXTRA),
                DiffEntry(name="bloatware", source="apt", diff_type=DiffType.EXTRA),
            ),
        )

        with (
            patch("popctl.cli.commands.apply.load_manifest", return_value=sample_manifest),
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch("popctl.operators.apt.command_exists", return_value=True),
            patch.object(
                __import__("popctl.core.diff", fromlist=["DiffEngine"]).DiffEngine,
                "compute_diff",
                return_value=protected_extra,
            ),
            patch("popctl.operators.apt.run_command") as mock_run,
        ):
            mock_run.return_value = __import__(
                "popctl.utils.shell", fromlist=["CommandResult"]
            ).CommandResult(stdout="", stderr="", returncode=0)

            result = runner.invoke(app, ["apply", "--yes"])

        # Should only have bloatware in the remove call, not systemd
        args = mock_run.call_args[0][0]
        assert "bloatware" in args
        assert "systemd" not in args


class TestApplyScannerAvailability:
    """Tests for scanner availability handling."""

    def test_apply_no_scanners_available(self, sample_manifest: Manifest) -> None:
        """Apply fails gracefully when no scanners available."""
        with (
            patch("popctl.cli.commands.apply.load_manifest", return_value=sample_manifest),
            patch("popctl.scanners.apt.command_exists", return_value=False),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
        ):
            result = runner.invoke(app, ["apply"])

        assert result.exit_code == 1
        assert "not available" in (result.stdout + result.stderr).lower()


class TestApplySourceFilter:
    """Tests for apply --source option."""

    def test_apply_source_apt_only(self, sample_manifest: Manifest) -> None:
        """Apply --source apt only processes APT packages."""
        apt_result = DiffResult(
            new=(),
            missing=(DiffEntry(name="vim", source="apt", diff_type=DiffType.MISSING),),
            extra=(),
        )

        with (
            patch("popctl.cli.commands.apply.load_manifest", return_value=sample_manifest),
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=True),
            patch("popctl.operators.apt.command_exists", return_value=True),
            patch.object(
                __import__("popctl.core.diff", fromlist=["DiffEngine"]).DiffEngine,
                "compute_diff",
                return_value=apt_result,
            ) as mock_diff,
            patch("popctl.operators.apt.run_command") as mock_run,
        ):
            mock_run.return_value = __import__(
                "popctl.utils.shell", fromlist=["CommandResult"]
            ).CommandResult(stdout="", stderr="", returncode=0)

            result = runner.invoke(app, ["apply", "--yes", "--source", "apt"])

        assert result.exit_code == 0
        # Verify source filter was passed to diff
        mock_diff.assert_called_once()
        call_args = mock_diff.call_args
        assert call_args[1].get("source_filter") == "apt" or (
            len(call_args[0]) > 1 and call_args[0][1] == "apt"
        )


class TestApplyFailures:
    """Tests for apply command failure handling."""

    def test_apply_reports_failures(self, sample_manifest: Manifest) -> None:
        """Apply reports failed actions in results."""
        missing_only = DiffResult(
            new=(),
            missing=(DiffEntry(name="nonexistent-pkg", source="apt", diff_type=DiffType.MISSING),),
            extra=(),
        )

        with (
            patch("popctl.cli.commands.apply.load_manifest", return_value=sample_manifest),
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

            result = runner.invoke(app, ["apply", "--yes"])

        # Should exit with error code
        assert result.exit_code == 1
        # Should show failure in output
        assert "FAIL" in result.stdout or "failed" in result.stdout.lower()
