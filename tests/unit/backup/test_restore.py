"""Unit tests for backup restore module."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from popctl.backup.backup import BackupError
from popctl.backup.restore import (
    _fix_sensitive_permissions,
    _restore_home_files,
    _restore_popctl_state,
    list_backups,
)
from popctl.models.backup import BackupMetadata


class TestRestorePopctlState:
    """Tests for popctl state file restoration."""

    def test_restores_manifest(self, tmp_path: Path) -> None:
        """Restores manifest.toml to config dir."""
        staging = tmp_path / "staging"
        popctl_dir = staging / "files" / "popctl"
        popctl_dir.mkdir(parents=True)
        (popctl_dir / "manifest.toml").write_text("[meta]\ncreated = '2026-01-01'")

        config_dir = tmp_path / "config"
        state_dir = tmp_path / "state"

        with (
            patch("popctl.backup.restore.get_config_dir", return_value=config_dir),
            patch("popctl.backup.restore.get_state_dir", return_value=state_dir),
        ):
            count = _restore_popctl_state(staging)

        assert count == 1
        assert (config_dir / "manifest.toml").exists()
        assert "[meta]" in (config_dir / "manifest.toml").read_text()

    def test_restores_history(self, tmp_path: Path) -> None:
        """Restores history.jsonl to state dir."""
        staging = tmp_path / "staging"
        popctl_dir = staging / "files" / "popctl"
        popctl_dir.mkdir(parents=True)
        (popctl_dir / "history.jsonl").write_text('{"id":"abc"}\n')

        config_dir = tmp_path / "config"
        state_dir = tmp_path / "state"

        with (
            patch("popctl.backup.restore.get_config_dir", return_value=config_dir),
            patch("popctl.backup.restore.get_state_dir", return_value=state_dir),
        ):
            count = _restore_popctl_state(staging)

        assert count == 1
        assert (state_dir / "history.jsonl").exists()

    def test_restores_advisor_memory(self, tmp_path: Path) -> None:
        """Restores advisor memory to nested state dir."""
        staging = tmp_path / "staging"
        popctl_dir = staging / "files" / "popctl"
        popctl_dir.mkdir(parents=True)
        (popctl_dir / "advisor-memory.md").write_text("# Memory")

        config_dir = tmp_path / "config"
        state_dir = tmp_path / "state"

        with (
            patch("popctl.backup.restore.get_config_dir", return_value=config_dir),
            patch("popctl.backup.restore.get_state_dir", return_value=state_dir),
        ):
            count = _restore_popctl_state(staging)

        assert count == 1
        assert (state_dir / "advisor" / "memory.md").exists()

    def test_returns_zero_if_no_popctl_dir(self, tmp_path: Path) -> None:
        """Returns 0 if staging has no popctl files."""
        staging = tmp_path / "staging"
        staging.mkdir()

        config_dir = tmp_path / "config"
        state_dir = tmp_path / "state"

        with (
            patch("popctl.backup.restore.get_config_dir", return_value=config_dir),
            patch("popctl.backup.restore.get_state_dir", return_value=state_dir),
        ):
            count = _restore_popctl_state(staging)

        assert count == 0


class TestRestoreHomeFiles:
    """Tests for home directory file restoration."""

    def test_restores_files_to_home(self, tmp_path: Path) -> None:
        """Files from staging/files/home/ are copied to current $HOME."""
        staging = tmp_path / "staging"
        home_dir = staging / "files" / "home"
        home_dir.mkdir(parents=True)
        (home_dir / ".bashrc").write_text("# bash config")

        subdir = home_dir / "projects" / "myapp"
        subdir.mkdir(parents=True)
        (subdir / "main.py").write_text("print('hello')")

        target_home = tmp_path / "target_home"
        target_home.mkdir()

        with patch("popctl.backup.restore.Path.home", return_value=target_home):
            count = _restore_home_files(staging)

        assert count == 2
        assert (target_home / ".bashrc").read_text() == "# bash config"
        assert (target_home / "projects" / "myapp" / "main.py").read_text() == "print('hello')"

    def test_returns_zero_if_no_home_dir(self, tmp_path: Path) -> None:
        """Returns 0 if staging has no home files."""
        staging = tmp_path / "staging"
        staging.mkdir()

        with patch("popctl.backup.restore.Path.home", return_value=tmp_path):
            count = _restore_home_files(staging)

        assert count == 0


class TestFixSensitivePermissions:
    """Tests for SSH/GPG permission fixing."""

    def test_fixes_ssh_permissions(self, tmp_path: Path) -> None:
        """Sets correct permissions on .ssh directory and files."""
        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir()
        key_file = ssh_dir / "id_ed25519"
        key_file.write_text("private key")
        key_file.chmod(0o644)  # wrong permission

        with patch("popctl.backup.restore.Path.home", return_value=tmp_path):
            _fix_sensitive_permissions()

        assert oct(ssh_dir.stat().st_mode)[-3:] == "700"
        assert oct(key_file.stat().st_mode)[-3:] == "600"

    def test_fixes_gnupg_permissions(self, tmp_path: Path) -> None:
        """Sets correct permissions on .gnupg directory."""
        gnupg_dir = tmp_path / ".gnupg"
        gnupg_dir.mkdir()
        gnupg_dir.chmod(0o755)  # wrong permission

        with patch("popctl.backup.restore.Path.home", return_value=tmp_path):
            _fix_sensitive_permissions()

        assert oct(gnupg_dir.stat().st_mode)[-3:] == "700"

    def test_handles_missing_dirs(self, tmp_path: Path) -> None:
        """Does not raise if .ssh/.gnupg don't exist."""
        with patch("popctl.backup.restore.Path.home", return_value=tmp_path):
            _fix_sensitive_permissions()  # should not raise


class TestListBackups:
    """Tests for backup listing."""

    def test_list_local_backups(self, tmp_path: Path) -> None:
        """Lists backup files from local directory."""
        (tmp_path / "popctl-backup-host-20260306-120000.tar.zst.age").write_text("")
        (tmp_path / "popctl-backup-host-20260307-120000.tar.zst.age").write_text("")
        (tmp_path / "other-file.txt").write_text("")

        result = list_backups(str(tmp_path))
        assert len(result) == 2
        assert "popctl-backup-host-20260306-120000.tar.zst.age" in result

    def test_list_empty_directory(self, tmp_path: Path) -> None:
        """Returns empty list for directory with no backups."""
        result = list_backups(str(tmp_path))
        assert result == []

    def test_list_nonexistent_directory(self, tmp_path: Path) -> None:
        """Returns empty list for non-existent directory."""
        result = list_backups(str(tmp_path / "nonexistent"))
        assert result == []

    def test_list_default_directory(self, tmp_path: Path) -> None:
        """Uses default backup dir when no target given."""
        with patch("popctl.backup.restore.get_backups_dir", return_value=tmp_path):
            result = list_backups()
        assert result == []
