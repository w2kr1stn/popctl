import logging
import os
import tomllib
from contextlib import suppress
from enum import Enum
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

import tomli_w
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from popctl.core.paths import get_config_dir

logger = logging.getLogger(__name__)

# Provider type alias
AdvisorProvider = Literal["claude", "gemini", "codex"]


class ProviderChoice(str, Enum):

    CLAUDE = "claude"
    GEMINI = "gemini"
    CODEX = "codex"

# Default models per provider
DEFAULT_MODELS: dict[AdvisorProvider, str] = {
    "claude": "sonnet",
    "gemini": "gemini-2.5-pro",
    "codex": "gpt-5.6-terra",
}


class AdvisorConfig(BaseModel):

    model_config = ConfigDict(extra="ignore")

    provider: Annotated[
        AdvisorProvider,
        Field(description="AI provider to use"),
    ] = "claude"
    model: Annotated[
        str | None,
        Field(description="Model name (None = use default per provider)"),
    ] = None
    api_key: Annotated[
        str,
        Field(description="Optional API key; a logged-in CLI works without it"),
    ] = ""
    timeout_seconds: Annotated[
        int,
        Field(ge=60, le=3600, description="Timeout in seconds (60-3600)"),
    ] = 600

    @property
    def effective_model(self) -> str:
        if self.model:
            return self.model
        return DEFAULT_MODELS[self.provider]


class AdvisorConfigError(Exception):
    pass


def _validation_error_fields(error: ValidationError) -> str:
    fields = dict.fromkeys(
        ".".join(str(part) for part in item["loc"])
        for item in error.errors(include_url=False)
        if item["loc"]
    )
    return ", ".join(fields) or "unknown field"


def load_advisor_config(path: Path | None = None) -> AdvisorConfig:
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
    except ValidationError as e:
        fields = _validation_error_fields(e)
        raise AdvisorConfigError(f"advisor.toml is invalid: {fields}") from None
    except ValueError:
        raise AdvisorConfigError("advisor.toml is invalid") from None


def save_advisor_config(config: AdvisorConfig, path: Path | None = None) -> Path:
    config_path = path or get_config_dir() / "advisor.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None

    try:
        if config_path.exists():
            config_path.chmod(0o600)

        temporary_path = config_path.with_name(f".{config_path.name}.{uuid4().hex}.tmp")
        file_descriptor = os.open(
            temporary_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(file_descriptor, "wb") as f:
            tomli_w.dump(config.model_dump(mode="json", exclude_none=True), f)
            f.flush()
            os.fsync(f.fileno())
        temporary_path.chmod(0o600)
        os.replace(temporary_path, config_path)
    except OSError as e:
        if temporary_path is not None:
            with suppress(OSError):
                temporary_path.unlink(missing_ok=True)
        raise AdvisorConfigError(f"Failed to write advisor config: {e}") from e

    return config_path


def load_or_create_config(
    provider: str | None = None,
    model: str | None = None,
) -> AdvisorConfig:
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
