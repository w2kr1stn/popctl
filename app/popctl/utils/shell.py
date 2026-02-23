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
) -> CommandResult:
    """Execute a shell command and return the result.

    Args:
        args: Command and arguments to execute.
        check: If True, raise CalledProcessError on non-zero exit.
        timeout: Maximum time in seconds to wait for command.

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
