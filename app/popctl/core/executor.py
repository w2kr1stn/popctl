"""Execution orchestration and history recording.

Provides operator factory functions, action execution dispatching,
and history recording for package management operations. These functions
are shared between the `apply` and `sync` CLI commands.
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
from popctl.operators.apt import AptOperator
from popctl.operators.base import Operator
from popctl.operators.flatpak import FlatpakOperator
from popctl.operators.snap import SnapOperator
from popctl.utils.formatting import print_warning

if TYPE_CHECKING:
    from popctl.models.action import Action, ActionResult

logger = logging.getLogger(__name__)

_OPERATOR_CLASSES: dict[PackageSource, type[Operator]] = {
    PackageSource.APT: AptOperator,
    PackageSource.FLATPAK: FlatpakOperator,
    PackageSource.SNAP: SnapOperator,
}


def get_available_operators(
    source: PackageSource | None = None, dry_run: bool = False
) -> list[Operator]:
    """Get operator instances that are available on this system.

    Args:
        source: Specific package source, or None for all sources.
        dry_run: Whether to run in dry-run mode.

    Returns:
        List of available operator instances.
    """
    classes = _OPERATOR_CLASSES if source is None else {source: _OPERATOR_CLASSES[source]}
    operators = [cls(dry_run=dry_run) for cls in classes.values()]
    return [op for op in operators if op.is_available()]


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
        if source_actions:
            source_results = operator.execute(source_actions)
            results.extend(source_results)

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
                    action_type=HistoryActionType(action_type.value),
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
