from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from djinn_in_a_box.sessions import SessionManager

from popctl.advisor.config import AdvisorConfig
from popctl.advisor.exchange import (
    DecisionsResult,
    DomainDecisions,
    PackageDecision,
    PathDecision,
    SourceDecisions,
    import_decisions,
)
from popctl.advisor.runner import AgentResult, AgentRunner
from popctl.advisor.workspace import (
    cleanup_empty_sessions,
    create_session_workspace,
    delete_session,
    find_all_unapplied_decisions,
)


def get_session_manager() -> SessionManager | None:
    try:
        from djinn_in_a_box.sessions import SessionManager

        return SessionManager("popctl")
    except ImportError:
        return None


__all__ = [
    "AdvisorConfig",
    "AgentResult",
    "AgentRunner",
    "DecisionsResult",
    "DomainDecisions",
    "PackageDecision",
    "PathDecision",
    "SourceDecisions",
    "cleanup_empty_sessions",
    "create_session_workspace",
    "delete_session",
    "find_all_unapplied_decisions",
    "get_session_manager",
    "import_decisions",
]
