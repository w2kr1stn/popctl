"""Unit tests for Pop!_OS baseline module.

Tests for protected package patterns and baseline package definitions.
"""

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from popctl.core.baseline import (
    is_package_protected,
)
from popctl.core.diff import compute_diff, diff_to_actions
from popctl.models.manifest import (
    Manifest,
    ManifestMeta,
    PackageConfig,
    PackageEntry,
    SystemConfig,
)
from popctl.models.package import PackageSource, PackageStatus, ScannedPackage
from popctl.scanners.base import Scanner


class StaticAptScanner(Scanner):
    """Scanner with a fixed set of APT packages for protection tests."""

    source = PackageSource.APT

    def __init__(self, packages: list[ScannedPackage]) -> None:
        self._packages = packages

    def scan(self) -> Iterator[ScannedPackage]:
        yield from self._packages

    def is_available(self) -> bool:
        return True


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
            "gnome-shell",
            "gnome-shell-common",
            "gnome-session",
            "gnome-session-bin",
            "gnome-session-common",
            "ubuntu-session",
            "gdm3",
            "mutter",
            "mutter-common",
            "gnome-settings-daemon",
            "gnome-settings-daemon-common",
            "sddm",
            "sddm-common",
            "kwin-x11",
            "plasma-desktop",
            "plasma-desktop-data",
            "plasma-workspace",
            "plasma-workspace-data",
            "kwin-wayland",
            "kwin-common",
            "kwin-data",
            "plasma-workspace-wayland",
            "kded5",
            "kded6",
            "polkit-kde-agent-1",
            "plasma-session-x11",
            "plasma-session-wayland",
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
            "gnome-calculator",
            "kdeconnect",
            "gnome-shell-extension-manager",
            "sddm-theme-breeze",
            "kwin-addons",
            "nautilus",
            "dolphin",
            "gnome-session-canberra",
            "mutter-tests",
            "kded5-dev",
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

    @pytest.mark.parametrize(
        "package_name",
        [
            "gnome-settings-daemon-common",
            "plasma-workspace-data",
            "plasma-desktop-data",
            "polkit-kde-agent-1",
            "ubuntu-session",
        ],
    )
    def test_protected_package_in_remove_list_produces_no_removal_action(
        self, package_name: str
    ) -> None:
        """Protected manual APT packages cannot produce removal actions."""
        now = datetime.now(UTC)
        manifest = Manifest(
            meta=ManifestMeta(created=now, updated=now),
            system=SystemConfig(name="test-machine"),
            packages=PackageConfig(remove={package_name: PackageEntry(source="apt")}),
        )
        scanner = StaticAptScanner(
            [
                ScannedPackage(
                    name=package_name,
                    source=PackageSource.APT,
                    version="1.0",
                    status=PackageStatus.MANUAL,
                )
            ]
        )

        diff = compute_diff(manifest, [scanner])

        assert diff.extra == ()
        assert diff_to_actions(diff) == []
