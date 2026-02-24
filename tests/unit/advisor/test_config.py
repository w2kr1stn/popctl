"""Unit tests for AdvisorConfig and related functions.

Tests for the advisor configuration module that provides Pydantic models
and I/O functions for AI-assisted package classification settings.
"""

from pathlib import Path

import pytest
from popctl.advisor.config import (
    DEFAULT_MODELS,
    AdvisorConfig,
    AdvisorConfigError,
    load_advisor_config,
    save_advisor_config,
)
from pydantic import ValidationError


class TestAdvisorConfig:
    """Tests for AdvisorConfig Pydantic model."""

    def test_default_values(self) -> None:
        """AdvisorConfig has correct default values."""
        config = AdvisorConfig()

        assert config.provider == "claude"
        assert config.model is None
        assert config.timeout_seconds == 600

    def test_custom_values(self) -> None:
        """AdvisorConfig accepts custom values."""
        config = AdvisorConfig(
            provider="gemini",
            model="gemini-2.5-flash",
            timeout_seconds=300,
        )

        assert config.provider == "gemini"
        assert config.model == "gemini-2.5-flash"
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

    def test_default_dev_container_path_is_none(self) -> None:
        """dev_container_path defaults to None."""
        config = AdvisorConfig()
        assert config.dev_container_path is None

    def test_dev_container_path_accepts_path(self) -> None:
        """dev_container_path accepts a Path value."""
        config = AdvisorConfig(dev_container_path=Path("/home/user/djinn"))
        assert config.dev_container_path == Path("/home/user/djinn")

    def test_container_mode_false_by_default(self) -> None:
        """container_mode is False when dev_container_path is None."""
        config = AdvisorConfig()
        assert config.container_mode is False

    def test_container_mode_true_when_path_set(self) -> None:
        """container_mode is True when dev_container_path is set."""
        config = AdvisorConfig(dev_container_path=Path("/some/path"))
        assert config.container_mode is True

    def test_extra_fields_ignored(self) -> None:
        """AdvisorConfig ignores extra fields for backward compat."""
        config = AdvisorConfig.model_validate(
            {"provider": "claude", "container_mode": True, "unknown_field": "value"}
        )

        assert config.provider == "claude"


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

        with pytest.raises(AdvisorConfigError):
            load_advisor_config(config_file)

    def test_load_invalid_toml(self, tmp_path: Path) -> None:
        """load_advisor_config raises error for invalid TOML syntax."""
        config_file = tmp_path / "invalid.toml"
        config_file.write_text("this is not valid toml [[[")

        with pytest.raises(AdvisorConfigError):
            load_advisor_config(config_file)

    def test_load_invalid_content(self, tmp_path: Path) -> None:
        """load_advisor_config raises error for invalid content."""
        config_file = tmp_path / "bad_content.toml"
        config_file.write_text('provider = "invalid_provider"\n')

        with pytest.raises(AdvisorConfigError):
            load_advisor_config(config_file)

    def test_load_ignores_unknown_fields(self, tmp_path: Path) -> None:
        """load_advisor_config ignores unknown fields (backward compat)."""
        config_file = tmp_path / "advisor.toml"
        config_file.write_text("""
provider = "claude"
container_mode = true
dev_script = "/old/path"
""")

        config = load_advisor_config(config_file)

        assert config.provider == "claude"


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
        """save_advisor_config only writes non-None values."""
        config_file = tmp_path / "advisor.toml"
        config = AdvisorConfig()  # All defaults

        save_advisor_config(config, config_file)

        content = config_file.read_text()
        assert "provider" in content
        assert "timeout_seconds" in content

    def test_save_excludes_none_dev_container_path(self, tmp_path: Path) -> None:
        """save_advisor_config omits dev_container_path when None."""
        config_file = tmp_path / "advisor.toml"
        config = AdvisorConfig()  # dev_container_path=None

        save_advisor_config(config, config_file)

        content = config_file.read_text()
        assert "dev_container_path" not in content

    def test_save_roundtrip(self, tmp_path: Path) -> None:
        """save + load roundtrip preserves config values."""
        config_file = tmp_path / "advisor.toml"
        original = AdvisorConfig(provider="gemini", model="flash", timeout_seconds=120)

        save_advisor_config(original, config_file)
        loaded = load_advisor_config(config_file)

        assert loaded.provider == original.provider
        assert loaded.model == original.model
        assert loaded.timeout_seconds == original.timeout_seconds

    def test_save_roundtrip_with_dev_container_path(self, tmp_path: Path) -> None:
        """save + load roundtrip preserves dev_container_path."""
        config_file = tmp_path / "advisor.toml"
        original = AdvisorConfig(dev_container_path=Path("/home/user/djinn"))

        save_advisor_config(original, config_file)
        loaded = load_advisor_config(config_file)

        assert loaded.dev_container_path == original.dev_container_path
        assert loaded.container_mode is True
