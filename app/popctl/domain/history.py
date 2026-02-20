"""Domain-agnostic deletion history recording.

Records deletion operations for filesystem and config domains
to the shared history file, enabling audit trails for cleanup actions.
"""

from typing import Literal

from popctl.core.state import StateManager
from popctl.models.history import (
    HistoryActionType,
    HistoryItem,
    create_history_entry,
)
from popctl.models.package import PackageSource

_ACTION_TYPES: dict[str, HistoryActionType] = {
    "filesystem": HistoryActionType.FS_DELETE,
    "configs": HistoryActionType.CONFIG_DELETE,
}

_DEFAULT_COMMANDS: dict[str, str] = {
    "filesystem": "popctl fs clean",
    "configs": "popctl config clean",
}


def record_domain_deletions(
    domain: Literal["filesystem", "configs"],
    deleted_paths: list[str],
    command: str | None = None,
) -> None:
    """Record domain deletions to history.

    Creates a HistoryEntry with the appropriate action type for each deleted
    path. Uses PackageSource.APT as a placeholder source since
    HistoryItem.source is typed as PackageSource. The domain is identified
    via the metadata field.

    This is a pragmatic MVP choice -- a proper HistoryItem generalization
    can follow in a future refactor.

    Args:
        domain: Domain identifier ("filesystem" or "configs").
        deleted_paths: List of absolute paths that were deleted.
        command: Command that triggered the deletions. Uses domain default if None.
    """
    # TODO(production): Replace PackageSource.APT placeholder with domain-typed HistoryItem
    items = [HistoryItem(name=path, source=PackageSource.APT) for path in deleted_paths]

    entry = create_history_entry(
        action_type=_ACTION_TYPES[domain],
        items=items,
        reversible=False,
        metadata={"domain": domain, "command": command or _DEFAULT_COMMANDS[domain]},
    )

    StateManager().record_action(entry)
