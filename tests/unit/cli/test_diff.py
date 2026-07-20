"""Unit tests for diff command.

Tests for the CLI diff command implementation.
"""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from popctl.cli.main import app
from popctl.cli.types import SourceChoice, compute_source_system_diff
from popctl.core.diff import DiffEntry, DiffResult, DiffType
from popctl.models.manifest import Manifest
from popctl.models.package import PackageSource
from popctl.sources.diff import SourceDiffEntry, SourceDiffResult, SourceDiffType, SourceRecordKind
from popctl.sources.models import SourceLocator, SourcePlatform, SourcesConfig
from typer.testing import CliRunner

runner = CliRunner()


@pytest.fixture
def in_sync_result() -> DiffResult:
    """Create a diff result that shows system in sync."""
    return DiffResult(new=(), missing=(), extra=())


@pytest.fixture
def diff_result_with_changes() -> DiffResult:
    """Create a diff result with various changes."""
    return DiffResult(
        new=(
            DiffEntry(
                name="htop", source=PackageSource.APT, diff_type=DiffType.NEW, version="3.2.2",
            ),
        ),
        missing=(DiffEntry(name="vim", source=PackageSource.APT, diff_type=DiffType.MISSING),),
        extra=(
            DiffEntry(
                name="bloatware", source=PackageSource.APT, diff_type=DiffType.EXTRA, version="1.0",
            ),
        ),
    )


class TestDiffCommandHelp:
    """Tests for diff command help."""

    def test_diff_help(self) -> None:
        """Diff command shows help."""
        result = runner.invoke(app, ["diff", "--help"])
        assert result.exit_code == 0
        assert "Compare manifest with current system state" in result.stdout


def test_diff_no_manifest_error(tmp_path: Path) -> None:
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


def test_diff_in_sync_message(sample_manifest: Manifest, in_sync_result: DiffResult) -> None:
    """Diff shows success message when in sync."""
    with (
        patch("popctl.core.manifest.load_manifest", return_value=sample_manifest),
        patch("popctl.scanners.apt.command_exists", return_value=True),
        patch("popctl.scanners.flatpak.command_exists", return_value=False),
        patch(
            "popctl.cli.commands.diff.compute_system_diff",
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
            patch(
                "popctl.cli.commands.diff.compute_system_diff",
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
            patch(
                "popctl.cli.commands.diff.compute_system_diff",
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
            patch(
                "popctl.cli.commands.diff.compute_system_diff",
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
            patch(
                "popctl.cli.commands.diff.compute_system_diff",
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
            patch(
                "popctl.cli.commands.diff.compute_system_diff",
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
            patch(
                "popctl.cli.commands.diff.compute_system_diff",
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
            patch(
                "popctl.cli.commands.diff.compute_system_diff",
                return_value=in_sync_result,
            ),
        ):
            result = runner.invoke(app, ["diff", "--json"])

        data = json.loads(result.stdout)
        assert data["in_sync"] is True
        assert data["summary"]["total"] == 0


def test_diff_source_apt(sample_manifest: Manifest) -> None:
    """Diff --source apt only processes APT packages."""
    apt_result = DiffResult(
        new=(DiffEntry(name="htop", source=PackageSource.APT, diff_type=DiffType.NEW),),
        missing=(),
        extra=(),
    )

    with (
        patch("popctl.core.manifest.load_manifest", return_value=sample_manifest),
        patch("popctl.scanners.apt.command_exists", return_value=True),
        patch("popctl.scanners.flatpak.command_exists", return_value=True),
        patch(
            "popctl.cli.commands.diff.compute_system_diff",
            return_value=apt_result,
        ) as mock_diff,
    ):
        result = runner.invoke(app, ["diff", "--source", "apt"])

    assert result.exit_code == 0
    # Verify source filter was passed to compute_system_diff
    mock_diff.assert_called_once()
    call_args = mock_diff.call_args
    # compute_system_diff(source, silent_warnings=...) — first positional is SourceChoice
    assert call_args[0][0].value == "apt"


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ([], "popctl-vendor"),
        (["--brief"], "Source changed: 1"),
        (["--json"], "sources"),
    ],
)
def test_diff_renders_source_changes_in_all_output_forms_and_applies_filter(
    sample_manifest: Manifest,
    arguments: list[str],
    expected: str,
) -> None:
    source_result = SourceDiffResult(
        changed=(
            SourceDiffEntry(
                locator=SourceLocator(manager=PackageSource.APT, parts=("popctl-vendor",)),
                kind=SourceRecordKind.APT,
                diff_type=SourceDiffType.CHANGED,
            ),
        ),
    )
    package_result = DiffResult(new=(), missing=(), extra=())

    with (
        patch("popctl.cli.commands.diff.compute_system_diff", return_value=package_result),
        patch(
            "popctl.cli.commands.diff.compute_source_system_diff", return_value=source_result
        ) as source_diff,
    ):
        result = runner.invoke(app, ["diff", "--source", "apt", *arguments])

    assert result.exit_code == 0
    assert expected in result.stdout
    source_diff.assert_called_once()
    assert source_diff.call_args.args[0].value == "apt"
    if arguments == ["--json"]:
        data = json.loads(result.stdout)
        assert data["sources"]["changed"][0]["label"] == "popctl-vendor"


def test_source_diff_scan_uses_the_selected_manager_filter(sample_manifest: Manifest) -> None:
    sources = SourcesConfig(platform=SourcePlatform(distro_id="ubuntu", codename="noble"))
    manifest = sample_manifest.model_copy(update={"sources": sources})
    expected = SourceDiffResult()

    with (
        patch("popctl.cli.types.require_manifest", return_value=manifest),
        patch("popctl.cli.types.capture_sources", return_value=sources) as capture_sources,
        patch("popctl.cli.types.compute_source_diff", return_value=expected) as source_diff,
    ):
        result = compute_source_system_diff(SourceChoice.APT)

    assert result is expected
    assert capture_sources.call_args.kwargs["managers"] == (PackageSource.APT,)
    assert source_diff.call_args.kwargs["source_filter"] is PackageSource.APT
    assert tuple(source_diff.call_args.kwargs["apt_package_names"]) == ("firefox", "neovim")


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
            patch("popctl.scanners.apt.AptScanner.scan", return_value=iter([])),
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            patch(
                "popctl.core.diff.compute_diff",
                return_value=in_sync_result,
            ),
        ):
            result = runner.invoke(app, ["diff"])

        assert result.exit_code == 0
        # Warning about Flatpak should appear in stderr
        assert "FLATPAK" in result.stderr or "flatpak" in result.stderr.lower()
