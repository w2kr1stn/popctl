"""Unit tests for Pop!_OS baseline module.

Tests for protected package patterns and baseline package definitions.
"""

import pytest
from popctl.core.baseline import (
    PROTECTED_PACKAGES,
    PROTECTED_PATTERNS,
    get_baseline_packages,
    get_protected_packages,
    get_protected_patterns,
    is_protected,
)


class TestIsProtected:
    """Tests for is_protected function."""

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
        assert is_protected(package_name) is True

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
        assert is_protected(package_name) is True

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
        assert is_protected(package_name) is False

    def test_case_insensitive_matching(self) -> None:
        """Pattern matching is case-insensitive."""
        # Linux pattern should match regardless of case
        assert is_protected("LINUX-image-generic") is True
        assert is_protected("Linux-Headers-6.5.0") is True

    def test_flatpak_style_names_not_protected(self) -> None:
        """Flatpak-style app IDs are not protected by APT patterns."""
        assert is_protected("com.spotify.Client") is False
        assert is_protected("org.mozilla.firefox") is False
        assert is_protected("io.github.something") is False


class TestGetProtectedPatterns:
    """Tests for get_protected_patterns function."""

    def test_returns_copy(self) -> None:
        """get_protected_patterns returns a copy, not the original list."""
        patterns1 = get_protected_patterns()
        patterns2 = get_protected_patterns()

        assert patterns1 == patterns2
        assert patterns1 is not PROTECTED_PATTERNS

    def test_contains_essential_patterns(self) -> None:
        """Protected patterns include essential system patterns."""
        patterns = get_protected_patterns()

        assert "linux-*" in patterns
        assert "systemd*" in patterns
        assert "pop-*" in patterns
        assert "cosmic-*" in patterns
        assert "apt*" in patterns


class TestGetProtectedPackages:
    """Tests for get_protected_packages function."""

    def test_returns_copy(self) -> None:
        """get_protected_packages returns a copy, not the original set."""
        packages1 = get_protected_packages()
        packages2 = get_protected_packages()

        assert packages1 == packages2
        assert packages1 is not PROTECTED_PACKAGES

    def test_contains_essential_packages(self) -> None:
        """Protected packages include essential system packages."""
        packages = get_protected_packages()

        assert "bash" in packages
        assert "sudo" in packages
        assert "apt" in packages
        assert "dpkg" in packages
        assert "systemd" in packages


class TestGetBaselinePackages:
    """Tests for get_baseline_packages function."""

    def test_includes_protected_packages(self) -> None:
        """Baseline packages include all protected packages."""
        baseline = get_baseline_packages()
        protected = get_protected_packages()

        # All protected packages should be in baseline
        assert protected.issubset(baseline)

    def test_includes_pop_os_packages(self) -> None:
        """Baseline packages include Pop!_OS specific packages."""
        baseline = get_baseline_packages()

        # Check for COSMIC packages
        assert "cosmic-files" in baseline
        assert "cosmic-term" in baseline
        assert "cosmic-settings" in baseline

        # Check for Pop!_OS packages
        assert "pop-default-settings" in baseline
        assert "pop-icon-theme" in baseline

    def test_includes_system76_packages(self) -> None:
        """Baseline packages include System76 packages."""
        baseline = get_baseline_packages()

        assert "system76-driver" in baseline
        assert "system76-firmware" in baseline
        assert "system76-power" in baseline

    def test_returns_set(self) -> None:
        """get_baseline_packages returns a set type."""
        baseline = get_baseline_packages()

        assert isinstance(baseline, set)

    def test_non_empty(self) -> None:
        """Baseline packages is not empty."""
        baseline = get_baseline_packages()

        assert len(baseline) > 0
