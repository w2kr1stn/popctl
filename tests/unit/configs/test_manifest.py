"""Tests for config manifest Pydantic models."""

import pytest
from popctl.configs.manifest import ConfigEntry, ConfigsConfig
from pydantic import ValidationError


class TestConfigEntry:
    """Tests for ConfigEntry model."""

    def test_config_entry_defaults(self) -> None:
        """Default entry has reason=None and category=None."""
        entry = ConfigEntry()
        assert entry.reason is None
        assert entry.category is None

    def test_config_entry_with_values(self) -> None:
        """Entry with explicit reason and category."""
        entry = ConfigEntry(reason="VS Code settings", category="editor")
        assert entry.reason == "VS Code settings"
        assert entry.category == "editor"

    def test_config_entry_partial_values(self) -> None:
        """Entry with only reason set."""
        entry = ConfigEntry(reason="Active desktop config")
        assert entry.reason == "Active desktop config"
        assert entry.category is None

    def test_config_entry_forbids_extra(self) -> None:
        """Extra fields should raise ValidationError."""
        with pytest.raises(ValidationError):
            ConfigEntry(reason="test", unknown_field="bad")  # type: ignore[call-arg]


class TestConfigsConfig:
    """Tests for ConfigsConfig model."""

    def test_configs_config_empty(self) -> None:
        """Default config has empty keep and remove dicts."""
        config = ConfigsConfig()
        assert config.keep == {}
        assert config.remove == {}

    def test_configs_config_with_entries(self) -> None:
        """Config with keep and remove entries."""
        config = ConfigsConfig(
            keep={
                "~/.config/Code": ConfigEntry(reason="VS Code settings", category="editor"),
            },
            remove={
                "~/.config/vlc": ConfigEntry(reason="VLC not installed", category="obsolete"),
            },
        )
        assert "~/.config/Code" in config.keep
        assert config.keep["~/.config/Code"].reason == "VS Code settings"
        assert config.keep["~/.config/Code"].category == "editor"
        assert "~/.config/vlc" in config.remove
        assert config.remove["~/.config/vlc"].category == "obsolete"

    def test_configs_config_no_duplicates_validator(self) -> None:
        """Same path in both keep and remove raises ValueError."""
        with pytest.raises(ValidationError, match="Paths cannot be in both keep and remove"):
            ConfigsConfig(
                keep={"~/.config/vlc": ConfigEntry(reason="keep it")},
                remove={"~/.config/vlc": ConfigEntry(reason="remove it")},
            )

    def test_configs_config_forbids_extra(self) -> None:
        """Extra fields on config should raise ValidationError."""
        with pytest.raises(ValidationError):
            ConfigsConfig(unknown="bad")  # type: ignore[call-arg]

    def test_configs_config_multiple_entries(self) -> None:
        """Config with multiple keep and remove entries."""
        config = ConfigsConfig(
            keep={
                "~/.config/Code": ConfigEntry(reason="Active"),
                "~/.config/nvim": ConfigEntry(reason="User config"),
            },
            remove={
                "~/.config/vlc": ConfigEntry(reason="Uninstalled"),
                "~/.config/sublime-text": ConfigEntry(reason="Switched editor"),
            },
        )
        assert len(config.keep) == 2
        assert len(config.remove) == 2

    def test_configs_config_disjoint_keys_pass(self) -> None:
        """Different keys in keep and remove should pass validation."""
        config = ConfigsConfig(
            keep={"~/.config/a": ConfigEntry()},
            remove={"~/.config/b": ConfigEntry()},
        )
        assert len(config.keep) == 1
        assert len(config.remove) == 1
