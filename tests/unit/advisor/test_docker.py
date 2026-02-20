"""Unit tests for Docker container utilities."""

from unittest.mock import MagicMock, patch

from popctl.advisor.docker import docker_cp, find_running_container, is_container_running
from popctl.utils.shell import CommandResult


class TestFindRunningContainer:
    """Tests for find_running_container function."""

    @patch("popctl.advisor.docker.run_command")
    def test_returns_full_name_on_match(self, mock_run: MagicMock) -> None:
        """find_running_container returns the actual container name."""
        mock_run.return_value = CommandResult(stdout="ai-dev-base-dev-1\n", stderr="", returncode=0)

        assert find_running_container("ai-dev") == "ai-dev-base-dev-1"

    @patch("popctl.advisor.docker.run_command")
    def test_returns_none_when_no_match(self, mock_run: MagicMock) -> None:
        """find_running_container returns None when no container matches."""
        mock_run.return_value = CommandResult(stdout="", stderr="", returncode=0)

        assert find_running_container("ai-dev") is None

    @patch("popctl.advisor.docker.run_command")
    def test_returns_none_on_docker_error(self, mock_run: MagicMock) -> None:
        """find_running_container returns None when docker command fails."""
        mock_run.return_value = CommandResult(stdout="", stderr="error", returncode=1)

        assert find_running_container("ai-dev") is None

    @patch("popctl.advisor.docker.run_command")
    def test_returns_none_when_docker_missing(self, mock_run: MagicMock) -> None:
        """find_running_container returns None when docker is not installed."""
        mock_run.side_effect = FileNotFoundError("docker not found")

        assert find_running_container("ai-dev") is None

    @patch("popctl.advisor.docker.run_command")
    def test_returns_first_matching_container(self, mock_run: MagicMock) -> None:
        """find_running_container returns the first matching line."""
        mock_run.return_value = CommandResult(
            stdout="ai-dev-base-dev-1\nai-dev-gpu-dev-1\n", stderr="", returncode=0
        )

        assert find_running_container("ai-dev") == "ai-dev-base-dev-1"


class TestIsContainerRunning:
    """Tests for is_container_running function."""

    @patch("popctl.advisor.docker.run_command")
    def test_returns_true_when_running(self, mock_run: MagicMock) -> None:
        """is_container_running returns True when container is found."""
        mock_run.return_value = CommandResult(stdout="ai-dev\n", stderr="", returncode=0)

        assert is_container_running("ai-dev") is True

    @patch("popctl.advisor.docker.run_command")
    def test_returns_false_when_not_running(self, mock_run: MagicMock) -> None:
        """is_container_running returns False when container not found."""
        mock_run.return_value = CommandResult(stdout="", stderr="", returncode=0)

        assert is_container_running("ai-dev") is False

    @patch("popctl.advisor.docker.run_command")
    def test_returns_false_on_docker_error(self, mock_run: MagicMock) -> None:
        """is_container_running returns False when docker command fails."""
        mock_run.return_value = CommandResult(stdout="", stderr="error", returncode=1)

        assert is_container_running("ai-dev") is False

    @patch("popctl.advisor.docker.run_command")
    def test_returns_false_when_docker_not_installed(self, mock_run: MagicMock) -> None:
        """is_container_running returns False when docker is not installed."""
        mock_run.side_effect = FileNotFoundError("docker not found")

        assert is_container_running("ai-dev") is False

    @patch("popctl.advisor.docker.run_command")
    def test_returns_false_on_os_error(self, mock_run: MagicMock) -> None:
        """is_container_running returns False on OSError."""
        mock_run.side_effect = OSError("permission denied")

        assert is_container_running("ai-dev") is False

    @patch("popctl.advisor.docker.run_command")
    def test_default_container_name(self, mock_run: MagicMock) -> None:
        """is_container_running defaults to 'ai-dev' container name."""
        mock_run.return_value = CommandResult(stdout="ai-dev\n", stderr="", returncode=0)

        is_container_running()

        args = mock_run.call_args[0][0]
        assert "name=ai-dev" in " ".join(args)

    @patch("popctl.advisor.docker.run_command")
    def test_custom_container_name(self, mock_run: MagicMock) -> None:
        """is_container_running accepts custom container name."""
        mock_run.return_value = CommandResult(stdout="my-container\n", stderr="", returncode=0)

        assert is_container_running("my-container") is True

    @patch("popctl.advisor.docker.run_command")
    def test_matches_substring_names(self, mock_run: MagicMock) -> None:
        """is_container_running matches containers whose name contains the pattern."""
        mock_run.return_value = CommandResult(stdout="ai-dev-base-dev-1\n", stderr="", returncode=0)

        assert is_container_running("ai-dev") is True


class TestDockerCp:
    """Tests for docker_cp function."""

    @patch("popctl.advisor.docker.run_command")
    def test_copies_host_to_container(self, mock_run: MagicMock) -> None:
        """docker_cp calls docker cp with correct arguments."""
        mock_run.return_value = CommandResult(stdout="", stderr="", returncode=0)

        result = docker_cp("/tmp/workspace", "ai-dev:/tmp/advisor/")

        mock_run.assert_called_once_with(
            ["docker", "cp", "/tmp/workspace", "ai-dev:/tmp/advisor/"],
            timeout=60.0,
        )
        assert result.success

    @patch("popctl.advisor.docker.run_command")
    def test_copies_container_to_host(self, mock_run: MagicMock) -> None:
        """docker_cp can copy from container to host."""
        mock_run.return_value = CommandResult(stdout="", stderr="", returncode=0)

        result = docker_cp("ai-dev:/tmp/decisions.toml", "/tmp/output/")

        mock_run.assert_called_once_with(
            ["docker", "cp", "ai-dev:/tmp/decisions.toml", "/tmp/output/"],
            timeout=60.0,
        )
        assert result.success

    @patch("popctl.advisor.docker.run_command")
    def test_returns_failure_on_error(self, mock_run: MagicMock) -> None:
        """docker_cp returns failure result on error."""
        mock_run.return_value = CommandResult(stdout="", stderr="no such container", returncode=1)

        result = docker_cp("/tmp/src", "missing:/tmp/dest")

        assert not result.success

    @patch("popctl.advisor.docker.run_command")
    def test_raises_when_docker_not_found(self, mock_run: MagicMock) -> None:
        """docker_cp raises FileNotFoundError when docker is not installed."""
        mock_run.side_effect = FileNotFoundError("docker not found")

        try:
            docker_cp("/tmp/src", "ai-dev:/tmp/dest")
            assert False, "Should have raised FileNotFoundError"  # noqa: B011
        except FileNotFoundError:
            pass
