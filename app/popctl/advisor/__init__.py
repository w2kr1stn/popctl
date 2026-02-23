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
- build_initial_prompt: Get initial prompt for interactive session
- create_session_workspace: Create ephemeral workspace for session
- find_latest_decisions: Find decisions from most recent session
- export_scan_for_advisor: Export scan results for AI agent
- export_prompt_files: Export prompt files to exchange directory
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
    DecisionsResult,
    PackageDecision,
    PackageScanEntry,
    ScanExport,
    SourceDecisions,
    cleanup_exchange_dir,
    export_prompt_files,
    export_scan_for_advisor,
    get_decisions_path,
    get_scan_json_path,
    import_decisions,
)
from popctl.advisor.prompts import (
    CATEGORIES,
    build_headless_prompt,
    build_initial_prompt,
    build_session_claude_md,
    get_decisions_schema,
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
    "DecisionsResult",
    "PackageDecision",
    "PackageScanEntry",
    "ScanExport",
    "SourceDecisions",
    "build_headless_prompt",
    "build_initial_prompt",
    "build_session_claude_md",
    "cleanup_exchange_dir",
    "create_session_workspace",
    "export_prompt_files",
    "export_scan_for_advisor",
    "find_latest_decisions",
    "get_decisions_path",
    "get_decisions_schema",
    "get_prompt_file_path",
    "get_scan_json_path",
    "import_decisions",
    "is_running_in_container",
    "list_sessions",
    "load_advisor_config",
    "save_advisor_config",
]
