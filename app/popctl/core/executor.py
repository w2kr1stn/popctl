"""Execution orchestration and history recording.

Provides operator factory functions, action execution dispatching,
and history recording for package management operations. These functions
are shared between the `apply` and `sync` CLI commands.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from popctl.cli.types import SourceChoice
from popctl.core.state import StateManager
from popctl.models.action import ActionType
from popctl.models.history import (
    HistoryActionType,
    HistoryItem,
    create_history_entry,
)
from popctl.operators.apt import AptOperator
from popctl.operators.base import Operator
from popctl.operators.flatpak import FlatpakOperator
from popctl.operators.snap import SnapOperator
from popctl.utils.formatting import print_warning

if TYPE_CHECKING:
    from popctl.models.action import Action, ActionResult
    from popctl.models.package import PackageSource

logger = logging.getLogger(__name__)

# Mapping from action model types to history model types.
ACTION_TO_HISTORY: dict[ActionType, HistoryActionType] = {
    ActionType.INSTALL: HistoryActionType.INSTALL,
    ActionType.REMOVE: HistoryActionType.REMOVE,
    ActionType.PURGE: HistoryActionType.PURGE,
}


def get_operators(source: SourceChoice, dry_run: bool = False) -> list[Operator]:
    """Get operator instances based on source selection.

    Args:
        source: The source choice (apt, flatpak, snap, or all).
        dry_run: Whether to run in dry-run mode.

    Returns:
        List of operator instances for the requested source(s).
    """
    operators: list[Operator] = []

    if source in (SourceChoice.APT, SourceChoice.ALL):
        operators.append(AptOperator(dry_run=dry_run))

    if source in (SourceChoice.FLATPAK, SourceChoice.ALL):
        operators.append(FlatpakOperator(dry_run=dry_run))

    if source in (SourceChoice.SNAP, SourceChoice.ALL):
        operators.append(SnapOperator(dry_run=dry_run))

    return operators


def get_available_operators(source: SourceChoice, dry_run: bool = False) -> list[Operator]:
    """Get available operator instances based on source selection.

    Wraps :func:`get_operators` and filters out operators whose
    underlying package manager is not installed on the system.

    Args:
        source: The source choice (apt, flatpak, or all).
        dry_run: Whether to run in dry-run mode.

    Returns:
        List of available operator instances.
    """
    return [op for op in get_operators(source, dry_run) if op.is_available()]


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
    actions_by_source: dict[PackageSource, list[Action]] = {}
    for action in actions:
        if action.source not in actions_by_source:
            actions_by_source[action.source] = []
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
        state = StateManager()

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
                    action_type=ACTION_TO_HISTORY[action_type],
                    items=successful_items,
                    metadata={"command": command},
                )
                state.record_action(entry)
                logger.debug(
                    "Recorded %d %s action(s) to history",
                    len(successful_items),
                    action_type.value,
                )

    except (OSError, RuntimeError) as e:
        logger.warning("Failed to record actions to history: %s", str(e))
        print_warning(f"Could not record actions to history: {e}")
