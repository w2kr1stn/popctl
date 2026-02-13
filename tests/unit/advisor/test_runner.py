"""Unit tests for AgentRunner and AgentResult.

Tests for the agent runner module that provides subprocess-based
AI agent execution for package classification.
"""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from popctl.advisor.config import AdvisorConfig
from popctl.advisor.runner import AgentResult, AgentRunner


class TestAgentResult:
    """Tests for AgentResult dataclass."""

    def test_successful_result(self, tmp_path: Path) -> None:
        """AgentResult with success=True has correct attributes."""
        decisions_path = tmp_path / "decisions.toml"

        result = AgentResult(
            success=True,
            output="Classification complete",
            decisions_path=decisions_path,
            workspace_path=tmp_path,
        )

        assert result.success is True
        assert result.output == "Classification complete"
        assert result.error is None
        assert result.decisions_path == decisions_path
        assert result.workspace_path == tmp_path

    def test_failed_result(self) -> None:
        """AgentResult with success=False has correct attributes."""
        result = AgentResult(
            success=False,
            output="",
            error="Agent crashed",
        )

        assert result.success is False
        assert result.output == ""
        assert result.error == "Agent crashed"
        assert result.decisions_path is None
        assert result.workspace_path is None

    def test_result_is_frozen(self) -> None:
        """AgentResult is immutable (frozen dataclass)."""
        result = AgentResult(success=True, output="test")

        with pytest.raises(AttributeError):
            result.success = False  # type: ignore[misc]


class TestAgentRunnerIsContainerMode:
    """Tests for AgentRunner._is_container_mode method."""

    def test_host_mode_when_container_mode_false(self) -> None:
        """_is_container_mode returns False when container_mode is False."""
        config = AdvisorConfig(container_mode=False)
        runner = AgentRunner(config=config)

        assert runner._is_container_mode() is False

    def test_container_mode_when_enabled(self) -> None:
        """_is_container_mode returns True when container_mode is True."""
        config = AdvisorConfig(container_mode=True)
        runner = AgentRunner(config=config)

        assert runner._is_container_mode() is True


class TestAgentRunnerBuildHeadlessCommand:
    """Tests for AgentRunner._build_headless_command method."""

    def test_build_command_host_mode_claude(self, tmp_path: Path) -> None:
        """_build_headless_command builds correct command for claude in host mode."""
        config = AdvisorConfig(provider="claude", container_mode=False)
        runner = AgentRunner(config=config)

        command = runner._build_headless_command(tmp_path)

        assert command[0] == "claude"
        assert "-p" in command
        assert "--output-format" in command
        assert "json" in command

    def test_build_command_host_mode_gemini(self, tmp_path: Path) -> None:
        """_build_headless_command builds correct command for gemini in host mode."""
        config = AdvisorConfig(provider="gemini", container_mode=False)
        runner = AgentRunner(config=config)

        command = runner._build_headless_command(tmp_path)

        assert command[0] == "gemini"
        assert "--prompt" in command

    def test_build_command_container_mode_claude(self, tmp_path: Path) -> None:
        """_build_headless_command builds codeagent command in container mode."""
        config = AdvisorConfig(provider="claude", container_mode=True, model="opus")
        runner = AgentRunner(config=config)

        command = runner._build_headless_command(tmp_path)

        assert command[0] == "codeagent"
        assert "run" in command
        assert "claude" in command
        assert "--write" in command
        assert "--mount" in command
        assert str(tmp_path) in command
        assert "--model" in command
        assert "opus" in command

    def test_build_command_container_mode_gemini(self, tmp_path: Path) -> None:
        """_build_headless_command builds codeagent command for gemini."""
        config = AdvisorConfig(provider="gemini", container_mode=True, model="gemini-2.5-flash")
        runner = AgentRunner(config=config)

        command = runner._build_headless_command(tmp_path)

        assert command[0] == "codeagent"
        assert "run" in command
        assert "gemini" in command
        assert "--write" in command
        assert "--model" in command
        assert "gemini-2.5-flash" in command

    def test_build_command_uses_effective_model(self, tmp_path: Path) -> None:
        """_build_headless_command uses effective_model when model is None."""
        config = AdvisorConfig(provider="claude", container_mode=True, model=None)
        runner = AgentRunner(config=config)

        command = runner._build_headless_command(tmp_path)

        assert "sonnet" in command


class TestAgentRunnerRunHeadless:
    """Tests for AgentRunner.run_headless method."""

    def test_run_headless_success(self, tmp_path: Path) -> None:
        """run_headless returns success result when agent completes."""
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        output_dir = workspace_dir / "output"
        output_dir.mkdir()
        decisions_file = output_dir / "decisions.toml"
        decisions_file.write_text('[packages.apt]\nkeep = ["vim"]')

        config = AdvisorConfig(provider="claude", container_mode=False)
        runner = AgentRunner(config=config)

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.stdout = "Classification complete"
        mock_result.stderr = ""
        mock_result.returncode = 0

        with patch("popctl.utils.shell.run_command", return_value=mock_result):
            result = runner.run_headless(workspace_dir)

        assert result.success is True
        assert result.output == "Classification complete"
        assert result.error is None
        assert result.decisions_path == decisions_file
        assert result.workspace_path == workspace_dir

    def test_run_headless_no_decisions_file(self, tmp_path: Path) -> None:
        """run_headless returns failure when decisions.toml not created."""
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        (workspace_dir / "output").mkdir()
        # Note: decisions.toml is NOT created

        config = AdvisorConfig(provider="claude", container_mode=False)
        runner = AgentRunner(config=config)

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.stdout = "Done"
        mock_result.stderr = ""
        mock_result.returncode = 0

        with patch("popctl.utils.shell.run_command", return_value=mock_result):
            result = runner.run_headless(workspace_dir)

        assert result.success is False
        assert "decisions.toml was not created" in result.error  # type: ignore[operator]
        assert result.workspace_path == workspace_dir

    def test_run_headless_nonzero_exit(self, tmp_path: Path) -> None:
        """run_headless returns failure when agent exits with error."""
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        (workspace_dir / "output").mkdir()

        config = AdvisorConfig(provider="claude", container_mode=False)
        runner = AgentRunner(config=config)

        mock_result = MagicMock()
        mock_result.success = False
        mock_result.stdout = ""
        mock_result.stderr = "API key invalid"
        mock_result.returncode = 1

        with patch("popctl.utils.shell.run_command", return_value=mock_result):
            result = runner.run_headless(workspace_dir)

        assert result.success is False
        assert result.error == "API key invalid"

    def test_run_headless_timeout(self, tmp_path: Path) -> None:
        """run_headless returns failure on timeout."""
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        (workspace_dir / "output").mkdir()

        config = AdvisorConfig(provider="claude", container_mode=False, timeout_seconds=60)
        runner = AgentRunner(config=config)

        with patch(
            "popctl.utils.shell.run_command",
            side_effect=subprocess.TimeoutExpired(cmd=["claude"], timeout=60),
        ):
            result = runner.run_headless(workspace_dir)

        assert result.success is False
        assert "timed out" in result.error  # type: ignore[operator]
        assert "60" in result.error  # type: ignore[operator]
        assert result.workspace_path == workspace_dir

    def test_run_headless_command_not_found(self, tmp_path: Path) -> None:
        """run_headless returns failure when command not found."""
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        (workspace_dir / "output").mkdir()

        config = AdvisorConfig(provider="claude", container_mode=False)
        runner = AgentRunner(config=config)

        with patch(
            "popctl.utils.shell.run_command",
            side_effect=FileNotFoundError("claude not found"),
        ):
            result = runner.run_headless(workspace_dir)

        assert result.success is False
        assert "not found" in result.error  # type: ignore[operator]

    def test_run_headless_os_error(self, tmp_path: Path) -> None:
        """run_headless returns failure on OSError."""
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        (workspace_dir / "output").mkdir()

        config = AdvisorConfig(provider="claude", container_mode=False)
        runner = AgentRunner(config=config)

        with patch(
            "popctl.utils.shell.run_command",
            side_effect=OSError("Permission denied"),
        ):
            result = runner.run_headless(workspace_dir)

        assert result.success is False
        assert "Permission denied" in result.error  # type: ignore[operator]

    def test_run_headless_uses_workspace_as_cwd(self, tmp_path: Path) -> None:
        """run_headless runs command with workspace_dir as cwd."""
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        output_dir = workspace_dir / "output"
        output_dir.mkdir()
        (output_dir / "decisions.toml").write_text("[packages.apt]")

        config = AdvisorConfig(provider="claude", container_mode=False)
        runner = AgentRunner(config=config)

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.stdout = ""
        mock_result.stderr = ""
        mock_result.returncode = 0

        with patch("popctl.utils.shell.run_command", return_value=mock_result) as mock_run:
            runner.run_headless(workspace_dir)

        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args
        assert call_kwargs.kwargs["cwd"] == str(workspace_dir)

    def test_run_headless_uses_configured_timeout(self, tmp_path: Path) -> None:
        """run_headless uses timeout from config."""
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        output_dir = workspace_dir / "output"
        output_dir.mkdir()
        (output_dir / "decisions.toml").write_text("[packages.apt]")

        config = AdvisorConfig(provider="claude", container_mode=False, timeout_seconds=300)
        runner = AgentRunner(config=config)

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.stdout = ""
        mock_result.stderr = ""
        mock_result.returncode = 0

        with patch("popctl.utils.shell.run_command", return_value=mock_result) as mock_run:
            runner.run_headless(workspace_dir)

        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args
        assert call_kwargs.kwargs["timeout"] == 300.0


class TestAgentRunnerLaunchInteractive:
    """Tests for AgentRunner.launch_interactive method."""

    def test_launch_interactive_non_tty_returns_manual(self, tmp_path: Path) -> None:
        """launch_interactive returns manual instructions when not a TTY."""
        config = AdvisorConfig(provider="claude", container_mode=False)
        runner = AgentRunner(config=config)

        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = False
            result = runner.launch_interactive(tmp_path)

        assert result.success is False
        assert result.error == "manual_mode"
        assert str(tmp_path) in result.output

    def test_launch_interactive_container_exec_success(self, tmp_path: Path) -> None:
        """launch_interactive succeeds via container exec."""
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        output_dir = workspace_dir / "output"
        output_dir.mkdir()

        config = AdvisorConfig(provider="claude", container_mode=True)
        runner = AgentRunner(config=config)

        # Create decisions file after docker cp would bring it back
        def mock_docker_cp_back(src: str, dest: str) -> MagicMock:
            if "output/decisions.toml" in src:
                (output_dir / "decisions.toml").write_text("[packages.apt]")
            result = MagicMock()
            result.success = True
            return result

        with (
            patch("sys.stdin") as mock_stdin,
            patch(
                "popctl.utils.shell.find_running_container",
                return_value="ai-dev",
            ),
            patch("popctl.utils.shell.run_command"),
            patch(
                "popctl.utils.shell.docker_cp",
                side_effect=mock_docker_cp_back,
            ),
            patch(
                "popctl.utils.shell.run_interactive",
                return_value=0,
            ),
        ):
            mock_stdin.isatty.return_value = True
            result = runner.launch_interactive(workspace_dir)

        assert result.success is True
        assert result.workspace_path == workspace_dir

    def test_launch_interactive_container_not_running_tries_codeagent(self, tmp_path: Path) -> None:
        """launch_interactive starts container via codeagent, then delegates to container exec."""
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        output_dir = workspace_dir / "output"
        output_dir.mkdir()

        config = AdvisorConfig(provider="claude", container_mode=True)
        runner = AgentRunner(config=config)

        def mock_docker_cp_back(src: str, dest: str) -> MagicMock:
            if "output/decisions.toml" in src:
                (output_dir / "decisions.toml").write_text("[packages.apt]")
            result = MagicMock()
            result.success = True
            return result

        mock_process = MagicMock()
        mock_process.poll.return_value = None  # Still running

        with (
            patch("sys.stdin") as mock_stdin,
            patch(
                "popctl.utils.shell.find_running_container",
                side_effect=[None, "ai-dev-base-dev-1"],
            ),
            patch("popctl.utils.shell.is_container_running", return_value=True),
            patch("shutil.which", return_value="/usr/bin/codeagent"),
            patch("subprocess.Popen", return_value=mock_process),
            patch("time.sleep"),
            patch("popctl.utils.shell.run_command"),
            patch("popctl.utils.shell.docker_cp", side_effect=mock_docker_cp_back),
            patch("popctl.utils.shell.run_interactive", return_value=0),
        ):
            mock_stdin.isatty.return_value = True
            result = runner.launch_interactive(workspace_dir)

        assert result.success is True
        assert result.workspace_path == workspace_dir
        mock_process.terminate.assert_called()

    def test_launch_interactive_host_exec_replaces_process(self, tmp_path: Path) -> None:
        """launch_interactive calls os.execvp for host mode."""
        config = AdvisorConfig(provider="claude", container_mode=False)
        runner = AgentRunner(config=config)

        with (
            patch("sys.stdin") as mock_stdin,
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("os.chdir") as mock_chdir,
            patch("os.execvp") as mock_execvp,
        ):
            mock_stdin.isatty.return_value = True
            runner.launch_interactive(tmp_path)

        mock_chdir.assert_called_once_with(tmp_path)
        mock_execvp.assert_called_once()
        args = mock_execvp.call_args
        assert args[0][0] == "claude"

    def test_launch_interactive_manual_fallback(self, tmp_path: Path) -> None:
        """launch_interactive falls back to manual when nothing available."""
        config = AdvisorConfig(provider="claude", container_mode=False)
        runner = AgentRunner(config=config)

        with (
            patch("sys.stdin") as mock_stdin,
            patch("shutil.which", return_value=None),
        ):
            mock_stdin.isatty.return_value = True
            result = runner.launch_interactive(tmp_path)

        assert result.success is False
        assert result.error == "manual_mode"
        assert "Workspace prepared" in result.output
        assert "popctl advisor apply" in result.output

    def test_container_exec_copyback_failure_returns_failure(self, tmp_path: Path) -> None:
        """_try_container_exec returns failure when copy-back doesn't produce file."""
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        output_dir = workspace_dir / "output"
        output_dir.mkdir()
        # Note: decisions.toml is NOT created (copy-back fails silently)

        config = AdvisorConfig(provider="claude", container_mode=True)
        runner = AgentRunner(config=config)

        mock_cp = MagicMock()
        mock_cp.success = True

        with (
            patch("sys.stdin") as mock_stdin,
            patch(
                "popctl.utils.shell.find_running_container",
                return_value="ai-dev-base-dev-1",
            ),
            patch("popctl.utils.shell.run_command"),
            patch("popctl.utils.shell.docker_cp", return_value=mock_cp),
            patch("popctl.utils.shell.run_interactive", return_value=0),
        ):
            mock_stdin.isatty.return_value = True
            result = runner.launch_interactive(workspace_dir)

        assert result.success is False
        assert result.error is not None
        assert "exited with code" in result.error
        assert result.workspace_path == workspace_dir

    def test_codeagent_start_exit_cascades_to_manual(self, tmp_path: Path) -> None:
        """_try_codeagent_start returns None when process exits early, cascade reaches manual."""
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        (workspace_dir / "output").mkdir()

        config = AdvisorConfig(provider="claude", container_mode=True)
        runner = AgentRunner(config=config)

        def mock_which(cmd: str) -> str | None:
            if cmd == "codeagent":
                return "/usr/bin/codeagent"
            return None

        mock_process = MagicMock()
        mock_process.poll.return_value = 1  # Exited prematurely

        with (
            patch("sys.stdin") as mock_stdin,
            patch("popctl.utils.shell.find_running_container", return_value=None),
            patch("shutil.which", side_effect=mock_which),
            patch("subprocess.Popen", return_value=mock_process),
        ):
            mock_stdin.isatty.return_value = True
            result = runner.launch_interactive(workspace_dir)

        assert result.success is False
        assert result.error == "manual_mode"

    def test_container_exec_run_interactive_raises_cascades(self, tmp_path: Path) -> None:
        """_try_container_exec returns None on FileNotFoundError, cascade continues."""
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        (workspace_dir / "output").mkdir()

        config = AdvisorConfig(provider="claude", container_mode=True)
        runner = AgentRunner(config=config)

        mock_cp = MagicMock()
        mock_cp.success = True

        with (
            patch("sys.stdin") as mock_stdin,
            patch(
                "popctl.utils.shell.find_running_container",
                return_value="ai-dev-base-dev-1",
            ),
            patch("popctl.utils.shell.run_command"),
            patch("popctl.utils.shell.docker_cp", return_value=mock_cp),
            patch(
                "popctl.utils.shell.run_interactive",
                side_effect=FileNotFoundError("docker not found"),
            ),
            patch("shutil.which", return_value=None),
        ):
            mock_stdin.isatty.return_value = True
            result = runner.launch_interactive(workspace_dir)

        # FileNotFoundError caught → None → codeagent (no which) → host (no which) → manual
        assert result.success is False
        assert result.error == "manual_mode"

    def test_manual_instructions_include_provider(self, tmp_path: Path) -> None:
        """Manual instructions mention the configured provider."""
        config = AdvisorConfig(provider="gemini", container_mode=False)
        runner = AgentRunner(config=config)

        result = runner._manual_instructions(tmp_path)

        assert "gemini" in result.output
        assert str(tmp_path) in result.output


class TestAgentRunnerIntegration:
    """Integration tests for AgentRunner."""

    def test_runner_with_default_config(self, tmp_path: Path) -> None:
        """AgentRunner works with default AdvisorConfig."""
        config = AdvisorConfig()  # All defaults
        runner = AgentRunner(config=config)

        # Should be container mode (container_mode defaults to True)
        assert runner._is_container_mode() is True

        # Container mode builds codeagent command
        command = runner._build_headless_command(tmp_path)
        assert command[0] == "codeagent"

    def test_runner_configuration_propagation(self, tmp_path: Path) -> None:
        """AgentRunner correctly uses all config settings."""
        config = AdvisorConfig(
            provider="gemini",
            model="gemini-2.5-flash",
            container_mode=True,
            timeout_seconds=120,
        )
        runner = AgentRunner(config=config)

        # Container mode
        assert runner._is_container_mode() is True

        # Correct model in command
        command = runner._build_headless_command(tmp_path)
        assert "codeagent" in command
        assert "gemini" in command
        assert "gemini-2.5-flash" in command
