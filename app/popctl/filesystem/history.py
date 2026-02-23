"""Filesystem history recording.

Records filesystem deletion operations to the shared history file,
enabling audit trails for cleanup actions.
"""

from popctl.core.state import StateManager
from popctl.models.history import (
    HistoryActionType,
    HistoryItem,
    create_history_entry,
)
from popctl.models.package import PackageSource


def record_fs_deletions(
    deleted_paths: list[str],
    command: str = "popctl fs clean",
) -> None:
    """Record filesystem deletions to history.

    Creates a HistoryEntry with FS_DELETE action type for each deleted
    path. Uses PackageSource.APT as a placeholder source since
    HistoryItem.source is typed as PackageSource. The filesystem domain
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
        action_type=HistoryActionType.FS_DELETE,
        items=items,
        reversible=False,
        metadata={"domain": "filesystem", "command": command},
    )

    StateManager().record_action(entry)
