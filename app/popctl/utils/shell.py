import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import cast


@dataclass(frozen=True, slots=True)
class CommandResult:
    stdout: str
    stderr: str
    returncode: int

    @property
    def success(self) -> bool:
        return self.returncode == 0


@dataclass(frozen=True, slots=True)
class BytesCommandResult:
    stdout: bytes
    stderr: bytes
    returncode: int

    @property
    def success(self) -> bool:
        return self.returncode == 0


def _run_subprocess(
    args: list[str],
    *,
    timeout: float | None,
    cwd: str | None,
    env: dict[str, str] | None,
    text: bool,
    input_data: str | bytes | None = None,
) -> subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]:
    if input_data is not None:
        result = subprocess.run(
            args,
            capture_output=True,
            text=text,
            check=False,
            timeout=timeout,
            cwd=cwd,
            env=env,
            input=input_data,
        )
    else:
        result = subprocess.run(
            args,
            capture_output=True,
            text=text,
            check=False,
            timeout=timeout,
            cwd=cwd,
            env=env,
        )
    return cast(
        "subprocess.CompletedProcess[str] | subprocess.CompletedProcess[bytes]",
        result,
    )


def run_command(
    args: list[str],
    *,
    timeout: float | None = 60.0,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> CommandResult:
    full_env = {**os.environ, **(env or {})} if env else None
    try:
        result = _run_subprocess(
            args,
            timeout=timeout,
            cwd=cwd,
            env=full_env,
            text=True,
        )
    except subprocess.TimeoutExpired:
        cmd_str = " ".join(args[:3])
        return CommandResult(
            stdout="",
            stderr=f"Command timed out after {timeout}s: {cmd_str}",
            returncode=-1,
        )
    except FileNotFoundError:
        command = args[0] if args else "command"
        return CommandResult(
            stdout="",
            stderr=f"Command not found: {command}",
            returncode=-1,
        )
    return CommandResult(
        stdout=cast("str", result.stdout),
        stderr=cast("str", result.stderr),
        returncode=result.returncode,
    )


def run_command_bytes(
    args: list[str],
    *,
    input: bytes | None = None,
    timeout: float | None = 60.0,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> BytesCommandResult:
    try:
        result = _run_subprocess(
            args,
            timeout=timeout,
            cwd=cwd,
            env=dict(env) if env is not None else None,
            text=False,
            input_data=input,
        )
    except subprocess.TimeoutExpired:
        cmd_str = " ".join(args[:3])
        return BytesCommandResult(
            stdout=b"",
            stderr=f"Command timed out after {timeout}s: {cmd_str}".encode(),
            returncode=-1,
        )
    return BytesCommandResult(
        stdout=cast("bytes", result.stdout),
        stderr=cast("bytes", result.stderr),
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
