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


def get_cache_dir() -> Path:
    """Get the cache directory path.

    Cache data includes temporary files that can be regenerated.

    Returns:
        Path to ~/.cache/popctl/ (or XDG_CACHE_HOME/popctl/).
    """
    return _get_xdg_dir("XDG_CACHE_HOME", ".cache")


def get_manifest_path() -> Path:
    """Get the default manifest file path.

    Returns:
        Path to ~/.config/popctl/manifest.toml.
    """
    return get_config_dir() / "manifest.toml"


def get_history_path() -> Path:
    """Get the history file path.

    Returns:
        Path to ~/.local/state/popctl/history.jsonl.
    """
    return get_state_dir() / "history.jsonl"


def get_last_scan_path() -> Path:
    """Get the last scan cache file path.

    Returns:
        Path to ~/.local/state/popctl/last-scan.json.
    """
    return get_state_dir() / "last-scan.json"


def ensure_config_dir() -> Path:
    """Create the configuration directory if it doesn't exist.

    Returns:
        Path to the configuration directory.

    Raises:
        RuntimeError: If the directory cannot be created.
    """
    config_dir = get_config_dir()
    try:
        config_dir.mkdir(parents=True, exist_ok=True)
    except PermissionError as e:
        msg = f"Cannot create config directory {config_dir}: Permission denied"
        raise RuntimeError(msg) from e
    except OSError as e:
        msg = f"Cannot create config directory {config_dir}: {e}"
        raise RuntimeError(msg) from e
    return config_dir


def ensure_state_dir() -> Path:
    """Create the state directory if it doesn't exist.

    Returns:
        Path to the state directory.

    Raises:
        RuntimeError: If the directory cannot be created.
    """
    state_dir = get_state_dir()
    try:
        state_dir.mkdir(parents=True, exist_ok=True)
    except PermissionError as e:
        msg = f"Cannot create state directory {state_dir}: Permission denied"
        raise RuntimeError(msg) from e
    except OSError as e:
        msg = f"Cannot create state directory {state_dir}: {e}"
        raise RuntimeError(msg) from e
    return state_dir


def ensure_cache_dir() -> Path:
    """Create the cache directory if it doesn't exist.

    Returns:
        Path to the cache directory.

    Raises:
        RuntimeError: If the directory cannot be created.
    """
    cache_dir = get_cache_dir()
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
    except PermissionError as e:
        msg = f"Cannot create cache directory {cache_dir}: Permission denied"
        raise RuntimeError(msg) from e
    except OSError as e:
        msg = f"Cannot create cache directory {cache_dir}: {e}"
        raise RuntimeError(msg) from e
    return cache_dir


def ensure_dirs() -> None:
    """Create all required application directories.

    Creates config, state, and cache directories if they don't exist.
    This should be called during application initialization.
    """
    ensure_config_dir()
    ensure_state_dir()
    ensure_cache_dir()
