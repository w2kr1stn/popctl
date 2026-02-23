"""XDG-compliant path management for popctl.

This module provides standardized paths following the XDG Base Directory
Specification for configuration, state, and cache storage.

XDG defaults:
- Config: ~/.config/popctl/
- State: ~/.local/state/popctl/
- Cache: ~/.cache/popctl/
"""

import os
from pathlib import Path

# Application identifier for directory naming
APP_NAME = "popctl"


def _get_xdg_dir(env_var: str, default_subdir: str) -> Path:
    """Get XDG directory respecting environment variable override.

    Args:
        env_var: XDG environment variable name (e.g., "XDG_CONFIG_HOME").
        default_subdir: Default subdirectory under home (e.g., ".config").

    Returns:
        Path to the application-specific directory.
    """
    base = os.environ.get(env_var)
    if base:
        return Path(base) / APP_NAME
    return Path.home() / default_subdir / APP_NAME


def get_config_dir() -> Path:
    """Get the configuration directory path.

    Returns:
        Path to ~/.config/popctl/ (or XDG_CONFIG_HOME/popctl/).
    """
    return _get_xdg_dir("XDG_CONFIG_HOME", ".config")


def get_state_dir() -> Path:
    """Get the state directory path.

    State data includes history files and runtime information
    that should persist between runs but is not configuration.

    Returns:
        Path to ~/.local/state/popctl/ (or XDG_STATE_HOME/popctl/).
    """
    return _get_xdg_dir("XDG_STATE_HOME", ".local/state")


def get_manifest_path() -> Path:
    """Get the default manifest file path.

    Returns:
        Path to ~/.config/popctl/manifest.toml.
    """
    return get_config_dir() / "manifest.toml"


def ensure_dir(path: Path, name: str) -> Path:
    """Create directory if it doesn't exist.

    Args:
        path: Directory path to create.
        name: Human-readable name for error messages.

    Returns:
        The created/existing directory path.

    Raises:
        RuntimeError: If directory cannot be created.
    """
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        msg = f"Cannot create {name} directory {path}: {e}"
        raise RuntimeError(msg) from e
    return path
