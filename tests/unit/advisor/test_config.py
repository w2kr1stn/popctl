"""Unit tests for AdvisorConfig and related functions.

Tests for the advisor configuration module that provides Pydantic models
and I/O functions for AI-assisted package classification settings.
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from popctl.advisor.config import (
    DEFAULT_MODELS,
    AdvisorConfig,
    AdvisorConfigError,
    AdvisorConfigNotFoundError,
    AdvisorConfigParseError,
    get_default_config,
    is_running_in_container,
    load_advisor_config,
    save_advisor_config,
)
from popctl.core.paths import ensure_exchange_dir, get_advisor_config_path, get_exchange_dir
from pydantic import ValidationError


class TestAdvisorConfig:
    """Tests for AdvisorConfig Pydantic model."""

    def test_default_values(self) -> None:
        """AdvisorConfig has correct default values."""
        config = AdvisorConfig()

        assert config.provider == "claude"
        assert config.model is None
        assert config.dev_script is None
        assert config.timeout_seconds == 600

    def test_custom_values(self) -> None:
        """AdvisorConfig accepts custom values."""
        config = AdvisorConfig(
            provider="gemini",
            model="gemini-2.5-flash",
            dev_script=Path("/opt/ai-dev/dev.sh"),
            timeout_seconds=300,
        )

        assert config.provider == "gemini"
        assert config.model == "gemini-2.5-flash"
        assert config.dev_script == Path("/opt/ai-dev/dev.sh")
        assert config.timeout_seconds == 300

    def test_timeout_minimum_validation(self) -> None:
        """AdvisorConfig validates minimum timeout."""
        with pytest.raises(ValidationError):
            AdvisorConfig(timeout_seconds=30)  # Below 60

    def test_timeout_maximum_validation(self) -> None:
        """AdvisorConfig validates maximum timeout."""
        with pytest.raises(ValidationError):
            AdvisorConfig(timeout_seconds=7200)  # Above 3600

    def test_invalid_provider(self) -> None:
        """AdvisorConfig rejects invalid provider."""
        with pytest.raises(ValidationError):
            AdvisorConfig(provider="invalid")  # type: ignore[arg-type]


class TestEffectiveModel:
    """Tests for the effective_model property."""

    def test_effective_model_with_explicit_model(self) -> None:
        """effective_model returns configured model when set."""
        config = AdvisorConfig(model="opus")

        assert config.effective_model == "opus"

    def test_effective_model_default_claude(self) -> None:
        """effective_model returns default for claude provider."""
        config = AdvisorConfig(provider="claude", model=None)

        assert config.effective_model == DEFAULT_MODELS["claude"]
        assert config.effective_model == "sonnet"

    def test_effective_model_default_gemini(self) -> None:
        """effective_model returns default for gemini provider."""
        config = AdvisorConfig(provider="gemini", model=None)

        assert config.effective_model == DEFAULT_MODELS["gemini"]
        assert config.effective_model == "gemini-2.5-pro"


class TestIsRunningInContainer:
    """Tests for is_running_in_container function."""

    def test_running_in_container(self, tmp_path: Path) -> None:
        """is_running_in_container returns True when /.dockerenv exists."""
        dockerenv = tmp_path / ".dockerenv"
        dockerenv.touch()

        with patch.object(Path, "exists", return_value=True):
            result = is_running_in_container()

        assert result is True

    def test_not_running_in_container(self) -> None:
        """is_running_in_container returns False when /.dockerenv doesn't exist."""
        with patch.object(Path, "exists", return_value=False):
            result = is_running_in_container()

        assert result is False


class TestLoadAdvisorConfig:
    """Tests for load_advisor_config function."""

    def test_load_valid_config(self, tmp_path: Path) -> None:
        """load_advisor_config loads valid TOML file."""
        config_file = tmp_path / "advisor.toml"
        config_file.write_text("""
provider = "gemini"
model = "gemini-2.5-flash"
timeout_seconds = 300
""")

        config = load_advisor_config(config_file)

        assert config.provider == "gemini"
        assert config.model == "gemini-2.5-flash"
        assert config.timeout_seconds == 300

    def test_load_minimal_config(self, tmp_path: Path) -> None:
        """load_advisor_config loads minimal config with defaults."""
        config_file = tmp_path / "advisor.toml"
        config_file.write_text('provider = "claude"\n')

        config = load_advisor_config(config_file)

        assert config.provider == "claude"
        assert config.model is None
        assert config.timeout_seconds == 600

    def test_load_nonexistent_file(self, tmp_path: Path) -> None:
        """load_advisor_config raises error for missing file."""
        config_file = tmp_path / "nonexistent.toml"

        with pytest.raises(AdvisorConfigNotFoundError):
            load_advisor_config(config_file)

    def test_load_invalid_toml(self, tmp_path: Path) -> None:
        """load_advisor_config raises error for invalid TOML syntax."""
        config_file = tmp_path / "invalid.toml"
        config_file.write_text("this is not valid toml [[[")

        with pytest.raises(AdvisorConfigParseError):
            load_advisor_config(config_file)

    def test_load_invalid_content(self, tmp_path: Path) -> None:
        """load_advisor_config raises error for invalid content."""
        config_file = tmp_path / "bad_content.toml"
        config_file.write_text('provider = "invalid_provider"\n')

        with pytest.raises(AdvisorConfigError):
            load_advisor_config(config_file)


class TestSaveAdvisorConfig:
    """Tests for save_advisor_config function."""

    def test_save_config(self, tmp_path: Path) -> None:
        """save_advisor_config writes valid TOML file."""
        config_file = tmp_path / "advisor.toml"
        config = AdvisorConfig(provider="gemini", model="gemini-2.5-pro")

        result = save_advisor_config(config, config_file)

        assert result == config_file
        assert config_file.exists()

        # Verify content can be read back
        loaded = load_advisor_config(config_file)
        assert loaded.provider == "gemini"
        assert loaded.model == "gemini-2.5-pro"

    def test_save_creates_directory(self, tmp_path: Path) -> None:
        """save_advisor_config creates parent directory if needed."""
        config_file = tmp_path / "subdir" / "advisor.toml"
        config = AdvisorConfig()

        save_advisor_config(config, config_file)

        assert config_file.exists()
        assert config_file.parent.exists()

    def test_save_minimal_config(self, tmp_path: Path) -> None:
        """save_advisor_config only writes non-default values."""
        config_file = tmp_path / "advisor.toml"
        config = AdvisorConfig()  # All defaults

        save_advisor_config(config, config_file)

        content = config_file.read_text()
        # Should only have provider (required field)
        assert "provider" in content
        # Should NOT have default timeout
        assert "timeout_seconds" not in content

    def test_save_with_dev_script(self, tmp_path: Path) -> None:
        """save_advisor_config saves dev_script path as string."""
        config_file = tmp_path / "advisor.toml"
        config = AdvisorConfig(dev_script=Path("/opt/ai-dev/dev.sh"))

        save_advisor_config(config, config_file)

        content = config_file.read_text()
        assert "dev_script" in content
        assert "/opt/ai-dev/dev.sh" in content


class TestGetDefaultConfig:
    """Tests for get_default_config function."""

    def test_returns_default_config(self) -> None:
        """get_default_config returns config with default values."""
        config = get_default_config()

        assert isinstance(config, AdvisorConfig)
        assert config.provider == "claude"
        assert config.model is None
        assert config.timeout_seconds == 600


class TestExchangeDirPaths:
    """Tests for exchange directory path functions."""

    def test_get_exchange_dir(self) -> None:
        """get_exchange_dir returns correct path."""
        result = get_exchange_dir()

        assert result == Path("/tmp/popctl-exchange")

    def test_ensure_exchange_dir_creates_directory(self, tmp_path: Path) -> None:
        """ensure_exchange_dir creates the directory."""
        # Patch the EXCHANGE_DIR constant
        test_dir = tmp_path / "popctl-exchange"

        with (
            patch("popctl.core.paths.EXCHANGE_DIR", test_dir),
            patch("popctl.core.paths.get_exchange_dir", return_value=test_dir),
        ):
            result = ensure_exchange_dir()

        assert result == test_dir
        assert test_dir.exists()
        assert test_dir.is_dir()

    def test_ensure_exchange_dir_idempotent(self, tmp_path: Path) -> None:
        """ensure_exchange_dir is idempotent."""
        test_dir = tmp_path / "popctl-exchange"

        with (
            patch("popctl.core.paths.EXCHANGE_DIR", test_dir),
            patch("popctl.core.paths.get_exchange_dir", return_value=test_dir),
        ):
            result1 = ensure_exchange_dir()
            result2 = ensure_exchange_dir()

        assert result1 == result2
        assert test_dir.exists()


class TestAdvisorConfigPath:
    """Tests for get_advisor_config_path function."""

    def test_default_advisor_config_path(self) -> None:
        """get_advisor_config_path returns correct default path."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("XDG_CONFIG_HOME", None)

            result = get_advisor_config_path()

        assert result == Path.home() / ".config" / "popctl" / "advisor.toml"

    def test_respects_xdg_config_home(self, tmp_path: Path) -> None:
        """get_advisor_config_path respects XDG_CONFIG_HOME."""
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(tmp_path)}):
            result = get_advisor_config_path()

        assert result == tmp_path / "popctl" / "advisor.toml"
