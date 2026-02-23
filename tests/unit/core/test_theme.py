"""Unit tests for theme module.

Tests for theme loading, validation, and Rich theme generation.
"""

# pyright: reportPrivateUsage=false

from pathlib import Path
from unittest.mock import patch

import popctl.core.theme as theme_module
import pytest
from popctl.core.theme import (
    ThemeColors,
    _load_toml_colors,
    get_rich_theme,
    get_theme,
    get_user_theme_path,
    load_theme,
    reload_theme,
)
from rich.theme import Theme


class TestThemeColors:
    """Tests for ThemeColors Pydantic model."""

    def test_default_values(self) -> None:
        """ThemeColors has sensible defaults."""
        colors = ThemeColors()
        assert colors.text == "#ffffff"
        assert colors.header == "#69B9A1"
        assert colors.success == "#03b971"
        assert colors.error == "#f53263"

    def test_valid_hex_colors(self) -> None:
        """ThemeColors accepts valid hex color codes."""
        colors = ThemeColors(
            text="#AABBCC",
            muted="#abc",
            header="#123456",
        )
        assert colors.text == "#AABBCC"
        assert colors.muted == "#abc"
        assert colors.header == "#123456"

    def test_invalid_hex_no_hash(self) -> None:
        """ThemeColors rejects colors without # prefix."""
        with pytest.raises(ValueError, match="must start with '#'"):
            ThemeColors(text="ffffff")

    def test_invalid_hex_wrong_length(self) -> None:
        """ThemeColors rejects colors with wrong length."""
        with pytest.raises(ValueError, match="must be #RGB or #RRGGBB"):
            ThemeColors(text="#ff")

        with pytest.raises(ValueError, match="must be #RGB or #RRGGBB"):
            ThemeColors(text="#fffffff")

    def test_invalid_hex_chars(self) -> None:
        """ThemeColors rejects invalid hex characters."""
        with pytest.raises(ValueError, match="invalid hex color"):
            ThemeColors(text="#gggggg")

    def test_extra_fields_forbidden(self) -> None:
        """ThemeColors rejects unknown fields."""
        with pytest.raises(ValueError):
            ThemeColors(unknown_field="#ffffff")  # type: ignore[call-arg]


class TestLoadTomlColors:
    """Tests for _load_toml_colors internal function."""

    def test_loads_valid_toml(self, tmp_path: Path) -> None:
        """Loads colors from valid TOML file."""
        theme_file = tmp_path / "theme.toml"
        theme_file.write_text('[colors]\ntext = "#000000"\nheader = "#aabbcc"\n')

        result = _load_toml_colors(theme_file)

        assert result is not None
        assert result["text"] == "#000000"
        assert result["header"] == "#aabbcc"

    def test_returns_none_for_missing_file(self, tmp_path: Path) -> None:
        """Returns None when file doesn't exist."""
        result = _load_toml_colors(tmp_path / "nonexistent.toml")
        assert result is None

    def test_returns_none_for_invalid_toml(self, tmp_path: Path) -> None:
        """Returns None for malformed TOML."""
        theme_file = tmp_path / "theme.toml"
        theme_file.write_text("not valid [ toml syntax")

        result = _load_toml_colors(theme_file)
        assert result is None

    def test_returns_empty_dict_for_missing_colors_section(self, tmp_path: Path) -> None:
        """Returns empty dict when colors section is missing."""
        theme_file = tmp_path / "theme.toml"
        theme_file.write_text('[other]\nkey = "value"\n')

        result = _load_toml_colors(theme_file)
        assert result == {}


class TestLoadTheme:
    """Tests for load_theme function."""

    def test_loads_bundled_theme(self) -> None:
        """Loads theme from bundled data file."""
        colors = load_theme()

        # Should have values from the bundled theme.toml
        assert colors.text == "#ffffff"
        assert colors.header == "#69B9A1"
        assert colors.package_manual == "#69B9A1"

    def test_user_theme_overrides_bundled(self, tmp_path: Path) -> None:
        """User theme overrides bundled theme values."""
        user_theme = tmp_path / "theme.toml"
        user_theme.write_text('[colors]\nheader = "#ff0000"\n')

        with patch(
            "popctl.core.theme.get_user_theme_path",
            return_value=user_theme,
        ):
            colors = load_theme()

        # Overridden value
        assert colors.header == "#ff0000"
        # Non-overridden values come from bundled theme
        assert colors.text == "#ffffff"
        assert colors.success == "#03b971"

    def test_graceful_fallback_on_invalid_user_theme(self, tmp_path: Path) -> None:
        """Falls back to bundled theme when user theme is invalid."""
        user_theme = tmp_path / "theme.toml"
        user_theme.write_text("invalid toml [[[")

        with patch(
            "popctl.core.theme.get_user_theme_path",
            return_value=user_theme,
        ):
            colors = load_theme()

        # Should fall back to bundled theme
        assert colors.text == "#ffffff"
        assert colors.header == "#69B9A1"

    def test_handles_partial_user_overrides(self, tmp_path: Path) -> None:
        """User can override only some colors."""
        user_theme = tmp_path / "theme.toml"
        user_theme.write_text('[colors]\nsuccess = "#00ff00"\nwarning = "#ffff00"\n')

        with patch(
            "popctl.core.theme.get_user_theme_path",
            return_value=user_theme,
        ):
            colors = load_theme()

        assert colors.success == "#00ff00"
        assert colors.warning == "#ffff00"
        # Other values from bundled theme
        assert colors.header == "#69B9A1"


class TestGetRichTheme:
    """Tests for get_rich_theme function."""

    def test_returns_rich_theme(self) -> None:
        """Returns a Rich Theme instance."""
        theme = get_rich_theme()
        assert isinstance(theme, Theme)

    def test_includes_base_styles(self) -> None:
        """Theme includes all base color styles."""
        colors = ThemeColors()
        theme = get_rich_theme(colors)

        # Check direct color mappings exist
        assert "text" in theme.styles
        assert "muted" in theme.styles
        assert "header" in theme.styles
        assert "success" in theme.styles
        assert "error" in theme.styles

    def test_includes_convenience_styles(self) -> None:
        """Theme includes convenience styles."""
        theme = get_rich_theme()

        assert "bold_header" in theme.styles
        assert "dim" in theme.styles
        assert "package.name" in theme.styles
        assert "package.version" in theme.styles
        assert "package.size" in theme.styles

    def test_includes_legacy_compatibility_styles(self) -> None:
        """Theme includes legacy package status styles."""
        theme = get_rich_theme()

        assert "package.manual" in theme.styles
        assert "package.auto" in theme.styles

    def test_uses_provided_colors(self) -> None:
        """Uses provided ThemeColors instance."""
        colors = ThemeColors(header="#123456")
        theme = get_rich_theme(colors)

        # The theme should use the custom color
        assert theme.styles["header"]._color is not None


class TestGetTheme:
    """Tests for get_theme caching function."""

    def test_returns_theme(self) -> None:
        """get_theme returns a Theme instance."""
        theme = get_theme()
        assert isinstance(theme, Theme)

    def test_caches_theme(self) -> None:
        """get_theme returns cached instance on subsequent calls."""
        # Reset cache

        theme_module._cached_theme = None

        theme1 = get_theme()
        theme2 = get_theme()

        assert theme1 is theme2

    def test_reload_creates_new_theme(self) -> None:
        """reload_theme creates a new Theme instance."""

        theme_module._cached_theme = None

        original = get_theme()
        reloaded = reload_theme()

        # New instance created
        assert reloaded is not original
        # But get_theme now returns the new one
        assert get_theme() is reloaded


class TestGetUserThemePath:
    """Tests for get_user_theme_path function."""

    def test_returns_xdg_config_path(self) -> None:
        """Returns path under ~/.config/popctl/."""
        path = get_user_theme_path()

        assert path.parts[-1] == "theme.toml"
        assert path.parts[-2] == "popctl"
        assert ".config" in path.parts
