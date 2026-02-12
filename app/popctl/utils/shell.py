"""Shell execution utilities.

Provides safe subprocess execution with proper error handling.
"""

import shutil
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CommandResult:
    """Result of a shell command execution.

    Attributes:
        stdout: Standard output from the command.
        stderr: Standard error from the command.
        returncode: Exit code of the command.
    """

    stdout: str
    stderr: str
    returncode: int

    @property
    def success(self) -> bool:
        """Check if command executed successfully."""
        return self.returncode == 0


def run_command(
    args: list[str],
    *,
    check: bool = False,
    timeout: float | None = 60.0,
    cwd: str | None = None,
) -> CommandResult:
    """Execute a shell command and return the result.

    Args:
        args: Command and arguments to execute.
        check: If True, raise CalledProcessError on non-zero exit.
        timeout: Maximum time in seconds to wait for command.
        cwd: Working directory for the command. If None, uses current directory.

    Returns:
        CommandResult with stdout, stderr, and returncode.

    Raises:
        subprocess.CalledProcessError: If check=True and command fails.
        subprocess.TimeoutExpired: If command exceeds timeout.
        FileNotFoundError: If command executable is not found.
    """
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=check,
        timeout=timeout,
        cwd=cwd,
    )
    return CommandResult(
        stdout=result.stdout,
        stderr=result.stderr,
        returncode=result.returncode,
    )


def command_exists(name: str) -> bool:
    """Check if a command exists in the system PATH.

    Args:
        name: Command name to check.

    Returns:
        True if command exists, False otherwise.
    """
    return shutil.which(name) is not None


def run_interactive(
    args: list[str],
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> int:
    """Execute a command interactively, inheriting the terminal.

    Unlike run_command(), this does NOT capture stdout/stderr,
    allowing the subprocess to interact with the user's terminal
    directly. Suitable for launching interactive CLI tools.

    Args:
        args: Command and arguments to execute.
        cwd: Working directory for the command.
        env: Additional environment variables (merged with current env).

    Returns:
        Exit code of the command.

    Raises:
        FileNotFoundError: If command executable is not found.
        OSError: If command cannot be executed.
    """
    import os

    full_env = {**os.environ, **(env or {})}
    result = subprocess.run(
        args,
        check=False,
        cwd=cwd,
        env=full_env,
    )
    return result.returncode


def is_container_running(name: str = "ai-dev") -> bool:
    """Check if a Docker container is running.

    Args:
        name: Container name to check.

    Returns:
        True if the container is running, False otherwise.
    """
    try:
        result = run_command(
            ["docker", "ps", "--filter", f"name={name}", "--format", "{{.Names}}"],
            timeout=10.0,
        )
        return result.success and name in result.stdout.strip().splitlines()
    except (FileNotFoundError, OSError):
        return False


def docker_cp(src: str, dest: str) -> CommandResult:
    """Copy files between host and a Docker container.

    Args:
        src: Source path (host path or container:path).
        dest: Destination path (host path or container:path).

    Returns:
        CommandResult with stdout, stderr, and returncode.

    Raises:
        FileNotFoundError: If docker is not found.
    """
    return run_command(["docker", "cp", src, dest], timeout=60.0)
