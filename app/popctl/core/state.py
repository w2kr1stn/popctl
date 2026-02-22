"""State management for history tracking.

This module provides functions for persisting and querying
history entries in a JSONL file format.
"""

import json
import logging
from pathlib import Path

from popctl.core.paths import get_state_dir
from popctl.models.history import (
    HistoryActionType,
    HistoryEntry,
    create_history_entry,
)

logger = logging.getLogger(__name__)

HISTORY_FILENAME = "history.jsonl"


def record_action(entry: HistoryEntry, state_dir: Path | None = None) -> None:
    """Append action to history file.

    Creates file and parent directories if they don't exist.
    Uses atomic append for safety.

    Args:
        entry: The history entry to record.
        state_dir: Optional override for state directory.

    Raises:
        RuntimeError: If the state directory cannot be created.
        OSError: If the file cannot be written.
    """
    resolved = state_dir if state_dir is not None else get_state_dir()

    # Ensure state directory exists
    try:
        resolved.mkdir(parents=True, exist_ok=True)
    except PermissionError as e:
        msg = f"Cannot create state directory {resolved}: Permission denied"
        raise RuntimeError(msg) from e

    # Serialize and append
    line = entry.to_json_line()
    path = resolved / HISTORY_FILENAME

    # Open in append mode for atomic writes
    with path.open(mode="a", encoding="utf-8") as f:
        f.write(line + "\n")
        f.flush()


def get_history(limit: int | None = None, state_dir: Path | None = None) -> list[HistoryEntry]:
    """Read history entries, newest first.

    Reads all entries from the history file and returns them in
    reverse chronological order (newest first).

    Args:
        limit: Maximum number of entries to return.
              If None, returns all entries.
        state_dir: Optional override for state directory.

    Returns:
        List of HistoryEntry, newest first.
        Returns empty list if file doesn't exist.
    """
    path = (state_dir if state_dir is not None else get_state_dir()) / HISTORY_FILENAME

    if not path.exists():
        return []

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

    # Apply limit if specified
    if limit is not None:
        return entries[:limit]

    return entries


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
    history = get_history(state_dir=state_dir)

    # Collect IDs of entries that have been reversed (from loaded history)
    reversed_ids = _extract_reversed_ids(history)

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
    # Create a reversal marker entry
    reversal_entry = create_history_entry(
        action_type=_get_inverse_action_type(entry.action_type),
        items=list(entry.items),
        reversible=False,  # Reversal entries are not reversible
        metadata={
            "reversed_entry_id": entry.id,
            "reversal_of": entry.action_type.value,
        },
    )

    # Record the reversal
    record_action(reversal_entry, state_dir=state_dir)


def _extract_reversed_ids(entries: list[HistoryEntry]) -> set[str]:
    """Extract IDs of entries that have been marked as reversed.

    Args:
        entries: List of history entries to extract reversed IDs from.

    Returns:
        Set of entry IDs that have been reversed.
    """
    reversed_ids: set[str] = set()

    for entry in entries:
        reversed_id = entry.metadata.get("reversed_entry_id")
        if reversed_id:
            reversed_ids.add(reversed_id)

    return reversed_ids


def _get_inverse_action_type(action_type: HistoryActionType) -> HistoryActionType:
    """Get the inverse action type for undo operations.

    Args:
        action_type: The original action type.

    Returns:
        The inverse action type (INSTALL becomes REMOVE, etc.).
    """
    inverse_map = {
        HistoryActionType.INSTALL: HistoryActionType.REMOVE,
        HistoryActionType.REMOVE: HistoryActionType.INSTALL,
        HistoryActionType.PURGE: HistoryActionType.INSTALL,
    }
    if action_type not in inverse_map:
        msg = f"Cannot invert non-reversible action type: {action_type}"
        raise ValueError(msg)
    return inverse_map[action_type]
