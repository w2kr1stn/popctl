"""Protected filesystem paths that should never be deleted.

This module defines path patterns for directories and files that are
critical for system operation, user security, or application state,
and must be excluded from filesystem cleanup operations.
"""

import fnmatch
from pathlib import Path

# Protected filesystem path patterns (glob-style).
# Patterns starting with ~ are expanded to the user's home directory
# before matching. Patterns starting with / are matched as-is.
PROTECTED_PATH_PATTERNS: list[str] = [
    # SSH and security
    "~/.ssh/*",
    "~/.gnupg/*",
    "~/.gpg/*",
    # Shell config
    "~/.config/zsh",
    "~/.config/bash",
    # XDG directories
    "~/.config/autostart",
    "~/.config/mimeapps.list",
    "~/.local/share/applications",
    "~/.local/share/icons",
    "~/.local/share/fonts",
    # Desktop environment
    "~/.config/cosmic*",
    "~/.config/dconf",
    "~/.config/gtk-*",
    "~/.config/systemd",
    # popctl itself
    "~/.config/popctl",
    "~/.local/share/popctl",
    "~/.local/state/popctl",
    # Package manager data
    "~/.local/share/flatpak",
    "~/.local/share/snap",
    # Container runtime
    "~/.config/docker",
    "~/.local/share/docker",
    "~/.local/share/containers",
    # Keyrings
    "~/.local/share/keyrings",
    # System (/etc)
    "/etc/fstab",
    "/etc/hosts",
    "/etc/hostname",
    "/etc/passwd",
    "/etc/shadow",
    "/etc/group",
    "/etc/sudoers*",
    "/etc/ssh/*",
    "/etc/ssl/*",
    "/etc/systemd/*",
    "/etc/NetworkManager/*",
    "/etc/apt/*",
    "/etc/dpkg/*",
    "/etc/default/*",
    "/etc/security/*",
    "/etc/pam.d/*",
]


def is_protected_path(path: str) -> bool:
    """Check if a filesystem path is protected and should not be deleted.

    The path argument should be an absolute path (e.g., /home/user/.ssh/id_rsa).
    Patterns using ~ notation are expanded to the actual home directory before
    comparison using fnmatch for glob-style matching.

    Args:
        path: Absolute filesystem path to check.

    Returns:
        True if the path matches any protected pattern, False otherwise.
    """
    home = str(Path.home())

    for pattern in PROTECTED_PATH_PATTERNS:
        expanded = home + pattern[1:] if pattern.startswith("~") else pattern

        if fnmatch.fnmatch(path, expanded):
            return True

    return False
