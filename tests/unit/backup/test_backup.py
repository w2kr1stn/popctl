"""Unit tests for backup creation module."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from popctl.backup.backup import (
    BackupError,
    is_rclone_remote,
    _should_exclude_dir,
    _walk_home,
    collect_backup_files,
)


class TestShouldExcludeDir:
    """Tests for directory exclusion logic."""

    def test_excludes_pycache(self) -> None:
        assert _should_exclude_dir(Path("project/__pycache__")) is True

    def test_excludes_cache(self) -> None:
        assert _should_exclude_dir(Path(".cache")) is True

    def test_excludes_node_modules(self) -> None:
        assert _should_exclude_dir(Path("project/node_modules")) is True

    def test_excludes_venv(self) -> None:
        assert _should_exclude_dir(Path("project/.venv")) is True

    def test_excludes_git_objects(self) -> None:
        assert _should_exclude_dir(Path("project/.git/objects")) is True

    def test_excludes_ruff_cache(self) -> None:
        assert _should_exclude_dir(Path(".ruff_cache")) is True

    def test_excludes_pytest_cache(self) -> None:
        assert _should_exclude_dir(Path(".pytest_cache")) is True

    def test_allows_normal_dir(self) -> None:
        assert _should_exclude_dir(Path("projects/my-app")) is False

    def test_allows_dotfile_dir(self) -> None:
        assert _should_exclude_dir(Path(".config")) is False

    def test_allows_ssh(self) -> None:
        assert _should_exclude_dir(Path(".ssh")) is False


class TestIsRcloneRemote:
    """Tests for rclone remote detection."""

    def test_detects_gdrive_remote(self) -> None:
        assert is_rclone_remote("gdrive:backups/") is True

    def test_detects_s3_remote(self) -> None:
        assert is_rclone_remote("s3:bucket/path") is True

    def test_rejects_local_path(self) -> None:
        assert is_rclone_remote("/mnt/usb/backups") is False

    def test_rejects_empty_string(self) -> None:
        assert is_rclone_remote("") is False

    def test_rejects_relative_path(self) -> None:
        assert is_rclone_remote("backups/dir") is False


class TestWalkHome:
    """Tests for home directory walking."""

    def test_walk_home_skips_symlink_dirs(self, tmp_path: Path) -> None:
        """Symlink directories are excluded from the walk."""
        # Create a real dir and a symlink dir
        real_dir = tmp_path / "real_project"
        real_dir.mkdir()
        (real_dir / "file.txt").write_text("content")

        # Create external target OUTSIDE of home (simulates external drive)
        external = tmp_path.parent / "external_data"
        external.mkdir(exist_ok=True)
        (external / "big_file.dat").write_text("data")

        # Symlink inside home pointing to external
        symlink_dir = tmp_path / "Documents"
        symlink_dir.symlink_to(external)

        with patch("popctl.backup.backup.Path.home", return_value=tmp_path):
            files = _walk_home()

        archive_paths = [ap for _, ap in files]
        assert any("real_project/file.txt" in ap for ap in archive_paths)
        assert not any("Documents" in ap for ap in archive_paths)
        assert not any("big_file.dat" in ap for ap in archive_paths)

    def test_walk_home_includes_dotfiles(self, tmp_path: Path) -> None:
        """Dotfiles in home root are included."""
        (tmp_path / ".bashrc").write_text("# bash config")
        (tmp_path / ".gitconfig").write_text("[user]")

        with patch("popctl.backup.backup.Path.home", return_value=tmp_path):
            files = _walk_home()

        archive_paths = [ap for _, ap in files]
        assert any(".bashrc" in ap for ap in archive_paths)
        assert any(".gitconfig" in ap for ap in archive_paths)

    def test_walk_home_excludes_cache_dirs(self, tmp_path: Path) -> None:
        """Cache directories are excluded."""
        cache_dir = tmp_path / ".cache"
        cache_dir.mkdir()
        (cache_dir / "something").write_text("cached")

        with patch("popctl.backup.backup.Path.home", return_value=tmp_path):
            files = _walk_home()

        archive_paths = [ap for _, ap in files]
        assert not any(".cache" in ap for ap in archive_paths)


class TestCollectBackupFiles:
    """Tests for the full file collection."""

    def test_includes_popctl_state(self, tmp_path: Path) -> None:
        """popctl state files are collected separately."""
        config_dir = tmp_path / "config"
        state_dir = tmp_path / "state"
        config_dir.mkdir(parents=True)
        state_dir.mkdir(parents=True)
        (config_dir / "manifest.toml").write_text("[meta]")
        (state_dir / "history.jsonl").write_text("{}")

        with (
            patch("popctl.backup.backup.get_config_dir", return_value=config_dir),
            patch("popctl.backup.backup.get_state_dir", return_value=state_dir),
            patch("popctl.backup.backup.Path.home", return_value=tmp_path),
        ):
            files = collect_backup_files()

        archive_paths = [ap for _, ap in files]
        assert "files/popctl/manifest.toml" in archive_paths
        assert "files/popctl/history.jsonl" in archive_paths

    def test_deduplicates_files(self, tmp_path: Path) -> None:
        """Files appearing in both popctl state and home walk are deduplicated."""
        config_dir = tmp_path / ".config" / "popctl"
        config_dir.mkdir(parents=True)
        state_dir = tmp_path / ".local" / "state" / "popctl"
        state_dir.mkdir(parents=True)
        manifest = config_dir / "manifest.toml"
        manifest.write_text("[meta]")

        with (
            patch("popctl.backup.backup.get_config_dir", return_value=config_dir),
            patch("popctl.backup.backup.get_state_dir", return_value=state_dir),
            patch("popctl.backup.backup.Path.home", return_value=tmp_path),
        ):
            files = collect_backup_files()

        # manifest.toml should appear only once (in popctl category, added first)
        resolved_paths = [p.resolve() for p, _ in files]
        assert resolved_paths.count(manifest.resolve()) == 1


class TestCreateBackup:
    """Tests for backup archive creation."""

    def test_create_backup_missing_age_raises(self) -> None:
        """Raises BackupError if age is not installed."""
        with patch("popctl.backup.backup.command_exists", return_value=False):
            with pytest.raises(BackupError, match="age is not installed"):
                from popctl.backup.backup import create_backup

                create_backup(recipient="age1test")

    def test_create_backup_no_recipient_no_default_raises(self, tmp_path: Path) -> None:
        """Raises BackupError if no recipient and no default file."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()

        def mock_exists(name: str) -> bool:
            return name in ("age", "zstd")

        with (
            patch("popctl.backup.backup.command_exists", side_effect=mock_exists),
            patch("popctl.backup.backup.get_config_dir", return_value=config_dir),
        ):
            with pytest.raises(BackupError, match="No age recipient specified"):
                from popctl.backup.backup import create_backup

                create_backup()
