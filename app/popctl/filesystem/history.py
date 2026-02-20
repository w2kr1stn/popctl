"""Filesystem history recording — delegates to domain.history."""

from popctl.domain.history import record_domain_deletions


def record_fs_deletions(
    deleted_paths: list[str],
    command: str = "popctl fs clean",
) -> None:
    """Record filesystem deletions to history."""
    record_domain_deletions("filesystem", deleted_paths, command=command)
