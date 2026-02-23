"""Tests for domain protected path patterns.

Consolidated from filesystem/test_protected.py and configs/test_protected.py.
Both domains use the same PROTECTED_PATTERNS dict and is_protected function
with different domain names.
"""

from pathlib import Path
from typing import Literal
from unittest.mock import patch

import pytest
from popctl.domain.protected import PROTECTED_PATTERNS, is_protected

_DOMAIN_PARAMS = [
    pytest.param("filesystem", id="filesystem"),
    pytest.param("configs", id="configs"),
]


# ---------------------------------------------------------------------------
# Shared tests (parametrized over both domains)
# ---------------------------------------------------------------------------


class TestProtectedPatternsShared:
    """Pattern-level tests that apply to both domains."""

    @pytest.mark.parametrize("domain", _DOMAIN_PARAMS)
    def test_patterns_not_empty(self, domain: str) -> None:
        """Protected patterns should contain entries."""
        assert len(PROTECTED_PATTERNS[domain]) > 0

    @pytest.mark.parametrize("domain", _DOMAIN_PARAMS)
    def test_patterns_is_tuple(self, domain: str) -> None:
        """Protected patterns should be an immutable tuple."""
        assert isinstance(PROTECTED_PATTERNS[domain], tuple)

    @pytest.mark.parametrize("domain", _DOMAIN_PARAMS)
    def test_patterns_contain_ssh(self, domain: str) -> None:
        """SSH wildcard pattern should be in the protected list."""
        assert "~/.ssh/*" in PROTECTED_PATTERNS[domain]


class TestIsProtectedShared:
    """is_protected tests that apply identically to both domains."""

    @staticmethod
    def _home() -> str:
        return str(Path.home())

    @pytest.mark.parametrize("domain", _DOMAIN_PARAMS)
    def test_ssh_contents_protected(self, domain: Literal["filesystem", "configs"]) -> None:
        """~/.ssh/id_rsa should be protected."""
        path = f"{self._home()}/.ssh/id_rsa"
        assert is_protected(path, domain) is True

    @pytest.mark.parametrize("domain", _DOMAIN_PARAMS)
    def test_gnupg_contents_protected(self, domain: Literal["filesystem", "configs"]) -> None:
        """~/.gnupg/pubring.kbx should be protected."""
        path = f"{self._home()}/.gnupg/pubring.kbx"
        assert is_protected(path, domain) is True

    @pytest.mark.parametrize("domain", _DOMAIN_PARAMS)
    def test_popctl_protected(self, domain: Literal["filesystem", "configs"]) -> None:
        """~/.config/popctl should be protected."""
        path = f"{self._home()}/.config/popctl"
        assert is_protected(path, domain) is True

    @pytest.mark.parametrize("domain", _DOMAIN_PARAMS)
    def test_random_path_not_protected(self, domain: Literal["filesystem", "configs"]) -> None:
        """~/.config/some-random-app should NOT be protected."""
        path = f"{self._home()}/.config/some-random-app"
        assert is_protected(path, domain) is False

    @pytest.mark.parametrize("domain", _DOMAIN_PARAMS)
    def test_tilde_expansion(self, domain: Literal["filesystem", "configs"]) -> None:
        """Patterns with ~ should be expanded to the actual home directory."""
        home = self._home()
        path = f"{home}/.config/cosmic-settings"
        assert is_protected(path, domain) is True

    @pytest.mark.parametrize("domain", _DOMAIN_PARAMS)
    def test_cosmic_wildcard_matches(self, domain: Literal["filesystem", "configs"]) -> None:
        """~/.config/cosmic* pattern matches various cosmic dirs."""
        home = self._home()
        assert is_protected(f"{home}/.config/cosmic", domain) is True
        assert is_protected(f"{home}/.config/cosmic-comp", domain) is True
        assert is_protected(f"{home}/.config/cosmic-term", domain) is True

    @pytest.mark.parametrize("domain", _DOMAIN_PARAMS)
    def test_gtk_wildcard_matches(self, domain: Literal["filesystem", "configs"]) -> None:
        """~/.config/gtk-* pattern matches gtk config dirs."""
        home = self._home()
        assert is_protected(f"{home}/.config/gtk-3.0", domain) is True
        assert is_protected(f"{home}/.config/gtk-4.0", domain) is True

    @pytest.mark.parametrize("domain", _DOMAIN_PARAMS)
    def test_docker_config_protected(self, domain: Literal["filesystem", "configs"]) -> None:
        """~/.config/docker should be protected."""
        path = f"{self._home()}/.config/docker"
        assert is_protected(path, domain) is True

    @pytest.mark.parametrize("domain", _DOMAIN_PARAMS)
    def test_with_mocked_home(self, domain: Literal["filesystem", "configs"]) -> None:
        """Verify ~ expansion uses Path.home() correctly with mocked home."""
        with patch.object(Path, "home", return_value=Path("/mock/home")):
            assert is_protected("/mock/home/.ssh/id_rsa", domain) is True
            assert is_protected("/mock/home/.config/popctl", domain) is True
            # Original home should no longer match
            assert is_protected("/real/home/.ssh/id_rsa", domain) is False


# ---------------------------------------------------------------------------
# Filesystem-specific tests
# ---------------------------------------------------------------------------


class TestProtectedPatternsFilesystem:
    """Pattern-level tests specific to the filesystem domain."""

    def test_patterns_contain_etc_entries(self) -> None:
        """System /etc patterns should be in the protected list."""
        assert "/etc/fstab" in PROTECTED_PATTERNS["filesystem"]
        assert "/etc/ssh/*" in PROTECTED_PATTERNS["filesystem"]


class TestIsProtectedFilesystem:
    """is_protected tests specific to the filesystem domain."""

    @staticmethod
    def _home() -> str:
        return str(Path.home())

    def test_etc_system_files_protected(self) -> None:
        """/etc/fstab should be protected."""
        assert is_protected("/etc/fstab", "filesystem") is True

    def test_etc_ssh_protected(self) -> None:
        """/etc/ssh/sshd_config should be protected."""
        assert is_protected("/etc/ssh/sshd_config", "filesystem") is True

    def test_unprotected_etc_path(self) -> None:
        """/etc/some-random-dir should NOT be protected."""
        assert is_protected("/etc/some-random-dir", "filesystem") is False

    def test_etc_sudoers_wildcard(self) -> None:
        """/etc/sudoers* pattern matches sudoers and sudoers.d."""
        assert is_protected("/etc/sudoers", "filesystem") is True
        assert is_protected("/etc/sudoers.d", "filesystem") is True

    def test_local_share_popctl_protected(self) -> None:
        """~/.local/share/popctl should be protected."""
        path = f"{self._home()}/.local/share/popctl"
        assert is_protected(path, "filesystem") is True

    def test_local_state_popctl_protected(self) -> None:
        """~/.local/state/popctl should be protected."""
        path = f"{self._home()}/.local/state/popctl"
        assert is_protected(path, "filesystem") is True

    def test_keyrings_protected(self) -> None:
        """~/.local/share/keyrings should be protected."""
        path = f"{self._home()}/.local/share/keyrings"
        assert is_protected(path, "filesystem") is True


# ---------------------------------------------------------------------------
# Configs-specific tests
# ---------------------------------------------------------------------------


class TestProtectedPatternsConfigs:
    """Pattern-level tests specific to the configs domain."""

    def test_patterns_contain_shell_configs(self) -> None:
        """Shell config patterns should be in the protected list."""
        assert "~/.bashrc" in PROTECTED_PATTERNS["configs"]
        assert "~/.zshrc" in PROTECTED_PATTERNS["configs"]
        assert "~/.profile" in PROTECTED_PATTERNS["configs"]
        assert "~/.bash_profile" in PROTECTED_PATTERNS["configs"]
        assert "~/.zprofile" in PROTECTED_PATTERNS["configs"]

    def test_patterns_contain_ssh_directory(self) -> None:
        """SSH directory pattern should be in the protected list."""
        assert "~/.ssh" in PROTECTED_PATTERNS["configs"]
        assert "~/.ssh/*" in PROTECTED_PATTERNS["configs"]

    def test_patterns_contain_gnupg(self) -> None:
        """GnuPG patterns should be in the protected list."""
        assert "~/.gnupg" in PROTECTED_PATTERNS["configs"]
        assert "~/.gnupg/*" in PROTECTED_PATTERNS["configs"]

    def test_patterns_contain_gpg(self) -> None:
        """GPG patterns should be in the protected list."""
        assert "~/.gpg" in PROTECTED_PATTERNS["configs"]
        assert "~/.gpg/*" in PROTECTED_PATTERNS["configs"]

    def test_patterns_contain_flatpak(self) -> None:
        """Flatpak config pattern should be in the protected list."""
        assert "~/.config/flatpak" in PROTECTED_PATTERNS["configs"]


class TestIsProtectedConfigs:
    """is_protected tests specific to the configs domain."""

    @staticmethod
    def _home() -> str:
        return str(Path.home())

    def test_shell_bashrc_protected(self) -> None:
        """~/.bashrc should be protected."""
        path = f"{self._home()}/.bashrc"
        assert is_protected(path, "configs") is True

    def test_shell_zshrc_protected(self) -> None:
        """~/.zshrc should be protected."""
        path = f"{self._home()}/.zshrc"
        assert is_protected(path, "configs") is True

    def test_ssh_directory_protected(self) -> None:
        """~/.ssh should be protected (directory itself)."""
        path = f"{self._home()}/.ssh"
        assert is_protected(path, "configs") is True

    def test_gnupg_directory_protected(self) -> None:
        """~/.gnupg should be protected (directory itself)."""
        path = f"{self._home()}/.gnupg"
        assert is_protected(path, "configs") is True

    def test_gpg_directory_protected(self) -> None:
        """~/.gpg should be protected (directory itself)."""
        path = f"{self._home()}/.gpg"
        assert is_protected(path, "configs") is True

    def test_gpg_contents_protected(self) -> None:
        """~/.gpg/keys should be protected (wildcard match)."""
        path = f"{self._home()}/.gpg/keys"
        assert is_protected(path, "configs") is True

    def test_flatpak_protected(self) -> None:
        """~/.config/flatpak should be protected."""
        path = f"{self._home()}/.config/flatpak"
        assert is_protected(path, "configs") is True

    def test_dconf_protected(self) -> None:
        """~/.config/dconf should be protected."""
        path = f"{self._home()}/.config/dconf"
        assert is_protected(path, "configs") is True

    def test_systemd_protected(self) -> None:
        """~/.config/systemd should be protected."""
        path = f"{self._home()}/.config/systemd"
        assert is_protected(path, "configs") is True

    def test_autostart_protected(self) -> None:
        """~/.config/autostart should be protected."""
        path = f"{self._home()}/.config/autostart"
        assert is_protected(path, "configs") is True

    def test_mimeapps_protected(self) -> None:
        """~/.config/mimeapps.list should be protected."""
        path = f"{self._home()}/.config/mimeapps.list"
        assert is_protected(path, "configs") is True

    def test_cosmic_panel_matches(self) -> None:
        """cosmic* pattern should also match cosmic-panel."""
        home = self._home()
        assert is_protected(f"{home}/.config/cosmic-panel", "configs") is True

    def test_with_mocked_home_configs_specific(self) -> None:
        """Verify configs-specific paths with mocked home."""
        with patch.object(Path, "home", return_value=Path("/mock/home")):
            assert is_protected("/mock/home/.ssh", "configs") is True
            assert is_protected("/mock/home/.bashrc", "configs") is True
            assert is_protected("/mock/home/.gnupg", "configs") is True
            assert is_protected("/mock/home/.gpg", "configs") is True
            assert is_protected("/mock/home/.config/flatpak", "configs") is True
