"""Advisor configuration and settings.

This module provides the configuration model and I/O functions for the
Claude Advisor, which enables AI-assisted package classification.

The advisor supports two providers:
- claude: Claude Code CLI (default)
- gemini: Gemini CLI

Configuration is stored in ~/.config/popctl/advisor.toml
"""

import tomllib
from pathlib import Path
from typing import Annotated, Literal

import tomli_w
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from popctl.core.paths import get_advisor_config_path

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
        container_mode: Route headless agent runs through codeagent container.
        timeout_seconds: Maximum time for advisor operations (default: 600s / 10 min).
    """

    model_config = ConfigDict(extra="forbid")

    provider: Annotated[
        AdvisorProvider,
        Field(description="AI provider to use"),
    ] = "claude"
    model: Annotated[
        str | None,
        Field(description="Model name (None = use default per provider)"),
    ] = None
    container_mode: Annotated[
        bool,
        Field(description="Route agent runs through dev-container (ai-dev)"),
    ] = True
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
    """Base exception for advisor configuration errors."""


class AdvisorConfigNotFoundError(AdvisorConfigError):
    """Raised when advisor config file is not found."""


class AdvisorConfigParseError(AdvisorConfigError):
    """Raised when advisor config file cannot be parsed."""


def load_advisor_config(path: Path | None = None) -> AdvisorConfig:
    """Load advisor configuration from a TOML file.

    Args:
        path: Path to the config file. If None, uses default advisor config path.

    Returns:
        Validated AdvisorConfig object.

    Raises:
        AdvisorConfigNotFoundError: If the config file doesn't exist.
        AdvisorConfigParseError: If the TOML syntax is invalid.
        AdvisorConfigError: If the content doesn't match the schema.
    """
    config_path = path or get_advisor_config_path()

    if not config_path.exists():
        raise AdvisorConfigNotFoundError(f"Advisor config not found: {config_path}")

    try:
        with open(config_path, "rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise AdvisorConfigParseError(f"Invalid TOML syntax: {e}") from e
    except OSError as e:
        raise AdvisorConfigError(f"Failed to read advisor config: {e}") from e

    # Migration: dev_script → container_mode
    if "dev_script" in data:
        import logging

        logger = logging.getLogger(__name__)
        logger.warning(
            "Deprecated 'dev_script' in advisor.toml. "
            "Migrating to 'container_mode = true'. "
            "Remove 'dev_script' from %s",
            config_path,
        )
        data.pop("dev_script")
        data["container_mode"] = True

    try:
        return AdvisorConfig.model_validate(data)
    except (ValueError, ValidationError) as e:
        raise AdvisorConfigError(f"Invalid advisor config content: {e}") from e


def save_advisor_config(config: AdvisorConfig, path: Path | None = None) -> Path:
    """Save advisor configuration to a TOML file.

    The file is written atomically by first writing to a temporary file
    and then using os.replace() for atomic rename.

    Args:
        config: The AdvisorConfig object to save.
        path: Path to save the config. If None, uses default advisor config path.

    Returns:
        Path where the config was saved.

    Raises:
        AdvisorConfigError: If the file cannot be written.
    """
    import os
    from tempfile import NamedTemporaryFile

    config_path = path or get_advisor_config_path()

    # Ensure parent directory exists
    config_path.parent.mkdir(parents=True, exist_ok=True)

    # Convert config to dictionary for TOML serialization
    data = _config_to_dict(config)

    tmp_path: Path | None = None
    try:
        # Write atomically using a temporary file in the same directory
        with NamedTemporaryFile(
            mode="wb",
            dir=config_path.parent,
            delete=False,
            suffix=".tmp",
        ) as f:
            tmp_path = Path(f.name)
            tomli_w.dump(data, f)
        # os.replace() is atomic on POSIX
        os.replace(str(tmp_path), str(config_path))
    except OSError as e:
        # Cleanup temp file on failure
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink()
        raise AdvisorConfigError(f"Failed to write advisor config: {e}") from e

    return config_path


def _config_to_dict(config: AdvisorConfig) -> dict[str, object]:
    """Convert AdvisorConfig to a dictionary for TOML serialization.

    Only includes non-None values to keep the file clean.

    Args:
        config: The AdvisorConfig to convert.

    Returns:
        Dictionary ready for TOML serialization.
    """
    result: dict[str, object] = {"provider": config.provider}

    if config.model is not None:
        result["model"] = config.model

    if config.container_mode:
        result["container_mode"] = config.container_mode

    if config.timeout_seconds != 600:  # Only include if non-default
        result["timeout_seconds"] = config.timeout_seconds

    return result


def is_running_in_container() -> bool:
    """Check if popctl is running inside a Docker container.

    Detects container environment by checking for the presence of
    /.dockerenv file, which is created by Docker.

    Returns:
        True if running in a container, False otherwise.

    Note:
        This detection method works for Docker containers. Other container
        runtimes (Podman, LXC) may use different indicators.
    """
    return Path("/.dockerenv").exists()


def get_default_config() -> AdvisorConfig:
    """Create a default AdvisorConfig.

    Returns:
        AdvisorConfig with default settings.
    """
    return AdvisorConfig()
