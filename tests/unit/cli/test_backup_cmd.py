"""Unit tests for backup CLI commands."""

from unittest.mock import patch

import pytest
from typer.testing import CliRunner

from popctl.cli.main import app

runner = CliRunner()


class TestBackupCreateCommand:
    """Tests for 'popctl backup create' command."""

    def test_create_dry_run(self, tmp_path: object) -> None:
        """Dry-run shows file count without creating archive."""
        mock_files = [
            (__import__("pathlib").Path("/tmp/test/.bashrc"), "files/home/.bashrc"),
        ]
        mock_path = __import__("pathlib").Path("/tmp/test/.bashrc")
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
