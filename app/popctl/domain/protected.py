"""Protected path patterns for filesystem and config domains.

Defines path patterns for directories and files that are critical for
system operation, user security, or application state, and must be
excluded from cleanup operations in each domain.
"""

import fnmatch
from pathlib import Path
from typing import Literal

PROTECTED_PATTERNS: dict[str, tuple[str, ...]] = {
    "filesystem": (
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
    ),
    "configs": (
        # Desktop environment
        "~/.config/cosmic*",
        "~/.config/dconf",
        "~/.config/gtk-*",
        "~/.config/systemd",
        # XDG essentials
        "~/.config/autostart",
        "~/.config/mimeapps.list",
        # Security
        "~/.ssh",
        "~/.ssh/*",
        "~/.gnupg",
        "~/.gnupg/*",
        "~/.gpg",
        "~/.gpg/*",
        # Shell configs
        "~/.bashrc",
        "~/.bash_profile",
        "~/.profile",
        "~/.zshrc",
        "~/.zprofile",
        # popctl itself
        "~/.config/popctl",
        # Container runtime
        "~/.config/docker",
        # Package managers
        "~/.config/flatpak",
    ),
}


def is_protected(path: str, domain: Literal["filesystem", "configs"]) -> bool:
    """Check if a path is protected in the given domain.

    The path argument should be an absolute path (e.g., /home/user/.ssh/id_rsa).
    Patterns using ~ notation are expanded to the actual home directory before
    comparison using fnmatch for glob-style matching.

    Args:
        path: Absolute path to check.
        domain: Domain to check against ("filesystem" or "configs").

    Returns:
        True if the path matches any protected pattern, False otherwise.
    """
    home = str(Path.home())

    for pattern in PROTECTED_PATTERNS[domain]:
        expanded = home + pattern[1:] if pattern.startswith("~") else pattern

        if fnmatch.fnmatch(path, expanded):
            return True

    return False
