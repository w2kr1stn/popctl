"""State management for history tracking.

This module provides the StateManager class for persisting and querying
history entries in a JSONL file format.
"""

import json
import logging
from pathlib import Path

from popctl.core.paths import ensure_state_dir, get_state_dir
from popctl.models.history import (
    HistoryActionType,
    HistoryEntry,
    create_history_entry,
)

logger = logging.getLogger(__name__)


class StateManager:
    """Manages history state in JSONL file.

    Storage location: ~/.local/state/popctl/history.jsonl

    The history file uses JSON Lines format where each line is a complete
    JSON object representing a HistoryEntry. This format allows for
    efficient append-only writes and easy parsing.

    Attributes:
        state_dir: Directory containing the history file.
    """

    HISTORY_FILENAME = "history.jsonl"

    def __init__(self, state_dir: Path | None = None) -> None:
        """Initialize StateManager.

        Args:
            state_dir: Optional override for state directory.
                      Default: ~/.local/state/popctl
        """
        self._state_dir = state_dir if state_dir is not None else get_state_dir()

    @property
    def history_path(self) -> Path:
        """Path to history.jsonl file.

        Returns:
            Path to the history file within the state directory.
        """
        return self._state_dir / self.HISTORY_FILENAME

    def record_action(self, entry: HistoryEntry) -> None:
        """Append action to history file.

        Creates file and parent directories if they don't exist.
        Uses atomic append for safety.

        Args:
            entry: The history entry to record.

        Raises:
            RuntimeError: If the state directory cannot be created.
            OSError: If the file cannot be written.
        """
        # Ensure state directory exists
        if self._state_dir == get_state_dir():
            ensure_state_dir()
        else:
            self._state_dir.mkdir(parents=True, exist_ok=True)

        # Serialize and append
        line = entry.to_json_line()

        # Open in append mode for atomic writes
        with self.history_path.open(mode="a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()

    def get_history(self, limit: int | None = None) -> list[HistoryEntry]:
        """Read history entries, newest first.

        Reads all entries from the history file and returns them in
        reverse chronological order (newest first).

        Args:
            limit: Maximum number of entries to return.
                  If None, returns all entries.

        Returns:
            List of HistoryEntry, newest first.
            Returns empty list if file doesn't exist.
        """
        if not self.history_path.exists():
            return []

        entries: list[HistoryEntry] = []

        with self.history_path.open(encoding="utf-8") as f:
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
                    continue

        # Reverse for newest first
        entries.reverse()

        # Apply limit if specified
        if limit is not None:
            return entries[:limit]

        return entries

    def get_last_reversible(self) -> HistoryEntry | None:
        """Get the most recent reversible action.

        Scans the history for the most recent entry that has
        reversible=True and has not been marked as reversed.

        Returns:
            Most recent reversible HistoryEntry, or None if no reversible
            actions exist.
        """
        # Get all history (newest first)
        history = self.get_history()

        # Collect IDs of entries that have been reversed
        reversed_ids = self._get_reversed_entry_ids()

        # Find first reversible entry that hasn't been reversed
        for entry in history:
            if entry.reversible and entry.id not in reversed_ids:
                return entry

        return None

    def _get_reversed_entry_ids(self) -> set[str]:
        """Get IDs of entries that have been marked as reversed.

        Reversal entries have action_type REVERSAL (which doesn't exist yet)
        or have metadata indicating they are reversals.

        Returns:
            Set of entry IDs that have been reversed.
        """
        history = self.get_history()
        reversed_ids: set[str] = set()

        for entry in history:
            # Check if this entry is a reversal marker
            reversed_id = entry.metadata.get("reversed_entry_id")
            if reversed_id:
                reversed_ids.add(reversed_id)

        return reversed_ids

    def get_entry_by_id(self, entry_id: str) -> HistoryEntry | None:
        """Find entry by ID.

        Searches the history file for an entry with the given ID.

        Args:
            entry_id: The entry ID to find.

        Returns:
            HistoryEntry if found, None otherwise.
        """
        # Get all history
        history = self.get_history()

        for entry in history:
            if entry.id == entry_id:
                return entry

        return None

    def mark_entry_reversed(self, entry_id: str) -> bool:
        """Mark an entry as reversed (not reversible anymore).

        This is done by appending a new "reversal" entry that references
        the original, rather than modifying the original entry. This
        maintains the append-only nature of the history file.

        Args:
            entry_id: The ID of the entry to mark as reversed.

        Returns:
            True if entry was found and marked, False otherwise.
        """
        # Find the original entry
        original = self.get_entry_by_id(entry_id)
        if original is None:
            return False

        # Create a reversal marker entry
        # We use the same items but mark it as a reversal
        reversal_entry = create_history_entry(
            action_type=self._get_inverse_action_type(original.action_type),
            items=list(original.items),
            reversible=False,  # Reversal entries are not reversible
            metadata={
                "reversed_entry_id": entry_id,
                "reversal_of": original.action_type.value,
            },
        )

        # Record the reversal
        self.record_action(reversal_entry)
        return True

    def _get_inverse_action_type(self, action_type: HistoryActionType) -> HistoryActionType:
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
            HistoryActionType.APPLY: HistoryActionType.APPLY,
            HistoryActionType.ADVISOR_APPLY: HistoryActionType.ADVISOR_APPLY,
        }
        return inverse_map.get(action_type, action_type)
