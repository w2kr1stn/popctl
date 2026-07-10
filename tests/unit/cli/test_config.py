"""Unit tests for config CLI commands.

Tests for the popctl config scan and popctl config clean commands.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import typer
from popctl.cli.commands import config
from popctl.cli.main import app
from popctl.domain.models import (
    DomainActionResult,
    OrphanReason,
    OrphanStatus,
    PathType,
    ScannedEntry,
)
from popctl.models.manifest import DomainConfig, DomainEntry
from typer.testing import CliRunner

runner = CliRunner()


def _make_orphan(
    path: str,
    path_type: PathType = PathType.DIRECTORY,
    confidence: float = 0.70,
    size: int = 4096,
) -> ScannedEntry:
    """Create a test ScannedEntry with ORPHAN status."""
    return ScannedEntry(
        path=path,
        path_type=path_type,
        status=OrphanStatus.ORPHAN,
        size_bytes=size,
        mtime="2024-01-15T10:00:00Z",
        parent_target=None,
        orphan_reason=OrphanReason.NO_PACKAGE_MATCH,
        confidence=confidence,
    )


def _make_manifest(
    remove_paths: dict[str, DomainEntry] | None = None,
    keep_paths: dict[str, DomainEntry] | None = None,
) -> MagicMock:
    """Create a mock Manifest with optional configs section."""
    manifest = MagicMock()
    if remove_paths is not None or keep_paths is not None:
        configs_config = DomainConfig(
            keep=keep_paths or {},
            remove=remove_paths or {},
        )
        manifest.configs = configs_config
        manifest.get_domain_remove.return_value = configs_config.remove
    else:
        manifest.configs = None
        manifest.get_domain_remove.return_value = {}
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

        with patch("popctl.cli.commands.config.collect_domain_orphans", return_value=orphans):
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

        with patch("popctl.cli.commands.config.collect_domain_orphans", return_value=orphans):
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

            with patch("popctl.cli.commands.config.collect_domain_orphans", return_value=orphans):
                result = runner.invoke(app, ["config", "scan", "--export", str(export_path)])

            assert result.exit_code == 0
            assert export_path.exists()

            data = json.loads(export_path.read_text())
            assert len(data) == 1
            assert data[0]["path"] == "/home/user/.config/vlc"

    def test_config_scan_limit(self) -> None:
        """Scan with --limit restricts displayed results."""
        # collect_domain_orphans returns already sorted by confidence desc
        orphans = [
            _make_orphan("/tmp/aaa", confidence=0.90),
            _make_orphan("/tmp/bbb", confidence=0.80),
            _make_orphan("/tmp/ccc", confidence=0.70),
        ]

        with patch("popctl.cli.commands.config.collect_domain_orphans", return_value=orphans):
            result = runner.invoke(app, ["config", "scan", "--limit", "1"])

        assert result.exit_code == 0
        # Should show the highest confidence one (sorted descending)
        assert "/tmp/aaa" in result.stdout
        # Summary should mention the limit
        assert "limited to 1" in result.stdout

    def test_config_scan_no_orphans_message(self) -> None:
        """Scan shows clean message when no orphans found."""
        with patch("popctl.cli.commands.config.collect_domain_orphans", return_value=[]):
            result = runner.invoke(app, ["config", "scan"])

        assert result.exit_code == 0
        assert "clean" in result.stdout.lower()
        assert "No orphaned configurations found" in result.stdout

    def test_config_scan_filters_non_orphans(self) -> None:
        """Scan filters to ORPHAN only; OWNED entries are excluded.

        collect_domain_orphans already filters internally, so the mock
        returns only orphan entries.
        """
        orphans_only = [
            _make_orphan("/tmp/vlc"),
        ]

        with patch("popctl.cli.commands.config.collect_domain_orphans", return_value=orphans_only):
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
                "/home/user/.config/vlc": DomainEntry(reason="VLC uninstalled"),
            }
        )
        dry_results = [
            DomainActionResult(path="/home/user/.config/vlc", success=True, dry_run=True),
        ]

        with (
            patch(
                "popctl.cli.commands.config.require_manifest",
                return_value=manifest,
            ),
            patch("popctl.cli.commands.config.ConfigOperator") as mock_op_class,
            patch("popctl.cli.commands.config.is_protected", return_value=False),
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
                "/home/user/.config/vlc": DomainEntry(reason="VLC uninstalled"),
            }
        )

        with (
            patch(
                "popctl.cli.commands.config.require_manifest",
                return_value=manifest,
            ),
            patch("popctl.cli.commands.config.is_protected", return_value=False),
        ):
            result = runner.invoke(app, ["config", "clean"], input="n\n")

        assert result.exit_code == 0
        assert "Aborted" in result.stdout

    def test_config_clean_skip_confirmation(self) -> None:
        """Clean with -y skips confirmation."""
        manifest = _make_manifest(
            remove_paths={
                "/home/user/.config/vlc": DomainEntry(reason="VLC uninstalled"),
            }
        )
        success_results = [
            DomainActionResult(
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
            patch("popctl.cli.types.record_domain_deletions") as mock_record,
            patch("popctl.cli.commands.config.is_protected", return_value=False),
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
                "/home/user/.config/vlc": DomainEntry(reason="VLC removed"),
                "/home/user/.config/obs-studio": DomainEntry(reason="OBS removed"),
            }
        )
        success_results = [
            DomainActionResult(
                path="/home/user/.config/vlc",
                success=True,
                backup_path="/backup/vlc",
            ),
            DomainActionResult(
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
            patch("popctl.cli.types.record_domain_deletions") as mock_record,
            patch("popctl.cli.commands.config.is_protected", return_value=False),
        ):
            mock_op = MagicMock()
            mock_op.delete.return_value = success_results
            mock_op_class.return_value = mock_op

            result = runner.invoke(app, ["config", "clean", "-y"])

        assert result.exit_code == 0
        mock_record.assert_called_once_with(
            "configs",
            ["/home/user/.config/vlc", "/home/user/.config/obs-studio"],
            command="popctl config clean",
        )

    def test_config_clean_shows_backup_paths(self) -> None:
        """Clean results include backup paths in output."""
        manifest = _make_manifest(
            remove_paths={
                "/tmp/vlc": DomainEntry(reason="VLC removed"),
            }
        )
        backup_path = "/tmp/backups/vlc"
        success_results = [
            DomainActionResult(
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
            patch("popctl.cli.types.record_domain_deletions"),
            patch("popctl.cli.commands.config.is_protected", return_value=False),
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
                "/home/user/.ssh/config": DomainEntry(reason="Should not delete"),
                "/home/user/.config/vlc": DomainEntry(reason="VLC removed"),
            }
        )
        success_results = [
            DomainActionResult(
                path="/home/user/.config/vlc",
                success=True,
                backup_path="/backup/vlc",
            ),
        ]

        def mock_is_protected(path: str, domain: str) -> bool:
            return ".ssh" in path

        with (
            patch(
                "popctl.cli.commands.config.require_manifest",
                return_value=manifest,
            ),
            patch("popctl.cli.commands.config.ConfigOperator") as mock_op_class,
            patch("popctl.cli.types.record_domain_deletions"),
            patch(
                "popctl.cli.commands.config.is_protected",
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
                "/home/user/.config/vlc": DomainEntry(reason="VLC removed"),
            }
        )
        fail_results = [
            DomainActionResult(
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
            patch("popctl.cli.commands.config.is_protected", return_value=False),
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


class TestConfigPath:
    """Tests for popctl config path."""

    def test_lists_known_locations_and_existence(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "popctl"
        manifest_path = config_dir / "manifest.toml"
        alerts_path = config_dir / "alerts.toml"
        theme_path = config_dir / "theme.toml"
        config_dir.mkdir()
        manifest_path.write_text("")
        alerts_path.write_text("")
        theme_path.write_text("")

        with (
            patch("popctl.cli.commands.config.get_config_dir", return_value=config_dir),
            patch(
                "popctl.cli.commands.config.get_manifest_path", return_value=manifest_path
            ),
            patch(
                "popctl.cli.commands.config.get_alerts_config_path", return_value=alerts_path
            ),
        ):
            result = runner.invoke(app, ["config", "path"])

        assert result.exit_code == 0
        assert f"manifest: {manifest_path} (exists)" in result.output
        assert f"advisor: {config_dir / 'advisor.toml'} (missing)" in result.output
        assert f"alerts: {alerts_path} (exists)" in result.output
        assert f"backup: {config_dir / 'backup.toml'} (missing)" in result.output
        assert f"theme: {theme_path} (exists)" in result.output


# =============================================================================
# config show tests
# =============================================================================


class TestConfigShow:
    """Tests for popctl config show."""

    def test_without_name_lists_paths_and_usage_hint(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "popctl"
        manifest_path = config_dir / "manifest.toml"
        alerts_path = config_dir / "alerts.toml"

        with (
            patch("popctl.cli.commands.config.get_config_dir", return_value=config_dir),
            patch(
                "popctl.cli.commands.config.get_manifest_path", return_value=manifest_path
            ),
            patch(
                "popctl.cli.commands.config.get_alerts_config_path", return_value=alerts_path
            ),
        ):
            result = runner.invoke(app, ["config", "show"])

        assert result.exit_code == 0
        assert "popctl configuration paths:" in result.output
        assert "popctl config show <name>" in result.output

    def test_redacts_advisor_api_key(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "popctl"
        advisor_path = config_dir / "advisor.toml"
        canary = "canary-advisor-key-do-not-leak"
        config_dir.mkdir()
        advisor_path.write_text(f'provider = "codex"\napi_key = "{canary}"\n')

        with (
            patch("popctl.cli.commands.config.get_config_dir", return_value=config_dir),
            patch(
                "popctl.cli.commands.config.get_manifest_path",
                return_value=config_dir / "manifest.toml",
            ),
            patch(
                "popctl.cli.commands.config.get_alerts_config_path",
                return_value=config_dir / "alerts.toml",
            ),
        ):
            result = runner.invoke(app, ["config", "show", "advisor"])

        assert result.exit_code == 0
        assert 'api_key = "********"' in result.output
        assert canary not in result.output

    def test_redacts_multiline_advisor_api_key(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "popctl"
        advisor_path = config_dir / "advisor.toml"
        canary = "canary-advisor-key-do-not-leak"
        config_dir.mkdir()
        advisor_path.write_text(
            f'provider = "codex"\napi_key = [\n  "{canary}",\n]\n'
        )

        with (
            patch("popctl.cli.commands.config.get_config_dir", return_value=config_dir),
            patch(
                "popctl.cli.commands.config.get_manifest_path",
                return_value=config_dir / "manifest.toml",
            ),
            patch(
                "popctl.cli.commands.config.get_alerts_config_path",
                return_value=config_dir / "alerts.toml",
            ),
        ):
            result = runner.invoke(app, ["config", "show", "advisor"])

        assert result.exit_code == 0
        assert 'api_key = "********"' in result.output
        assert canary not in result.output

    def test_invalid_advisor_config_never_prints_raw_contents(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "popctl"
        advisor_path = config_dir / "advisor.toml"
        canary = "canary-advisor-key-do-not-leak"
        config_dir.mkdir()
        advisor_path.write_text(f'api_key = "{canary}"\ninvalid = [\n')

        with (
            patch("popctl.cli.commands.config.get_config_dir", return_value=config_dir),
            patch(
                "popctl.cli.commands.config.get_manifest_path",
                return_value=config_dir / "manifest.toml",
            ),
            patch(
                "popctl.cli.commands.config.get_alerts_config_path",
                return_value=config_dir / "alerts.toml",
            ),
        ):
            result = runner.invoke(app, ["config", "show", "advisor"])

        assert result.exit_code == 0
        assert (
            "The advisor config file exists but is not valid TOML — fix or recreate it "
            "via popctl setup."
        ) in result.output
        assert canary not in result.output
        assert "api_key =" not in result.output
        assert "invalid = [" not in result.output

    @pytest.mark.parametrize(
        ("name", "command"),
        [
            ("manifest", "popctl init"),
            ("advisor", "popctl setup"),
            ("alerts", "popctl alerts init-config"),
            ("backup", "popctl backup init"),
            ("theme", "popctl config edit theme"),
        ],
    )
    def test_missing_file_shows_creation_hint(
        self, tmp_path: Path, name: str, command: str
    ) -> None:
        config_dir = tmp_path / "popctl"

        with (
            patch("popctl.cli.commands.config.get_config_dir", return_value=config_dir),
            patch(
                "popctl.cli.commands.config.get_manifest_path",
                return_value=config_dir / "manifest.toml",
            ),
            patch(
                "popctl.cli.commands.config.get_alerts_config_path",
                return_value=config_dir / "alerts.toml",
            ),
        ):
            result = runner.invoke(app, ["config", "show", name])

        assert result.exit_code == 0
        assert "not been created yet" in result.output
        assert command in result.output


# =============================================================================
# config edit tests
# =============================================================================


class TestConfigEdit:
    """Tests for popctl config edit."""

    def test_invokes_editor_without_creating_file(self, tmp_path: Path) -> None:
        config_dir = tmp_path / "nested" / "popctl"
        advisor_path = config_dir / "advisor.toml"

        with (
            patch("popctl.cli.commands.config.get_config_dir", return_value=config_dir),
            patch(
                "popctl.cli.commands.config.get_manifest_path",
                return_value=config_dir / "manifest.toml",
            ),
            patch(
                "popctl.cli.commands.config.get_alerts_config_path",
                return_value=config_dir / "alerts.toml",
            ),
            patch("popctl.cli.commands.config.run_interactive", return_value=0) as mock_run,
            patch("popctl.cli.commands.config.sys.stdin") as mock_stdin,
            patch.dict("popctl.cli.commands.config.os.environ", {"EDITOR": "test-editor"}),
        ):
            mock_stdin.isatty.return_value = True
            config.edit("advisor")

        mock_run.assert_called_once_with(["test-editor", str(advisor_path)])
        assert config_dir.is_dir()
        assert not advisor_path.exists()

    def test_refuses_to_launch_editor_without_tty(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        config_dir = tmp_path / "popctl"
        advisor_path = config_dir / "advisor.toml"

        with (
            patch("popctl.cli.commands.config.get_config_dir", return_value=config_dir),
            patch(
                "popctl.cli.commands.config.get_manifest_path",
                return_value=config_dir / "manifest.toml",
            ),
            patch(
                "popctl.cli.commands.config.get_alerts_config_path",
                return_value=config_dir / "alerts.toml",
            ),
            patch("popctl.cli.commands.config.run_interactive") as mock_run,
            patch("popctl.cli.commands.config.sys.stdin") as mock_stdin,
        ):
            mock_stdin.isatty.return_value = False
            with pytest.raises(typer.Exit) as exc_info:
                config.edit("advisor")

        assert exc_info.value.exit_code == 1
        assert str(advisor_path) in capsys.readouterr().err
        mock_run.assert_not_called()
