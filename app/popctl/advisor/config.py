"""Advisor configuration and settings.

This module provides the configuration model and I/O functions for the
Claude Advisor, which enables AI-assisted package classification.

The advisor supports two providers:
- claude: Claude Code CLI (default)
- gemini: Gemini CLI

Configuration is stored in ~/.config/popctl/advisor.toml
"""

import logging
import tomllib
from pathlib import Path
from typing import Annotated, Literal

import tomli_w
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from popctl.core.paths import get_config_dir

logger = logging.getLogger(__name__)

# Provider type alias
AdvisorProvider = Literal["claude", "gemini"]

# Default models per provider
DEFAULT_MODELS: dict[AdvisorProvider, str] = {
    "claude": "sonnet",
    "gemini": "gemini-2.5-pro",
}


class AdvisorConfig(BaseModel):
    """Configuration for Claude Advisor.

    Defines settings for AI-assisted package classification including
    which provider to use, model selection, and timeout settings.

    Attributes:
        provider: AI provider to use ("claude" or "gemini").
        model: Model name to use. If None, uses default per provider.
        timeout_seconds: Maximum time for advisor operations (default: 600s / 10 min).
    """

    model_config = ConfigDict(extra="ignore")

    provider: Annotated[
        AdvisorProvider,
        Field(description="AI provider to use"),
    ] = "claude"
    model: Annotated[
        str | None,
        Field(description="Model name (None = use default per provider)"),
    ] = None
    timeout_seconds: Annotated[
        int,
        Field(ge=60, le=3600, description="Timeout in seconds (60-3600)"),
    ] = 600

    @property
    def effective_model(self) -> str:
        """Get the effective model name.

        Returns the configured model if set, otherwise returns the
        default model for the selected provider.

        Returns:
            Model name to use for API calls.
        """
        if self.model:
            return self.model
        return DEFAULT_MODELS[self.provider]


class AdvisorConfigError(Exception):
    """Exception for advisor configuration errors."""


def load_advisor_config(path: Path | None = None) -> AdvisorConfig:
    """Load advisor configuration from a TOML file.

    Args:
        path: Path to the config file. If None, uses default advisor config path.

    Returns:
        Validated AdvisorConfig object.

    Raises:
        AdvisorConfigError: If the config file is missing, unreadable, or invalid.
    """
    config_path = path or get_config_dir() / "advisor.toml"

    if not config_path.exists():
        raise AdvisorConfigError(f"Advisor config not found: {config_path}")

    try:
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise AdvisorConfigError(f"Invalid TOML syntax: {e}") from e
    except OSError as e:
        raise AdvisorConfigError(f"Failed to read advisor config: {e}") from e

    try:
        return AdvisorConfig.model_validate(data)
    except (ValueError, ValidationError) as e:
        raise AdvisorConfigError(f"Invalid advisor config content: {e}") from e


def save_advisor_config(config: AdvisorConfig, path: Path | None = None) -> Path:
    """Save advisor configuration to a TOML file.

    Args:
        config: The AdvisorConfig object to save.
        path: Path to save the config. If None, uses default advisor config path.

    Returns:
        Path where the config was saved.

    Raises:
        AdvisorConfigError: If the file cannot be written.
    """
    config_path = path or get_config_dir() / "advisor.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        with open(config_path, "wb") as f:
            tomli_w.dump(config.model_dump(exclude_none=True), f)
    except OSError as e:
        raise AdvisorConfigError(f"Failed to write advisor config: {e}") from e

    return config_path


def load_or_create_config(
    provider: str | None = None,
    model: str | None = None,
) -> AdvisorConfig:
    """Load existing config or create default, with optional overrides.

    Args:
        provider: Optional provider override (e.g. "claude", "gemini").
        model: Optional model name override.

    Returns:
        AdvisorConfig with applied overrides.
    """
    config_path = get_config_dir() / "advisor.toml"
    try:
        config = load_advisor_config()
    except AdvisorConfigError as e:
        if config_path.exists():
            # Config file exists but is corrupt — warn the user
            logger.warning("Advisor config is corrupt, using defaults: %s", e)
        config = AdvisorConfig()
        try:
            save_advisor_config(config)
        except AdvisorConfigError as save_err:
            logger.debug("Could not save default config: %s", save_err)

    if provider is not None or model is not None:
        updates: dict[str, str] = {}
        if provider is not None:
            updates["provider"] = provider
        if model is not None:
            updates["model"] = model
        config = config.model_copy(update=updates)

    return config
