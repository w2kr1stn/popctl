"""Unit tests for Pop!_OS baseline module.

Tests for protected package patterns and baseline package definitions.
"""

import pytest
from popctl.core.baseline import (
    is_package_protected,
)


class TestIsProtected:
    """Tests for is_package_protected function."""

    # Test exact matches
    @pytest.mark.parametrize(
        "package_name",
        [
            "bash",
            "coreutils",
            "sudo",
            "systemd",
            "apt",
            "dpkg",
            "init",
        ],
    )
    def test_exact_match_packages_are_protected(self, package_name: str) -> None:
        """Packages in PROTECTED_PACKAGES are protected."""
        assert is_package_protected(package_name) is True

    # Test pattern matches
    @pytest.mark.parametrize(
        "package_name",
        [
            "linux-image-6.5.0-generic",
            "linux-headers-generic",
            "systemd-sysv",
            "systemd-timesyncd",
            "pop-default-settings",
            "pop-icon-theme",
            "cosmic-applets",
            "cosmic-files",
            "system76-driver",
            "system76-firmware",
            "grub-common",
            "grub-efi-amd64",
            "apt-utils",
            "apt-transport-https",
            "dpkg-dev",
        ],
    )
    def test_pattern_match_packages_are_protected(self, package_name: str) -> None:
        """Packages matching PROTECTED_PATTERNS are protected."""
        assert is_package_protected(package_name) is True

    # Test non-protected packages
    @pytest.mark.parametrize(
        "package_name",
        [
            "firefox",
            "neovim",
            "git",
            "docker-ce",
            "nodejs",
            "python3-pip",
            "vscode",
            "spotify-client",
            "random-app",
        ],
    )
    def test_non_protected_packages(self, package_name: str) -> None:
        """Regular user packages are not protected."""
        assert is_package_protected(package_name) is False

    # Test snap infrastructure protection
    @pytest.mark.parametrize(
        "package_name",
        [
            "snapd",
            "bare",
            "core22",
            "core24",
            "snapd-something",
        ],
    )
    def test_snap_infrastructure_is_package_protected(self, package_name: str) -> None:
        """Snap infrastructure packages are protected."""
        assert is_package_protected(package_name) is True

    def test_case_insensitive_matching(self) -> None:
        """Both exact and pattern matching are case-insensitive."""
        # Exact match: BASH -> bash
        assert is_package_protected("BASH") is True
        assert is_package_protected("Sudo") is True
        # Pattern match: Linux -> linux-*
        assert is_package_protected("LINUX-image-generic") is True
        assert is_package_protected("Linux-Headers-6.5.0") is True

    def test_flatpak_style_names_not_protected(self) -> None:
        """Flatpak-style app IDs are not protected by APT patterns."""
        assert is_package_protected("com.spotify.Client") is False
        assert is_package_protected("org.mozilla.firefox") is False
        assert is_package_protected("io.github.something") is False
