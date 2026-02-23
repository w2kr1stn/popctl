"""Pop!_OS baseline package definitions.

This module defines protected packages and patterns that are essential
for system operation and should never be removed.
"""

import fnmatch

# Protected package name patterns (glob-style)
# These patterns match critical system packages that should never be removed
PROTECTED_PATTERNS: list[str] = [
    # Kernel and boot
    "linux-*",
    "grub-*",
    "initramfs-tools*",
    # System core
    "systemd*",
    "dbus*",
    "udev*",
    # Pop!_OS specific
    "pop-*",
    "cosmic-*",
    "system76-*",
    "kernelstub*",
    # Package management
    "apt*",
    "dpkg*",
    "flatpak",
    # Snap infrastructure
    "core*",
    "snapd*",
    # Essential system libs
    "libc6*",
    "libsystemd*",
    "libnss*",
    "libpam*",
    # Network essentials
    "networkmanager*",
    "network-manager*",
    # Display and session
    "gdm*",
    "plymouth*",
    # Recovery
    "pop-upgrade*",
    "system76-firmware*",
]


# Explicitly protected package names (exact matches)
PROTECTED_PACKAGES: set[str] = {
    # Core utilities that must exist
    "bash",
    "coreutils",
    "util-linux",
    "sudo",
    "passwd",
    "login",
    # Essential networking
    "iproute2",
    "netbase",
    "hostname",
    # Package management
    "apt",
    "dpkg",
    "apt-utils",
    # Init and services
    "init",
    "systemd",
    "systemd-sysv",
    # Snap infrastructure
    "snapd",
    "bare",
}


def is_protected(package_name: str) -> bool:
    """Check if a package is protected and should not be removed.

    A package is protected if it matches any of the protected patterns
    or is in the explicit protected packages set.

    Args:
        package_name: Name of the package to check.

    Returns:
        True if the package is protected, False otherwise.
    """
    # Check exact matches first (faster)
    if package_name in PROTECTED_PACKAGES:
        return True

    # Check pattern matches
    for pattern in PROTECTED_PATTERNS:
        if fnmatch.fnmatch(package_name.lower(), pattern.lower()):
            return True

    return False


def get_protected_patterns() -> list[str]:
    """Get the list of protected package patterns.

    Returns:
        List of glob-style patterns for protected packages.
    """
    return PROTECTED_PATTERNS.copy()


def get_protected_packages() -> set[str]:
    """Get the set of explicitly protected package names.

    Returns:
        Set of package names that are always protected.
    """
    return PROTECTED_PACKAGES.copy()


def get_baseline_packages() -> set[str]:
    """Get baseline package names for Pop!_OS 24.04.

    These are packages that come pre-installed with a fresh Pop!_OS
    installation and are considered part of the base system.

    Note: This is not an exhaustive list but covers the main system
    packages. Protected packages are a subset of these.

    Returns:
        Set of baseline package names.
    """
    # Start with explicitly protected packages
    baseline = PROTECTED_PACKAGES.copy()

    # Add common Pop!_OS base packages
    baseline.update(
        {
            # Desktop environment (COSMIC)
            "cosmic-applets",
            "cosmic-app-library",
            "cosmic-bg",
            "cosmic-comp",
            "cosmic-edit",
            "cosmic-files",
            "cosmic-greeter",
            "cosmic-icons",
            "cosmic-launcher",
            "cosmic-notifications",
            "cosmic-osd",
            "cosmic-panel",
            "cosmic-randr",
            "cosmic-screenshot",
            "cosmic-session",
            "cosmic-settings",
            "cosmic-settings-daemon",
            "cosmic-store",
            "cosmic-term",
            "cosmic-workspaces",
            # Pop!_OS branding and tools
            "pop-default-settings",
            "pop-fonts",
            "pop-gnome-initial-setup",
            "pop-icon-theme",
            "pop-launcher",
            "pop-shell",
            "pop-sound-theme",
            "pop-theme",
            "pop-upgrade",
            "pop-wallpapers",
            # System76 hardware support
            "system76-driver",
            "system76-firmware",
            "system76-power",
            # Essential CLI tools
            "curl",
            "wget",
            "git",
            "vim",
            "nano",
            "less",
            "gzip",
            "tar",
            "xz-utils",
            # Essential system services
            "cups",
            "avahi-daemon",
            "bluetooth",
            "pulseaudio",
            "pipewire",
            # File systems
            "ntfs-3g",
            "exfat-fuse",
            "btrfs-progs",
        }
    )

    return baseline
