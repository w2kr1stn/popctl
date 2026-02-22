"""Agent runner for AI-assisted package classification.

This module provides the AgentRunner class for executing AI agents
(Claude Code or Gemini CLI) in headless or interactive session mode.
"""

import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from popctl.advisor.config import AdvisorConfig
from popctl.advisor.prompts import INITIAL_PROMPT
from popctl.core.paths import ensure_dir, get_state_dir
from popctl.utils.shell import run_command, run_interactive

MANUAL_MODE_SENTINEL: str = "manual_mode"


@dataclass(frozen=True, slots=True)
class AgentResult:
    """Result from agent execution.

    Attributes:
        success: Whether the agent execution completed successfully.
        output: Standard output from the agent.
        error: Error message if execution failed, None otherwise.
        decisions_path: Path to decisions.toml if successful, None otherwise.
        workspace_path: Path to the session workspace directory.
    """

    success: bool
    output: str
    error: str | None = None
    decisions_path: Path | None = None
    workspace_path: Path | None = None


@dataclass
class AgentRunner:
    """Runs AI agents for package classification.

    Executes AI agents (Claude Code or Gemini CLI) for package
    classification on the host system.

    Attributes:
        config: AdvisorConfig with provider, model, and timeout settings.
    """

    config: AdvisorConfig

    def run_headless(self, workspace_dir: Path) -> AgentResult:
        """Run agent in headless mode (autonomous classification).

        Executes the AI agent autonomously using the workspace directory.
        The agent reads scan.json and CLAUDE.md from the workspace and
        writes decisions to workspace/output/decisions.toml.

        Args:
            workspace_dir: Session workspace directory with scan.json and CLAUDE.md.

        Returns:
            AgentResult with path to decisions.toml if successful.
        """
        command = self._build_headless_command(workspace_dir)

        try:
            result = run_command(
                command,
                timeout=float(self.config.timeout_seconds),
                cwd=str(workspace_dir),
            )

            decisions_path = workspace_dir / "output" / "decisions.toml"

            if result.success:
                if decisions_path.exists():
                    return AgentResult(
                        success=True,
                        output=result.stdout,
                        decisions_path=decisions_path,
                        workspace_path=workspace_dir,
                    )
                return AgentResult(
                    success=False,
                    output=result.stdout,
                    error="Agent completed but output/decisions.toml was not created",
                    workspace_path=workspace_dir,
                )
            return AgentResult(
                success=False,
                output=result.stdout,
                error=result.stderr or f"Agent exited with code {result.returncode}",
                workspace_path=workspace_dir,
            )

        except subprocess.TimeoutExpired:
            return AgentResult(
                success=False,
                output="",
                error=f"Agent execution timed out after {self.config.timeout_seconds} seconds",
                workspace_path=workspace_dir,
            )
        except FileNotFoundError as e:
            return AgentResult(
                success=False,
                output="",
                error=f"Agent command not found: {e}",
                workspace_path=workspace_dir,
            )
        except OSError as e:
            return AgentResult(
                success=False,
                output="",
                error=f"Failed to execute agent: {e}",
                workspace_path=workspace_dir,
            )

    def launch_interactive(self, workspace_dir: Path) -> AgentResult:
        """Launch interactive AI session with fallback to manual instructions.

        Args:
            workspace_dir: Session workspace directory.

        Returns:
            AgentResult from the session.
        """
        if not sys.stdin.isatty():
            return self._manual_instructions(workspace_dir)

        host_result = self._try_host_exec(workspace_dir)
        if host_result is not None:
            return host_result

        return self._manual_instructions(workspace_dir)

    def _try_host_exec(self, workspace_dir: Path) -> AgentResult | None:
        """Try to launch the AI CLI directly on the host.

        Uses run_interactive() for TTY handover, allowing post-session
        cleanup (memory persistence, decisions detection).

        Returns None if the CLI tool is not available.
        """
        provider = self.config.provider

        if provider == "claude" and shutil.which("claude") is not None:
            cmd = ["claude", INITIAL_PROMPT, *self._model_flags()]
        elif provider == "gemini" and shutil.which("gemini") is not None:
            cmd = ["gemini", "--prompt", INITIAL_PROMPT, *self._model_flags()]
        else:
            return None

        run_interactive(cmd, cwd=str(workspace_dir))

        # Post-session: persist memory if agent updated it
        memory_src = workspace_dir / "memory.md"
        if memory_src.exists():
            self._persist_memory(memory_src)

        # Check for decisions
        decisions = workspace_dir / "output" / "decisions.toml"
        return AgentResult(
            success=decisions.exists(),
            output="",
            decisions_path=decisions if decisions.exists() else None,
            workspace_path=workspace_dir,
        )

    def _manual_instructions(self, workspace_dir: Path) -> AgentResult:
        """Return manual instructions when no automated launch is possible."""
        provider = self.config.provider

        return AgentResult(
            success=False,
            output=(
                f"Workspace prepared: {workspace_dir}\n"
                f"\n"
                f"To start manually:\n"
                f"  cd {workspace_dir}\n"
                f'  {provider} "{INITIAL_PROMPT}"\n'
                f"\n"
                f"After classification:\n"
                f"  popctl advisor apply\n"
            ),
            error=MANUAL_MODE_SENTINEL,
            workspace_path=workspace_dir,
        )

    def _model_flags(self) -> list[str]:
        """Return --model flags if a model is explicitly configured."""
        if self.config.model:
            return ["--model", self.config.effective_model]
        return []

    def _persist_memory(self, workspace_memory: Path) -> None:
        """Copy memory.md from workspace to persistent XDG state location.

        Args:
            workspace_memory: Path to memory.md in the session workspace.
        """
        logger = logging.getLogger(__name__)
        try:
            advisor_dir = ensure_dir(get_state_dir() / "advisor", "advisor memory")
            persistent_path = advisor_dir / "memory.md"
            shutil.copy2(workspace_memory, persistent_path)
            logger.debug("Persisted memory.md to %s", persistent_path)
        except (OSError, RuntimeError) as e:
            logger.warning("Could not persist memory.md: %s", e)

    def _build_headless_command(self, workspace_dir: Path) -> list[str]:
        """Build command for headless agent execution.

        Args:
            workspace_dir: Session workspace directory.

        Returns:
            List of command arguments for subprocess execution.
        """
        provider = self.config.provider

        if provider == "claude":
            return [
                "claude",
                "-p",
                INITIAL_PROMPT,
                "--output-format",
                "json",
                *self._model_flags(),
            ]
        # gemini
        return ["gemini", "--prompt", INITIAL_PROMPT, *self._model_flags()]
