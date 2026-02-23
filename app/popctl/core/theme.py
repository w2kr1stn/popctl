"""Theme management for popctl CLI.

Provides color theming via TOML configuration files with user override support.
"""

import functools
import logging
import sys
import tomllib
from importlib import resources
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, field_validator
from rich.theme import Theme

from popctl.core.paths import get_config_dir

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
        # Use raw stderr — print_warning depends on formatting.py which imports this module
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
    bundled_path = resources.files("popctl.data").joinpath("theme.toml")
    bundled_colors = _load_toml_colors(Path(bundled_path))  # type: ignore[arg-type]

    if bundled_colors is None:
        logger.error("Failed to load bundled theme - installation may be corrupted")
        # Use raw stderr — print_warning depends on formatting.py which imports this module
        print(
            "Error: Could not load default theme. Installation may be corrupted.",
            file=sys.stderr,
        )
        bundled_colors = {}

    # Try to load user overrides
    user_path = get_config_dir() / "theme.toml"
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
        # Use raw stderr — print_warning depends on formatting.py which imports this module
        print(f"Warning: Invalid theme configuration: {e}", file=sys.stderr)
        return ThemeColors()


@functools.cache
def get_theme() -> Theme:
    """Get the Rich theme, loading and caching it if necessary.

    This is the primary entry point for getting the theme.
    The theme is loaded once and cached for performance.

    Returns:
        Cached Rich Theme instance.
    """
    colors = load_theme()
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
        "package_manual": f"bold {colors.package_manual}",
        "package_auto": colors.package_auto,
        # Convenience styles
        "bold_header": f"bold {colors.header}",
        "dim": colors.muted,
    }
    return Theme(styles)
