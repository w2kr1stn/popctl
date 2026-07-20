"""Unit tests for backup CLI commands."""

import os
import stat
from pathlib import Path
from unittest.mock import patch

from popctl.cli.main import app
from popctl.utils.shell import CommandResult
from typer.testing import CliRunner

runner = CliRunner()


class TestBackupInitCommand:
    """Tests for popctl backup init."""

    def test_missing_age_keygen_shows_install_hint(self) -> None:
        identity_path = Path("/tmp/age/key.txt")
        with (
            patch("popctl.cli.commands.backup.which", return_value=None),
            patch(
                "popctl.cli.commands.backup._get_age_identity_path",
                return_value=identity_path,
            ),
            patch(
                "popctl.cli.commands.backup.get_config_dir", return_value=Path("/tmp/popctl")
            ),
        ):
            result = runner.invoke(app, ["backup", "init", "--yes"])

        assert result.exit_code == 0
        assert "Install age" in result.output
        assert str(identity_path) in result.output
        assert "Manual setup" in result.output

    def test_refuses_existing_identity_without_running_age_keygen(self) -> None:
        identity_path = Path.home() / ".config" / "age" / "key.txt"
        identity_path.parent.mkdir(parents=True)
        identity_path.write_text("AGE-SECRET-KEY-EXISTING\n")

        with (
            patch(
                "popctl.cli.commands.backup.which", return_value="/usr/bin/age-keygen"
            ),
            patch("popctl.cli.commands.backup.run_command") as mock_run,
        ):
            result = runner.invoke(app, ["backup", "init", "--yes"])

        assert result.exit_code == 1
        assert "Refusing to overwrite" in result.output
        mock_run.assert_not_called()

    def test_creates_identity_and_backup_config(self) -> None:
        identity_path = Path.home() / ".config" / "age" / "key.txt"

        def generate_identity(args: list[str]) -> CommandResult:
            assert args == ["/usr/bin/age-keygen", "-o", str(identity_path)]
            identity_path.parent.mkdir(parents=True, exist_ok=True)
            identity_path.write_text("# public key: age1generatedkey\nAGE-SECRET-KEY-TEST\n")
            return CommandResult(
                stdout="", stderr="Public key: age1generatedkey\n", returncode=0
            )

        with (
            patch(
                "popctl.cli.commands.backup.which", return_value="/usr/bin/age-keygen"
            ),
            patch(
                "popctl.cli.commands.backup.run_command", side_effect=generate_identity
            ),
        ):
            result = runner.invoke(app, ["backup", "init", "--yes"])

        config_path = Path(os.environ["XDG_CONFIG_HOME"]) / "popctl" / "backup.toml"
        assert result.exit_code == 0
        assert stat.S_IMODE(identity_path.stat().st_mode) == 0o600
        assert 'recipients = "age1generatedkey"' in config_path.read_text()
        assert f'identity = "{identity_path}"' in config_path.read_text()
        assert "Next: run popctl backup create." in result.output


class TestBackupCreateCommand:
    """Tests for 'popctl backup create' command."""

    def test_create_dry_run(self, tmp_path: object) -> None:
        """Dry-run shows file count without creating archive."""
        mock_files = [
            (__import__("pathlib").Path("/tmp/test/.bashrc"), "files/home/.bashrc"),
        ]
        mock_stat = type("stat", (), {"st_size": 100})()

        with (
            patch("popctl.backup.backup.collect_backup_files", return_value=mock_files),
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.stat", return_value=mock_stat),
        ):
            result = runner.invoke(app, ["backup", "create", "--dry-run"])

        assert result.exit_code == 0
        assert "1 files" in result.output

    def test_create_no_age_shows_error(self) -> None:
        """Shows error when age is not installed."""
        from popctl.backup.backup import BackupError

        with patch(
            "popctl.backup.backup.create_backup",
            side_effect=BackupError("age is not installed"),
        ):
            result = runner.invoke(
                app, ["backup", "create", "--recipient", "age1test"]
            )

        assert result.exit_code == 1
        assert "age is not installed" in result.output


class TestBackupRestoreCommand:
    """Tests for 'popctl backup restore' command."""

    def test_restore_files_only_and_packages_only_conflict(self) -> None:
        """Cannot use --files-only and --packages-only together."""
        result = runner.invoke(
            app,
            ["backup", "restore", "/tmp/backup.tar.zst.age",
             "--files-only", "--packages-only"],
        )
        assert result.exit_code == 1
        assert "Cannot use" in result.output

    def test_restore_shows_metadata_before_confirm(self) -> None:
        """Shows backup metadata and asks for confirmation."""
        from popctl.models.backup import BackupMetadata

        meta = BackupMetadata(
            created="2026-03-06T12:00:00+00:00",
            hostname="testhost",
            popctl_version="0.1.0",
        )

        with patch(
            "popctl.backup.restore.read_backup_metadata",
            return_value=meta,
        ):
            result = runner.invoke(
                app,
                ["backup", "restore", "/tmp/backup.tar.zst.age"],
                input="n\n",
            )

        assert "testhost" in result.output
        assert "2026-03-06" in result.output

    def test_restore_plumbs_source_and_dry_run(self) -> None:
        from popctl.models.backup import BackupMetadata

        meta = BackupMetadata(
            created="2026-03-06T12:00:00+00:00",
            hostname="testhost",
            popctl_version="0.1.0",
        )
        counts = {
            "popctl_state": 0,
            "home_files": 0,
            "packages_installed": 0,
            "packages_failed": 0,
        }

        with (
            patch("popctl.backup.restore.read_backup_metadata", return_value=meta),
            patch("popctl.backup.restore.restore_backup", return_value=counts) as restore_backup,
            patch("popctl.cli.commands.backup.typer.confirm") as confirm,
        ):
            result = runner.invoke(
                app,
                [
                    "backup",
                    "restore",
                    "/tmp/backup.tar.zst.age",
                    "--source",
                    "flatpak",
                    "--dry-run",
                ],
            )

        assert result.exit_code == 0
        confirm.assert_not_called()
        assert restore_backup.call_args.args == ("/tmp/backup.tar.zst.age", None)
        assert restore_backup.call_args.kwargs["package_source"].value == "flatpak"
        assert restore_backup.call_args.kwargs["dry_run"] is True
        assert restore_backup.call_args.kwargs["interaction"].yes is False
        assert "Dry-run mode" in result.output


class TestBackupListCommand:
    """Tests for 'popctl backup list' command."""

    def test_list_no_backups(self) -> None:
        """Shows info message when no backups found."""
        with patch("popctl.backup.restore.list_backups", return_value=[]):
            result = runner.invoke(app, ["backup", "list"])

        assert "No backups found" in result.output

    def test_list_shows_backups(self) -> None:
        """Lists available backups."""
        backups = [
            "popctl-backup-myhost-20260306-120000.tar.zst.age",
            "popctl-backup-myhost-20260307-120000.tar.zst.age",
        ]
        with patch("popctl.backup.restore.list_backups", return_value=backups):
            result = runner.invoke(app, ["backup", "list"])

        assert "2 backups" in result.output or "myhost" in result.output


class TestBackupInfoCommand:
    """Tests for 'popctl backup info' command."""

    def test_info_shows_metadata(self) -> None:
        """Displays metadata from backup."""
        from popctl.models.backup import BackupMetadata

        meta = BackupMetadata(
            created="2026-03-06T12:00:00+00:00",
            hostname="testhost",
            popctl_version="0.1.0",
        )

        with patch(
            "popctl.backup.restore.read_backup_metadata",
            return_value=meta,
        ):
            result = runner.invoke(
                app, ["backup", "info", "/tmp/backup.tar.zst.age"]
            )

        assert result.exit_code == 0
        assert "testhost" in result.output
        assert "0.1.0" in result.output

    def test_info_error_shows_message(self) -> None:
        """Shows error on invalid backup."""
        from popctl.backup.backup import BackupError

        with patch(
            "popctl.backup.restore.read_backup_metadata",
            side_effect=BackupError("file not found"),
        ):
            result = runner.invoke(
                app, ["backup", "info", "/tmp/nonexistent.tar.zst.age"]
            )

        assert result.exit_code == 1
        assert "file not found" in result.output
