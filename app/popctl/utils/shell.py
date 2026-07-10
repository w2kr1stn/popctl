import os
import shutil
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CommandResult:
    stdout: str
    stderr: str
    returncode: int

    @property
    def success(self) -> bool:
        return self.returncode == 0


def run_command(
    args: list[str],
    *,
    timeout: float | None = 60.0,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> CommandResult:
    full_env = {**os.environ, **(env or {})} if env else None
    try:
        result = subprocess.run(
            args,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            cwd=cwd,
            env=full_env,
        )
    except subprocess.TimeoutExpired:
        cmd_str = " ".join(args[:3])
        return CommandResult(
            stdout="",
            stderr=f"Command timed out after {timeout}s: {cmd_str}",
            returncode=-1,
        )
    return CommandResult(
        stdout=result.stdout,
        stderr=result.stderr,
        returncode=result.returncode,
    )


def command_exists(name: str) -> bool:
    return shutil.which(name) is not None


def safe_resolve(path: str) -> str:
    return os.path.normpath(os.path.abspath(os.path.expanduser(path)))


def run_interactive(
    args: list[str],
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> int:
    """Unlike run_command(), does not capture stdout/stderr -- inherits the terminal."""
    full_env = {**os.environ, **(env or {})}
    result = subprocess.run(
        args,
        check=False,
        cwd=cwd,
        env=full_env,
    )
    return result.returncode
