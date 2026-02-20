"""Config history recording — delegates to domain.history."""

from popctl.domain.history import record_domain_deletions


def record_config_deletions(
    deleted_paths: list[str],
    command: str = "popctl config clean",
) -> None:
    """Record config deletions to history."""
    record_domain_deletions("configs", deleted_paths, command=command)
