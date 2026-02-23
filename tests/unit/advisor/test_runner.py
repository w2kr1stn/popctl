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


class TestAgentRunnerBuildHeadlessCommand:
    """Tests for AgentRunner._build_headless_command method."""

    def test_build_command_claude(self, tmp_path: Path) -> None:
        """_build_headless_command builds correct command for claude without model."""
        config = AdvisorConfig(provider="claude")
        runner = AgentRunner(config=config)

        command = runner._build_headless_command(tmp_path)

        assert command[0] == "claude"
        assert "-p" in command
        assert "--output-format" in command
        assert "json" in command
        assert "--model" not in command

    def test_build_command_gemini(self, tmp_path: Path) -> None:
        """_build_headless_command builds correct command for gemini without model."""
        config = AdvisorConfig(provider="gemini")
        runner = AgentRunner(config=config)

        command = runner._build_headless_command(tmp_path)

        assert command[0] == "gemini"
        assert "--prompt" in command
        assert "--model" not in command

    def test_build_headless_command_with_model_claude(self, tmp_path: Path) -> None:
        """_build_headless_command includes --model when model is explicitly set (claude)."""
        config = AdvisorConfig(provider="claude", model="claude-sonnet-4-5-20250514")
        runner = AgentRunner(config=config)

        command = runner._build_headless_command(tmp_path)

        assert command[0] == "claude"
        assert "--model" in command
        assert "claude-sonnet-4-5-20250514" in command

    def test_build_headless_command_with_model_gemini(self, tmp_path: Path) -> None:
        """_build_headless_command includes --model when model is explicitly set (gemini)."""
        config = AdvisorConfig(provider="gemini", model="gemini-2.5-flash")
        runner = AgentRunner(config=config)

        command = runner._build_headless_command(tmp_path)

        assert command[0] == "gemini"
        assert "--model" in command
        assert "gemini-2.5-flash" in command


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

        config = AdvisorConfig(provider="claude")
        runner = AgentRunner(config=config)

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.stdout = "Classification complete"
        mock_result.stderr = ""
        mock_result.returncode = 0

        with patch("popctl.advisor.runner.run_command", return_value=mock_result):
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

        config = AdvisorConfig(provider="claude")
        runner = AgentRunner(config=config)

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.stdout = "Done"
        mock_result.stderr = ""
        mock_result.returncode = 0

        with patch("popctl.advisor.runner.run_command", return_value=mock_result):
            result = runner.run_headless(workspace_dir)

        assert result.success is False
        assert "decisions.toml was not created" in result.error  # type: ignore[operator]
        assert result.workspace_path == workspace_dir

    def test_run_headless_nonzero_exit(self, tmp_path: Path) -> None:
        """run_headless returns failure when agent exits with error."""
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        (workspace_dir / "output").mkdir()

        config = AdvisorConfig(provider="claude")
        runner = AgentRunner(config=config)

        mock_result = MagicMock()
        mock_result.success = False
        mock_result.stdout = ""
        mock_result.stderr = "API key invalid"
        mock_result.returncode = 1

        with patch("popctl.advisor.runner.run_command", return_value=mock_result):
            result = runner.run_headless(workspace_dir)

        assert result.success is False
        assert result.error == "API key invalid"

    def test_run_headless_timeout(self, tmp_path: Path) -> None:
        """run_headless returns failure on timeout."""
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        (workspace_dir / "output").mkdir()

        config = AdvisorConfig(provider="claude", timeout_seconds=60)
        runner = AgentRunner(config=config)

        with patch(
            "popctl.advisor.runner.run_command",
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

        config = AdvisorConfig(provider="claude")
        runner = AgentRunner(config=config)

        with patch(
            "popctl.advisor.runner.run_command",
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

        config = AdvisorConfig(provider="claude")
        runner = AgentRunner(config=config)

        with patch(
            "popctl.advisor.runner.run_command",
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

        config = AdvisorConfig(provider="claude")
        runner = AgentRunner(config=config)

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.stdout = ""
        mock_result.stderr = ""
        mock_result.returncode = 0

        with patch("popctl.advisor.runner.run_command", return_value=mock_result) as mock_run:
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

        config = AdvisorConfig(provider="claude", timeout_seconds=300)
        runner = AgentRunner(config=config)

        mock_result = MagicMock()
        mock_result.success = True
        mock_result.stdout = ""
        mock_result.stderr = ""
        mock_result.returncode = 0

        with patch("popctl.advisor.runner.run_command", return_value=mock_result) as mock_run:
            runner.run_headless(workspace_dir)

        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args
        assert call_kwargs.kwargs["timeout"] == 300.0


class TestAgentRunnerLaunchInteractive:
    """Tests for AgentRunner.launch_interactive method."""

    def test_launch_interactive_non_tty_returns_manual(self, tmp_path: Path) -> None:
        """launch_interactive returns manual instructions when not a TTY."""
        config = AdvisorConfig(provider="claude")
        runner = AgentRunner(config=config)

        with patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = False
            result = runner.launch_interactive(tmp_path)

        assert result.success is False
        assert result.error == "manual_mode"
        assert str(tmp_path) in result.output

    def test_launch_interactive_host_exec_success(self, tmp_path: Path) -> None:
        """launch_interactive succeeds when CLI tool is available and decisions exist."""
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        output_dir = workspace_dir / "output"
        output_dir.mkdir()

        config = AdvisorConfig(provider="claude")
        runner = AgentRunner(config=config)

        def mock_run_interactive(cmd: list[str], **kwargs: object) -> int:
            # Simulate agent creating decisions.toml
            (output_dir / "decisions.toml").write_text("[packages.apt]")
            return 0

        with (
            patch("sys.stdin") as mock_stdin,
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch(
                "popctl.advisor.runner.run_interactive",
                side_effect=mock_run_interactive,
            ),
        ):
            mock_stdin.isatty.return_value = True
            result = runner.launch_interactive(workspace_dir)

        assert result.success is True
        assert result.workspace_path == workspace_dir
        assert result.decisions_path == output_dir / "decisions.toml"

    def test_launch_interactive_host_exec_no_decisions(self, tmp_path: Path) -> None:
        """launch_interactive returns failure when agent doesn't create decisions."""
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        (workspace_dir / "output").mkdir()

        config = AdvisorConfig(provider="claude")
        runner = AgentRunner(config=config)

        with (
            patch("sys.stdin") as mock_stdin,
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("popctl.advisor.runner.run_interactive", return_value=0),
        ):
            mock_stdin.isatty.return_value = True
            result = runner.launch_interactive(workspace_dir)

        assert result.success is False
        assert result.decisions_path is None

    def test_launch_interactive_persists_memory(self, tmp_path: Path) -> None:
        """launch_interactive persists memory.md after session."""
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        (workspace_dir / "output").mkdir()

        config = AdvisorConfig(provider="claude")
        runner = AgentRunner(config=config)

        def mock_run_interactive(cmd: list[str], **kwargs: object) -> int:
            # Simulate agent creating memory.md
            (workspace_dir / "memory.md").write_text("# Memory\n")
            return 0

        with (
            patch("sys.stdin") as mock_stdin,
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("popctl.advisor.runner.run_interactive", side_effect=mock_run_interactive),
            patch.object(runner, "_persist_memory") as mock_persist,
        ):
            mock_stdin.isatty.return_value = True
            runner.launch_interactive(workspace_dir)

        mock_persist.assert_called_once_with(workspace_dir / "memory.md")

    def test_launch_interactive_manual_fallback(self, tmp_path: Path) -> None:
        """launch_interactive falls back to manual when nothing available."""
        config = AdvisorConfig(provider="claude")
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

    def test_launch_interactive_gemini(self, tmp_path: Path) -> None:
        """launch_interactive works with gemini provider."""
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        (workspace_dir / "output").mkdir()

        config = AdvisorConfig(provider="gemini")
        runner = AgentRunner(config=config)

        with (
            patch("sys.stdin") as mock_stdin,
            patch("shutil.which", return_value="/usr/bin/gemini"),
            patch("popctl.advisor.runner.run_interactive", return_value=0) as mock_run,
        ):
            mock_stdin.isatty.return_value = True
            runner.launch_interactive(workspace_dir)

        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "gemini"
        assert "--prompt" in cmd
        assert "--model" not in cmd

    def test_launch_interactive_with_model_claude(self, tmp_path: Path) -> None:
        """launch_interactive passes --model to claude when model is set."""
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        (workspace_dir / "output").mkdir()

        config = AdvisorConfig(provider="claude", model="claude-sonnet-4-5-20250514")
        runner = AgentRunner(config=config)

        with (
            patch("sys.stdin") as mock_stdin,
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("popctl.advisor.runner.run_interactive", return_value=0) as mock_run,
        ):
            mock_stdin.isatty.return_value = True
            runner.launch_interactive(workspace_dir)

        cmd = mock_run.call_args[0][0]
        assert "--model" in cmd
        assert "claude-sonnet-4-5-20250514" in cmd

    def test_launch_interactive_with_model_gemini(self, tmp_path: Path) -> None:
        """launch_interactive passes --model to gemini when model is set."""
        workspace_dir = tmp_path / "workspace"
        workspace_dir.mkdir()
        (workspace_dir / "output").mkdir()

        config = AdvisorConfig(provider="gemini", model="gemini-2.5-flash")
        runner = AgentRunner(config=config)

        with (
            patch("sys.stdin") as mock_stdin,
            patch("shutil.which", return_value="/usr/bin/gemini"),
            patch("popctl.advisor.runner.run_interactive", return_value=0) as mock_run,
        ):
            mock_stdin.isatty.return_value = True
            runner.launch_interactive(workspace_dir)

        cmd = mock_run.call_args[0][0]
        assert "--model" in cmd
        assert "gemini-2.5-flash" in cmd

    def test_manual_instructions_include_provider(self, tmp_path: Path) -> None:
        """Manual instructions mention the configured provider."""
        config = AdvisorConfig(provider="gemini")
        runner = AgentRunner(config=config)

        result = runner._manual_instructions(tmp_path)

        assert "gemini" in result.output
        assert str(tmp_path) in result.output


class TestAgentRunnerPersistMemory:
    """Tests for AgentRunner._persist_memory method."""

    def test_persist_memory_copies_file(self, tmp_path: Path) -> None:
        """_persist_memory copies memory.md to persistent location."""
        workspace_memory = tmp_path / "workspace" / "memory.md"
        workspace_memory.parent.mkdir()
        workspace_memory.write_text("# Advisor Memory\n")

        persistent_dir = tmp_path / "state" / "popctl" / "advisor"
        persistent_path = persistent_dir / "memory.md"

        config = AdvisorConfig()
        runner = AgentRunner(config=config)

        with patch(
            "popctl.advisor.runner.ensure_dir",
            return_value=persistent_dir,
        ):
            persistent_dir.mkdir(parents=True, exist_ok=True)
            runner._persist_memory(workspace_memory)

        assert persistent_path.exists()
        assert "Advisor Memory" in persistent_path.read_text()

    def test_persist_memory_handles_runtime_error(self, tmp_path: Path) -> None:
        """_persist_memory logs warning and prints user-visible warning on failure."""
        workspace_memory = tmp_path / "memory.md"
        workspace_memory.write_text("# Memory\n")

        config = AdvisorConfig()
        runner = AgentRunner(config=config)

        with (
            patch(
                "popctl.advisor.runner.ensure_dir",
                side_effect=RuntimeError("Permission denied"),
            ),
            patch("popctl.advisor.runner.print_warning") as mock_warn,
        ):
            # Should not raise
            runner._persist_memory(workspace_memory)

        mock_warn.assert_called_once_with("Could not persist advisor memory: Permission denied")


class TestAgentRunnerIntegration:
    """Integration tests for AgentRunner."""

    def test_runner_with_default_config(self, tmp_path: Path) -> None:
        """AgentRunner works with default AdvisorConfig."""
        config = AdvisorConfig()
        runner = AgentRunner(config=config)

        command = runner._build_headless_command(tmp_path)
        assert command[0] == "claude"

    def test_runner_configuration_propagation(self, tmp_path: Path) -> None:
        """AgentRunner correctly uses all config settings."""
        config = AdvisorConfig(
            provider="gemini",
            model="gemini-2.5-flash",
            timeout_seconds=120,
        )
        runner = AgentRunner(config=config)

        command = runner._build_headless_command(tmp_path)
        assert command[0] == "gemini"
        assert "--model" in command
        assert "gemini-2.5-flash" in command
