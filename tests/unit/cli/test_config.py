"""Unit tests for config CLI commands.

Tests for the popctl config scan and popctl config clean commands.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from popctl.cli.main import app
from popctl.configs.manifest import ConfigEntry, ConfigsConfig
from popctl.configs.models import ConfigOrphanReason, ConfigStatus, ConfigType, ScannedConfig
from popctl.configs.operator import ConfigActionResult
from typer.testing import CliRunner

runner = CliRunner()


def _make_orphan(
    path: str,
    config_type: ConfigType = ConfigType.DIRECTORY,
    confidence: float = 0.70,
    size: int = 4096,
) -> ScannedConfig:
    """Create a test ScannedConfig with ORPHAN status."""
    return ScannedConfig(
        path=path,
        config_type=config_type,
        status=ConfigStatus.ORPHAN,
        size_bytes=size,
        mtime="2024-01-15T10:00:00Z",
        orphan_reason=ConfigOrphanReason.NO_PACKAGE_MATCH,
        confidence=confidence,
        description=None,
    )


def _make_owned(path: str) -> ScannedConfig:
    """Create a test ScannedConfig with OWNED status."""
    return ScannedConfig(
        path=path,
        config_type=ConfigType.DIRECTORY,
        status=ConfigStatus.OWNED,
        size_bytes=1024,
        mtime="2024-01-15T10:00:00Z",
        orphan_reason=None,
        confidence=0.0,
        description=None,
    )


def _make_manifest(
    remove_paths: dict[str, ConfigEntry] | None = None,
    keep_paths: dict[str, ConfigEntry] | None = None,
) -> MagicMock:
    """Create a mock Manifest with optional configs section."""
    manifest = MagicMock()
    if remove_paths is not None or keep_paths is not None:
        configs_config = ConfigsConfig(
            keep=keep_paths or {},
            remove=remove_paths or {},
        )
        manifest.configs = configs_config
        manifest.get_config_remove_paths.return_value = configs_config.remove
        manifest.get_config_keep_paths.return_value = configs_config.keep
    else:
        manifest.configs = None
        manifest.get_config_remove_paths.return_value = {}
        manifest.get_config_keep_paths.return_value = {}
    return manifest


# =============================================================================
# config scan tests
# =============================================================================


class TestConfigScan:
    """Tests for popctl config scan command."""

    def test_config_scan_default(self) -> None:
        """Scan returns table output with orphans."""
        orphans = [
            _make_orphan("/tmp/vlc", confidence=0.70, size=4096),
            _make_orphan("/tmp/obs", confidence=0.70, size=8192),
        ]

        with patch("popctl.cli.commands.config.ConfigScanner") as mock_scanner_class:
            mock_scanner = MagicMock()
            mock_scanner.scan.return_value = iter(orphans)
            mock_scanner_class.return_value = mock_scanner

            result = runner.invoke(app, ["config", "scan"])

        assert result.exit_code == 0
        assert "Orphaned Configuration Entries" in result.stdout
        assert "/tmp/vlc" in result.stdout
        assert "/tmp/obs" in result.stdout
        assert "Found 2 orphaned configs" in result.stdout

    def test_config_scan_json_format(self) -> None:
        """Scan with --format json outputs valid JSON."""
        orphans = [
            _make_orphan("/home/user/.config/vlc"),
        ]

        with patch("popctl.cli.commands.config.ConfigScanner") as mock_scanner_class:
            mock_scanner = MagicMock()
            mock_scanner.scan.return_value = iter(orphans)
            mock_scanner_class.return_value = mock_scanner

            result = runner.invoke(app, ["config", "scan", "--format", "json"])

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert len(data) == 1
        assert data[0]["path"] == "/home/user/.config/vlc"
        assert data[0]["status"] == "orphan"
        assert data[0]["confidence"] == 0.70

    def test_config_scan_export(self) -> None:
        """Scan with --export writes JSON file."""
        orphans = [
            _make_orphan("/home/user/.config/vlc"),
        ]

        with tempfile.TemporaryDirectory() as tmpdir:
            export_path = Path(tmpdir) / "orphans.json"

            with patch("popctl.cli.commands.config.ConfigScanner") as mock_scanner_class:
                mock_scanner = MagicMock()
                mock_scanner.scan.return_value = iter(orphans)
                mock_scanner_class.return_value = mock_scanner

                result = runner.invoke(app, ["config", "scan", "--export", str(export_path)])

            assert result.exit_code == 0
            assert export_path.exists()

            data = json.loads(export_path.read_text())
            assert len(data) == 1
            assert data[0]["path"] == "/home/user/.config/vlc"

    def test_config_scan_limit(self) -> None:
        """Scan with --limit restricts displayed results."""
        orphans = [
            _make_orphan("/tmp/aaa", confidence=0.90),
            _make_orphan("/tmp/bbb", confidence=0.80),
            _make_orphan("/tmp/ccc", confidence=0.70),
        ]

        with patch("popctl.cli.commands.config.ConfigScanner") as mock_scanner_class:
            mock_scanner = MagicMock()
            mock_scanner.scan.return_value = iter(orphans)
            mock_scanner_class.return_value = mock_scanner

            result = runner.invoke(app, ["config", "scan", "--limit", "1"])

        assert result.exit_code == 0
        # Should show the highest confidence one (sorted descending)
        assert "/tmp/aaa" in result.stdout
        # Summary should mention the limit
        assert "limited to 1" in result.stdout

    def test_config_scan_no_orphans_message(self) -> None:
        """Scan shows clean message when no orphans found."""
        with patch("popctl.cli.commands.config.ConfigScanner") as mock_scanner_class:
            mock_scanner = MagicMock()
            mock_scanner.scan.return_value = iter([])
            mock_scanner_class.return_value = mock_scanner

            result = runner.invoke(app, ["config", "scan"])

        assert result.exit_code == 0
        assert "clean" in result.stdout.lower()
        assert "No orphaned configurations found" in result.stdout

    def test_config_scan_filters_non_orphans(self) -> None:
        """Scan filters to ORPHAN only; OWNED entries are excluded."""
        mixed = [
            _make_orphan("/tmp/vlc"),
            _make_owned("/tmp/firefox"),
        ]

        with patch("popctl.cli.commands.config.ConfigScanner") as mock_scanner_class:
            mock_scanner = MagicMock()
            mock_scanner.scan.return_value = iter(mixed)
            mock_scanner_class.return_value = mock_scanner

            result = runner.invoke(app, ["config", "scan"])

        assert result.exit_code == 0
        assert "/tmp/vlc" in result.stdout
        assert "/tmp/firefox" not in result.stdout
        assert "Found 1 orphaned configs" in result.stdout


# =============================================================================
# config clean tests
# =============================================================================


class TestConfigClean:
    """Tests for popctl config clean command."""

    def test_config_clean_dry_run(self) -> None:
        """Clean with --dry-run shows plan without deleting."""
        manifest = _make_manifest(
            remove_paths={
                "/home/user/.config/vlc": ConfigEntry(reason="VLC uninstalled"),
            }
        )
        dry_results = [
            ConfigActionResult(path="/home/user/.config/vlc", success=True, dry_run=True),
        ]

        with (
            patch(
                "popctl.cli.commands.config.require_manifest",
                return_value=manifest,
            ),
            patch("popctl.cli.commands.config.ConfigOperator") as mock_op_class,
            patch("popctl.cli.commands.config.is_protected_config", return_value=False),
        ):
            mock_op = MagicMock()
            mock_op.delete.return_value = dry_results
            mock_op_class.return_value = mock_op

            result = runner.invoke(app, ["config", "clean", "--dry-run"])

        assert result.exit_code == 0
        assert "dry-run" in result.stdout.lower()
        assert "/home/user/.config/vlc" in result.stdout
        mock_op_class.assert_called_once_with(dry_run=True)

    def test_config_clean_with_confirmation(self) -> None:
        """Clean prompts for confirmation and aborts on 'n'."""
        manifest = _make_manifest(
            remove_paths={
                "/home/user/.config/vlc": ConfigEntry(reason="VLC uninstalled"),
            }
        )

        with (
            patch(
                "popctl.cli.commands.config.require_manifest",
                return_value=manifest,
            ),
            patch("popctl.cli.commands.config.is_protected_config", return_value=False),
        ):
            result = runner.invoke(app, ["config", "clean"], input="n\n")

        assert result.exit_code == 0
        assert "Aborted" in result.stdout

    def test_config_clean_skip_confirmation(self) -> None:
        """Clean with -y skips confirmation."""
        manifest = _make_manifest(
            remove_paths={
                "/home/user/.config/vlc": ConfigEntry(reason="VLC uninstalled"),
            }
        )
        success_results = [
            ConfigActionResult(
                path="/home/user/.config/vlc",
                success=True,
                backup_path="/home/user/.local/state/popctl/config-backups/20240115T100000Z/.config/vlc",
            ),
        ]

        with (
            patch(
                "popctl.cli.commands.config.require_manifest",
                return_value=manifest,
            ),
            patch("popctl.cli.commands.config.ConfigOperator") as mock_op_class,
            patch("popctl.cli.commands.config.record_config_deletions") as mock_record,
            patch("popctl.cli.commands.config.is_protected_config", return_value=False),
        ):
            mock_op = MagicMock()
            mock_op.delete.return_value = success_results
            mock_op_class.return_value = mock_op

            result = runner.invoke(app, ["config", "clean", "-y"])

        assert result.exit_code == 0
        assert "processed successfully" in result.stdout
        mock_record.assert_called_once()

    def test_config_clean_no_manifest_configs(self) -> None:
        """Clean with no configs section in manifest shows info."""
        manifest = _make_manifest()

        with patch("popctl.cli.commands.config.require_manifest", return_value=manifest):
            result = runner.invoke(app, ["config", "clean"])

        assert result.exit_code == 0
        assert "No config entries marked for removal" in result.stdout

    def test_config_clean_records_history(self) -> None:
        """Clean records successful deletions to history."""
        manifest = _make_manifest(
            remove_paths={
                "/home/user/.config/vlc": ConfigEntry(reason="VLC removed"),
                "/home/user/.config/obs-studio": ConfigEntry(reason="OBS removed"),
            }
        )
        success_results = [
            ConfigActionResult(
                path="/home/user/.config/vlc",
                success=True,
                backup_path="/backup/vlc",
            ),
            ConfigActionResult(
                path="/home/user/.config/obs-studio",
                success=True,
                backup_path="/backup/obs-studio",
            ),
        ]

        with (
            patch(
                "popctl.cli.commands.config.require_manifest",
                return_value=manifest,
            ),
            patch("popctl.cli.commands.config.ConfigOperator") as mock_op_class,
            patch("popctl.cli.commands.config.record_config_deletions") as mock_record,
            patch("popctl.cli.commands.config.is_protected_config", return_value=False),
        ):
            mock_op = MagicMock()
            mock_op.delete.return_value = success_results
            mock_op_class.return_value = mock_op

            result = runner.invoke(app, ["config", "clean", "-y"])

        assert result.exit_code == 0
        mock_record.assert_called_once_with(
            ["/home/user/.config/vlc", "/home/user/.config/obs-studio"],
            command="popctl config clean",
        )

    def test_config_clean_shows_backup_paths(self) -> None:
        """Clean results include backup paths in output."""
        manifest = _make_manifest(
            remove_paths={
                "/tmp/vlc": ConfigEntry(reason="VLC removed"),
            }
        )
        backup_path = "/tmp/backups/vlc"
        success_results = [
            ConfigActionResult(
                path="/tmp/vlc",
                success=True,
                backup_path=backup_path,
            ),
        ]

        with (
            patch(
                "popctl.cli.commands.config.require_manifest",
                return_value=manifest,
            ),
            patch("popctl.cli.commands.config.ConfigOperator") as mock_op_class,
            patch("popctl.cli.commands.config.record_config_deletions"),
            patch("popctl.cli.commands.config.is_protected_config", return_value=False),
        ):
            mock_op = MagicMock()
            mock_op.delete.return_value = success_results
            mock_op_class.return_value = mock_op

            result = runner.invoke(app, ["config", "clean", "-y"])

        assert result.exit_code == 0
        assert "/tmp/backups/vlc" in result.stdout

    def test_config_clean_protected_config_skipped(self) -> None:
        """Clean skips protected config paths with warning."""
        manifest = _make_manifest(
            remove_paths={
                "/home/user/.ssh/config": ConfigEntry(reason="Should not delete"),
                "/home/user/.config/vlc": ConfigEntry(reason="VLC removed"),
            }
        )
        success_results = [
            ConfigActionResult(
                path="/home/user/.config/vlc",
                success=True,
                backup_path="/backup/vlc",
            ),
        ]

        def mock_is_protected(path: str) -> bool:
            return ".ssh" in path

        with (
            patch(
                "popctl.cli.commands.config.require_manifest",
                return_value=manifest,
            ),
            patch("popctl.cli.commands.config.ConfigOperator") as mock_op_class,
            patch("popctl.cli.commands.config.record_config_deletions"),
            patch(
                "popctl.cli.commands.config.is_protected_config",
                side_effect=mock_is_protected,
            ),
        ):
            mock_op = MagicMock()
            mock_op.delete.return_value = success_results
            mock_op_class.return_value = mock_op

            result = runner.invoke(app, ["config", "clean", "-y"])

        assert result.exit_code == 0
        assert "Skipping protected config" in result.stderr
        # Only the non-protected path should be passed to operator
        mock_op.delete.assert_called_once_with(["/home/user/.config/vlc"])

    def test_config_clean_failed_deletion_exits_with_error(self) -> None:
        """Clean exits with code 1 if any deletion fails."""
        manifest = _make_manifest(
            remove_paths={
                "/home/user/.config/vlc": ConfigEntry(reason="VLC removed"),
            }
        )
        fail_results = [
            ConfigActionResult(
                path="/home/user/.config/vlc",
                success=False,
                error="Permission denied",
            ),
        ]

        with (
            patch(
                "popctl.cli.commands.config.require_manifest",
                return_value=manifest,
            ),
            patch("popctl.cli.commands.config.ConfigOperator") as mock_op_class,
            patch("popctl.cli.commands.config.is_protected_config", return_value=False),
        ):
            mock_op = MagicMock()
            mock_op.delete.return_value = fail_results
            mock_op_class.return_value = mock_op

            result = runner.invoke(app, ["config", "clean", "-y"])

        assert result.exit_code == 1


# =============================================================================
# Help tests
# =============================================================================


class TestConfigHelp:
    """Tests for config help output."""

    def test_config_help(self) -> None:
        """popctl config --help shows command group help."""
        result = runner.invoke(app, ["config", "--help"])
        assert result.exit_code == 0
        assert "Scan and clean orphaned configuration files" in result.stdout
        assert "scan" in result.stdout
        assert "clean" in result.stdout

    def test_config_scan_help(self) -> None:
        """popctl config scan --help shows scan command help."""
        result = runner.invoke(app, ["config", "scan", "--help"])
        assert result.exit_code == 0
        assert "Scan ~/.config/" in result.stdout
        assert "--format" in result.stdout
        assert "--export" in result.stdout
        assert "--limit" in result.stdout

    def test_config_clean_help(self) -> None:
        """popctl config clean --help shows clean command help."""
        result = runner.invoke(app, ["config", "clean", "--help"])
        assert result.exit_code == 0
        assert "Clean up config entries" in result.stdout
        assert "--dry-run" in result.stdout
        assert "--yes" in result.stdout


# =============================================================================
# Format size helper tests
# =============================================================================


class TestFormatSize:
    """Tests for the _format_size helper function."""

    def test_format_size_zero(self) -> None:
        """Format 0 bytes."""
        from popctl.cli.commands.config import _format_size

        assert _format_size(0) == "0 B"

    def test_format_size_none(self) -> None:
        """Format None bytes."""
        from popctl.cli.commands.config import _format_size

        assert _format_size(None) == "0 B"

    def test_format_size_bytes(self) -> None:
        """Format small byte values."""
        from popctl.cli.commands.config import _format_size

        assert _format_size(512) == "512 B"

    def test_format_size_kilobytes(self) -> None:
        """Format kilobyte values."""
        from popctl.cli.commands.config import _format_size

        result = _format_size(2048)
        assert "KB" in result

    def test_format_size_megabytes(self) -> None:
        """Format megabyte values."""
        from popctl.cli.commands.config import _format_size

        result = _format_size(5 * 1024 * 1024)
        assert "MB" in result
