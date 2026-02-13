"""Agent runner for AI-assisted package classification.

This module provides the AgentRunner class for executing AI agents
(Claude Code or Gemini CLI) in headless or interactive session mode.

Two execution modes:

1. Host-Mode (container_mode is False):
   Direct call to claude/gemini CLI on the host system.

2. Container-Mode (container_mode is True):
   Call via codeagent CLI for container execution.
"""

import contextlib
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from popctl.advisor.config import AdvisorConfig
from popctl.advisor.prompts import build_initial_prompt


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

    The AgentRunner executes AI agents (Claude Code or Gemini CLI) for
    package classification. It supports two execution modes:

    - Host-Mode: Direct invocation of CLI tools on the host system
    - Container-Mode: Invocation via codeagent CLI for container execution

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
        from popctl.utils.shell import run_command

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
        """Launch real interactive Claude Code session with cascading fallback.

        Priority chain:
        1. Running container → docker cp workspace → docker exec claude
        2. codeagent available → start container
        3. Host claude/gemini → os.execvp (replaces process)
        4. Print manual instructions (last resort)

        Args:
            workspace_dir: Session workspace directory.

        Returns:
            AgentResult from the session.
        """
        if not sys.stdin.isatty():
            return self._manual_instructions(workspace_dir)

        if self.config.container_mode:
            # Step 1: Check for running container
            container_result = self._try_container_exec(workspace_dir)
            if container_result is not None:
                return container_result

            # Step 2: Try starting via codeagent
            codeagent_result = self._try_codeagent_start(workspace_dir)
            if codeagent_result is not None:
                return codeagent_result

        # Step 3: Try host CLI
        host_result = self._try_host_exec(workspace_dir)
        if host_result is not None:
            return host_result

        # Step 4: Manual fallback
        return self._manual_instructions(workspace_dir)

    def _try_container_exec(self, workspace_dir: Path) -> AgentResult | None:
        """Try to exec into a running container.

        Returns AgentResult if container was found, None to continue cascade.
        """
        from popctl.utils.shell import docker_cp, is_container_running, run_command, run_interactive

        container_name = "ai-dev"
        if not is_container_running(container_name):
            return None

        remote_dir = "/tmp/popctl-advisor"
        initial_prompt = build_initial_prompt()
        provider = self.config.provider

        # Pre-clean to ensure idempotent docker cp behavior
        run_command(["docker", "exec", container_name, "rm", "-rf", remote_dir], timeout=30.0)

        # Copy workspace into container
        cp_result = docker_cp(str(workspace_dir), f"{container_name}:{remote_dir}")
        if not cp_result.success:
            return None

        # Build provider-specific command
        if provider == "claude":
            agent_cmd = [provider, initial_prompt]
        else:
            agent_cmd = [provider, "--prompt", initial_prompt]

        # Docker exec interactive session
        try:
            exit_code = run_interactive(
                ["docker", "exec", "-it", "-w", remote_dir, container_name, *agent_cmd]
            )
        except (FileNotFoundError, OSError):
            return None

        # Copy results back
        decisions_remote = f"{container_name}:{remote_dir}/output/decisions.toml"
        docker_cp(decisions_remote, str(workspace_dir / "output") + "/")

        # Cleanup container workspace
        run_command(["docker", "exec", container_name, "rm", "-rf", remote_dir], timeout=30.0)

        decisions_path = workspace_dir / "output" / "decisions.toml"
        found = decisions_path.exists()
        return AgentResult(
            success=found,
            output="",
            error=None if found else f"Container session exited with code {exit_code}",
            decisions_path=decisions_path if found else None,
            workspace_path=workspace_dir,
        )

    def _try_codeagent_start(self, workspace_dir: Path) -> AgentResult | None:
        """Try to start a container via codeagent, then delegate to container logic.

        Starts the container without mounting to avoid ~/workspace/ conflicts
        (e.g. when the container was previously opened with --here).
        codeagent start is a foreground command (like docker-compose up),
        so it runs as a background process while we interact via docker exec.

        Returns AgentResult if codeagent is available, None to continue cascade.
        """
        import shutil
        import time

        from popctl.utils.shell import is_container_running

        if shutil.which("codeagent") is None:
            return None

        container_name = "ai-dev"

        # codeagent start is a blocking foreground command — run in background
        try:
            process = subprocess.Popen(
                ["codeagent", "start"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (FileNotFoundError, OSError):
            return None

        # Poll until container is ready (max ~60s)
        ready = False
        for _ in range(30):
            if process.poll() is not None:
                return None
            if is_container_running(container_name):
                ready = True
                break
            time.sleep(2)

        if not ready:
            process.terminate()
            return None

        # Container running — delegate to docker cp + exec
        try:
            return self._try_container_exec(workspace_dir)
        finally:
            with contextlib.suppress(ProcessLookupError):
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()

    def _try_host_exec(self, workspace_dir: Path) -> AgentResult | None:
        """Try to launch the AI CLI directly on the host.

        Uses os.execvp() to replace the current process for TTY handover.
        This method never returns on success (process is replaced).

        Returns None if the CLI tool is not available.
        """
        import os
        import shutil

        provider = self.config.provider
        initial_prompt = build_initial_prompt()

        if provider == "claude" and shutil.which("claude") is not None:
            os.chdir(workspace_dir)
            os.execvp("claude", ["claude", initial_prompt])

        if provider == "gemini" and shutil.which("gemini") is not None:
            os.chdir(workspace_dir)
            os.execvp("gemini", ["gemini", "--prompt", initial_prompt])

        return None

    def _manual_instructions(self, workspace_dir: Path) -> AgentResult:
        """Return manual instructions when no automated launch is possible."""
        initial_prompt = build_initial_prompt()
        provider = self.config.provider

        return AgentResult(
            success=False,
            output=(
                f"Workspace prepared: {workspace_dir}\n"
                f"\n"
                f"To start manually:\n"
                f"  cd {workspace_dir}\n"
                f'  {provider} "{initial_prompt}"\n'
                f"\n"
                f"After classification:\n"
                f"  popctl advisor apply\n"
            ),
            error="manual_mode",
            workspace_path=workspace_dir,
        )

    def _build_headless_command(self, workspace_dir: Path) -> list[str]:
        """Build command for headless agent execution.

        Args:
            workspace_dir: Session workspace directory.

        Returns:
            List of command arguments for subprocess execution.
        """
        provider = self.config.provider
        model = self.config.effective_model
        initial_prompt = build_initial_prompt()

        if self._is_container_mode():
            return [
                "codeagent",
                "run",
                provider,
                initial_prompt,
                "--write",
                "--mount",
                str(workspace_dir),
                "--model",
                model,
            ]

        if provider == "claude":
            return [
                "claude",
                "-p",
                initial_prompt,
                "--output-format",
                "json",
            ]
        # gemini
        return [
            "gemini",
            "--prompt",
            initial_prompt,
        ]

    def _is_container_mode(self) -> bool:
        """Check if running in container mode.

        Returns:
            True if container_mode is enabled in config.
        """
        return self.config.container_mode
