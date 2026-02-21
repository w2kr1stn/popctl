"""Docker container utilities for advisor communication.

Provides functions for finding running containers, checking container
status, and copying files between host and containers.
"""

from popctl.utils.shell import CommandResult, run_command


def find_running_container(name_pattern: str = "ai-dev") -> str | None:
    """Find a running Docker container whose name contains the pattern.

    Docker Compose generates names like ``ai-dev-base-dev-1``, so this
    uses substring matching rather than exact name comparison.

    Args:
        name_pattern: Substring to match against container names.

    Returns:
        Full container name if found, None otherwise.
    """
    try:
        result = run_command(
            ["docker", "ps", "--filter", f"name={name_pattern}", "--format", "{{.Names}}"],
            timeout=10.0,
        )
        if not result.success:
            return None
        for line in result.stdout.strip().splitlines():
            if name_pattern in line:
                return line.strip()
        return None
    except (FileNotFoundError, OSError):
        return None


def is_container_running(name: str = "ai-dev") -> bool:
    """Check if a Docker container matching the name is running.

    Args:
        name: Substring to match against container names.

    Returns:
        True if a matching container is running, False otherwise.
    """
    return find_running_container(name) is not None


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
