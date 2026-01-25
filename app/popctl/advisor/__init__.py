"""Claude Advisor integration for AI-assisted package classification.

This module provides AI-assisted package classification using Claude Code
or Gemini CLI. It supports both interactive and headless (autonomous) modes.

Public API:
- AdvisorConfig: Configuration model for advisor settings
- load_advisor_config: Load advisor configuration from TOML file
- save_advisor_config: Save advisor configuration to TOML file
- is_running_in_container: Check if popctl is running inside a container
- AgentResult: Result from agent execution
- AgentRunner: Runs AI agents for package classification
- build_headless_prompt: Build prompt for headless classification
- build_interactive_instructions: Build instructions.md for interactive mode
"""

from popctl.advisor.config import (
    AdvisorConfig,
    AdvisorProvider,
    is_running_in_container,
    load_advisor_config,
    save_advisor_config,
)
from popctl.advisor.prompts import (
    CATEGORIES,
    build_headless_prompt,
    build_interactive_instructions,
    get_decisions_schema,
    get_instructions_file_path,
    get_prompt_file_path,
)
from popctl.advisor.runner import AgentResult, AgentRunner

__all__ = [
    "AdvisorConfig",
    "AdvisorProvider",
    "AgentResult",
    "AgentRunner",
    "CATEGORIES",
    "build_headless_prompt",
    "build_interactive_instructions",
    "get_decisions_schema",
    "get_instructions_file_path",
    "get_prompt_file_path",
    "is_running_in_container",
    "load_advisor_config",
    "save_advisor_config",
]
