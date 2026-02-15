"""Protected configuration paths that should never be deleted.

This module defines path patterns for configuration directories and files
that are critical for system operation, user security, or application state,
and must be excluded from configuration cleanup operations.
"""

import fnmatch
from pathlib import Path

# Protected configuration path patterns (glob-style).
# Patterns starting with ~ are expanded to the user's home directory
# before matching. Uses immutable tuple per project conventions.
PROTECTED_CONFIG_PATTERNS: tuple[str, ...] = (
    # Desktop environment
    "~/.config/cosmic*",
    "~/.config/dconf",
    "~/.config/gtk-*",
    "~/.config/systemd",
    "~/.config/autostart",
    "~/.config/mimeapps.list",
    # Shell configs (user-created, always keep)
    "~/.bashrc",
    "~/.bash_profile",
    "~/.profile",
    "~/.zshrc",
    "~/.zprofile",
    "~/.config/zsh",
    "~/.config/bash",
    # Security
    "~/.ssh/*",
    "~/.gnupg/*",
    # popctl itself
    "~/.config/popctl",
    # Container runtime
    "~/.config/docker",
    # Common user configs
    "~/.config/nvim",
    "~/.vimrc",
    "~/.gitconfig",
    "~/.config/git",
)


def is_protected_config(path: str) -> bool:
    """Check if a configuration path is protected and should not be deleted.

    The path argument should be an absolute path (e.g., /home/user/.config/nvim).
    Patterns using ~ notation are expanded to the actual home directory before
    comparison using fnmatch for glob-style matching.

    Args:
        path: Absolute configuration path to check.

    Returns:
        True if the path matches any protected pattern, False otherwise.
    """
    home = str(Path.home())

    for pattern in PROTECTED_CONFIG_PATTERNS:
        expanded = home + pattern[1:] if pattern.startswith("~") else pattern

        if fnmatch.fnmatch(path, expanded):
            return True

    return False
