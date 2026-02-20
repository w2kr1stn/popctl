"""Unit tests for shell execution utilities."""

from unittest.mock import MagicMock, patch

import pytest
from popctl.utils.shell import run_interactive


class TestRunInteractive:
    """Tests for run_interactive function."""

    @patch("popctl.utils.shell.subprocess.run")
    def test_returns_exit_code(self, mock_run: MagicMock) -> None:
        """run_interactive returns the subprocess exit code."""
        mock_run.return_value = MagicMock(returncode=0)

        result = run_interactive(["echo", "hello"])

        assert result == 0

    @patch("popctl.utils.shell.subprocess.run")
    def test_returns_nonzero_exit_code(self, mock_run: MagicMock) -> None:
        """run_interactive returns nonzero exit codes."""
        mock_run.return_value = MagicMock(returncode=1)

        result = run_interactive(["false"])

        assert result == 1

    @patch("popctl.utils.shell.subprocess.run")
    def test_does_not_capture_output(self, mock_run: MagicMock) -> None:
        """run_interactive does not capture stdout/stderr (inherits TTY)."""
        mock_run.return_value = MagicMock(returncode=0)

        run_interactive(["echo", "hello"])

        call_kwargs = mock_run.call_args
        # subprocess.run should NOT have capture_output or stdout/stderr pipes
        assert "capture_output" not in call_kwargs.kwargs
        assert "stdout" not in call_kwargs.kwargs
        assert "stderr" not in call_kwargs.kwargs

    @patch("popctl.utils.shell.subprocess.run")
    def test_passes_cwd(self, mock_run: MagicMock) -> None:
        """run_interactive passes the working directory."""
        mock_run.return_value = MagicMock(returncode=0)

        run_interactive(["ls"], cwd="/tmp")

        mock_run.assert_called_once()
        assert mock_run.call_args.kwargs["cwd"] == "/tmp"

    @patch("popctl.utils.shell.subprocess.run")
    def test_merges_env(self, mock_run: MagicMock) -> None:
        """run_interactive merges custom env with current environment."""
        mock_run.return_value = MagicMock(returncode=0)

        run_interactive(["echo"], env={"MY_VAR": "value"})

        call_env = mock_run.call_args.kwargs["env"]
        assert call_env["MY_VAR"] == "value"
        # Should also contain inherited env vars
        assert "PATH" in call_env

    @patch("popctl.utils.shell.subprocess.run")
    def test_no_env_inherits_all(self, mock_run: MagicMock) -> None:
        """run_interactive with no env still passes inherited environment."""
        mock_run.return_value = MagicMock(returncode=0)

        run_interactive(["echo"])

        call_env = mock_run.call_args.kwargs["env"]
        assert "PATH" in call_env

    def test_raises_file_not_found(self) -> None:
        """run_interactive raises FileNotFoundError for missing commands."""
        with pytest.raises(FileNotFoundError):
            run_interactive(["nonexistent_command_xyz_12345"])
