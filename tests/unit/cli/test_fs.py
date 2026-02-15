"""Unit tests for filesystem CLI commands.

Tests for the popctl fs scan and popctl fs clean commands.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from popctl.cli.main import app
from popctl.filesystem.manifest import FilesystemConfig, FilesystemEntry
from popctl.filesystem.models import OrphanReason, PathStatus, PathType, ScannedPath
from popctl.filesystem.operator import FilesystemActionResult
from typer.testing import CliRunner

runner = CliRunner()


def _make_orphan(
    path: str,
    parent: str = "~/.config",
    confidence: float = 0.70,
    size: int = 4096,
) -> ScannedPath:
    """Create a test ScannedPath with ORPHAN status."""
    return ScannedPath(
        path=path,
        path_type=PathType.DIRECTORY,
        status=PathStatus.ORPHAN,
        size_bytes=size,
        mtime="2024-01-15T10:00:00Z",
        parent_target=parent,
        orphan_reason=OrphanReason.NO_PACKAGE_MATCH,
        confidence=confidence,
        description=None,
    )


def _make_owned(path: str) -> ScannedPath:
    """Create a test ScannedPath with OWNED status."""
    return ScannedPath(
        path=path,
        path_type=PathType.DIRECTORY,
        status=PathStatus.OWNED,
        size_bytes=1024,
        mtime="2024-01-15T10:00:00Z",
        parent_target="~/.config",
        orphan_reason=None,
        confidence=0.0,
        description=None,
    )


def _make_manifest(
    remove_paths: dict[str, FilesystemEntry] | None = None,
    keep_paths: dict[str, FilesystemEntry] | None = None,
) -> MagicMock:
    """Create a mock Manifest with optional filesystem section."""
    manifest = MagicMock()
    if remove_paths is not None or keep_paths is not None:
        fs_config = FilesystemConfig(
            keep=keep_paths or {},
            remove=remove_paths or {},
        )
        manifest.filesystem = fs_config
        manifest.get_fs_remove_paths.return_value = fs_config.remove
        manifest.get_fs_keep_paths.return_value = fs_config.keep
    else:
        manifest.filesystem = None
        manifest.get_fs_remove_paths.return_value = {}
        manifest.get_fs_keep_paths.return_value = {}
    return manifest


# =============================================================================
# fs scan tests
# =============================================================================


class TestFsScan:
    """Tests for popctl fs scan command."""

    def test_fs_scan_default(self) -> None:
        """Scan returns table output with orphans."""
        orphans = [
            _make_orphan("/tmp/vlc", confidence=0.70, size=4096),
            _make_orphan("/tmp/obs", confidence=0.80, size=8192),
        ]

        with patch("popctl.cli.commands.fs.FilesystemScanner") as mock_scanner_class:
            mock_scanner = MagicMock()
            mock_scanner.scan.return_value = iter(orphans)
            mock_scanner_class.return_value = mock_scanner

            result = runner.invoke(app, ["fs", "scan"])

        assert result.exit_code == 0
        assert "Orphaned Filesystem Entries" in result.stdout
        assert "/tmp/vlc" in result.stdout
        assert "/tmp/obs" in result.stdout
        assert "Found 2 orphaned entries" in result.stdout

    def test_fs_scan_no_orphans(self) -> None:
        """Scan shows clean message when no orphans found."""
        with patch("popctl.cli.commands.fs.FilesystemScanner") as mock_scanner_class:
            mock_scanner = MagicMock()
            mock_scanner.scan.return_value = iter([])
            mock_scanner_class.return_value = mock_scanner

            result = runner.invoke(app, ["fs", "scan"])

        assert result.exit_code == 0
        assert "clean" in result.stdout.lower()
        assert "No orphaned entries found" in result.stdout

    def test_fs_scan_with_files_flag(self) -> None:
        """Scan passes include_files=True to scanner."""
        with patch("popctl.cli.commands.fs.FilesystemScanner") as mock_scanner_class:
            mock_scanner = MagicMock()
            mock_scanner.scan.return_value = iter([])
            mock_scanner_class.return_value = mock_scanner

            result = runner.invoke(app, ["fs", "scan", "--files"])

        assert result.exit_code == 0
        mock_scanner_class.assert_called_once_with(include_files=True, include_etc=False)

    def test_fs_scan_with_etc_flag(self) -> None:
        """Scan passes include_etc=True to scanner."""
        with patch("popctl.cli.commands.fs.FilesystemScanner") as mock_scanner_class:
            mock_scanner = MagicMock()
            mock_scanner.scan.return_value = iter([])
            mock_scanner_class.return_value = mock_scanner

            result = runner.invoke(app, ["fs", "scan", "--include-etc"])

        assert result.exit_code == 0
        mock_scanner_class.assert_called_once_with(include_files=False, include_etc=True)

    def test_fs_scan_json_format(self) -> None:
        """Scan with --format json outputs valid JSON."""
        orphans = [
            _make_orphan("/home/user/.config/vlc"),
        ]

        with patch("popctl.cli.commands.fs.FilesystemScanner") as mock_scanner_class:
            mock_scanner = MagicMock()
            mock_scanner.scan.return_value = iter(orphans)
            mock_scanner_class.return_value = mock_scanner

            result = runner.invoke(app, ["fs", "scan", "--format", "json"])

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert len(data) == 1
        assert data[0]["path"] == "/home/user/.config/vlc"
        assert data[0]["status"] == "orphan"
        assert data[0]["confidence"] == 0.70

    def test_fs_scan_export(self) -> None:
        """Scan with --export writes JSON file."""
        orphans = [
            _make_orphan("/home/user/.config/vlc"),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            export_path = Path(tmpdir) / "orphans.json"

            with patch("popctl.cli.commands.fs.FilesystemScanner") as mock_scanner_class:
                mock_scanner = MagicMock()
                mock_scanner.scan.return_value = iter(orphans)
                mock_scanner_class.return_value = mock_scanner

                result = runner.invoke(app, ["fs", "scan", "--export", str(export_path)])

            assert result.exit_code == 0
            assert export_path.exists()

            data = json.loads(export_path.read_text())
            assert len(data) == 1
            assert data[0]["path"] == "/home/user/.config/vlc"

    def test_fs_scan_limit(self) -> None:
        """Scan with --limit restricts displayed results."""
        orphans = [
            _make_orphan("/tmp/aaa", confidence=0.90),
            _make_orphan("/tmp/bbb", confidence=0.80),
            _make_orphan("/tmp/ccc", confidence=0.70),
        ]

        with patch("popctl.cli.commands.fs.FilesystemScanner") as mock_scanner_class:
            mock_scanner = MagicMock()
            mock_scanner.scan.return_value = iter(orphans)
            mock_scanner_class.return_value = mock_scanner

            result = runner.invoke(app, ["fs", "scan", "--limit", "1"])

        assert result.exit_code == 0
        # Should show the highest confidence one (sorted descending)
        assert "/tmp/aaa" in result.stdout
        # Summary should mention the limit
        assert "limited to 1" in result.stdout

    def test_fs_scan_mixed_statuses(self) -> None:
        """Scan filters to ORPHAN only; OWNED entries are excluded."""
        mixed = [
            _make_orphan("/tmp/vlc"),
            _make_owned("/tmp/firefox"),
        ]

        with patch("popctl.cli.commands.fs.FilesystemScanner") as mock_scanner_class:
            mock_scanner = MagicMock()
            mock_scanner.scan.return_value = iter(mixed)
            mock_scanner_class.return_value = mock_scanner

            result = runner.invoke(app, ["fs", "scan"])

        assert result.exit_code == 0
        assert "/tmp/vlc" in result.stdout
        assert "/tmp/firefox" not in result.stdout
        assert "Found 1 orphaned entries" in result.stdout


# =============================================================================
# fs clean tests
# =============================================================================


class TestFsClean:
    """Tests for popctl fs clean command."""

    def test_fs_clean_dry_run(self) -> None:
        """Clean with --dry-run shows plan without deleting."""
        manifest = _make_manifest(
            remove_paths={
                "/home/user/.config/vlc": FilesystemEntry(reason="VLC uninstalled"),
            }
        )
        dry_results = [
            FilesystemActionResult(path="/home/user/.config/vlc", success=True, dry_run=True),
        ]

        with (
            patch(
                "popctl.cli.commands.fs.require_manifest",
                return_value=manifest,
            ),
            patch("popctl.cli.commands.fs.FilesystemOperator") as mock_op_class,
        ):
            mock_op = MagicMock()
            mock_op.delete.return_value = dry_results
            mock_op_class.return_value = mock_op

            result = runner.invoke(app, ["fs", "clean", "--dry-run"])

        assert result.exit_code == 0
        assert "dry-run" in result.stdout.lower()
        assert "/home/user/.config/vlc" in result.stdout
        mock_op_class.assert_called_once_with(dry_run=True)

    def test_fs_clean_with_yes(self) -> None:
        """Clean with -y skips confirmation."""
        manifest = _make_manifest(
            remove_paths={
                "/home/user/.config/vlc": FilesystemEntry(reason="VLC uninstalled"),
            }
        )
        success_results = [
            FilesystemActionResult(path="/home/user/.config/vlc", success=True),
        ]

        with (
            patch(
                "popctl.cli.commands.fs.require_manifest",
                return_value=manifest,
            ),
            patch("popctl.cli.commands.fs.FilesystemOperator") as mock_op_class,
            patch("popctl.cli.commands.fs.record_fs_deletions") as mock_record,
        ):
            mock_op = MagicMock()
            mock_op.delete.return_value = success_results
            mock_op_class.return_value = mock_op

            result = runner.invoke(app, ["fs", "clean", "-y"])

        assert result.exit_code == 0
        assert "processed successfully" in result.stdout
        mock_record.assert_called_once()

    def test_fs_clean_no_filesystem_section(self) -> None:
        """Clean with no filesystem section in manifest shows info."""
        manifest = _make_manifest()

        with patch("popctl.cli.commands.fs.require_manifest", return_value=manifest):
            result = runner.invoke(app, ["fs", "clean"])

        assert result.exit_code == 0
        assert "No filesystem entries marked for removal" in result.stdout

    def test_fs_clean_records_history(self) -> None:
        """Clean records successful deletions to history."""
        manifest = _make_manifest(
            remove_paths={
                "/home/user/.config/vlc": FilesystemEntry(reason="VLC removed"),
                "/home/user/.cache/mozilla": FilesystemEntry(reason="Cache stale"),
            }
        )
        success_results = [
            FilesystemActionResult(path="/home/user/.config/vlc", success=True),
            FilesystemActionResult(path="/home/user/.cache/mozilla", success=True),
        ]

        with (
            patch(
                "popctl.cli.commands.fs.require_manifest",
                return_value=manifest,
            ),
            patch("popctl.cli.commands.fs.FilesystemOperator") as mock_op_class,
            patch("popctl.cli.commands.fs.record_fs_deletions") as mock_record,
        ):
            mock_op = MagicMock()
            mock_op.delete.return_value = success_results
            mock_op_class.return_value = mock_op

            result = runner.invoke(app, ["fs", "clean", "-y"])

        assert result.exit_code == 0
        mock_record.assert_called_once_with(
            ["/home/user/.config/vlc", "/home/user/.cache/mozilla"],
            command="popctl fs clean",
        )

    def test_fs_clean_etc_filtered_without_flag(self) -> None:
        """Clean skips /etc paths without --include-etc flag."""
        manifest = _make_manifest(
            remove_paths={
                "/etc/vlc": FilesystemEntry(reason="Obsolete config"),
                "/home/user/.config/vlc": FilesystemEntry(reason="VLC removed"),
            }
        )
        success_results = [
            FilesystemActionResult(path="/home/user/.config/vlc", success=True),
        ]

        with (
            patch(
                "popctl.cli.commands.fs.require_manifest",
                return_value=manifest,
            ),
            patch("popctl.cli.commands.fs.FilesystemOperator") as mock_op_class,
            patch("popctl.cli.commands.fs.record_fs_deletions"),
        ):
            mock_op = MagicMock()
            mock_op.delete.return_value = success_results
            mock_op_class.return_value = mock_op

            result = runner.invoke(app, ["fs", "clean", "-y"])

        assert result.exit_code == 0
        # The /etc path should have been skipped
        assert "Skipping /etc path" in result.stderr
        # Only the non-etc path should be passed to operator
        mock_op.delete.assert_called_once_with(["/home/user/.config/vlc"])

    def test_fs_clean_failed_deletion_exits_with_error(self) -> None:
        """Clean exits with code 1 if any deletion fails."""
        manifest = _make_manifest(
            remove_paths={
                "/home/user/.config/vlc": FilesystemEntry(reason="VLC removed"),
            }
        )
        fail_results = [
            FilesystemActionResult(
                path="/home/user/.config/vlc",
                success=False,
                error="Permission denied",
            ),
        ]

        with (
            patch(
                "popctl.cli.commands.fs.require_manifest",
                return_value=manifest,
            ),
            patch("popctl.cli.commands.fs.FilesystemOperator") as mock_op_class,
        ):
            mock_op = MagicMock()
            mock_op.delete.return_value = fail_results
            mock_op_class.return_value = mock_op

            result = runner.invoke(app, ["fs", "clean", "-y"])

        assert result.exit_code == 1


# =============================================================================
# Help tests
# =============================================================================


class TestFsHelp:
    """Tests for fs help output."""

    def test_fs_help(self) -> None:
        """popctl fs --help shows command group help."""
        result = runner.invoke(app, ["fs", "--help"])
        assert result.exit_code == 0
        assert "Filesystem scanning and cleanup" in result.stdout
        assert "scan" in result.stdout
        assert "clean" in result.stdout

    def test_fs_scan_help(self) -> None:
        """popctl fs scan --help shows scan command help."""
        result = runner.invoke(app, ["fs", "scan", "--help"])
        assert result.exit_code == 0
        assert "Scan filesystem for orphaned" in result.stdout
        assert "--files" in result.stdout
        assert "--include-etc" in result.stdout
        assert "--format" in result.stdout
        assert "--export" in result.stdout
        assert "--limit" in result.stdout

    def test_fs_clean_help(self) -> None:
        """popctl fs clean --help shows clean command help."""
        result = runner.invoke(app, ["fs", "clean", "--help"])
        assert result.exit_code == 0
        assert "Clean up filesystem entries" in result.stdout
        assert "--dry-run" in result.stdout
        assert "--yes" in result.stdout
        assert "--include-etc" in result.stdout


# =============================================================================
# Format size helper tests
# =============================================================================


class TestFormatSize:
    """Tests for the _format_size helper function."""

    def test_format_size_zero(self) -> None:
        """Format 0 bytes."""
        from popctl.cli.commands.fs import _format_size

        assert _format_size(0) == "0 B"

    def test_format_size_none(self) -> None:
        """Format None bytes."""
        from popctl.cli.commands.fs import _format_size

        assert _format_size(None) == "0 B"

    def test_format_size_bytes(self) -> None:
        """Format small byte values."""
        from popctl.cli.commands.fs import _format_size

        assert _format_size(512) == "512 B"

    def test_format_size_kilobytes(self) -> None:
        """Format kilobyte values."""
        from popctl.cli.commands.fs import _format_size

        result = _format_size(2048)
        assert "KB" in result

    def test_format_size_megabytes(self) -> None:
        """Format megabyte values."""
        from popctl.cli.commands.fs import _format_size

        result = _format_size(5 * 1024 * 1024)
        assert "MB" in result
