"""Claude Advisor integration for AI-assisted package classification.

This module provides AI-assisted package classification using Claude Code
or Gemini CLI. It supports workspace-based interactive sessions and
headless (autonomous) batch classification.

Public API:
- AdvisorConfig: Configuration model for advisor settings
- load_advisor_config: Load advisor configuration from TOML file
- save_advisor_config: Save advisor configuration to TOML file
- is_running_in_container: Check if popctl is running inside a container
- AgentResult: Result from agent execution
- AgentRunner: Runs AI agents for package classification
- build_headless_prompt: Build prompt for headless classification
- build_session_claude_md: Build CLAUDE.md for interactive workspace
- create_session_workspace: Create ephemeral workspace for session
- find_latest_decisions: Find decisions from most recent session
- import_decisions: Import and validate decisions.toml
- cleanup_exchange_dir: Remove files from exchange directory
"""

from popctl.advisor.config import (
    AdvisorConfig,
    AdvisorProvider,
    is_running_in_container,
    load_advisor_config,
    save_advisor_config,
)
from popctl.advisor.exchange import (
    ConfigOrphanEntry,
    DecisionsResult,
    DomainDecisions,
    FilesystemOrphanEntry,
    PackageDecision,
    PackageScanEntry,
    PathDecision,
    ScanExport,
    SourceDecisions,
    cleanup_exchange_dir,
    import_decisions,
)
from popctl.advisor.prompts import (
    CATEGORIES,
    build_headless_prompt,
    build_session_claude_md,
    get_prompt_file_path,
)
from popctl.advisor.runner import AgentResult, AgentRunner
from popctl.advisor.workspace import (
    create_session_workspace,
    find_latest_decisions,
    list_sessions,
)

__all__ = [
    "AdvisorConfig",
    "AdvisorProvider",
    "AgentResult",
    "AgentRunner",
    "CATEGORIES",
    "ConfigOrphanEntry",
    "DecisionsResult",
    "DomainDecisions",
    "FilesystemOrphanEntry",
    "PackageDecision",
    "PackageScanEntry",
    "PathDecision",
    "ScanExport",
    "SourceDecisions",
    "build_headless_prompt",
    "build_session_claude_md",
    "cleanup_exchange_dir",
    "create_session_workspace",
    "find_latest_decisions",
    "get_prompt_file_path",
    "import_decisions",
    "is_running_in_container",
    "list_sessions",
    "load_advisor_config",
    "save_advisor_config",
]
