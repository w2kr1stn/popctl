"""Agent runner for AI-assisted package classification.

This module provides the AgentRunner class for executing AI agents
(Claude Code or Gemini CLI) in either headless (autonomous) or
interactive mode.

Two execution modes are supported:

1. Host-Mode (dev_script is None):
   Direct call to claude/gemini CLI on the host system.

2. Container-Mode (dev_script is set):
   Call via ai-dev-base dev.sh script for container execution.
"""

import subprocess
from dataclasses import dataclass
from pathlib import Path

from popctl.advisor.config import AdvisorConfig


@dataclass(frozen=True, slots=True)
class AgentResult:
    """Result from agent execution.

    Attributes:
        success: Whether the agent execution completed successfully.
        output: Standard output from the agent.
        error: Error message if execution failed, None otherwise.
        decisions_path: Path to decisions.toml if successful, None otherwise.
    """

    success: bool
    output: str
    error: str | None = None
    decisions_path: Path | None = None


@dataclass
class AgentRunner:
    """Runs AI agents for package classification.

    The AgentRunner executes AI agents (Claude Code or Gemini CLI) for
    autonomous package classification. It supports two execution modes:

    - Host-Mode: Direct invocation of CLI tools when running on the host
    - Container-Mode: Invocation via dev.sh script when using ai-dev-base

    Attributes:
        config: AdvisorConfig with provider, model, and timeout settings.
    """

    config: AdvisorConfig

    def run_headless(self, prompt_file: Path, exchange_dir: Path) -> AgentResult:
        """Run agent in headless mode (autonomous classification).

        Executes the AI agent autonomously with the provided prompt file.
        The agent is expected to write its decisions to decisions.toml
        in the exchange directory.

        Args:
            prompt_file: Path to the prompt file containing classification request.
            exchange_dir: Path to the exchange directory for file communication.

        Returns:
            AgentResult with path to decisions.toml if successful.
        """
        from popctl.utils.shell import run_command

        if not prompt_file.exists():
            return AgentResult(
                success=False,
                output="",
                error=f"Prompt file not found: {prompt_file}",
            )

        command = self._build_command(prompt_file)

        try:
            result = run_command(
                command,
                timeout=float(self.config.timeout_seconds),
                cwd=str(exchange_dir),
            )

            decisions_path = exchange_dir / "decisions.toml"

            if result.success:
                # Check if decisions file was created
                if decisions_path.exists():
                    return AgentResult(
                        success=True,
                        output=result.stdout,
                        decisions_path=decisions_path,
                    )
                else:
                    return AgentResult(
                        success=False,
                        output=result.stdout,
                        error="Agent completed but decisions.toml was not created",
                    )
            else:
                return AgentResult(
                    success=False,
                    output=result.stdout,
                    error=result.stderr or f"Agent exited with code {result.returncode}",
                )

        except subprocess.TimeoutExpired:
            return AgentResult(
                success=False,
                output="",
                error=f"Agent execution timed out after {self.config.timeout_seconds} seconds",
            )
        except FileNotFoundError as e:
            return AgentResult(
                success=False,
                output="",
                error=f"Agent command not found: {e}",
            )
        except OSError as e:
            return AgentResult(
                success=False,
                output="",
                error=f"Failed to execute agent: {e}",
            )

    def prepare_interactive(self, exchange_dir: Path) -> str:
        """Prepare for interactive mode.

        Prepares the exchange directory and returns instructions for the
        user to start the container/agent manually. This mode is used
        when the user wants to interactively guide the classification.

        Args:
            exchange_dir: Path to the exchange directory for file communication.

        Returns:
            Instructions string for user to start container/agent.
        """
        provider = self.config.provider
        model = self.config.effective_model

        if self._is_host_mode():
            # Direct host execution
            if provider == "claude":
                cmd = f'claude --print "$(cat {exchange_dir}/prompt.txt)" --output-format json'
            else:  # gemini
                cmd = f'gemini --prompt "$(cat {exchange_dir}/prompt.txt)"'

            return (
                f"Interactive Mode - Exchange Directory: {exchange_dir}\n"
                f"\n"
                f"Files prepared:\n"
                f"  - {exchange_dir}/scan.json     (system scan data)\n"
                f"  - {exchange_dir}/prompt.txt    (classification prompt)\n"
                f"  - {exchange_dir}/manifest.toml (current manifest)\n"
                f"\n"
                f"To start the {provider} agent manually:\n"
                f"\n"
                f"  {cmd}\n"
                f"\n"
                f"After classification, the agent should write:\n"
                f"  - {exchange_dir}/decisions.toml\n"
                f"\n"
                f"Then run: popctl advisor apply\n"
            )
        else:
            # Container mode via dev.sh
            dev_script = self.config.dev_script
            prompt_path = f"{exchange_dir}/prompt.txt"
            if provider == "claude":
                cmd = f'{dev_script} run claude "$(cat {prompt_path})" --write --model {model}'
            else:  # gemini
                cmd = f'{dev_script} run gemini "$(cat {prompt_path})" --write --model {model}'

            return (
                f"Interactive Mode (Container) - Exchange Directory: {exchange_dir}\n"
                f"\n"
                f"Files prepared:\n"
                f"  - {exchange_dir}/scan.json     (system scan data)\n"
                f"  - {exchange_dir}/prompt.txt    (classification prompt)\n"
                f"  - {exchange_dir}/manifest.toml (current manifest)\n"
                f"\n"
                f"To start the {provider} agent in container:\n"
                f"\n"
                f"  {cmd}\n"
                f"\n"
                f"After classification, the agent should write:\n"
                f"  - {exchange_dir}/decisions.toml\n"
                f"\n"
                f"Then run: popctl advisor apply\n"
            )

    def _build_command(self, prompt_file: Path) -> list[str]:
        """Build command for agent execution.

        Constructs the command line arguments for invoking the AI agent.
        The command differs based on the execution mode:

        - Host-Mode (dev_script is None): Direct call to claude/gemini
        - Container-Mode (dev_script set): Call via dev.sh run

        Args:
            prompt_file: Path to the prompt file to pass to the agent.

        Returns:
            List of command arguments for subprocess execution.
        """
        provider = self.config.provider
        model = self.config.effective_model

        # Read prompt content
        prompt_content = prompt_file.read_text()

        if self._is_host_mode():
            # Host-Mode: Direct call to CLI
            if provider == "claude":
                return [
                    "claude",
                    "--print",
                    prompt_content,
                    "--output-format",
                    "json",
                ]
            else:  # gemini
                return [
                    "gemini",
                    "--prompt",
                    prompt_content,
                ]
        else:
            # Container-Mode: Call via dev.sh run
            dev_script = str(self.config.dev_script)
            if provider == "claude":
                return [
                    dev_script,
                    "run",
                    "claude",
                    prompt_content,
                    "--write",
                    "--model",
                    model,
                ]
            else:  # gemini
                return [
                    dev_script,
                    "run",
                    "gemini",
                    prompt_content,
                    "--write",
                    "--model",
                    model,
                ]

    def _is_host_mode(self) -> bool:
        """Check if running in host mode (no dev_script configured).

        Host mode means the AI agent CLI tools are available directly
        on the system, without needing to go through a container.

        Returns:
            True if running in host mode, False if container mode.
        """
        return self.config.dev_script is None
