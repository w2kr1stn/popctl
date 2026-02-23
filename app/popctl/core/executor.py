"""Execution orchestration and history recording.

Provides action execution dispatching and history recording for package
management operations. These functions are shared between the `apply`
and `sync` CLI commands.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import TYPE_CHECKING

from popctl.core.state import record_action
from popctl.models.action import ActionType
from popctl.models.history import (
    HistoryActionType,
    HistoryItem,
    create_history_entry,
)
from popctl.models.package import PackageSource
from popctl.utils.formatting import print_warning

if TYPE_CHECKING:
    from popctl.models.action import Action, ActionResult
    from popctl.operators.base import Operator

logger = logging.getLogger(__name__)

_PACKAGE_TO_HISTORY: dict[ActionType, HistoryActionType] = {
    ActionType.INSTALL: HistoryActionType.INSTALL,
    ActionType.REMOVE: HistoryActionType.REMOVE,
    ActionType.PURGE: HistoryActionType.PURGE,
}


def execute_actions(
    actions: list[Action],
    operators: list[Operator],
) -> list[ActionResult]:
    """Execute actions using the appropriate operators.

    Groups actions by their :attr:`~Action.source` and dispatches each
    group to the matching operator.

    Args:
        actions: List of actions to execute.
        operators: List of available operators.

    Returns:
        List of :class:`ActionResult` for all executed actions.
    """

    results: list[ActionResult] = []

    # Group actions by source
    actions_by_source: dict[PackageSource, list[Action]] = defaultdict(list)
    for action in actions:
        actions_by_source[action.source].append(action)

    # Execute actions for each source
    for operator in operators:
        source_actions = actions_by_source.get(operator.source, [])
        if not source_actions:
            continue

        # Group actions by type for batch processing
        install_pkgs = [a.package for a in source_actions if a.action_type == ActionType.INSTALL]
        remove_pkgs = [a.package for a in source_actions if a.action_type == ActionType.REMOVE]
        purge_pkgs = [a.package for a in source_actions if a.action_type == ActionType.PURGE]

        if install_pkgs:
            results.extend(operator.install(install_pkgs))
        if remove_pkgs:
            results.extend(operator.remove(remove_pkgs, purge=False))
        if purge_pkgs:
            results.extend(operator.remove(purge_pkgs, purge=True))

    return results


def record_actions_to_history(
    results: list[ActionResult],
    command: str = "popctl apply",
) -> None:
    """Record successful actions to history.

    Groups results by action type and records separate history entries
    for each type.  Only successful actions are recorded.

    Errors during history recording are logged but do **not** interrupt
    the calling command's flow.

    Args:
        results: List of action results from execution.
        command: Command string stored in the history entry metadata.
            Defaults to ``"popctl apply"`` for backward compatibility.
    """
    try:
        for action_type in (ActionType.INSTALL, ActionType.REMOVE, ActionType.PURGE):
            successful_items = [
                HistoryItem(
                    name=r.action.package,
                    source=r.action.source,
                )
                for r in results
                if r.success and r.action.action_type == action_type
            ]

            if successful_items:
                entry = create_history_entry(
                    action_type=_PACKAGE_TO_HISTORY[action_type],
                    items=successful_items,
                    metadata={"command": command},
                )
                record_action(entry)
                logger.debug(
                    "Recorded %d %s action(s) to history",
                    len(successful_items),
                    action_type.value,
                )

    except (OSError, RuntimeError) as e:
        logger.warning("Failed to record actions to history: %s", str(e))
        print_warning(f"Could not record actions to history: {e}")
