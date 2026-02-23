"""Unit tests for FilesystemOperator.

Tests deletion of directories, files, symlinks, protected path
rejection, dry-run mode, sudo escalation, and error handling.
"""

from pathlib import Path
from unittest.mock import patch

import pytest
from popctl.filesystem.operator import FilesystemActionResult, FilesystemOperator
from popctl.utils.shell import CommandResult


class TestFilesystemOperator:
    """Tests for FilesystemOperator."""

    def test_delete_directory(self, tmp_path: Path) -> None:
        """Deleting a directory uses shutil.rmtree."""
        target = tmp_path / "orphan_dir"
        target.mkdir()
        (target / "file.txt").write_text("content")

        op = FilesystemOperator()
        results = op.delete([str(target)])

        assert len(results) == 1
        assert results[0].success is True
        assert results[0].path == str(target)
        assert results[0].dry_run is False
        assert not target.exists()

    def test_delete_file(self, tmp_path: Path) -> None:
        """Deleting a file uses Path.unlink."""
        target = tmp_path / "orphan_file.txt"
        target.write_text("content")

        op = FilesystemOperator()
        results = op.delete([str(target)])

        assert len(results) == 1
        assert results[0].success is True
        assert not target.exists()

    def test_delete_symlink(self, tmp_path: Path) -> None:
        """Deleting a symlink removes the link, not the target."""
        real_file = tmp_path / "real_file.txt"
        real_file.write_text("content")
        link = tmp_path / "link_to_file"
        link.symlink_to(real_file)

        op = FilesystemOperator()
        results = op.delete([str(link)])

        assert len(results) == 1
        assert results[0].success is True
        assert not link.exists()
        # Original file should still exist
        assert real_file.exists()

    def test_delete_dead_symlink(self, tmp_path: Path) -> None:
        """Deleting a dead symlink succeeds."""
        link = tmp_path / "dead_link"
        link.symlink_to("/nonexistent/target")
        assert link.is_symlink()
        assert not link.exists()

        op = FilesystemOperator()
        results = op.delete([str(link)])

        assert len(results) == 1
        assert results[0].success is True
        assert not link.is_symlink()

    def test_delete_protected_path_rejected(self) -> None:
        """Protected paths are rejected with an error result."""
        home = str(Path.home())
        protected_path = f"{home}/.ssh/id_rsa"

        op = FilesystemOperator()
        results = op.delete([protected_path])

        assert len(results) == 1
        assert results[0].success is False
        assert results[0].error is not None
        assert "Protected path" in results[0].error

    def test_delete_nonexistent_path(self) -> None:
        """Deleting a nonexistent path returns a failure result."""
        op = FilesystemOperator()
        results = op.delete(["/tmp/nonexistent_path_abc123xyz"])

        assert len(results) == 1
        assert results[0].success is False
        assert results[0].error is not None
        assert "does not exist" in results[0].error

    def test_delete_dry_run(self, tmp_path: Path) -> None:
        """Dry-run mode returns success without deleting anything."""
        target_dir = tmp_path / "keep_dir"
        target_dir.mkdir()
        target_file = tmp_path / "keep_file.txt"
        target_file.write_text("content")

        op = FilesystemOperator(dry_run=True)
        results = op.delete([str(target_dir), str(target_file)])

        assert len(results) == 2
        for result in results:
            assert result.success is True
            assert result.dry_run is True
            assert result.error is None

        # Nothing should be deleted
        assert target_dir.exists()
        assert target_file.exists()

    @patch("popctl.filesystem.operator.run_command")
    def test_delete_etc_uses_sudo(self, mock_run: object) -> None:
        """Paths under /etc use sudo rm -rf via run_command."""
        from unittest.mock import MagicMock

        mock_run_typed = MagicMock(return_value=CommandResult(stdout="", stderr="", returncode=0))
        with patch("popctl.filesystem.operator.run_command", mock_run_typed):
            op = FilesystemOperator()
            results = op.delete(["/etc/old_app/config.conf"])

        assert len(results) == 1
        assert results[0].success is True
        mock_run_typed.assert_called_once_with(["sudo", "rm", "-rf", "/etc/old_app/config.conf"])

    @patch("popctl.filesystem.operator.run_command")
    def test_delete_etc_sudo_failure(self, mock_run: object) -> None:
        """Failed sudo rm returns failure result with stderr."""
        from unittest.mock import MagicMock

        mock_run_typed = MagicMock(
            return_value=CommandResult(stdout="", stderr="Permission denied", returncode=1)
        )
        with patch("popctl.filesystem.operator.run_command", mock_run_typed):
            op = FilesystemOperator()
            results = op.delete(["/etc/old_app/config.conf"])

        assert len(results) == 1
        assert results[0].success is False
        assert results[0].error == "Permission denied"

    def test_delete_permission_error(self, tmp_path: Path) -> None:
        """OSError during deletion returns a failure result."""
        target = tmp_path / "no_perm_file.txt"
        target.write_text("content")

        with (
            patch.object(Path, "is_dir", return_value=False),
            patch.object(Path, "exists", return_value=True),
            patch.object(Path, "is_symlink", return_value=False),
            patch.object(Path, "unlink", side_effect=OSError("Permission denied")),
        ):
            op = FilesystemOperator()
            results = op.delete([str(target)])

        assert len(results) == 1
        assert results[0].success is False
        assert results[0].error is not None
        assert "Permission denied" in results[0].error

    def test_is_available_always_true(self) -> None:
        """is_available always returns True."""
        op = FilesystemOperator()
        assert op.is_available() is True

        op_dry = FilesystemOperator(dry_run=True)
        assert op_dry.is_available() is True

    def test_delete_multiple_paths(self, tmp_path: Path) -> None:
        """Deleting multiple paths returns mixed results."""
        good_file = tmp_path / "good.txt"
        good_file.write_text("content")
        good_dir = tmp_path / "good_dir"
        good_dir.mkdir()
        nonexistent = str(tmp_path / "nonexistent")

        op = FilesystemOperator()
        results = op.delete([str(good_file), nonexistent, str(good_dir)])

        assert len(results) == 3
        # First: success (file deleted)
        assert results[0].success is True
        assert not good_file.exists()
        # Second: failure (nonexistent)
        assert results[1].success is False
        assert results[1].error is not None
        # Third: success (dir deleted)
        assert results[2].success is True
        assert not good_dir.exists()

    def test_delete_empty_list(self) -> None:
        """Deleting an empty list returns empty results."""
        op = FilesystemOperator()
        results = op.delete([])
        assert results == []

    @patch("popctl.filesystem.operator.is_protected_path")
    def test_delete_protected_via_mock(self, mock_protected: object) -> None:
        """Protected path check delegates to is_protected_path."""
        from unittest.mock import MagicMock

        mock_protected_typed = MagicMock(return_value=True)
        with patch("popctl.filesystem.operator.is_protected_path", mock_protected_typed):
            op = FilesystemOperator()
            results = op.delete(["/some/random/path"])

        assert len(results) == 1
        assert results[0].success is False
        mock_protected_typed.assert_called_once_with("/some/random/path")

    def test_filesystem_action_result_defaults(self) -> None:
        """FilesystemActionResult has correct defaults."""
        result = FilesystemActionResult(path="/test", success=True)
        assert result.error is None
        assert result.dry_run is False

    def test_filesystem_action_result_frozen(self) -> None:
        """FilesystemActionResult is immutable."""
        result = FilesystemActionResult(path="/test", success=True)
        with pytest.raises(AttributeError):
            result.success = False  # type: ignore[misc]
