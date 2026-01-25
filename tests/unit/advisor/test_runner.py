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
        )

        assert result.success is True
        assert result.output == "Classification complete"
        assert result.error is None
        assert result.decisions_path == decisions_path

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

    def test_result_is_frozen(self) -> None:
        """AgentResult is immutable (frozen dataclass)."""
        result = AgentResult(success=True, output="test")

        with pytest.raises(AttributeError):
            result.success = False  # type: ignore[misc]


class TestAgentRunnerIsHostMode:
    """Tests for AgentRunner._is_host_mode method."""

    def test_host_mode_when_dev_script_none(self) -> None:
        """_is_host_mode returns True when dev_script is None."""
        config = AdvisorConfig(dev_script=None)
        runner = AgentRunner(config=config)

        assert runner._is_host_mode() is True

    def test_container_mode_when_dev_script_set(self) -> None:
        """_is_host_mode returns False when dev_script is set."""
        config = AdvisorConfig(dev_script=Path("/opt/ai-dev/dev.sh"))
        runner = AgentRunner(config=config)

        assert runner._is_host_mode() is False


class TestAgentRunnerBuildCommand:
    """Tests for AgentRunner._build_command method."""

    def test_build_command_host_mode_claude(self, tmp_path: Path) -> None:
        """_build_command builds correct command for claude in host mode."""
        prompt_file = tmp_path / "prompt.txt"
        prompt_file.write_text("Classify these packages")

        config = AdvisorConfig(provider="claude", dev_script=None)
        runner = AgentRunner(config=config)

        command = runner._build_command(prompt_file)

        assert command[0] == "claude"
        assert "--print" in command
        assert "Classify these packages" in command
        assert "--output-format" in command
        assert "json" in command

    def test_build_command_host_mode_gemini(self, tmp_path: Path) -> None:
        """_build_command builds correct command for gemini in host mode."""
        prompt_file = tmp_path / "prompt.txt"
        prompt_file.write_text("Classify these packages")

        config = AdvisorConfig(provider="gemini", dev_script=None)
        runner = AgentRunner(config=config)

        command = runner._build_command(prompt_file)

        assert command[0] == "gemini"
        assert "--prompt" in command
        assert "Classify these packages" in command

    def test_build_command_container_mode_claude(self, tmp_path: Path) -> None:
        """_build_command builds correct command for claude in container mode."""
        prompt_file = tmp_path / "prompt.txt"
        prompt_file.write_text("Classify these packages")
        dev_script = Path("/opt/ai-dev/dev.sh")

        config = AdvisorConfig(provider="claude", dev_script=dev_script, model="opus")
        runner = AgentRunner(config=config)

        command = runner._build_command(prompt_file)

        assert command[0] == str(dev_script)
        assert "run" in command
        assert "claude" in command
        assert "Classify these packages" in command
        assert "--write" in command
        assert "--model" in command
        assert "opus" in command

    def test_build_command_container_mode_gemini(self, tmp_path: Path) -> None:
        """_build_command builds correct command for gemini in container mode."""
        prompt_file = tmp_path / "prompt.txt"
        prompt_file.write_text("Classify these packages")
        dev_script = Path("/opt/ai-dev/dev.sh")

        config = AdvisorConfig(provider="gemini", dev_script=dev_script, model="gemini-2.5-flash")
        runner = AgentRunner(config=config)

        command = runner._build_command(prompt_file)

        assert command[0] == str(dev_script)
        assert "run" in command
        assert "gemini" in command
        assert "Classify these packages" in command
        assert "--write" in command
        assert "--model" in command
        assert "gemini-2.5-flash" in command

    def test_build_command_uses_effective_model(self, tmp_path: Path) -> None:
        """_build_command uses effective_model (default) when model is None."""
        prompt_file = tmp_path / "prompt.txt"
        prompt_file.write_text("Classify these packages")
        dev_script = Path("/opt/ai-dev/dev.sh")

        # No explicit model -> should use default "sonnet"
        config = AdvisorConfig(provider="claude", dev_script=dev_script, model=None)
        runner = AgentRunner(config=config)

        command = runner._build_command(prompt_file)

        assert "sonnet" in command


class TestAgentRunnerRunHeadless:
    """Tests for AgentRunner.run_headless method."""

    def test_run_headless_success(self, tmp_path: Path) -> None:
        """run_headless returns success result when agent completes."""
        prompt_file = tmp_path / "prompt.txt"
        prompt_file.write_text("Classify these packages")
        exchange_dir = tmp_path / "exchange"
        exchange_dir.mkdir()
        decisions_file = exchange_dir / "decisions.toml"
        decisions_file.write_text('[packages.apt]\nkeep = ["vim"]')

        config = AdvisorConfig(provider="claude", dev_script=None)
        runner = AgentRunner(config=config)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Classification complete"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = runner.run_headless(prompt_file, exchange_dir)

        assert result.success is True
        assert result.output == "Classification complete"
        assert result.error is None
        assert result.decisions_path == decisions_file

    def test_run_headless_no_decisions_file(self, tmp_path: Path) -> None:
        """run_headless returns failure when decisions.toml not created."""
        prompt_file = tmp_path / "prompt.txt"
        prompt_file.write_text("Classify these packages")
        exchange_dir = tmp_path / "exchange"
        exchange_dir.mkdir()
        # Note: decisions.toml is NOT created

        config = AdvisorConfig(provider="claude", dev_script=None)
        runner = AgentRunner(config=config)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "Done"
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result):
            result = runner.run_headless(prompt_file, exchange_dir)

        assert result.success is False
        assert "decisions.toml was not created" in result.error  # type: ignore[operator]

    def test_run_headless_nonzero_exit(self, tmp_path: Path) -> None:
        """run_headless returns failure when agent exits with error."""
        prompt_file = tmp_path / "prompt.txt"
        prompt_file.write_text("Classify these packages")
        exchange_dir = tmp_path / "exchange"
        exchange_dir.mkdir()

        config = AdvisorConfig(provider="claude", dev_script=None)
        runner = AgentRunner(config=config)

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "API key invalid"

        with patch("subprocess.run", return_value=mock_result):
            result = runner.run_headless(prompt_file, exchange_dir)

        assert result.success is False
        assert result.error == "API key invalid"

    def test_run_headless_timeout(self, tmp_path: Path) -> None:
        """run_headless returns failure on timeout."""
        prompt_file = tmp_path / "prompt.txt"
        prompt_file.write_text("Classify these packages")
        exchange_dir = tmp_path / "exchange"
        exchange_dir.mkdir()

        config = AdvisorConfig(provider="claude", dev_script=None, timeout_seconds=60)
        runner = AgentRunner(config=config)

        with patch(
            "subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd=["claude"], timeout=60),
        ):
            result = runner.run_headless(prompt_file, exchange_dir)

        assert result.success is False
        assert "timed out" in result.error  # type: ignore[operator]
        assert "60" in result.error  # type: ignore[operator]

    def test_run_headless_command_not_found(self, tmp_path: Path) -> None:
        """run_headless returns failure when command not found."""
        prompt_file = tmp_path / "prompt.txt"
        prompt_file.write_text("Classify these packages")
        exchange_dir = tmp_path / "exchange"
        exchange_dir.mkdir()

        config = AdvisorConfig(provider="claude", dev_script=None)
        runner = AgentRunner(config=config)

        with patch(
            "subprocess.run",
            side_effect=FileNotFoundError("claude not found"),
        ):
            result = runner.run_headless(prompt_file, exchange_dir)

        assert result.success is False
        assert "not found" in result.error  # type: ignore[operator]

    def test_run_headless_prompt_file_missing(self, tmp_path: Path) -> None:
        """run_headless returns failure when prompt file doesn't exist."""
        prompt_file = tmp_path / "nonexistent.txt"
        exchange_dir = tmp_path / "exchange"
        exchange_dir.mkdir()

        config = AdvisorConfig(provider="claude", dev_script=None)
        runner = AgentRunner(config=config)

        result = runner.run_headless(prompt_file, exchange_dir)

        assert result.success is False
        assert "Prompt file not found" in result.error  # type: ignore[operator]

    def test_run_headless_uses_exchange_dir_as_cwd(self, tmp_path: Path) -> None:
        """run_headless runs command with exchange_dir as cwd."""
        prompt_file = tmp_path / "prompt.txt"
        prompt_file.write_text("Classify these packages")
        exchange_dir = tmp_path / "exchange"
        exchange_dir.mkdir()
        decisions_file = exchange_dir / "decisions.toml"
        decisions_file.write_text('[packages.apt]\nkeep = ["vim"]')

        config = AdvisorConfig(provider="claude", dev_script=None)
        runner = AgentRunner(config=config)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            runner.run_headless(prompt_file, exchange_dir)

        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["cwd"] == str(exchange_dir)

    def test_run_headless_uses_configured_timeout(self, tmp_path: Path) -> None:
        """run_headless uses timeout from config."""
        prompt_file = tmp_path / "prompt.txt"
        prompt_file.write_text("Classify these packages")
        exchange_dir = tmp_path / "exchange"
        exchange_dir.mkdir()
        decisions_file = exchange_dir / "decisions.toml"
        decisions_file.write_text('[packages.apt]\nkeep = ["vim"]')

        config = AdvisorConfig(provider="claude", dev_script=None, timeout_seconds=300)
        runner = AgentRunner(config=config)

        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = ""
        mock_result.stderr = ""

        with patch("subprocess.run", return_value=mock_result) as mock_run:
            runner.run_headless(prompt_file, exchange_dir)

        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args[1]
        assert call_kwargs["timeout"] == 300


class TestAgentRunnerPrepareInteractive:
    """Tests for AgentRunner.prepare_interactive method."""

    def test_prepare_interactive_host_mode_claude(self, tmp_path: Path) -> None:
        """prepare_interactive returns correct instructions for claude in host mode."""
        exchange_dir = tmp_path / "exchange"
        exchange_dir.mkdir()

        config = AdvisorConfig(provider="claude", dev_script=None)
        runner = AgentRunner(config=config)

        instructions = runner.prepare_interactive(exchange_dir)

        assert "Interactive Mode" in instructions
        assert str(exchange_dir) in instructions
        assert "scan.json" in instructions
        assert "prompt.txt" in instructions
        assert "decisions.toml" in instructions
        assert "claude" in instructions
        assert "--print" in instructions
        assert "popctl advisor apply" in instructions

    def test_prepare_interactive_host_mode_gemini(self, tmp_path: Path) -> None:
        """prepare_interactive returns correct instructions for gemini in host mode."""
        exchange_dir = tmp_path / "exchange"
        exchange_dir.mkdir()

        config = AdvisorConfig(provider="gemini", dev_script=None)
        runner = AgentRunner(config=config)

        instructions = runner.prepare_interactive(exchange_dir)

        assert "Interactive Mode" in instructions
        assert "gemini" in instructions
        assert "--prompt" in instructions

    def test_prepare_interactive_container_mode_claude(self, tmp_path: Path) -> None:
        """prepare_interactive returns correct instructions for container mode."""
        exchange_dir = tmp_path / "exchange"
        exchange_dir.mkdir()
        dev_script = Path("/opt/ai-dev/dev.sh")

        config = AdvisorConfig(provider="claude", dev_script=dev_script, model="opus")
        runner = AgentRunner(config=config)

        instructions = runner.prepare_interactive(exchange_dir)

        assert "Interactive Mode (Container)" in instructions
        assert str(dev_script) in instructions
        assert "run" in instructions
        assert "claude" in instructions
        assert "--write" in instructions
        assert "--model" in instructions
        assert "opus" in instructions

    def test_prepare_interactive_container_mode_gemini(self, tmp_path: Path) -> None:
        """prepare_interactive returns correct instructions for gemini container mode."""
        exchange_dir = tmp_path / "exchange"
        exchange_dir.mkdir()
        dev_script = Path("/opt/ai-dev/dev.sh")

        config = AdvisorConfig(provider="gemini", dev_script=dev_script, model="gemini-2.5-flash")
        runner = AgentRunner(config=config)

        instructions = runner.prepare_interactive(exchange_dir)

        assert "Interactive Mode (Container)" in instructions
        assert "gemini" in instructions
        assert "gemini-2.5-flash" in instructions

    def test_prepare_interactive_lists_expected_files(self, tmp_path: Path) -> None:
        """prepare_interactive mentions all expected files in instructions."""
        exchange_dir = tmp_path / "exchange"
        exchange_dir.mkdir()

        config = AdvisorConfig(provider="claude", dev_script=None)
        runner = AgentRunner(config=config)

        instructions = runner.prepare_interactive(exchange_dir)

        # Input files
        assert "scan.json" in instructions
        assert "prompt.txt" in instructions
        assert "manifest.toml" in instructions
        # Output file
        assert "decisions.toml" in instructions


class TestAgentRunnerIntegration:
    """Integration tests for AgentRunner."""

    def test_runner_with_default_config(self, tmp_path: Path) -> None:
        """AgentRunner works with default AdvisorConfig."""
        prompt_file = tmp_path / "prompt.txt"
        prompt_file.write_text("Test prompt")
        exchange_dir = tmp_path / "exchange"
        exchange_dir.mkdir()

        config = AdvisorConfig()  # All defaults
        runner = AgentRunner(config=config)

        # Should be host mode (no dev_script)
        assert runner._is_host_mode() is True

        # Should build claude command
        command = runner._build_command(prompt_file)
        assert command[0] == "claude"

    def test_runner_configuration_propagation(self, tmp_path: Path) -> None:
        """AgentRunner correctly uses all config settings."""
        prompt_file = tmp_path / "prompt.txt"
        prompt_file.write_text("Test prompt")

        config = AdvisorConfig(
            provider="gemini",
            model="gemini-2.5-flash",
            dev_script=Path("/custom/dev.sh"),
            timeout_seconds=120,
        )
        runner = AgentRunner(config=config)

        # Container mode
        assert runner._is_host_mode() is False

        # Correct model in command
        command = runner._build_command(prompt_file)
        assert "gemini" in command
        assert "gemini-2.5-flash" in command
        assert "/custom/dev.sh" in command
