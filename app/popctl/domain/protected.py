import fnmatch
from pathlib import Path
from typing import Literal

_COMMON_PATTERNS: tuple[str, ...] = (
    # Desktop environment
    "~/.config/cosmic*",
    "~/.config/dconf",
    "~/.config/gtk-*",
    "~/.config/systemd",
    # XDG essentials
    "~/.config/autostart",
    "~/.config/mimeapps.list",
    # Security
    "~/.ssh/*",
    "~/.gnupg/*",
    "~/.gpg/*",
    # popctl itself
    "~/.config/popctl",
    # Container runtime
    "~/.config/docker",
)

PROTECTED_PATTERNS: dict[str, tuple[str, ...]] = {
    "filesystem": (
        *_COMMON_PATTERNS,
        # Shell config
        "~/.config/zsh",
        "~/.config/bash",
        # XDG directories
        "~/.local/share/applications",
        "~/.local/share/icons",
        "~/.local/share/fonts",
        # popctl state
        "~/.local/share/popctl",
        "~/.local/state/popctl",
        # Package manager data
        "~/.local/share/flatpak",
        "~/.local/share/snap",
        # Container runtime data
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
        *_COMMON_PATTERNS,
        # Security directories (in addition to globs from _COMMON_PATTERNS)
        "~/.ssh",
        "~/.gnupg",
        "~/.gpg",
        # Shell configs
        "~/.bashrc",
        "~/.bash_profile",
        "~/.profile",
        "~/.zshrc",
        "~/.zprofile",
        # Package managers
        "~/.config/flatpak",
    ),
}


def is_protected(path: str, domain: Literal["filesystem", "configs"]) -> bool:
    """Patterns use ~ notation, expanded to the real home dir before fnmatch."""
    home = str(Path.home())

    for pattern in PROTECTED_PATTERNS[domain]:
        expanded = home + pattern[1:] if pattern.startswith("~") else pattern

        if fnmatch.fnmatch(path, expanded):
            return True

    return False
