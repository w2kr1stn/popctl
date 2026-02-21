"""Advisor-specific path management.

Provides paths for exchange directories, advisor configuration,
session storage, and persistent memory files.
"""

from pathlib import Path

from popctl.core.paths import _ensure_dir, get_config_dir, get_state_dir

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
