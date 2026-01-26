"""Unit tests for diff command.

Tests for the CLI diff command implementation.
"""

import json
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
def diff_result_with_changes() -> DiffResult:
    """Create a diff result with various changes."""
    return DiffResult(
        new=(DiffEntry(name="htop", source="apt", diff_type=DiffType.NEW, version="3.2.2"),),
        missing=(DiffEntry(name="vim", source="apt", diff_type=DiffType.MISSING),),
        extra=(DiffEntry(name="bloatware", source="apt", diff_type=DiffType.EXTRA, version="1.0"),),
    )


class TestDiffCommandHelp:
    """Tests for diff command help."""

    def test_diff_help(self) -> None:
        """Diff command shows help."""
        result = runner.invoke(app, ["diff", "--help"])
        assert result.exit_code == 0
        assert "Compare manifest with current system state" in result.stdout

    def test_diff_help_shows_options(self) -> None:
        """Diff help shows all available options."""
        result = runner.invoke(app, ["diff", "--help"])
        assert "--source" in result.stdout
        assert "--brief" in result.stdout
        assert "--json" in result.stdout


class TestDiffNoManifest:
    """Tests for diff command when no manifest exists."""

    def test_diff_no_manifest_error(self, tmp_path: Path) -> None:
        """Diff shows error when manifest doesn't exist."""
        from popctl.core.manifest import ManifestNotFoundError

        with patch(
            "popctl.core.manifest.load_manifest",
            side_effect=ManifestNotFoundError("Manifest not found"),
        ):
            result = runner.invoke(app, ["diff"])

        assert result.exit_code == 1
        assert "Manifest not found" in (result.stdout + result.stderr)
        assert "popctl init" in (result.stdout + result.stderr)


class TestDiffInSync:
    """Tests for diff command when system is in sync."""

    def test_diff_in_sync_message(
        self, sample_manifest: Manifest, in_sync_result: DiffResult
    ) -> None:
        """Diff shows success message when in sync."""
        with (
            patch("popctl.core.manifest.load_manifest", return_value=sample_manifest),
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch.object(
                __import__("popctl.core.diff", fromlist=["DiffEngine"]).DiffEngine,
                "compute_diff",
                return_value=in_sync_result,
            ),
        ):
            result = runner.invoke(app, ["diff"])

        assert result.exit_code == 0
        assert "in sync" in result.stdout.lower()


class TestDiffWithChanges:
    """Tests for diff command when there are changes."""

    def test_diff_shows_table(
        self, sample_manifest: Manifest, diff_result_with_changes: DiffResult
    ) -> None:
        """Diff shows table with changes."""
        with (
            patch("popctl.core.manifest.load_manifest", return_value=sample_manifest),
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch.object(
                __import__("popctl.core.diff", fromlist=["DiffEngine"]).DiffEngine,
                "compute_diff",
                return_value=diff_result_with_changes,
            ),
        ):
            result = runner.invoke(app, ["diff"])

        assert result.exit_code == 0
        assert "htop" in result.stdout
        assert "vim" in result.stdout
        assert "bloatware" in result.stdout

    def test_diff_shows_summary(
        self, sample_manifest: Manifest, diff_result_with_changes: DiffResult
    ) -> None:
        """Diff shows summary line."""
        with (
            patch("popctl.core.manifest.load_manifest", return_value=sample_manifest),
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch.object(
                __import__("popctl.core.diff", fromlist=["DiffEngine"]).DiffEngine,
                "compute_diff",
                return_value=diff_result_with_changes,
            ),
        ):
            result = runner.invoke(app, ["diff"])

        assert result.exit_code == 0
        assert "Summary" in result.stdout
        assert "3 total" in result.stdout


class TestDiffBrief:
    """Tests for diff --brief option."""

    def test_diff_brief_in_sync(
        self, sample_manifest: Manifest, in_sync_result: DiffResult
    ) -> None:
        """Brief diff shows success message when in sync."""
        with (
            patch("popctl.core.manifest.load_manifest", return_value=sample_manifest),
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch.object(
                __import__("popctl.core.diff", fromlist=["DiffEngine"]).DiffEngine,
                "compute_diff",
                return_value=in_sync_result,
            ),
        ):
            result = runner.invoke(app, ["diff", "--brief"])

        assert result.exit_code == 0
        assert "in sync" in result.stdout.lower()

    def test_diff_brief_with_changes(
        self, sample_manifest: Manifest, diff_result_with_changes: DiffResult
    ) -> None:
        """Brief diff shows counts only."""
        with (
            patch("popctl.core.manifest.load_manifest", return_value=sample_manifest),
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch.object(
                __import__("popctl.core.diff", fromlist=["DiffEngine"]).DiffEngine,
                "compute_diff",
                return_value=diff_result_with_changes,
            ),
        ):
            result = runner.invoke(app, ["diff", "--brief"])

        assert result.exit_code == 0
        # Should show counts, not full table
        assert "New:" in result.stdout or "new" in result.stdout.lower()
        assert "Missing:" in result.stdout or "missing" in result.stdout.lower()
        assert "Extra:" in result.stdout or "extra" in result.stdout.lower()


class TestDiffJson:
    """Tests for diff --json option."""

    def test_diff_json_output(
        self, sample_manifest: Manifest, diff_result_with_changes: DiffResult
    ) -> None:
        """JSON output is valid JSON."""
        with (
            patch("popctl.core.manifest.load_manifest", return_value=sample_manifest),
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch.object(
                __import__("popctl.core.diff", fromlist=["DiffEngine"]).DiffEngine,
                "compute_diff",
                return_value=diff_result_with_changes,
            ),
        ):
            result = runner.invoke(app, ["diff", "--json"])

        assert result.exit_code == 0
        # Should be valid JSON
        data = json.loads(result.stdout)
        assert "in_sync" in data
        assert "summary" in data
        assert "new" in data
        assert "missing" in data
        assert "extra" in data

    def test_diff_json_structure(
        self, sample_manifest: Manifest, diff_result_with_changes: DiffResult
    ) -> None:
        """JSON output has expected structure."""
        with (
            patch("popctl.core.manifest.load_manifest", return_value=sample_manifest),
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch.object(
                __import__("popctl.core.diff", fromlist=["DiffEngine"]).DiffEngine,
                "compute_diff",
                return_value=diff_result_with_changes,
            ),
        ):
            result = runner.invoke(app, ["diff", "--json"])

        data = json.loads(result.stdout)
        assert data["in_sync"] is False
        assert data["summary"]["new"] == 1
        assert data["summary"]["missing"] == 1
        assert data["summary"]["extra"] == 1
        assert data["summary"]["total"] == 3

    def test_diff_json_in_sync(self, sample_manifest: Manifest, in_sync_result: DiffResult) -> None:
        """JSON output shows in_sync true when system matches."""
        with (
            patch("popctl.core.manifest.load_manifest", return_value=sample_manifest),
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch.object(
                __import__("popctl.core.diff", fromlist=["DiffEngine"]).DiffEngine,
                "compute_diff",
                return_value=in_sync_result,
            ),
        ):
            result = runner.invoke(app, ["diff", "--json"])

        data = json.loads(result.stdout)
        assert data["in_sync"] is True
        assert data["summary"]["total"] == 0


class TestDiffSourceFilter:
    """Tests for diff --source option."""

    def test_diff_source_apt(self, sample_manifest: Manifest) -> None:
        """Diff --source apt only processes APT packages."""
        apt_result = DiffResult(
            new=(DiffEntry(name="htop", source="apt", diff_type=DiffType.NEW),),
            missing=(),
            extra=(),
        )

        with (
            patch("popctl.core.manifest.load_manifest", return_value=sample_manifest),
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=True),
            patch.object(
                __import__("popctl.core.diff", fromlist=["DiffEngine"]).DiffEngine,
                "compute_diff",
                return_value=apt_result,
            ) as mock_diff,
        ):
            result = runner.invoke(app, ["diff", "--source", "apt"])

        assert result.exit_code == 0
        # Verify source filter was passed
        mock_diff.assert_called_once()
        call_args = mock_diff.call_args
        assert call_args[1].get("source_filter") == "apt" or call_args[0][1] == "apt"


class TestDiffScannerAvailability:
    """Tests for scanner availability handling."""

    def test_diff_no_scanners_available(self, sample_manifest: Manifest) -> None:
        """Diff fails gracefully when no scanners available."""
        with (
            patch("popctl.core.manifest.load_manifest", return_value=sample_manifest),
            patch("popctl.scanners.apt.command_exists", return_value=False),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
        ):
            result = runner.invoke(app, ["diff"])

        assert result.exit_code == 1
        assert "not available" in (result.stdout + result.stderr).lower()

    def test_diff_warns_when_flatpak_unavailable(
        self, sample_manifest: Manifest, in_sync_result: DiffResult
    ) -> None:
        """Diff warns when Flatpak is unavailable but continues with APT."""
        with (
            patch("popctl.core.manifest.load_manifest", return_value=sample_manifest),
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch.object(
                __import__("popctl.core.diff", fromlist=["DiffEngine"]).DiffEngine,
                "compute_diff",
                return_value=in_sync_result,
            ),
        ):
            result = runner.invoke(app, ["diff"])

        assert result.exit_code == 0
        # Warning about Flatpak should appear in stderr
        assert "FLATPAK" in result.stderr or "flatpak" in result.stderr.lower()
