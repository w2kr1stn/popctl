"""Agent runner for AI-assisted package classification.

This module provides the AgentRunner class for executing AI agents
(Claude Code or Gemini CLI) in headless or interactive session mode,
on the host or inside a Docker dev container.
"""

import logging
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from popctl.advisor.config import AdvisorConfig
from popctl.advisor.container import (
    CONTAINER_WORKSPACE,
    container_cleanup,
    container_has_command,
    docker_cp,
    ensure_container,
)
from popctl.advisor.prompts import INITIAL_PROMPT
from popctl.core.paths import ensure_dir, get_state_dir
from popctl.utils.formatting import print_warning
from popctl.utils.shell import run_command, run_interactive

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
    classification on the host system or inside a dev container.

    Attributes:
        config: AdvisorConfig with provider, model, timeout, and container settings.
    """

    config: AdvisorConfig

    # ── Public API ──────────────────────────────────────────────

    def run_headless(self, workspace_dir: Path) -> AgentResult:
        """Run agent in headless mode (autonomous classification).

        Args:
            workspace_dir: Session workspace directory with scan.json and CLAUDE.md.

        Returns:
            AgentResult with path to decisions.toml if successful.
        """
        if self.config.container_mode:
            return self._run_headless_container(workspace_dir)
        return self._run_headless_host(workspace_dir)

    def launch_interactive(self, workspace_dir: Path) -> AgentResult:
        """Launch interactive AI session with fallback to manual instructions.

        Host mode: CLI check → TTY check → interactive or headless.
        Container mode: ensure container → CLI check → TTY check → exec.

        Args:
            workspace_dir: Session workspace directory.

        Returns:
            AgentResult from the session.
        """
        if self.config.container_mode:
            return self._launch_container(workspace_dir)
        return self._launch_host(workspace_dir)

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

    # ── Container mode ──────────────────────────────────────────

    def _launch_container(self, workspace_dir: Path) -> AgentResult:
        """Container-mode launch: ensure container → CLI check → exec."""
        compose_dir = str(self.config.dev_container_path)
        container = ensure_container(compose_dir)
        if not container:
            print_warning(f"Could not start dev container from {compose_dir}")
            return self._manual_instructions(workspace_dir)
        if not container_has_command(container, self.config.provider):
            print_warning(f"'{self.config.provider}' not found in container {container[:12]}")
            return self._manual_instructions(workspace_dir)
        if not sys.stdin.isatty():
            return self._exec_container_headless(workspace_dir, container)
        return self._exec_container_interactive(workspace_dir, container)

    def _run_headless_container(self, workspace_dir: Path) -> AgentResult:
        """Run headless agent inside a dev container."""
        compose_dir = str(self.config.dev_container_path)
        container = ensure_container(compose_dir)
        if not container:
            return AgentResult(
                success=False,
                output="",
                error="Could not find or start dev container",
                workspace_path=workspace_dir,
            )
        return self._exec_container_headless(workspace_dir, container)

    def _exec_container_interactive(self, workspace_dir: Path, container_id: str) -> AgentResult:
        """Run interactive session inside a dev container."""
        remote = CONTAINER_WORKSPACE
        if not self._copy_workspace_to_container(container_id, workspace_dir, remote):
            return AgentResult(
                success=False,
                output="",
                error="Failed to copy workspace into container",
                workspace_path=workspace_dir,
            )

        shell_cmd = self._build_shell_command(interactive=True)
        try:
            run_interactive(
                [
                    "docker", "exec", "-it", "-w", remote,
                    "-e", "TERM=xterm-256color", "-e", "COLORTERM=truecolor",
                    container_id, "bash", "-lc", shell_cmd,
                ]
            )
        except (FileNotFoundError, OSError) as e:
            container_cleanup(container_id, remote)
            return AgentResult(
                success=False,
                output="",
                error=f"Container exec failed: {e}",
                workspace_path=workspace_dir,
            )

        self._copy_results_from_container(container_id, workspace_dir, remote)
        container_cleanup(container_id, remote)
        return self._decisions_result(workspace_dir)

    def _exec_container_headless(self, workspace_dir: Path, container_id: str) -> AgentResult:
        """Run headless agent inside a dev container."""
        remote = CONTAINER_WORKSPACE
        if not self._copy_workspace_to_container(container_id, workspace_dir, remote):
            return AgentResult(
                success=False,
                output="",
                error="Failed to copy workspace into container",
                workspace_path=workspace_dir,
            )

        shell_cmd = self._build_shell_command(interactive=False)
        try:
            result = run_command(
                [
                    "docker", "exec", "-w", remote,
                    "-e", "TERM=xterm-256color", "-e", "COLORTERM=truecolor",
                    container_id, "bash", "-lc", shell_cmd,
                ],
                timeout=float(self.config.timeout_seconds),
            )
        except subprocess.TimeoutExpired:
            container_cleanup(container_id, remote)
            return AgentResult(
                success=False,
                output="",
                error=f"Container agent timed out after {self.config.timeout_seconds}s",
                workspace_path=workspace_dir,
            )
        except (FileNotFoundError, OSError) as e:
            container_cleanup(container_id, remote)
            return AgentResult(
                success=False,
                output="",
                error=f"Container exec failed: {e}",
                workspace_path=workspace_dir,
            )

        self._copy_results_from_container(container_id, workspace_dir, remote)
        container_cleanup(container_id, remote)

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
            error=result.stderr or f"Container agent exited with code {result.returncode}",
            workspace_path=workspace_dir,
        )

    # ── Container workspace helpers ─────────────────────────────

    @staticmethod
    def _copy_workspace_to_container(container_id: str, workspace_dir: Path, remote: str) -> bool:
        """Copy host workspace into container. Returns True on success."""
        container_cleanup(container_id, remote)
        return docker_cp(f"{workspace_dir}/.", f"{container_id}:{remote}")

    def _copy_results_from_container(
        self, container_id: str, workspace_dir: Path, remote: str
    ) -> None:
        """Copy decisions and memory from container back to host (best-effort)."""
        logger = logging.getLogger(__name__)
        output_dir = workspace_dir / "output"
        output_dir.mkdir(exist_ok=True)

        if not docker_cp(f"{container_id}:{remote}/output/decisions.toml", f"{output_dir}/"):
            logger.debug("No decisions.toml found in container")

        if docker_cp(f"{container_id}:{remote}/memory.md", f"{workspace_dir}/"):
            memory_src = workspace_dir / "memory.md"
            if memory_src.exists():
                self._persist_memory(memory_src)
        else:
            logger.debug("No memory.md found in container")

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

    def _build_shell_command(self, *, interactive: bool) -> str:
        """Build a shell command string for docker exec bash -lc."""
        cmd = self._build_interactive_command() if interactive else self._build_headless_command()
        return shlex.join(cmd)

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
        memory_src = workspace_dir / "memory.md"
        if memory_src.exists():
            self._persist_memory(memory_src)
        return self._decisions_result(workspace_dir)

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
