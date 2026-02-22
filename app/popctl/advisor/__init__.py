"""Claude Advisor integration for AI-assisted package classification.

This module provides AI-assisted package classification using Claude Code
or Gemini CLI. It supports workspace-based interactive sessions.

Public API:
- AdvisorConfig: Configuration model for advisor settings
- load_advisor_config: Load advisor configuration from TOML file
- save_advisor_config: Save advisor configuration to TOML file
- AgentResult: Result from agent execution
- AgentRunner: Runs AI agents for package classification
- build_session_claude_md: Build CLAUDE.md for interactive workspace
- create_session_workspace: Create ephemeral workspace for session
- find_latest_decisions: Find decisions from most recent session
- import_decisions: Import and validate decisions.toml
"""

from popctl.advisor.config import (
    AdvisorConfig,
    AdvisorProvider,
    load_advisor_config,
    save_advisor_config,
)
from popctl.advisor.exchange import (
    DecisionsResult,
    DomainDecisions,
    OrphanEntry,
    PackageDecision,
    PackageScanEntry,
    PathDecision,
    ScanExport,
    SourceDecisions,
    apply_decisions_to_manifest,
    import_decisions,
)
from popctl.advisor.prompts import (
    CATEGORIES,
    build_session_claude_md,
)
from popctl.advisor.runner import AgentResult, AgentRunner
from popctl.advisor.workspace import (
    create_session_workspace,
    find_latest_decisions,
)

__all__ = [
    "AdvisorConfig",
    "AdvisorProvider",
    "AgentResult",
    "AgentRunner",
    "CATEGORIES",
    "apply_decisions_to_manifest",
    "DecisionsResult",
    "DomainDecisions",
    "OrphanEntry",
    "PackageDecision",
    "PackageScanEntry",
    "PathDecision",
    "ScanExport",
    "SourceDecisions",
    "build_session_claude_md",
    "create_session_workspace",
    "find_latest_decisions",
    "import_decisions",
    "load_advisor_config",
    "save_advisor_config",
]
