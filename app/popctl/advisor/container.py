"""Docker dev-container helpers for advisor execution.

Provides functions for finding/starting containers via docker compose
and copying files between host and containers.
"""

import contextlib

from popctl.utils.shell import run_command

CONTAINER_WORKSPACE = "/tmp/popctl-advisor"  # noqa: S108


def find_container(compose_dir: str) -> str | None:
    """Find the first running container from a docker compose project.

    Args:
        compose_dir: Path to directory containing docker-compose.yml.

    Returns:
        Container ID if found, None otherwise.
    """
    try:
        result = run_command(
            ["docker", "compose", "ps", "-q", "--status", "running"],
            timeout=15.0,
            cwd=compose_dir,
        )
    except (FileNotFoundError, OSError):
        return None

    if not result.success:
        return None

    for line in result.stdout.strip().splitlines():
        container_id = line.strip()
        if container_id:
            return container_id
    return None


def ensure_container(compose_dir: str) -> str | None:
    """Ensure a container is running, starting via docker compose if needed.

    Args:
        compose_dir: Path to directory containing docker-compose.yml.

    Returns:
        Container ID if running, None if start failed.
    """
    container = find_container(compose_dir)
    if container is not None:
        return container

    try:
        result = run_command(
            ["docker", "compose", "up", "-d"],
            timeout=120.0,
            cwd=compose_dir,
        )
    except (FileNotFoundError, OSError):
        return None

    if not result.success:
        return None

    return find_container(compose_dir)


def container_has_command(container_id: str, command: str) -> bool:
    """Check if a command exists in the container's PATH.

    Args:
        container_id: Docker container ID.
        command: Command name to check (e.g. "claude").

    Returns:
        True if the command is found, False otherwise.
    """
    try:
        result = run_command(
            ["docker", "exec", container_id, "bash", "-lc", f"which {command}"],
            timeout=10.0,
        )
    except (FileNotFoundError, OSError):
        return False
    return result.success


def docker_cp(src: str, dest: str) -> bool:
    """Copy files between host and a Docker container.

    Args:
        src: Source path (host path or container_id:path).
        dest: Destination path (host path or container_id:path).

    Returns:
        True if copy succeeded, False otherwise.
    """
    try:
        result = run_command(["docker", "cp", src, dest], timeout=60.0)
    except (FileNotFoundError, OSError):
        return False
    return result.success


def container_cleanup(container_id: str, path: str) -> None:
    """Remove a directory inside a container (best-effort).

    Args:
        container_id: Docker container ID.
        path: Absolute path inside the container to remove.
    """
    with contextlib.suppress(FileNotFoundError, OSError):
        run_command(
            ["docker", "exec", container_id, "rm", "-rf", path],
            timeout=30.0,
        )
