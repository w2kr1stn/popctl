"""Unit tests for ConfigOperator.

Tests backup and deletion of config directories and files, protected
path rejection, dry-run mode, backup failure handling, and relative
path structure preservation in backups.
"""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from popctl.configs.operator import ConfigActionResult, ConfigOperator


class TestConfigOperator:
    """Tests for ConfigOperator."""

    def test_delete_directory_with_backup(self, tmp_path: Path) -> None:
        """Deleting a directory creates backup then removes it."""
        home = tmp_path / "home"
        home.mkdir()
        config_dir = home / ".config" / "old_app"
        config_dir.mkdir(parents=True)
        (config_dir / "settings.conf").write_text("key=value")

        backup_base = tmp_path / "backups"
        backup_base.mkdir()

        with (
            patch("popctl.configs.operator.Path.home", return_value=home),
            patch(
                "popctl.configs.operator.ensure_config_backup_dir",
                return_value=backup_base,
            ),
        ):
            op = ConfigOperator(dry_run=False)
            results = op.delete([str(config_dir)])

        assert len(results) == 1
        assert results[0].success is True
        assert results[0].dry_run is False
        assert results[0].backup_path is not None
        assert not config_dir.exists()

        # Verify backup was created
        backup_path = Path(results[0].backup_path)
        assert backup_path.exists()
        assert (backup_path / "settings.conf").exists()
        assert (backup_path / "settings.conf").read_text() == "key=value"

    def test_delete_file_with_backup(self, tmp_path: Path) -> None:
        """Deleting a file creates backup then removes it."""
        home = tmp_path / "home"
        home.mkdir()
        config_file = home / ".gitconfig"
        config_file.write_text("[user]\nname = Test")

        backup_base = tmp_path / "backups"
        backup_base.mkdir()

        with (
            patch("popctl.configs.operator.Path.home", return_value=home),
            patch(
                "popctl.configs.operator.ensure_config_backup_dir",
                return_value=backup_base,
            ),
        ):
            op = ConfigOperator(dry_run=False)
            results = op.delete([str(config_file)])

        assert len(results) == 1
        assert results[0].success is True
        assert results[0].backup_path is not None
        assert not config_file.exists()

        # Verify backup content
        backup_path = Path(results[0].backup_path)
        assert backup_path.exists()
        assert backup_path.read_text() == "[user]\nname = Test"

    def test_delete_protected_config_rejected(self, tmp_path: Path) -> None:
        """Protected config paths are rejected with an error result."""
        home = str(Path.home())
        protected_path = f"{home}/.bashrc"

        backup_base = tmp_path / "backups"
        backup_base.mkdir()

        with patch(
            "popctl.configs.operator.ensure_config_backup_dir",
            return_value=backup_base,
        ):
            op = ConfigOperator(dry_run=False)
            results = op.delete([protected_path])

        assert len(results) == 1
        assert results[0].success is False
        assert results[0].error is not None
        assert "Protected config" in results[0].error

    def test_delete_nonexistent_path(self, tmp_path: Path) -> None:
        """Deleting a nonexistent path returns a failure result."""
        nonexistent = str(tmp_path / "nonexistent_config")

        backup_base = tmp_path / "backups"
        backup_base.mkdir()

        with patch(
            "popctl.configs.operator.ensure_config_backup_dir",
            return_value=backup_base,
        ):
            op = ConfigOperator(dry_run=False)
            results = op.delete([nonexistent])

        assert len(results) == 1
        assert results[0].success is False
        assert results[0].error is not None
        assert "does not exist" in results[0].error

    def test_delete_dry_run(self, tmp_path: Path) -> None:
        """Dry-run mode returns success without deleting or backing up."""
        home = tmp_path / "home"
        home.mkdir()
        config_dir = home / ".config" / "old_app"
        config_dir.mkdir(parents=True)
        config_file = home / ".old_config"
        config_file.write_text("content")

        backup_base = tmp_path / "backups"
        backup_base.mkdir()

        with (
            patch("popctl.configs.operator.Path.home", return_value=home),
            patch(
                "popctl.configs.operator.ensure_config_backup_dir",
                return_value=backup_base,
            ),
        ):
            op = ConfigOperator(dry_run=True)
            results = op.delete([str(config_dir), str(config_file)])

        assert len(results) == 2
        for result in results:
            assert result.success is True
            assert result.dry_run is True
            assert result.error is None

        # Nothing should be deleted
        assert config_dir.exists()
        assert config_file.exists()

    def test_delete_backup_failure_continues(self, tmp_path: Path) -> None:
        """Backup failure does not prevent deletion."""
        home = tmp_path / "home"
        home.mkdir()
        config_dir = home / ".config" / "old_app"
        config_dir.mkdir(parents=True)
        (config_dir / "file.txt").write_text("content")

        backup_base = tmp_path / "backups"
        backup_base.mkdir()

        with (
            patch("popctl.configs.operator.Path.home", return_value=home),
            patch(
                "popctl.configs.operator.ensure_config_backup_dir",
                return_value=backup_base,
            ),
            patch("popctl.configs.operator.shutil.copytree", side_effect=OSError("Disk full")),
        ):
            op = ConfigOperator(dry_run=False)
            results = op.delete([str(config_dir)])

        assert len(results) == 1
        assert results[0].success is True
        assert results[0].backup_path is None  # Backup failed
        assert not config_dir.exists()  # But deletion succeeded

    def test_backup_creates_relative_structure(self, tmp_path: Path) -> None:
        """Backup preserves relative path from home directory."""
        home = tmp_path / "home"
        home.mkdir()
        config_dir = home / ".config" / "old_app" / "subdir"
        config_dir.mkdir(parents=True)
        (config_dir / "nested.conf").write_text("nested")

        # Create parent to also test the directory backup
        parent_dir = home / ".config" / "old_app"

        backup_base = tmp_path / "backups"
        backup_base.mkdir()

        with (
            patch("popctl.configs.operator.Path.home", return_value=home),
            patch(
                "popctl.configs.operator.ensure_config_backup_dir",
                return_value=backup_base,
            ),
        ):
            op = ConfigOperator(dry_run=False)
            results = op.delete([str(parent_dir)])

        assert len(results) == 1
        assert results[0].success is True
        assert results[0].backup_path is not None

        # Verify the relative structure is preserved
        backup_path = Path(results[0].backup_path)
        assert backup_path.name == "old_app"
        # The backup should be under <timestamp>/.config/old_app
        assert ".config" in str(backup_path)
        assert (backup_path / "subdir" / "nested.conf").exists()

    def test_delete_permission_error(self, tmp_path: Path) -> None:
        """OSError during deletion returns a failure result."""
        home = tmp_path / "home"
        home.mkdir()
        config_file = home / ".some_config"
        config_file.write_text("content")

        backup_base = tmp_path / "backups"
        backup_base.mkdir()

        with (
            patch("popctl.configs.operator.Path.home", return_value=home),
            patch(
                "popctl.configs.operator.ensure_config_backup_dir",
                return_value=backup_base,
            ),
            patch.object(Path, "unlink", side_effect=OSError("Permission denied")),
        ):
            op = ConfigOperator(dry_run=False)
            results = op.delete([str(config_file)])

        assert len(results) == 1
        assert results[0].success is False
        assert results[0].error is not None
        assert "Permission denied" in results[0].error

    def test_delete_empty_list(self) -> None:
        """Deleting an empty list returns empty results."""
        op = ConfigOperator(dry_run=False)
        results = op.delete([])
        assert results == []

    def test_config_action_result_defaults(self) -> None:
        """ConfigActionResult has correct defaults."""
        result = ConfigActionResult(path="/test", success=True)
        assert result.error is None
        assert result.dry_run is False
        assert result.backup_path is None

    def test_config_action_result_frozen(self) -> None:
        """ConfigActionResult is immutable."""
        result = ConfigActionResult(path="/test", success=True)
        with pytest.raises(AttributeError):
            result.success = False  # type: ignore[misc]

    @patch("popctl.configs.operator.is_protected_config")
    def test_delete_protected_via_mock(self, mock_protected: MagicMock, tmp_path: Path) -> None:
        """Protected path check delegates to is_protected_config."""
        mock_protected.return_value = True

        backup_base = tmp_path / "backups"
        backup_base.mkdir()

        with patch(
            "popctl.configs.operator.ensure_config_backup_dir",
            return_value=backup_base,
        ):
            op = ConfigOperator(dry_run=False)
            results = op.delete(["/some/random/path"])

        assert len(results) == 1
        assert results[0].success is False
        mock_protected.assert_called_once_with("/some/random/path")
