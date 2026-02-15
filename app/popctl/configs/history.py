"""Config deletion history recording.

Records config deletion operations to the shared history file,
enabling audit trails for config cleanup actions.
"""

from popctl.core.state import StateManager
from popctl.models.history import (
    HistoryActionType,
    HistoryItem,
    create_history_entry,
)
from popctl.models.package import PackageSource


def record_config_deletions(
    deleted_paths: list[str],
    command: str = "popctl config clean",
) -> None:
    """Record config deletions to history.

    Creates a HistoryEntry with CONFIG_DELETE action type for each deleted
    path. Uses PackageSource.APT as a placeholder source since
    HistoryItem.source is typed as PackageSource. The configs domain
    is identified via the metadata field.

    This is a pragmatic MVP choice -- a proper HistoryItem generalization
    can follow in a future refactor.

    Args:
        deleted_paths: List of absolute paths that were deleted.
        command: Command that triggered the deletions.

    Raises:
        ValueError: If deleted_paths is empty.
    """
    items = [HistoryItem(name=path, source=PackageSource.APT) for path in deleted_paths]

    entry = create_history_entry(
        action_type=HistoryActionType.CONFIG_DELETE,
        items=items,
        reversible=False,
        metadata={"domain": "configs", "command": command},
    )

    StateManager().record_action(entry)
