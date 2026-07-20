"""Unit tests for shell execution utilities."""

import subprocess
from unittest.mock import MagicMock, patch

import pytest
from popctl.utils.shell import run_command, run_command_bytes, run_interactive


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

    @patch("popctl.utils.shell.subprocess.run", side_effect=FileNotFoundError)
    def test_raises_file_not_found(self, mock_run: MagicMock) -> None:
        """run_interactive raises FileNotFoundError for missing commands."""
        with pytest.raises(FileNotFoundError):
            run_interactive(["nonexistent_command_xyz_12345"])


class TestRunCommandBytes:
    @patch("popctl.utils.shell.subprocess.run")
    def test_preserves_crlf_and_non_utf8_output(self, mock_run: MagicMock) -> None:
        stdout = b"first\r\nsecond\r\n\xff"
        stderr = b"warning\r\n\xfe"
        mock_run.return_value = MagicMock(stdout=stdout, stderr=stderr, returncode=0)

        result = run_command_bytes(["git", "cat-file", "blob"])

        assert result.stdout == stdout
        assert result.stderr == stderr
        assert result.success
        assert mock_run.call_args.kwargs["text"] is False

    @patch("popctl.utils.shell.subprocess.run")
    def test_passes_bytes_stdin_and_replaces_environment(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(stdout=b"", stderr=b"", returncode=0)

        run_command_bytes(["git", "hash-object", "--stdin"], input=b"a\r\n\xff", env={"PATH": "/x"})

        assert mock_run.call_args.kwargs["input"] == b"a\r\n\xff"
        assert mock_run.call_args.kwargs["env"] == {"PATH": "/x"}

    @patch("popctl.utils.shell.subprocess.run")
    def test_text_api_keeps_merging_environment(self, mock_run: MagicMock) -> None:
        mock_run.return_value = MagicMock(stdout="", stderr="", returncode=0)

        run_command(["git", "status"], env={"POPCTL_TEST": "1"})

        env = mock_run.call_args.kwargs["env"]
        assert env["POPCTL_TEST"] == "1"
        assert "PATH" in env

    @patch("popctl.utils.shell.subprocess.run", side_effect=subprocess.TimeoutExpired("git", 3))
    def test_timeout_keeps_byte_result(self, mock_run: MagicMock) -> None:
        result = run_command_bytes(["git", "cat-file"], timeout=3)

        assert result.stdout == b""
        assert result.stderr == b"Command timed out after 3s: git cat-file"
        assert result.returncode == -1
        mock_run.assert_called_once()


class TestRunCommand:
    @patch("popctl.utils.shell._run_subprocess", side_effect=FileNotFoundError)
    def test_missing_binary_returns_failed_result(self, _run_subprocess: MagicMock) -> None:
        result = run_command(["missing-binary"])

        assert result.success is False
        assert result.returncode == -1
        assert result.stderr == "Command not found: missing-binary"
