"""Unit tests for shell execution utilities."""

from unittest.mock import MagicMock, patch

import pytest
from popctl.utils.shell import (
    CommandResult,
    docker_cp,
    is_container_running,
    run_interactive,
)


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


class TestIsContainerRunning:
    """Tests for is_container_running function."""

    @patch("popctl.utils.shell.run_command")
    def test_returns_true_when_running(self, mock_run: MagicMock) -> None:
        """is_container_running returns True when container is found."""
        mock_run.return_value = CommandResult(stdout="ai-dev\n", stderr="", returncode=0)

        assert is_container_running("ai-dev") is True

    @patch("popctl.utils.shell.run_command")
    def test_returns_false_when_not_running(self, mock_run: MagicMock) -> None:
        """is_container_running returns False when container not found."""
        mock_run.return_value = CommandResult(stdout="", stderr="", returncode=0)

        assert is_container_running("ai-dev") is False

    @patch("popctl.utils.shell.run_command")
    def test_returns_false_on_docker_error(self, mock_run: MagicMock) -> None:
        """is_container_running returns False when docker command fails."""
        mock_run.return_value = CommandResult(stdout="", stderr="error", returncode=1)

        assert is_container_running("ai-dev") is False

    @patch("popctl.utils.shell.run_command")
    def test_returns_false_when_docker_not_installed(self, mock_run: MagicMock) -> None:
        """is_container_running returns False when docker is not installed."""
        mock_run.side_effect = FileNotFoundError("docker not found")

        assert is_container_running("ai-dev") is False

    @patch("popctl.utils.shell.run_command")
    def test_returns_false_on_os_error(self, mock_run: MagicMock) -> None:
        """is_container_running returns False on OSError."""
        mock_run.side_effect = OSError("permission denied")

        assert is_container_running("ai-dev") is False

    @patch("popctl.utils.shell.run_command")
    def test_default_container_name(self, mock_run: MagicMock) -> None:
        """is_container_running defaults to 'ai-dev' container name."""
        mock_run.return_value = CommandResult(stdout="ai-dev\n", stderr="", returncode=0)

        is_container_running()

        args = mock_run.call_args[0][0]
        assert "name=ai-dev" in " ".join(args)

    @patch("popctl.utils.shell.run_command")
    def test_custom_container_name(self, mock_run: MagicMock) -> None:
        """is_container_running accepts custom container name."""
        mock_run.return_value = CommandResult(stdout="my-container\n", stderr="", returncode=0)

        assert is_container_running("my-container") is True

    @patch("popctl.utils.shell.run_command")
    def test_does_not_match_partial_names(self, mock_run: MagicMock) -> None:
        """is_container_running does not match partial container names."""
        mock_run.return_value = CommandResult(stdout="ai-dev-other\n", stderr="", returncode=0)

        assert is_container_running("ai-dev") is False


class TestDockerCp:
    """Tests for docker_cp function."""

    @patch("popctl.utils.shell.run_command")
    def test_copies_host_to_container(self, mock_run: MagicMock) -> None:
        """docker_cp calls docker cp with correct arguments."""
        mock_run.return_value = CommandResult(stdout="", stderr="", returncode=0)

        result = docker_cp("/tmp/workspace", "ai-dev:/tmp/advisor/")

        mock_run.assert_called_once_with(
            ["docker", "cp", "/tmp/workspace", "ai-dev:/tmp/advisor/"],
            timeout=60.0,
        )
        assert result.success

    @patch("popctl.utils.shell.run_command")
    def test_copies_container_to_host(self, mock_run: MagicMock) -> None:
        """docker_cp can copy from container to host."""
        mock_run.return_value = CommandResult(stdout="", stderr="", returncode=0)

        result = docker_cp("ai-dev:/tmp/decisions.toml", "/tmp/output/")

        mock_run.assert_called_once_with(
            ["docker", "cp", "ai-dev:/tmp/decisions.toml", "/tmp/output/"],
            timeout=60.0,
        )
        assert result.success

    @patch("popctl.utils.shell.run_command")
    def test_returns_failure_on_error(self, mock_run: MagicMock) -> None:
        """docker_cp returns failure result on error."""
        mock_run.return_value = CommandResult(stdout="", stderr="no such container", returncode=1)

        result = docker_cp("/tmp/src", "missing:/tmp/dest")

        assert not result.success

    @patch("popctl.utils.shell.run_command")
    def test_raises_when_docker_not_found(self, mock_run: MagicMock) -> None:
        """docker_cp raises FileNotFoundError when docker is not installed."""
        mock_run.side_effect = FileNotFoundError("docker not found")

        try:
            docker_cp("/tmp/src", "ai-dev:/tmp/dest")
            assert False, "Should have raised FileNotFoundError"  # noqa: B011
        except FileNotFoundError:
            pass
