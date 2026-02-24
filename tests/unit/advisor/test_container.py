"""Unit tests for Docker dev-container helpers."""

from unittest.mock import MagicMock, patch

from popctl.advisor.container import (
    container_cleanup,
    container_has_command,
    docker_cp,
    ensure_container,
    find_container,
)


class TestFindContainer:
    """Tests for find_container."""

    def test_finds_running_container(self) -> None:
        mock_result = MagicMock(success=True, stdout="abc123\n", returncode=0)
        with patch("popctl.advisor.container.run_command", return_value=mock_result):
            assert find_container("/some/dir") == "abc123"

    def test_returns_first_container(self) -> None:
        mock_result = MagicMock(success=True, stdout="abc123\ndef456\n", returncode=0)
        with patch("popctl.advisor.container.run_command", return_value=mock_result):
            assert find_container("/some/dir") == "abc123"

    def test_no_running_containers(self) -> None:
        mock_result = MagicMock(success=True, stdout="", returncode=0)
        with patch("popctl.advisor.container.run_command", return_value=mock_result):
            assert find_container("/some/dir") is None

    def test_command_failure(self) -> None:
        mock_result = MagicMock(success=False, stdout="", returncode=1)
        with patch("popctl.advisor.container.run_command", return_value=mock_result):
            assert find_container("/some/dir") is None

    def test_docker_not_installed(self) -> None:
        with patch(
            "popctl.advisor.container.run_command",
            side_effect=FileNotFoundError("docker not found"),
        ):
            assert find_container("/some/dir") is None

    def test_passes_compose_dir_as_cwd(self) -> None:
        mock_result = MagicMock(success=True, stdout="abc123\n", returncode=0)
        with patch("popctl.advisor.container.run_command", return_value=mock_result) as mock_run:
            find_container("/my/compose/dir")
        assert mock_run.call_args.kwargs["cwd"] == "/my/compose/dir"


class TestEnsureContainer:
    """Tests for ensure_container."""

    def test_finds_existing_container(self) -> None:
        with patch("popctl.advisor.container.find_container", return_value="abc123"):
            assert ensure_container("/dir") == "abc123"

    def test_starts_and_finds_container(self) -> None:
        mock_result = MagicMock(success=True, returncode=0)
        with (
            patch(
                "popctl.advisor.container.find_container",
                side_effect=[None, "new123"],
            ),
            patch("popctl.advisor.container.run_command", return_value=mock_result),
        ):
            assert ensure_container("/dir") == "new123"

    def test_start_fails(self) -> None:
        mock_result = MagicMock(success=False, returncode=1)
        with (
            patch("popctl.advisor.container.find_container", return_value=None),
            patch("popctl.advisor.container.run_command", return_value=mock_result),
        ):
            assert ensure_container("/dir") is None

    def test_start_succeeds_but_still_not_running(self) -> None:
        mock_result = MagicMock(success=True, returncode=0)
        with (
            patch("popctl.advisor.container.find_container", return_value=None),
            patch("popctl.advisor.container.run_command", return_value=mock_result),
        ):
            assert ensure_container("/dir") is None

    def test_docker_not_installed(self) -> None:
        with (
            patch("popctl.advisor.container.find_container", return_value=None),
            patch(
                "popctl.advisor.container.run_command",
                side_effect=FileNotFoundError,
            ),
        ):
            assert ensure_container("/dir") is None


class TestContainerHasCommand:
    """Tests for container_has_command."""

    def test_command_exists(self) -> None:
        mock_result = MagicMock(success=True, returncode=0)
        with patch("popctl.advisor.container.run_command", return_value=mock_result):
            assert container_has_command("abc123", "claude") is True

    def test_command_missing(self) -> None:
        mock_result = MagicMock(success=False, returncode=1)
        with patch("popctl.advisor.container.run_command", return_value=mock_result):
            assert container_has_command("abc123", "claude") is False

    def test_exec_fails(self) -> None:
        with patch(
            "popctl.advisor.container.run_command",
            side_effect=FileNotFoundError,
        ):
            assert container_has_command("abc123", "claude") is False


class TestDockerCp:
    """Tests for docker_cp."""

    def test_copy_success(self) -> None:
        mock_result = MagicMock(success=True, returncode=0)
        with patch("popctl.advisor.container.run_command", return_value=mock_result):
            assert docker_cp("/host/path", "cid:/container/path") is True

    def test_copy_failure(self) -> None:
        mock_result = MagicMock(success=False, returncode=1)
        with patch("popctl.advisor.container.run_command", return_value=mock_result):
            assert docker_cp("/host/path", "cid:/container/path") is False

    def test_docker_not_installed(self) -> None:
        with patch(
            "popctl.advisor.container.run_command",
            side_effect=FileNotFoundError,
        ):
            assert docker_cp("/host/path", "cid:/path") is False


class TestContainerCleanup:
    """Tests for container_cleanup."""

    def test_cleanup_succeeds(self) -> None:
        mock_result = MagicMock(success=True, returncode=0)
        with patch("popctl.advisor.container.run_command", return_value=mock_result):
            container_cleanup("abc123", "/tmp/popctl-advisor")  # noqa: S108

    def test_cleanup_fails_silently(self) -> None:
        with patch(
            "popctl.advisor.container.run_command",
            side_effect=OSError("connection refused"),
        ):
            container_cleanup("abc123", "/tmp/popctl-advisor")  # noqa: S108
