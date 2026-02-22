"""History recording for advisor operations.

This module provides functions for recording advisor apply decisions
to the history system, keeping this logic in the advisor domain layer
rather than in CLI command modules.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from popctl.core.state import record_action
from popctl.models.history import (
    HistoryActionType,
    HistoryItem,
    create_history_entry,
)
from popctl.models.package import PACKAGE_SOURCE_KEYS, PackageSource
from popctl.utils.formatting import print_warning

if TYPE_CHECKING:
    from popctl.advisor.exchange import DecisionsResult


def record_advisor_apply_to_history(
    decisions: DecisionsResult,
) -> None:
    """Record advisor apply decisions to history.

    Creates a single history entry for all classifications applied.
    Errors during recording are logged but do not interrupt the flow.

    Args:
        decisions: The decisions result that was applied.
    """
    _logger = logging.getLogger(__name__)

    try:
        # Collect all items from decisions
        items: list[HistoryItem] = []

        for source_str in PACKAGE_SOURCE_KEYS:
            source_decisions = decisions.packages.get(source_str)  # type: ignore[arg-type]
            if source_decisions is None:
                continue

            pkg_source = PackageSource(source_str)

            # Add keep decisions
            for decision in source_decisions.keep:
                items.append(HistoryItem(name=decision.name, source=pkg_source))

            # Add remove decisions
            for decision in source_decisions.remove:
                items.append(HistoryItem(name=decision.name, source=pkg_source))

        if items:
            entry = create_history_entry(
                action_type=HistoryActionType.ADVISOR_APPLY,
                items=items,
                metadata={"command": "popctl advisor apply"},
            )
            record_action(entry)
            _logger.debug("Recorded %d advisor apply item(s) to history", len(items))

    except (OSError, RuntimeError) as e:
        _logger.warning("Failed to record advisor apply to history: %s", str(e))
        print_warning(f"Could not record classifications to history: {e}")
