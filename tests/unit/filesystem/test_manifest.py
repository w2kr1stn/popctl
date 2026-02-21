"""Tests for filesystem manifest Pydantic models."""

import pytest
from popctl.domain.manifest import DomainConfig, DomainEntry
from pydantic import ValidationError


class TestDomainEntry:
    """Tests for DomainEntry model."""

    def test_filesystem_entry_defaults(self) -> None:
        """Default entry has reason=None and category=None."""
        entry = DomainEntry()
        assert entry.reason is None
        assert entry.category is None

    def test_filesystem_entry_with_values(self) -> None:
        """Entry with explicit reason and category."""
        entry = DomainEntry(reason="VLC uninstalled", category="obsolete")
        assert entry.reason == "VLC uninstalled"
        assert entry.category == "obsolete"

    def test_filesystem_entry_forbids_extra(self) -> None:
        """Extra fields should raise ValidationError."""
        with pytest.raises(ValidationError):
            DomainEntry(reason="test", unknown_field="bad")  # type: ignore[call-arg]

    def test_filesystem_entry_partial_values(self) -> None:
        """Entry with only reason set."""
        entry = DomainEntry(reason="Active desktop config")
        assert entry.reason == "Active desktop config"
        assert entry.category is None


class TestDomainConfig:
    """Tests for DomainConfig model."""

    def test_filesystem_config_empty(self) -> None:
        """Default config has empty keep and remove dicts."""
        config = DomainConfig()
        assert config.keep == {}
        assert config.remove == {}

    def test_filesystem_config_with_entries(self) -> None:
        """Config with keep and remove entries."""
        config = DomainConfig(
            keep={
                "~/.config/nvim": DomainEntry(reason="Neovim config"),
            },
            remove={
                "~/.config/vlc": DomainEntry(reason="VLC uninstalled", category="obsolete"),
            },
        )
        assert "~/.config/nvim" in config.keep
        assert config.keep["~/.config/nvim"].reason == "Neovim config"
        assert "~/.config/vlc" in config.remove
        assert config.remove["~/.config/vlc"].category == "obsolete"

    def test_filesystem_config_no_duplicates_validator(self) -> None:
        """Same path in both keep and remove raises ValueError."""
        with pytest.raises(ValidationError, match="Paths cannot be in both keep and remove"):
            DomainConfig(
                keep={"~/.config/vlc": DomainEntry(reason="keep it")},
                remove={"~/.config/vlc": DomainEntry(reason="remove it")},
            )

    def test_filesystem_config_forbids_extra(self) -> None:
        """Extra fields on config should raise ValidationError."""
        with pytest.raises(ValidationError):
            DomainConfig(unknown="bad")  # type: ignore[call-arg]

    def test_filesystem_config_multiple_entries(self) -> None:
        """Config with multiple keep and remove entries."""
        config = DomainConfig(
            keep={
                "~/.config/nvim": DomainEntry(reason="Active"),
                "~/.config/cosmic": DomainEntry(reason="Desktop"),
            },
            remove={
                "~/.cache/mozilla": DomainEntry(reason="Stale cache"),
                "~/.config/vlc": DomainEntry(reason="Uninstalled"),
            },
        )
        assert len(config.keep) == 2
        assert len(config.remove) == 2

    def test_filesystem_config_disjoint_keys_pass(self) -> None:
        """Different keys in keep and remove should pass validation."""
        config = DomainConfig(
            keep={"~/.config/a": DomainEntry()},
            remove={"~/.config/b": DomainEntry()},
        )
        assert len(config.keep) == 1
        assert len(config.remove) == 1
