"""Pop!_OS baseline package definitions.

This module defines protected packages and patterns that are essential
for system operation and should never be removed.
"""

import fnmatch

# Protected package name patterns (glob-style)
# These patterns match critical system packages that should never be removed
PROTECTED_PACKAGE_PATTERNS: list[str] = [
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


def is_package_protected(package_name: str) -> bool:
    """Check if a package is protected and should not be removed.

    A package is protected if it matches any of the protected patterns
    or is in the explicit protected packages set.

    Args:
        package_name: Name of the package to check.

    Returns:
        True if the package is protected, False otherwise.
    """
    # Check exact matches first (faster, case-insensitive)
    if package_name.lower() in PROTECTED_PACKAGES:
        return True

    # Check pattern matches
    return any(
        fnmatch.fnmatch(package_name.lower(), pattern) for pattern in PROTECTED_PACKAGE_PATTERNS
    )
