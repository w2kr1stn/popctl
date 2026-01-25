"""Claude Advisor integration for AI-assisted package classification.

This module provides AI-assisted package classification using Claude Code
or Gemini CLI. It supports both interactive and headless (autonomous) modes.

Public API:
- AdvisorConfig: Configuration model for advisor settings
- load_advisor_config: Load advisor configuration from TOML file
- save_advisor_config: Save advisor configuration to TOML file
- is_running_in_container: Check if popctl is running inside a container
"""

from popctl.advisor.config import (
    AdvisorConfig,
    AdvisorProvider,
    is_running_in_container,
    load_advisor_config,
    save_advisor_config,
)

__all__ = [
    "AdvisorConfig",
    "AdvisorProvider",
    "is_running_in_container",
    "load_advisor_config",
    "save_advisor_config",
]
