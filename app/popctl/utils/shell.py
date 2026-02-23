"""Shell execution utilities.

Provides safe subprocess execution with proper error handling.
"""

import os
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
    timeout: float | None = 60.0,
    cwd: str | None = None,
) -> CommandResult:
    """Execute a shell command and return the result.

    Args:
        args: Command and arguments to execute.
        timeout: Maximum time in seconds to wait for command.
        cwd: Working directory for the command. If None, uses current directory.

    Returns:
        CommandResult with stdout, stderr, and returncode.

    Raises:
        subprocess.TimeoutExpired: If command exceeds timeout.
        FileNotFoundError: If command executable is not found.
    """
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        check=False,
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
    full_env = {**os.environ, **(env or {})}
    result = subprocess.run(
        args,
        check=False,
        cwd=cwd,
        env=full_env,
    )
    return result.returncode
