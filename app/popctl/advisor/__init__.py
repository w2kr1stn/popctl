"""Claude Advisor integration for AI-assisted package classification.

This module provides AI-assisted package classification using Claude Code
or Gemini CLI. It supports workspace-based interactive sessions.

Public API:
- AdvisorConfig: Configuration model for advisor settings
- AgentResult: Result from agent execution
- AgentRunner: Runs AI agents for package classification
- create_session_workspace: Create ephemeral workspace for session
- find_latest_decisions: Find decisions from most recent session
- import_decisions: Import and validate decisions.toml

Exchange models (DecisionsResult, SourceDecisions, etc.) are available
via ``popctl.advisor.exchange``. Prompt-related symbols (CATEGORIES,
build_session_claude_md) are in ``popctl.advisor.prompts``.
"""

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
    create_session_workspace,
    find_latest_decisions,
)

__all__ = [
    "AdvisorConfig",
    "AgentResult",
    "AgentRunner",
    "DecisionsResult",
    "DomainDecisions",
    "PackageDecision",
    "PathDecision",
    "SourceDecisions",
    "create_session_workspace",
    "find_latest_decisions",
    "import_decisions",
]
