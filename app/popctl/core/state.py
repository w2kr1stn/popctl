import json
import logging
from pathlib import Path
from typing import Literal

from popctl.core.paths import ensure_dir, get_state_dir
from popctl.models.history import (
    HistoryActionType,
    HistoryEntry,
    HistoryItem,
    create_history_entry,
)

logger = logging.getLogger(__name__)

HISTORY_FILENAME = "history.jsonl"

INVERSE_ACTION_TYPES: dict[HistoryActionType, HistoryActionType] = {
    HistoryActionType.INSTALL: HistoryActionType.REMOVE,
    HistoryActionType.REMOVE: HistoryActionType.INSTALL,
    HistoryActionType.PURGE: HistoryActionType.INSTALL,
}


def record_action(entry: HistoryEntry, state_dir: Path | None = None) -> None:
    resolved = state_dir if state_dir is not None else get_state_dir()

    ensure_dir(resolved, "state")

    # Serialize and append
    line = entry.to_json_line()
    path = resolved / HISTORY_FILENAME

    # Open in append mode for atomic writes
    with path.open(mode="a", encoding="utf-8") as f:
        f.write(line + "\n")


def get_history(
    limit: int | None = None,
    since: str | None = None,
    state_dir: Path | None = None,
) -> tuple[list[HistoryEntry], int]:
    """Returns ``(entries_newest_first, corrupt_count)``."""
    path = (state_dir if state_dir is not None else get_state_dir()) / HISTORY_FILENAME

    if not path.exists():
        return [], 0

    entries: list[HistoryEntry] = []
    corrupt_count = 0

    with path.open(encoding="utf-8") as f:
        for line_num, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue

            try:
                entry = HistoryEntry.from_json_line(line)
                entries.append(entry)
            except (json.JSONDecodeError, KeyError, ValueError) as e:
                logger.warning(
                    "Skipping corrupt history line %d: %s",
                    line_num,
                    str(e),
                )
                corrupt_count += 1
                continue

    # Log summary of corrupt lines if any were found
    if corrupt_count > 0:
        logger.warning(
            "Found %d corrupt line(s) in history file: %s",
            corrupt_count,
            path,
        )

    # Reverse for newest first
    entries.reverse()

    # Apply since filter
    if since is not None:
        entries = [e for e in entries if e.timestamp[:10] >= since]

    # Apply limit if specified
    if limit is not None:
        return entries[:limit], corrupt_count

    return entries, corrupt_count


def get_last_reversible(state_dir: Path | None = None) -> HistoryEntry | None:
    """Get the most recent reversible action.

    Scans the history for the most recent entry that has
    reversible=True and has not been marked as reversed.

    Args:
        state_dir: Optional override for state directory.

    Returns:
        Most recent reversible HistoryEntry, or None if no reversible
        actions exist.
    """
    # Get all history (newest first)
    history, _ = get_history(state_dir=state_dir)

    # Collect IDs of entries that have been reversed (from loaded history)
    reversed_ids = {
        e.metadata["reversed_entry_id"] for e in history if "reversed_entry_id" in e.metadata
    }

    # Find first reversible entry that hasn't been reversed
    for entry in history:
        if entry.reversible and entry.id not in reversed_ids:
            return entry

    return None


def mark_entry_reversed(entry: HistoryEntry, state_dir: Path | None = None) -> None:
    """Mark an entry as reversed (not reversible anymore).

    This is done by appending a new "reversal" entry that references
    the original, rather than modifying the original entry. This
    maintains the append-only nature of the history file.

    Args:
        entry: The history entry to mark as reversed.
        state_dir: Optional override for state directory.
    """
    inverse = INVERSE_ACTION_TYPES.get(entry.action_type)
    if inverse is None:
        msg = f"Cannot reverse action type: {entry.action_type.value}"
        raise ValueError(msg)

    # Create a reversal marker entry
    reversal_entry = create_history_entry(
        action_type=inverse,
        items=list(entry.items),
        reversible=False,  # Reversal entries are not reversible
        metadata={
            "reversed_entry_id": entry.id,
            "reversal_of": entry.action_type.value,
        },
    )

    # Record the reversal
    record_action(reversal_entry, state_dir=state_dir)


# ---------------------------------------------------------------------------
# Domain deletion history
# ---------------------------------------------------------------------------


def record_domain_deletions(
    domain: Literal["filesystem", "configs"],
    deleted_paths: list[str],
    command: str,
) -> None:
    """Record domain deletions to history.

    Creates a HistoryEntry with the appropriate action type for each deleted
    path. Domain deletions use ``source=None`` since they are not
    package-manager operations.

    Args:
        domain: Domain identifier ("filesystem" or "configs").
        deleted_paths: List of absolute paths that were deleted.
        command: Command that triggered the deletions.
    """
    action_type = (
        HistoryActionType.FS_DELETE if domain == "filesystem" else HistoryActionType.CONFIG_DELETE
    )
    items = [HistoryItem(name=path) for path in deleted_paths]

    entry = create_history_entry(
        action_type=action_type,
        items=items,
        reversible=False,
        metadata={"domain": domain, "command": command},
    )

    record_action(entry)
