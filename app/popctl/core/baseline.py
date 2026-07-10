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
    # GNOME desktop environment
    "gnome-shell",
    "gnome-shell-common",
    "gnome-session",
    "gnome-session-bin",
    "gnome-session-common",
    "ubuntu-session",
    "mutter",
    "mutter-common",
    "gnome-settings-daemon",
    "gnome-settings-daemon-common",
    # KDE Plasma desktop environment
    "sddm",
    "sddm-common",
    "kwin-x11",
    "kwin-wayland",
    "kwin-common",
    "kwin-data",
    "plasma-desktop",
    "plasma-desktop-data",
    "plasma-workspace",
    "plasma-workspace-data",
    "plasma-workspace-wayland",
    "kded5",
    "kded6",
    "polkit-kde-agent-1",
    "plasma-session*",
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
    # Check exact matches first (faster, case-insensitive)
    if package_name.lower() in PROTECTED_PACKAGES:
        return True

    # Check pattern matches
    return any(
        fnmatch.fnmatch(package_name.lower(), pattern) for pattern in PROTECTED_PACKAGE_PATTERNS
    )
