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


def _ensure_dir(path: Path, name: str) -> Path:
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
    except PermissionError as e:
        msg = f"Cannot create {name} directory {path}: Permission denied"
        raise RuntimeError(msg) from e
    except OSError as e:
        msg = f"Cannot create {name} directory {path}: {e}"
        raise RuntimeError(msg) from e
    return path


def ensure_config_dir() -> Path:
    """Create the configuration directory if it doesn't exist.

    Returns:
        Path to the configuration directory.

    Raises:
        RuntimeError: If the directory cannot be created.
    """
    return _ensure_dir(get_config_dir(), "config")


def ensure_state_dir() -> Path:
    """Create the state directory if it doesn't exist.

    Returns:
        Path to the state directory.

    Raises:
        RuntimeError: If the directory cannot be created.
    """
    return _ensure_dir(get_state_dir(), "state")


def ensure_cache_dir() -> Path:
    """Create the cache directory if it doesn't exist.

    Returns:
        Path to the cache directory.

    Raises:
        RuntimeError: If the directory cannot be created.
    """
    return _ensure_dir(get_cache_dir(), "cache")


def ensure_dirs() -> None:
    """Create all required application directories.

    Creates config, state, and cache directories if they don't exist.
    This should be called during application initialization.
    """
    ensure_config_dir()
    ensure_state_dir()
    ensure_cache_dir()


# =============================================================================
# Config backup paths
# =============================================================================


def get_config_backup_dir() -> Path:
    """Get the config backup directory path.

    Config backups are stored under the state directory in a dedicated
    subdirectory. Each backup operation creates a timestamped subdirectory
    within this location.

    Returns:
        Path to ~/.local/state/popctl/config-backups/.
    """
    return get_state_dir() / "config-backups"


def ensure_config_backup_dir() -> Path:
    """Create the config backup directory if it doesn't exist.

    Returns:
        Path to the config backup directory.

    Raises:
        RuntimeError: If the directory cannot be created.
    """
    return _ensure_dir(get_config_backup_dir(), "config backup")


# =============================================================================
# Advisor-specific paths
# =============================================================================

# Exchange directory for file-based communication with AI advisors
EXCHANGE_DIR = Path("/tmp/popctl-exchange")


def get_exchange_dir() -> Path:
    """Get the exchange directory path for advisor communication.

    The exchange directory is used for file-based communication between
    popctl and AI advisors (Claude Code / Gemini CLI). Files placed here
    include scan.json, prompt.txt, and decisions.toml.

    Returns:
        Path to /tmp/popctl-exchange.
    """
    return EXCHANGE_DIR


def ensure_exchange_dir() -> Path:
    """Create the exchange directory if it doesn't exist.

    The exchange directory is created with standard permissions in /tmp.
    This should be called before any advisor operations.

    Returns:
        Path to the exchange directory.

    Raises:
        RuntimeError: If the directory cannot be created.
    """
    return _ensure_dir(get_exchange_dir(), "exchange")


def get_advisor_config_path() -> Path:
    """Get the advisor configuration file path.

    Returns:
        Path to ~/.config/popctl/advisor.toml.
    """
    return get_config_dir() / "advisor.toml"


def get_advisor_sessions_dir() -> Path:
    """Get the advisor sessions directory path.

    Each interactive advisor session creates a timestamped subdirectory
    containing the workspace files (CLAUDE.md, scan.json, output/).

    Returns:
        Path to ~/.local/state/popctl/advisor-sessions/.
    """
    return get_state_dir() / "advisor-sessions"


def ensure_advisor_sessions_dir() -> Path:
    """Create the advisor sessions directory if it doesn't exist.

    Returns:
        Path to the advisor sessions directory.

    Raises:
        RuntimeError: If the directory cannot be created.
    """
    return _ensure_dir(get_advisor_sessions_dir(), "advisor sessions")


def get_advisor_memory_path() -> Path:
    """Get the persistent advisor memory file path.

    The memory file stores learned user preferences and past classification
    decisions that chain across advisor sessions.

    Returns:
        Path to ~/.local/state/popctl/advisor/memory.md.
    """
    return get_state_dir() / "advisor" / "memory.md"


def ensure_advisor_memory_dir() -> Path:
    """Create the advisor memory directory if it doesn't exist.

    Returns:
        Path to ~/.local/state/popctl/advisor/.

    Raises:
        RuntimeError: If the directory cannot be created.
    """
    return _ensure_dir(get_state_dir() / "advisor", "advisor memory")
