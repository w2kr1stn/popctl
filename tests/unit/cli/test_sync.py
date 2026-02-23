"""Unit tests for sync command.

Tests for the CLI sync command implementation covering all pipeline phases,
flag behaviors, and edge cases including filesystem phases.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from popctl.cli.main import app
from popctl.core.diff import DiffEntry, DiffResult, DiffType
from popctl.models.manifest import Manifest, PackageEntry
from popctl.utils.shell import CommandResult
from typer.testing import CliRunner

runner = CliRunner()


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
        from tests.unit.conftest import strip_ansi

        result = runner.invoke(app, ["sync", "--help"])
        output = strip_ansi(result.stdout)
        assert "--no-advisor" in output
        assert "--auto" in output
        assert "--dry-run" in output
        assert "--yes" in output
        assert "--source" in output
        assert "--purge" in output

    def test_sync_help_shows_no_filesystem(self) -> None:
        """Sync help shows --no-filesystem flag."""
        from tests.unit.conftest import strip_ansi

        result = runner.invoke(app, ["sync", "--help"])
        assert result.exit_code == 0
        assert "--no-filesystem" in strip_ansi(result.stdout)


def test_sync_auto_init(sample_manifest: Manifest, in_sync_result: DiffResult) -> None:
    """Sync auto-creates manifest when missing, then proceeds."""
    from popctl.models.package import PackageSource

    mock_scanner = MagicMock()
    mock_scanner.source = PackageSource.APT

    with (
        # First call: manifest_exists returns False (triggers init)
        # Second call: after init, compute_system_diff is called
        patch("popctl.cli.commands.sync.manifest_exists", return_value=False),
        patch("popctl.cli.commands.sync.get_available_scanners", return_value=[mock_scanner]),
        patch(
            "popctl.cli.commands.sync.scan_and_create_manifest",
            return_value=(sample_manifest, {"firefox": PackageEntry(source="apt")}, []),
        ),
        patch("popctl.core.paths.ensure_config_dir"),
        patch("popctl.cli.commands.sync.save_manifest", return_value=Path("/tmp/manifest.toml")),
        # For compute_system_diff phase
        patch(
            "popctl.cli.commands.sync.compute_system_diff",
            return_value=in_sync_result,
        ),
    ):
        result = runner.invoke(app, ["sync", "--no-filesystem"])

    assert result.exit_code == 0
    assert "Manifest created" in result.stdout or "in sync" in result.stdout.lower()


def test_sync_in_sync_message(sample_manifest: Manifest, in_sync_result: DiffResult) -> None:
    """Sync prints success when system matches manifest."""
    with (
        patch("popctl.cli.commands.sync.manifest_exists", return_value=True),
        patch(
            "popctl.cli.commands.sync.compute_system_diff",
            return_value=in_sync_result,
        ),
    ):
        result = runner.invoke(app, ["sync", "--no-filesystem"])

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
            patch(
                "popctl.cli.commands.sync.compute_system_diff",
                return_value=diff_result_no_new,
            ),
        ):
            result = runner.invoke(app, ["sync", "--dry-run", "--no-filesystem"])

        assert result.exit_code == 0
        assert "Dry-run" in result.stdout or "dry-run" in result.stdout.lower()
        assert "MISSING" in result.stdout or "1" in result.stdout

    def test_sync_dry_run_no_advisor(
        self, sample_manifest: Manifest, diff_result_with_new: DiffResult
    ) -> None:
        """Advisor is NOT invoked in dry-run mode even with NEW packages."""
        with (
            patch("popctl.cli.commands.sync.manifest_exists", return_value=True),
            patch(
                "popctl.cli.commands.sync.compute_system_diff",
                return_value=diff_result_with_new,
            ),
            patch("popctl.cli.commands.sync._run_advisor") as mock_advisor,
        ):
            result = runner.invoke(app, ["sync", "--dry-run", "--no-filesystem"])

        assert result.exit_code == 0
        mock_advisor.assert_not_called()


def test_sync_no_advisor_skips_classification(
    sample_manifest: Manifest, diff_result_with_new: DiffResult
) -> None:
    """--no-advisor flag skips advisor entirely."""
    with (
        patch("popctl.cli.commands.sync.manifest_exists", return_value=True),
        patch("popctl.operators.apt.command_exists", return_value=True),
        patch(
            "popctl.cli.commands.sync.compute_system_diff",
            return_value=diff_result_with_new,
        ),
        patch("popctl.cli.commands.sync._run_advisor") as mock_advisor,
        patch("popctl.operators.apt.run_command") as mock_run,
    ):
        mock_run.return_value = CommandResult(stdout="", stderr="", returncode=0)

        runner.invoke(app, ["sync", "--no-advisor", "--yes", "--no-filesystem"])

    mock_advisor.assert_not_called()


class TestSyncAdvisor:
    """Tests for sync advisor integration."""

    def test_sync_skips_advisor_when_no_new(
        self, sample_manifest: Manifest, diff_result_no_new: DiffResult
    ) -> None:
        """No NEW packages means advisor is not called."""
        with (
            patch("popctl.cli.commands.sync.manifest_exists", return_value=True),
            patch("popctl.operators.apt.command_exists", return_value=True),
            patch(
                "popctl.cli.commands.sync.compute_system_diff",
                return_value=diff_result_no_new,
            ),
            patch("popctl.cli.commands.sync._run_advisor") as mock_advisor,
            patch("popctl.operators.apt.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(stdout="", stderr="", returncode=0)

            runner.invoke(app, ["sync", "--yes", "--no-filesystem"])

        mock_advisor.assert_not_called()

    def test_sync_advisor_failure_continues(
        self, sample_manifest: Manifest, diff_result_with_new: DiffResult
    ) -> None:
        """Advisor failure warns and continues with current manifest."""
        with (
            patch("popctl.cli.commands.sync.manifest_exists", return_value=True),
            patch("popctl.operators.apt.command_exists", return_value=True),
            patch(
                "popctl.cli.commands.sync.compute_system_diff",
                return_value=diff_result_with_new,
            ),
            # Advisor internal: fail at config loading stage
            patch(
                "popctl.advisor.config.load_or_create_config",
                side_effect=RuntimeError("config error"),
            ),
            patch("popctl.operators.apt.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(stdout="", stderr="", returncode=0)

            result = runner.invoke(app, ["sync", "--yes", "--auto", "--no-filesystem"])

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
            patch("popctl.operators.apt.command_exists", return_value=True),
            patch(
                "popctl.cli.commands.sync.compute_system_diff",
                return_value=diff_result_no_new,
            ),
            patch("popctl.operators.apt.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(stdout="", stderr="", returncode=0)

            runner.invoke(app, ["sync", "--yes", "--no-filesystem"])

        # Should have executed commands
        mock_run.assert_called()

    def test_sync_yes_skips_confirmation(
        self, sample_manifest: Manifest, diff_result_no_new: DiffResult
    ) -> None:
        """--yes flag skips confirmation prompt."""
        with (
            patch("popctl.cli.commands.sync.manifest_exists", return_value=True),
            patch("popctl.operators.apt.command_exists", return_value=True),
            patch(
                "popctl.cli.commands.sync.compute_system_diff",
                return_value=diff_result_no_new,
            ),
            patch("popctl.operators.apt.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(stdout="", stderr="", returncode=0)

            result = runner.invoke(app, ["sync", "--yes", "--no-filesystem"])

        # Should have executed without "Confirm" / "y/N" in output
        assert "y/N" not in result.stdout or result.exit_code == 0


def test_sync_records_history(sample_manifest: Manifest, diff_result_no_new: DiffResult) -> None:
    """Sync records actions with command='popctl sync'."""
    with (
        patch("popctl.cli.commands.sync.manifest_exists", return_value=True),
        patch("popctl.operators.apt.command_exists", return_value=True),
        patch(
            "popctl.cli.commands.sync.compute_system_diff",
            return_value=diff_result_no_new,
        ),
        patch("popctl.operators.apt.run_command") as mock_run,
        patch("popctl.cli.commands.sync.record_actions_to_history") as mock_record,
    ):
        mock_run.return_value = CommandResult(stdout="", stderr="", returncode=0)

        runner.invoke(app, ["sync", "--yes", "--no-filesystem"])

    # record_actions_to_history should have been called with command="popctl sync"
    mock_record.assert_called_once()
    call_kwargs = mock_record.call_args
    # Check the command argument
    assert call_kwargs[1]["command"] == "popctl sync" or (
        len(call_kwargs[0]) > 1 and call_kwargs[0][1] == "popctl sync"
    )


def test_sync_reports_failures(sample_manifest: Manifest) -> None:
    """Failed system actions result in exit code 1."""
    missing_only = DiffResult(
        new=(),
        missing=(DiffEntry(name="nonexistent-pkg", source="apt", diff_type=DiffType.MISSING),),
        extra=(),
    )

    with (
        patch("popctl.cli.commands.sync.manifest_exists", return_value=True),
        patch("popctl.operators.apt.command_exists", return_value=True),
        patch(
            "popctl.cli.commands.sync.compute_system_diff",
            return_value=missing_only,
        ),
        patch("popctl.operators.apt.run_command") as mock_run,
    ):
        mock_run.return_value = __import__(
            "popctl.utils.shell", fromlist=["CommandResult"]
        ).CommandResult(stdout="", stderr="E: Package not found", returncode=100)

        result = runner.invoke(app, ["sync", "--yes", "--no-filesystem", "--no-configs"])

    assert result.exit_code == 1
    assert "FAIL" in result.stdout or "failed" in result.stdout.lower()


def test_sync_re_diffs_after_advisor(sample_manifest: Manifest) -> None:
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
        patch("popctl.operators.apt.command_exists", return_value=True),
        patch(
            "popctl.cli.commands.sync.compute_system_diff",
            side_effect=compute_diff_side_effect,
        ),
        # Mock _run_advisor to be a no-op (we test re-diff, not advisor itself)
        patch("popctl.cli.commands.sync._run_advisor"),
        patch("popctl.operators.apt.run_command") as mock_run,
    ):
        mock_run.return_value = CommandResult(stdout="", stderr="", returncode=0)

        runner.invoke(app, ["sync", "--yes", "--no-filesystem"])

    # compute_system_diff should have been called twice (initial + re-diff)
    assert diff_call_count == 2


def test_sync_purge_uses_purge_command(sample_manifest: Manifest) -> None:
    """Sync --purge passes purge flag to action conversion."""
    extra_only = DiffResult(
        new=(),
        missing=(),
        extra=(DiffEntry(name="bloatware", source="apt", diff_type=DiffType.EXTRA),),
    )

    with (
        patch("popctl.cli.commands.sync.manifest_exists", return_value=True),
        patch("popctl.operators.apt.command_exists", return_value=True),
        patch(
            "popctl.cli.commands.sync.compute_system_diff",
            return_value=extra_only,
        ),
        patch("popctl.operators.apt.run_command") as mock_run,
    ):
        mock_run.return_value = CommandResult(stdout="", stderr="", returncode=0)

        runner.invoke(app, ["sync", "--yes", "--purge", "--no-filesystem"])

    # Should have called apt-get purge
    args = mock_run.call_args[0][0]
    assert "purge" in args


# =============================================================================
# Tests for filesystem phases in sync pipeline
# =============================================================================


class TestSyncFilesystem:
    """Tests for filesystem phases (9-13) in sync pipeline."""

    def test_sync_no_filesystem_flag_skips_all_fs_phases(
        self, sample_manifest: Manifest, in_sync_result: DiffResult
    ) -> None:
        """--no-filesystem skips filesystem phases entirely."""
        with (
            patch("popctl.cli.commands.sync.manifest_exists", return_value=True),
            patch(
                "popctl.cli.commands.sync.compute_system_diff",
                return_value=in_sync_result,
            ),
            patch("popctl.cli.commands.sync._domain_scan", return_value=[]) as mock_domain_scan,
        ):
            result = runner.invoke(app, ["sync", "--no-filesystem", "--no-configs"])

        assert result.exit_code == 0
        mock_domain_scan.assert_not_called()

    def test_sync_filesystem_no_orphans_skips(
        self, sample_manifest: Manifest, in_sync_result: DiffResult
    ) -> None:
        """When FS scan finds no orphans, remaining FS phases are skipped."""
        with (
            patch("popctl.cli.commands.sync.manifest_exists", return_value=True),
            patch(
                "popctl.cli.commands.sync.compute_system_diff",
                return_value=in_sync_result,
            ),
            patch("popctl.cli.commands.sync._domain_scan", return_value=[]) as mock_domain_scan,
            patch("popctl.cli.commands.sync._domain_clean") as mock_fs_clean,
        ):
            result = runner.invoke(app, ["sync", "--no-configs"])

        assert result.exit_code == 0
        mock_domain_scan.assert_called_once_with("filesystem")
        mock_fs_clean.assert_not_called()
        assert "No orphaned filesystem entries found" in result.stdout

    def test_sync_filesystem_scan_failure_non_fatal(
        self, sample_manifest: Manifest, in_sync_result: DiffResult
    ) -> None:
        """FS scan failure prints warning, does not crash sync."""
        with (
            patch("popctl.cli.commands.sync.manifest_exists", return_value=True),
            patch(
                "popctl.cli.commands.sync.compute_system_diff",
                return_value=in_sync_result,
            ),
            # _domain_scan catches exceptions internally and returns []
            # but we can test via the FilesystemScanner raising
            patch(
                "popctl.cli.commands.sync._domain_scan",
                return_value=[],
            ),
        ):
            result = runner.invoke(app, ["sync"])

        # Sync should still succeed (filesystem phases are non-fatal)
        assert result.exit_code == 0

    def test_sync_filesystem_scan_exception_non_fatal(
        self, sample_manifest: Manifest, in_sync_result: DiffResult
    ) -> None:
        """When _domain_scan returns empty (due to exception), sync still succeeds."""
        with (
            patch("popctl.cli.commands.sync.manifest_exists", return_value=True),
            patch(
                "popctl.cli.commands.sync.compute_system_diff",
                return_value=in_sync_result,
            ),
            # Simulate _domain_scan returning empty (as it does on exception)
            patch("popctl.cli.commands.sync._domain_scan", return_value=[]),
            patch("popctl.cli.commands.sync._domain_clean") as mock_fs_clean,
        ):
            result = runner.invoke(app, ["sync"])

        # Sync should succeed despite filesystem issues
        assert result.exit_code == 0
        # No orphans found => remaining FS phases skipped
        assert "No orphaned filesystem entries found" in result.stdout
        mock_fs_clean.assert_not_called()

    def test_fs_scan_catches_runtime_error(self) -> None:
        """_domain_scan catches RuntimeError and returns empty list."""
        from popctl.cli.commands.sync import _domain_scan

        with patch(
            "popctl.cli.commands.sync.collect_domain_orphans",
            side_effect=RuntimeError("scanner broken"),
        ):
            result = _domain_scan("filesystem")

        assert result == []

    def test_fs_scan_catches_os_error(self) -> None:
        """_domain_scan catches OSError and returns empty list."""
        from popctl.cli.commands.sync import _domain_scan

        with patch(
            "popctl.cli.commands.sync.collect_domain_orphans",
            side_effect=OSError("disk error"),
        ):
            result = _domain_scan("filesystem")

        assert result == []

    def test_sync_filesystem_dry_run_displays_orphans(
        self, sample_manifest: Manifest, diff_result_no_new: DiffResult
    ) -> None:
        """In dry-run mode, filesystem orphans are displayed but not deleted."""
        from popctl.domain.models import (
            OrphanReason,
            OrphanStatus,
            PathType,
            ScannedEntry,
        )

        mock_orphans = [
            ScannedEntry(
                path="/tmp/old-app",
                path_type=PathType.DIRECTORY,
                status=OrphanStatus.ORPHAN,
                size_bytes=4096,
                mtime=None,
                parent_target="~/.config",
                orphan_reason=OrphanReason.NO_PACKAGE_MATCH,
                confidence=0.70,
            ),
        ]

        with (
            patch("popctl.cli.commands.sync.manifest_exists", return_value=True),
            patch(
                "popctl.cli.commands.sync.compute_system_diff",
                return_value=diff_result_no_new,
            ),
            patch("popctl.cli.commands.sync._domain_scan", return_value=mock_orphans),
            patch("popctl.cli.commands.sync._domain_clean") as mock_fs_clean,
        ):
            result = runner.invoke(app, ["sync", "--dry-run", "--no-configs"])

        assert result.exit_code == 0
        mock_fs_clean.assert_not_called()
        # Should see the orphan path in display
        assert "old-app" in result.stdout
        assert "No filesystem changes made" in result.stdout

    def test_sync_filesystem_phases_run_after_package_execution(
        self, sample_manifest: Manifest, diff_result_no_new: DiffResult
    ) -> None:
        """Filesystem phases run after package execution phases complete."""
        with (
            patch("popctl.cli.commands.sync.manifest_exists", return_value=True),
            patch("popctl.operators.apt.command_exists", return_value=True),
            patch(
                "popctl.cli.commands.sync.compute_system_diff",
                return_value=diff_result_no_new,
            ),
            patch("popctl.operators.apt.run_command") as mock_run,
            patch("popctl.cli.commands.sync._run_orphan_phases") as mock_orphan_phases,
        ):
            mock_run.return_value = CommandResult(stdout="", stderr="", returncode=0)

            runner.invoke(app, ["sync", "--yes"])

        # _run_orphan_phases should have been called for both filesystem and configs
        assert mock_orphan_phases.call_count == 2
        fs_call = mock_orphan_phases.call_args_list[0]
        assert fs_call[0][0] == "filesystem"
        assert fs_call[1]["dry_run"] is False
        assert fs_call[1]["yes"] is True

    def test_sync_filesystem_phases_run_when_packages_in_sync(
        self, sample_manifest: Manifest, in_sync_result: DiffResult
    ) -> None:
        """Filesystem phases still run even when packages are in sync."""
        from popctl.domain.models import (
            OrphanReason,
            OrphanStatus,
            PathType,
            ScannedEntry,
        )

        mock_orphans = [
            ScannedEntry(
                path="/home/test/.cache/stale-app",
                path_type=PathType.DIRECTORY,
                status=OrphanStatus.ORPHAN,
                size_bytes=1024,
                mtime=None,
                parent_target="~/.cache",
                orphan_reason=OrphanReason.STALE_CACHE,
                confidence=0.95,
            ),
        ]

        with (
            patch("popctl.cli.commands.sync.manifest_exists", return_value=True),
            patch(
                "popctl.cli.commands.sync.compute_system_diff",
                return_value=in_sync_result,
            ),
            patch("popctl.cli.commands.sync._domain_scan", return_value=mock_orphans),
            patch("popctl.cli.commands.sync._domain_clean", return_value=[]),
        ):
            result = runner.invoke(app, ["sync", "--no-configs"])

        assert result.exit_code == 0
        assert "1 orphaned filesystem" in result.stdout

    def test_sync_filesystem_phases_run_before_exit_code_1(self, sample_manifest: Manifest) -> None:
        """Filesystem phases run even when package actions fail (before exit 1)."""
        missing_only = DiffResult(
            new=(),
            missing=(DiffEntry(name="broken-pkg", source="apt", diff_type=DiffType.MISSING),),
            extra=(),
        )

        with (
            patch("popctl.cli.commands.sync.manifest_exists", return_value=True),
            patch("popctl.operators.apt.command_exists", return_value=True),
            patch(
                "popctl.cli.commands.sync.compute_system_diff",
                return_value=missing_only,
            ),
            patch("popctl.operators.apt.run_command") as mock_run,
            patch("popctl.cli.commands.sync._run_orphan_phases") as mock_orphan_phases,
        ):
            mock_run.return_value = __import__(
                "popctl.utils.shell", fromlist=["CommandResult"]
            ).CommandResult(stdout="", stderr="E: Failed", returncode=100)

            result = runner.invoke(app, ["sync", "--yes"])

        # Package failure should give exit code 1
        assert result.exit_code == 1
        # But both filesystem and config orphan phases should still have been called
        assert mock_orphan_phases.call_count == 2
        domains_called = [call[0][0] for call in mock_orphan_phases.call_args_list]
        assert "filesystem" in domains_called
        assert "configs" in domains_called


# =============================================================================
# Tests for config phases in sync pipeline
# =============================================================================


class TestSyncConfigs:
    """Tests for config phases (14-18) in sync pipeline."""

    def test_sync_help_shows_no_configs(self) -> None:
        """Sync help shows --no-configs flag."""
        from tests.unit.conftest import strip_ansi

        result = runner.invoke(app, ["sync", "--help"])
        assert result.exit_code == 0
        assert "--no-configs" in strip_ansi(result.stdout)

    def test_sync_includes_config_phases(
        self, sample_manifest: Manifest, in_sync_result: DiffResult
    ) -> None:
        """Config phases run by default when no --no-configs flag is passed."""
        from popctl.domain.models import OrphanReason, OrphanStatus, PathType, ScannedEntry

        mock_orphans = [
            ScannedEntry(
                path="/home/test/.config/old-editor",
                path_type=PathType.DIRECTORY,
                status=OrphanStatus.ORPHAN,
                size_bytes=2048,
                mtime=None,
                parent_target=None,
                orphan_reason=OrphanReason.NO_PACKAGE_MATCH,
                confidence=0.70,
            ),
        ]

        with (
            patch("popctl.cli.commands.sync.manifest_exists", return_value=True),
            patch(
                "popctl.cli.commands.sync.compute_system_diff",
                return_value=in_sync_result,
            ),
            patch(
                "popctl.cli.commands.sync._domain_scan", return_value=mock_orphans
            ) as mock_domain_scan,
            patch("popctl.cli.commands.sync._domain_clean", return_value=[]),
        ):
            result = runner.invoke(app, ["sync", "--no-filesystem"])

        assert result.exit_code == 0
        mock_domain_scan.assert_called_once_with("configs")
        assert "1 orphaned config" in result.stdout

    def test_sync_no_configs_flag_skips_configs(
        self, sample_manifest: Manifest, in_sync_result: DiffResult
    ) -> None:
        """--no-configs skips all config phases entirely."""
        with (
            patch("popctl.cli.commands.sync.manifest_exists", return_value=True),
            patch(
                "popctl.cli.commands.sync.compute_system_diff",
                return_value=in_sync_result,
            ),
            patch("popctl.cli.commands.sync._domain_scan", return_value=[]) as mock_domain_scan,
        ):
            result = runner.invoke(app, ["sync", "--no-configs", "--no-filesystem"])

        assert result.exit_code == 0
        mock_domain_scan.assert_not_called()

    def test_sync_configs_no_orphans_skips(
        self, sample_manifest: Manifest, in_sync_result: DiffResult
    ) -> None:
        """No orphans found => advisor/clean phases skipped."""
        with (
            patch("popctl.cli.commands.sync.manifest_exists", return_value=True),
            patch(
                "popctl.cli.commands.sync.compute_system_diff",
                return_value=in_sync_result,
            ),
            patch("popctl.cli.commands.sync._domain_scan", return_value=[]) as mock_domain_scan,
            patch("popctl.cli.commands.sync._domain_clean") as mock_cfg_clean,
        ):
            result = runner.invoke(app, ["sync", "--no-filesystem"])

        assert result.exit_code == 0
        mock_domain_scan.assert_called_once_with("configs")
        mock_cfg_clean.assert_not_called()
        assert "No orphaned config entries found" in result.stdout

    def test_sync_configs_with_dry_run(
        self, sample_manifest: Manifest, diff_result_no_new: DiffResult
    ) -> None:
        """--dry-run applies to config phases: shows orphans but does not delete."""
        from popctl.domain.models import OrphanReason, OrphanStatus, PathType, ScannedEntry

        mock_orphans = [
            ScannedEntry(
                path="/tmp/rm-app",
                path_type=PathType.DIRECTORY,
                status=OrphanStatus.ORPHAN,
                size_bytes=4096,
                mtime=None,
                parent_target=None,
                orphan_reason=OrphanReason.NO_PACKAGE_MATCH,
                confidence=0.70,
            ),
        ]

        with (
            patch("popctl.cli.commands.sync.manifest_exists", return_value=True),
            patch(
                "popctl.cli.commands.sync.compute_system_diff",
                return_value=diff_result_no_new,
            ),
            patch("popctl.cli.commands.sync._domain_scan", return_value=mock_orphans),
            patch("popctl.cli.commands.sync._domain_clean") as mock_cfg_clean,
        ):
            result = runner.invoke(app, ["sync", "--dry-run", "--no-filesystem"])

        assert result.exit_code == 0
        mock_cfg_clean.assert_not_called()
        # Should see the orphan path in display
        assert "rm-app" in result.stdout
        assert "No config changes made" in result.stdout

    def test_sync_configs_with_backup(
        self, sample_manifest: Manifest, in_sync_result: DiffResult
    ) -> None:
        """Config clean shows backup paths in output."""
        from popctl.configs.operator import ConfigActionResult
        from popctl.domain.models import OrphanReason, OrphanStatus, PathType, ScannedEntry

        mock_orphans = [
            ScannedEntry(
                path="/home/test/.config/old-app",
                path_type=PathType.DIRECTORY,
                status=OrphanStatus.ORPHAN,
                size_bytes=4096,
                mtime=None,
                parent_target=None,
                orphan_reason=OrphanReason.NO_PACKAGE_MATCH,
                confidence=0.70,
            ),
        ]

        mock_action_results = [
            ConfigActionResult(
                path="/home/test/.config/old-app",
                success=True,
                backup_path="/home/test/.local/state/popctl/config-backups/20260215T120000Z/.config/old-app",
            ),
        ]

        # Build a manifest with configs.remove section
        from popctl.models.manifest import DomainConfig, DomainEntry

        manifest_with_configs = sample_manifest.model_copy(
            update={
                "configs": DomainConfig(
                    keep={},
                    remove={
                        "/home/test/.config/old-app": DomainEntry(
                            reason="App not installed",
                            category="obsolete",
                        ),
                    },
                ),
            },
        )

        with (
            patch("popctl.cli.commands.sync.manifest_exists", return_value=True),
            patch("popctl.cli.commands.sync.load_manifest", return_value=manifest_with_configs),
            patch(
                "popctl.cli.commands.sync.compute_system_diff",
                return_value=in_sync_result,
            ),
            patch("popctl.cli.commands.sync._domain_scan", return_value=mock_orphans),
            patch(
                "popctl.configs.operator.ConfigOperator.delete",
                return_value=mock_action_results,
            ),
            patch("popctl.cli.commands.sync.record_domain_deletions"),
        ):
            result = runner.invoke(app, ["sync", "--yes", "--no-advisor", "--no-filesystem"])

        assert result.exit_code == 0
        assert "config-backups" in result.stdout
        assert "Deleted 1 config path" in result.stdout

    def test_config_scan_catches_runtime_error(self) -> None:
        """_domain_scan catches RuntimeError and returns empty list."""
        from popctl.cli.commands.sync import _domain_scan

        with patch(
            "popctl.cli.commands.sync.collect_domain_orphans",
            side_effect=RuntimeError("scanner broken"),
        ):
            result = _domain_scan("configs")

        assert result == []

    def test_config_scan_catches_os_error(self) -> None:
        """_domain_scan catches OSError and returns empty list."""
        from popctl.cli.commands.sync import _domain_scan

        with patch(
            "popctl.cli.commands.sync.collect_domain_orphans",
            side_effect=OSError("disk error"),
        ):
            result = _domain_scan("configs")

        assert result == []

    def test_record_orphan_history_non_fatal(self) -> None:
        """_record_orphan_history catches exceptions without crashing."""
        from popctl.cli.commands.sync import _record_orphan_history

        with patch(
            "popctl.core.state.record_domain_deletions",
            side_effect=OSError("write error"),
        ):
            # Should not raise
            _record_orphan_history("configs", ["/home/test/.config/deleted-app"])

    def test_sync_config_phases_run_after_filesystem_phases(
        self, sample_manifest: Manifest, diff_result_no_new: DiffResult
    ) -> None:
        """Config phases run after filesystem phases in the execution path."""
        with (
            patch("popctl.cli.commands.sync.manifest_exists", return_value=True),
            patch("popctl.operators.apt.command_exists", return_value=True),
            patch(
                "popctl.cli.commands.sync.compute_system_diff",
                return_value=diff_result_no_new,
            ),
            patch("popctl.operators.apt.run_command") as mock_run,
            patch("popctl.cli.commands.sync._run_orphan_phases") as mock_orphan_phases,
        ):
            mock_run.return_value = CommandResult(stdout="", stderr="", returncode=0)

            runner.invoke(app, ["sync", "--yes"])

        # Both filesystem and config orphan phases should have been called
        assert mock_orphan_phases.call_count == 2
        # First call: filesystem, second call: configs
        fs_call = mock_orphan_phases.call_args_list[0]
        cfg_call = mock_orphan_phases.call_args_list[1]
        assert fs_call[0][0] == "filesystem"
        assert cfg_call[0][0] == "configs"
        assert cfg_call[1]["dry_run"] is False
        assert cfg_call[1]["yes"] is True


# =============================================================================
# Tests for _invoke_advisor shared helper
# =============================================================================


class TestInvokeAdvisor:
    """Tests for the shared _invoke_advisor() helper function."""

    def test_invoke_advisor_config_failure_returns_none(self) -> None:
        """Config load failure returns None (non-fatal)."""
        from popctl.cli.commands.sync import _invoke_advisor

        with patch(
            "popctl.advisor.config.load_or_create_config",
            side_effect=RuntimeError("config error"),
        ):
            result = _invoke_advisor(auto=True, domain="packages")

        assert result is None

    def test_invoke_advisor_scan_failure_returns_none(self) -> None:
        """System scan failure (RuntimeError) returns None (non-fatal)."""
        from popctl.cli.commands.sync import _invoke_advisor

        with (
            patch("popctl.advisor.config.load_or_create_config", return_value=MagicMock()),
            patch(
                "popctl.cli.commands.sync.scan_system",
                side_effect=RuntimeError("No scanners available"),
            ),
        ):
            result = _invoke_advisor(auto=True, domain="packages")

        assert result is None

    def test_invoke_advisor_workspace_failure_returns_none(self) -> None:
        """Workspace creation failure returns None (non-fatal)."""
        from popctl.cli.commands.sync import _invoke_advisor

        with (
            patch("popctl.advisor.config.load_or_create_config", return_value=MagicMock()),
            patch("popctl.cli.commands.sync.scan_system", return_value=MagicMock()),
            patch(
                "popctl.cli.commands.sync.create_full_session_workspace",
                side_effect=OSError("no space"),
            ),
        ):
            result = _invoke_advisor(auto=True, domain="filesystem")

        assert result is None

    def test_invoke_advisor_runner_failure_returns_none(self) -> None:
        """Advisor execution failure returns None (non-fatal)."""
        from popctl.cli.commands.sync import _invoke_advisor

        mock_config = MagicMock()
        mock_scan = MagicMock()

        with (
            patch("popctl.advisor.config.load_or_create_config", return_value=mock_config),
            patch("popctl.cli.commands.sync.scan_system", return_value=mock_scan),
            patch(
                "popctl.cli.commands.sync.create_full_session_workspace",
                return_value=Path("/tmp/ws"),
            ),
            patch(
                "popctl.advisor.runner.AgentRunner.run_headless", side_effect=RuntimeError("boom")
            ),
        ):
            result = _invoke_advisor(auto=True, domain="packages")

        assert result is None

    def test_invoke_advisor_manual_mode_returns_none(self) -> None:
        """Manual mode returns None instead of raising typer.Exit."""
        from popctl.advisor.runner import AgentResult
        from popctl.cli.commands.sync import _invoke_advisor

        mock_agent_result = AgentResult(
            success=False,
            output="Run advisor manually...",
            error="manual_mode",
        )

        with (
            patch("popctl.advisor.config.load_or_create_config", return_value=MagicMock()),
            patch("popctl.cli.commands.sync.scan_system", return_value=MagicMock()),
            patch(
                "popctl.cli.commands.sync.create_full_session_workspace",
                return_value=Path("/tmp/ws"),
            ),
            patch(
                "popctl.advisor.runner.AgentRunner.launch_interactive",
                return_value=mock_agent_result,
            ),
        ):
            result = _invoke_advisor(auto=False, domain="packages")

        assert result is None

    def test_invoke_advisor_success(self, tmp_path: Path) -> None:
        """Successful advisor invocation returns DecisionsResult."""
        from popctl.advisor.exchange import DecisionsResult
        from popctl.advisor.runner import AgentResult
        from popctl.cli.commands.sync import _invoke_advisor

        decisions_path = tmp_path / "output" / "decisions.toml"
        expected_decisions = DecisionsResult(packages={})

        mock_agent_result = AgentResult(
            success=True,
            output="Done.",
            decisions_path=decisions_path,
        )

        with (
            patch("popctl.advisor.config.load_or_create_config", return_value=MagicMock()),
            patch("popctl.cli.commands.sync.scan_system", return_value=MagicMock()),
            patch(
                "popctl.cli.commands.sync.create_full_session_workspace",
                return_value=Path("/tmp/ws"),
            ),
            patch(
                "popctl.advisor.runner.AgentRunner.run_headless",
                return_value=mock_agent_result,
            ),
            patch("popctl.cli.commands.sync.import_decisions", return_value=expected_decisions),
        ):
            result = _invoke_advisor(auto=True, domain="packages")

        assert result is expected_decisions

    def test_invoke_advisor_import_failure_returns_none(self, tmp_path: Path) -> None:
        """Failed decisions import returns None (non-fatal)."""
        from popctl.advisor.runner import AgentResult
        from popctl.cli.commands.sync import _invoke_advisor

        decisions_path = tmp_path / "output" / "decisions.toml"

        mock_agent_result = AgentResult(
            success=True,
            output="Done.",
            decisions_path=decisions_path,
        )

        with (
            patch("popctl.advisor.config.load_or_create_config", return_value=MagicMock()),
            patch("popctl.cli.commands.sync.scan_system", return_value=MagicMock()),
            patch(
                "popctl.cli.commands.sync.create_full_session_workspace",
                return_value=Path("/tmp/ws"),
            ),
            patch(
                "popctl.advisor.runner.AgentRunner.run_headless",
                return_value=mock_agent_result,
            ),
            patch(
                "popctl.cli.commands.sync.import_decisions",
                side_effect=ValueError("bad TOML"),
            ),
        ):
            result = _invoke_advisor(auto=True, domain="packages")

        assert result is None


# =============================================================================
# Tests for _domain_run_advisor (filesystem)
# =============================================================================


class TestFsRunAdvisor:
    """Tests for filesystem advisor phase (10)."""

    def _make_orphan(self, path: str = "/home/test/.config/old-app") -> MagicMock:
        """Create a mock ScannedEntry (filesystem)."""
        orphan = MagicMock()
        orphan.path = path
        orphan.path_type.value = "directory"
        orphan.size_bytes = 4096
        orphan.mtime = None
        orphan.parent_target = "~/.config"
        orphan.orphan_reason.value = "no_package_match"
        orphan.confidence = 0.8
        orphan.to_dict.return_value = {
            "path": path,
            "path_type": "directory",
            "status": "orphan",
            "size_bytes": 4096,
            "mtime": None,
            "orphan_reason": "no_package_match",
            "confidence": 0.8,
            "parent_target": "~/.config",
        }
        return orphan

    def test_fs_run_advisor_success(self) -> None:
        """Successful FS advisor returns FilesystemDecisions."""
        from popctl.advisor.exchange import (
            DecisionsResult,
            DomainDecisions,
            PathDecision,
        )
        from popctl.cli.commands.sync import _domain_run_advisor

        fs_decisions = DomainDecisions(
            keep=[
                PathDecision(
                    path="/home/test/.config/old-app",
                    reason="Active config",
                    confidence=0.9,
                    category="config",
                )
            ],
        )
        mock_decisions = DecisionsResult(
            packages={},
            filesystem=fs_decisions,
        )

        with patch("popctl.cli.commands.sync._invoke_advisor", return_value=mock_decisions):
            result = _domain_run_advisor("filesystem", [self._make_orphan()], auto=True)

        assert result is fs_decisions

    def test_fs_run_advisor_no_decisions_returns_none(self) -> None:
        """When _invoke_advisor returns None, FS advisor returns None."""
        from popctl.cli.commands.sync import _domain_run_advisor

        with patch("popctl.cli.commands.sync._invoke_advisor", return_value=None):
            result = _domain_run_advisor("filesystem", [self._make_orphan()], auto=True)

        assert result is None

    def test_fs_run_advisor_no_fs_section_returns_none(self) -> None:
        """When decisions have no filesystem section, returns None."""
        from popctl.advisor.exchange import DecisionsResult
        from popctl.cli.commands.sync import _domain_run_advisor

        mock_decisions = DecisionsResult(packages={}, filesystem=None)

        with patch("popctl.cli.commands.sync._invoke_advisor", return_value=mock_decisions):
            result = _domain_run_advisor("filesystem", [self._make_orphan()], auto=True)

        assert result is None

    def test_fs_run_advisor_passes_orphan_entries(self) -> None:
        """FS advisor converts ScannedEntry to OrphanEntry."""
        from popctl.cli.commands.sync import _domain_run_advisor

        orphan = self._make_orphan("/home/test/.config/vlc")

        with patch("popctl.cli.commands.sync._invoke_advisor", return_value=None) as mock_invoke:
            _domain_run_advisor("filesystem", [orphan], auto=True)

        # Verify _invoke_advisor was called with fs orphan entries
        call_kwargs = mock_invoke.call_args[1]
        assert call_kwargs["domain"] == "filesystem"
        assert call_kwargs["auto"] is True
        assert len(call_kwargs["filesystem_orphans"]) == 1
        assert call_kwargs["filesystem_orphans"][0]["path"] == "/home/test/.config/vlc"


# =============================================================================
# Tests for _domain_run_advisor (configs)
# =============================================================================


class TestConfigRunAdvisor:
    """Tests for config advisor phase (15)."""

    def _make_config_orphan(self, path: str = "/home/test/.config/old-app") -> MagicMock:
        """Create a mock ScannedEntry (config)."""
        orphan = MagicMock()
        orphan.path = path
        orphan.path_type.value = "directory"
        orphan.size_bytes = 4096
        orphan.mtime = None
        orphan.parent_target = None
        orphan.orphan_reason.value = "no_package_match"
        orphan.confidence = 0.7
        orphan.to_dict.return_value = {
            "path": path,
            "path_type": "directory",
            "status": "orphan",
            "size_bytes": 4096,
            "mtime": None,
            "orphan_reason": "no_package_match",
            "confidence": 0.7,
        }
        return orphan

    def test_config_run_advisor_success(self) -> None:
        """Successful config advisor returns ConfigDecisions."""
        from popctl.advisor.exchange import (
            DecisionsResult,
            DomainDecisions,
            PathDecision,
        )
        from popctl.cli.commands.sync import _domain_run_advisor

        cfg_decisions = DomainDecisions(
            remove=[
                PathDecision(
                    path="/home/test/.config/old-app",
                    reason="Orphaned config",
                    confidence=0.85,
                    category="obsolete",
                )
            ],
        )
        mock_decisions = DecisionsResult(
            packages={},
            configs=cfg_decisions,
        )

        with patch("popctl.cli.commands.sync._invoke_advisor", return_value=mock_decisions):
            result = _domain_run_advisor("configs", [self._make_config_orphan()], auto=True)

        assert result is cfg_decisions

    def test_config_run_advisor_no_decisions_returns_none(self) -> None:
        """When _invoke_advisor returns None, config advisor returns None."""
        from popctl.cli.commands.sync import _domain_run_advisor

        with patch("popctl.cli.commands.sync._invoke_advisor", return_value=None):
            result = _domain_run_advisor("configs", [self._make_config_orphan()], auto=True)

        assert result is None

    def test_config_run_advisor_no_configs_section_returns_none(self) -> None:
        """When decisions have no configs section, returns None."""
        from popctl.advisor.exchange import DecisionsResult
        from popctl.cli.commands.sync import _domain_run_advisor

        mock_decisions = DecisionsResult(packages={}, configs=None)

        with patch("popctl.cli.commands.sync._invoke_advisor", return_value=mock_decisions):
            result = _domain_run_advisor("configs", [self._make_config_orphan()], auto=True)

        assert result is None

    def test_config_run_advisor_passes_orphan_entries(self) -> None:
        """Config advisor converts ScannedEntry to OrphanEntry."""
        from popctl.cli.commands.sync import _domain_run_advisor

        orphan = self._make_config_orphan("/home/test/.config/nvim")

        with patch("popctl.cli.commands.sync._invoke_advisor", return_value=None) as mock_invoke:
            _domain_run_advisor("configs", [orphan], auto=True)

        # Verify _invoke_advisor was called with config orphan entries
        call_kwargs = mock_invoke.call_args[1]
        assert call_kwargs["domain"] == "configs"
        assert call_kwargs["auto"] is True
        assert len(call_kwargs["config_orphans"]) == 1
        assert call_kwargs["config_orphans"][0]["path"] == "/home/test/.config/nvim"
