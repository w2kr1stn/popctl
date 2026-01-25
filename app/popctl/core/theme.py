"""Theme management for popctl CLI.

Provides color theming via TOML configuration files with user override support.
"""

import logging
import tomllib
from importlib import resources
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, field_validator
from rich.theme import Theme

logger = logging.getLogger(__name__)


class ThemeColors(BaseModel):
    """Color configuration for popctl CLI.

    All colors must be valid hex codes (#RRGGBB or #RGB).
    """

    model_config = ConfigDict(extra="forbid")

    # Base colors
    text: str = "#ffffff"
    muted: str = "#b2bec3"
    header: str = "#69B9A1"
    border: str = "#29526d"

    # Semantic colors
    success: str = "#03b971"
    warning: str = "#f5b332"
    error: str = "#f53263"
    info: str = "#0ec1c8"

    # Diff/Action colors
    added: str = "#c1ff62"
    removed: str = "#f53263"
    changed: str = "#0e8ac8"

    # Confidence levels
    confidence_high: str = "#03b971"
    confidence_medium: str = "#faf870"
    confidence_low: str = "#d44ebc"

    # Package status
    package_manual: str = "#69B9A1"
    package_auto: str = "#226666"

    @field_validator("*", mode="before")
    @classmethod
    def validate_hex_color(cls, v: object, info: Any) -> str:
        """Validate that all color values are valid hex codes."""
        if not isinstance(v, str):
            msg = f"{info.field_name}: color must be a string"
            raise ValueError(msg)
        color = v.strip()
        if not color.startswith("#"):
            msg = f"{info.field_name}: color must start with '#'"
            raise ValueError(msg)
        color_part = color[1:]
        if len(color_part) not in (3, 6):
            msg = f"{info.field_name}: color must be #RGB or #RRGGBB format"
            raise ValueError(msg)
        try:
            int(color_part, 16)
        except ValueError:
            msg = f"{info.field_name}: invalid hex color '{color}'"
            raise ValueError(msg) from None
        return color


def get_user_theme_path() -> Path:
    """Get the user theme configuration path.

    Returns:
        Path to ~/.config/popctl/theme.toml
    """
    return Path.home() / ".config" / "popctl" / "theme.toml"


def get_bundled_theme_path() -> Path:
    """Get the bundled default theme path.

    Returns:
        Path to the bundled data/theme.toml
    """
    return resources.files("popctl.data").joinpath("theme.toml")  # type: ignore[return-value]


def _load_toml_colors(path: Path) -> dict[str, str] | None:
    """Load colors section from a TOML file.

    Args:
        path: Path to the TOML file.

    Returns:
        Dictionary of color name to hex value, or None if loading failed.
    """
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
        colors_raw: object = data.get("colors", {})
        if not isinstance(colors_raw, dict):
            logger.warning("Invalid 'colors' section in %s", path)
            return None
        # TOML dictionaries have string keys, extract string values only
        result: dict[str, str] = {}
        for key, value in cast(dict[str, object], colors_raw).items():
            if isinstance(value, str):
                result[key] = value
        return result
    except FileNotFoundError:
        return None
    except tomllib.TOMLDecodeError as e:
        logger.warning("Failed to parse TOML file %s: %s", path, e)
        import sys

        print(f"Warning: Failed to parse {path}: {e}", file=sys.stderr)
        return None
    except OSError as e:
        logger.warning("Failed to read theme file %s: %s", path, e)
        return None


def load_theme() -> ThemeColors:
    """Load theme colors with user override support.

    Priority:
    1. User theme (~/.config/popctl/theme.toml) - partial or full override
    2. Bundled default theme (data/theme.toml)

    Returns:
        ThemeColors instance with merged configuration.
    """
    # Start with bundled defaults
    bundled_path = get_bundled_theme_path()
    bundled_colors = _load_toml_colors(Path(bundled_path))

    if bundled_colors is None:
        logger.error("Failed to load bundled theme - installation may be corrupted")
        import sys

        print(
            "Error: Could not load default theme. Installation may be corrupted.",
            file=sys.stderr,
        )
        bundled_colors = {}

    # Try to load user overrides
    user_path = get_user_theme_path()
    user_colors = _load_toml_colors(user_path)

    if user_colors is not None:
        logger.debug("Loaded user theme overrides from %s", user_path)
        # Merge: user colors override bundled colors
        merged_colors = {**bundled_colors, **user_colors}
    else:
        merged_colors = bundled_colors

    # Create and validate ThemeColors
    try:
        return ThemeColors(**merged_colors)
    except ValueError as e:
        logger.warning("Theme validation failed, using defaults: %s", e)
        # Also print to stderr so user sees it without debug logging
        import sys

        print(f"Warning: Invalid theme configuration: {e}", file=sys.stderr)
        return ThemeColors()


def get_rich_theme(colors: ThemeColors | None = None) -> Theme:
    """Convert ThemeColors to a Rich Theme.

    Creates style mappings for Rich console output including:
    - All base color names directly as styles
    - Convenience styles for common patterns

    Args:
        colors: ThemeColors instance to convert. If None, loads theme automatically.

    Returns:
        Rich Theme instance configured with the color scheme.
    """
    if colors is None:
        colors = load_theme()

    # Build style dictionary
    styles: dict[str, str] = {
        # Direct color mappings
        "text": colors.text,
        "muted": colors.muted,
        "header": colors.header,
        "border": colors.border,
        "success": colors.success,
        "warning": colors.warning,
        "error": f"bold {colors.error}",
        "info": colors.info,
        "added": colors.added,
        "removed": colors.removed,
        "changed": colors.changed,
        "confidence_high": colors.confidence_high,
        "confidence_medium": colors.confidence_medium,
        "confidence_low": colors.confidence_low,
        "package_manual": f"bold {colors.package_manual}",
        "package_auto": colors.package_auto,
        # Convenience styles
        "bold_header": f"bold {colors.header}",
        "dim": colors.muted,
        "package.name": f"bold {colors.text}",
        "package.version": colors.muted,
        "package.size": colors.info,
        # Legacy compatibility styles
        "package.manual": f"bold {colors.package_manual}",
        "package.auto": colors.package_auto,
    }

    return Theme(styles)


# Module-level cached theme instance
_cached_theme: Theme | None = None


def get_theme() -> Theme:
    """Get the Rich theme, loading and caching it if necessary.

    This is the primary entry point for getting the theme.
    The theme is loaded once and cached for performance.

    Returns:
        Cached Rich Theme instance.
    """
    global _cached_theme
    if _cached_theme is None:
        _cached_theme = get_rich_theme()
    return _cached_theme


def reload_theme() -> Theme:
    """Force reload the theme from configuration files.

    Useful when the configuration has changed at runtime.

    Returns:
        Newly loaded Rich Theme instance.
    """
    global _cached_theme
    _cached_theme = get_rich_theme()
    return _cached_theme
