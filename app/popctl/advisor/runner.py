"""Agent runner for AI-assisted package classification.

This module provides the AgentRunner class for executing AI agents
(Claude Code or Gemini CLI) in headless or interactive session mode,
on the host or via Djinn SessionManager.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from popctl.advisor.config import AdvisorConfig
from popctl.advisor.prompts import INITIAL_PROMPT
from popctl.core.paths import ensure_dir, get_state_dir
from popctl.utils.formatting import print_warning
from popctl.utils.shell import run_command, run_interactive

if TYPE_CHECKING:
    from djinn_in_a_box.sessions import SessionManager

MANUAL_MODE_SENTINEL: str = "manual_mode"

# Ensure rich color output in advisor sessions (Claude Code / Gemini CLI).
_SESSION_ENV: dict[str, str] = {
    "TERM": "xterm-256color",
    "COLORTERM": "truecolor",
}


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
    classification on the host system or via Djinn SessionManager.

    Attributes:
        config: AdvisorConfig with provider, model, and timeout settings.
        session: Optional SessionManager for container-based execution.
    """

    config: AdvisorConfig
    session: SessionManager | None = field(default=None)

    # ── Public API ──────────────────────────────────────────────

    def run_headless(self, workspace_dir: Path) -> AgentResult:
        """Run agent in headless mode (autonomous classification).

        Delegates to SessionManager if available, otherwise runs on host.

        Args:
            workspace_dir: Session workspace directory with scan.json and CLAUDE.md.

        Returns:
            AgentResult with path to decisions.toml if successful.
        """
        if self.session is not None:
            return self._run_headless_session(workspace_dir)
        return self._run_headless_host(workspace_dir)

    def launch_interactive(self, workspace_dir: Path) -> AgentResult:
        """Launch interactive AI session with fallback to manual instructions.

        Delegates to SessionManager if available, otherwise uses host CLI.

        Args:
            workspace_dir: Session workspace directory.

        Returns:
            AgentResult from the session.
        """
        if self.session is not None:
            return self._launch_interactive_session(workspace_dir)
        return self._launch_host(workspace_dir)

    # ── SessionManager mode ──────────────────────────────────────

    def _run_headless_session(self, workspace_dir: Path) -> AgentResult:
        """Run headless agent via SessionManager."""
        assert self.session is not None
        try:
            headless_kwargs: dict[str, object] = {
                "workspace_dir": workspace_dir,
                "prompt": INITIAL_PROMPT,
                "timeout": self.config.timeout_seconds,
            }
            if self.config.model:
                headless_kwargs["model"] = self.config.effective_model
            result = self.session.run_headless(**headless_kwargs)  # type: ignore[arg-type]
        except ValueError as e:
            return AgentResult(
                success=False, output="", error=str(e), workspace_path=workspace_dir,
            )

        self._post_session_persist_memory(workspace_dir)
        decisions_path = workspace_dir / "output" / "decisions.toml"
        if result.success and decisions_path.exists():
            return AgentResult(
                success=True,
                output=result.stdout,
                decisions_path=decisions_path,
                workspace_path=workspace_dir,
            )
        return AgentResult(
            success=False,
            output=result.stdout,
            error=result.stderr or f"Agent exited with code {result.returncode}",
            workspace_path=workspace_dir,
        )

    def _launch_interactive_session(self, workspace_dir: Path) -> AgentResult:
        """Launch interactive session via SessionManager."""
        assert self.session is not None
        try:
            interactive_kwargs: dict[str, object] = {
                "workspace_dir": workspace_dir,
                "initial_prompt": INITIAL_PROMPT,
            }
            if self.config.model:
                interactive_kwargs["model"] = self.config.effective_model
            result = self.session.run_interactive(**interactive_kwargs)  # type: ignore[arg-type]
        except ValueError as e:
            return AgentResult(
                success=False, output="", error=str(e), workspace_path=workspace_dir,
            )

        if result.returncode != 0:
            return AgentResult(
                success=False,
                output=result.stdout,
                error=result.stderr or f"Session exited with code {result.returncode}",
                workspace_path=workspace_dir,
            )
        return self._post_session_result(workspace_dir)

    # ── Host mode ───────────────────────────────────────────────

    def _launch_host(self, workspace_dir: Path) -> AgentResult:
        """Host-mode launch: CLI check → TTY check → exec or headless."""
        if not shutil.which(self.config.provider):
            return self._manual_instructions(workspace_dir)
        if not sys.stdin.isatty():
            return self._run_headless_host(workspace_dir)
        return self._exec_host_interactive(workspace_dir)

    def _exec_host_interactive(self, workspace_dir: Path) -> AgentResult:
        """Run interactive session on host. Caller must verify CLI exists."""
        cmd = self._build_interactive_command()
        run_interactive(cmd, cwd=str(workspace_dir), env=_SESSION_ENV)
        return self._post_session_result(workspace_dir)

    def _run_headless_host(self, workspace_dir: Path) -> AgentResult:
        """Run headless agent on host."""
        command = self._build_headless_command()

        try:
            result = run_command(
                command,
                timeout=float(self.config.timeout_seconds),
                cwd=str(workspace_dir),
                env=_SESSION_ENV,
            )
        except subprocess.TimeoutExpired:
            return AgentResult(
                success=False,
                output="",
                error=f"Agent execution timed out after {self.config.timeout_seconds} seconds",
                workspace_path=workspace_dir,
            )
        except (FileNotFoundError, OSError) as e:
            return AgentResult(
                success=False,
                output="",
                error=f"Agent command failed: {e}",
                workspace_path=workspace_dir,
            )

        decisions_path = workspace_dir / "output" / "decisions.toml"
        if result.success and decisions_path.exists():
            return AgentResult(
                success=True,
                output=result.stdout,
                decisions_path=decisions_path,
                workspace_path=workspace_dir,
            )
        return AgentResult(
            success=False,
            output=result.stdout,
            error=result.stderr or f"Agent exited with code {result.returncode}",
            workspace_path=workspace_dir,
        )

    # ── Shared helpers ──────────────────────────────────────────

    def _build_interactive_command(self) -> list[str]:
        """Build command for interactive agent execution."""
        provider = self.config.provider
        if provider == "claude":
            return ["claude", INITIAL_PROMPT, *self._model_flags()]
        return ["gemini", "--prompt", INITIAL_PROMPT, *self._model_flags()]

    def _build_headless_command(self) -> list[str]:
        """Build command for headless agent execution."""
        provider = self.config.provider
        if provider == "claude":
            return ["claude", "-p", INITIAL_PROMPT, "--output-format", "json", *self._model_flags()]
        return ["gemini", "--prompt", INITIAL_PROMPT, *self._model_flags()]

    def _model_flags(self) -> list[str]:
        """Return --model flags if a model is explicitly configured."""
        if self.config.model:
            return ["--model", self.config.effective_model]
        return []

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

    def _post_session_result(self, workspace_dir: Path) -> AgentResult:
        """Build AgentResult after an interactive session, persisting memory."""
        self._post_session_persist_memory(workspace_dir)
        return self._decisions_result(workspace_dir)

    def _post_session_persist_memory(self, workspace_dir: Path) -> None:
        """Persist memory.md from workspace if it exists."""
        memory_src = workspace_dir / "memory.md"
        if memory_src.exists():
            self._persist_memory(memory_src)

    @staticmethod
    def _decisions_result(workspace_dir: Path) -> AgentResult:
        """Build AgentResult based on whether decisions.toml exists."""
        decisions = workspace_dir / "output" / "decisions.toml"
        return AgentResult(
            success=decisions.exists(),
            output="",
            decisions_path=decisions if decisions.exists() else None,
            workspace_path=workspace_dir,
        )

    def _persist_memory(self, workspace_memory: Path) -> None:
        """Copy memory.md from workspace to persistent XDG state location."""
        logger = logging.getLogger(__name__)
        try:
            advisor_dir = ensure_dir(get_state_dir() / "advisor", "advisor memory")
            persistent_path = advisor_dir / "memory.md"
            shutil.copy2(workspace_memory, persistent_path)
            logger.debug("Persisted memory.md to %s", persistent_path)
        except (OSError, RuntimeError) as e:
            logger.warning("Could not persist memory.md: %s", e)
            print_warning(f"Could not persist advisor memory: {e}")
