"""Tests for config protected path patterns."""

from pathlib import Path
from unittest.mock import patch

from popctl.configs.protected import PROTECTED_CONFIG_PATTERNS, is_protected_config


class TestProtectedConfigPatterns:
    """Tests for the PROTECTED_CONFIG_PATTERNS tuple."""

    def test_patterns_not_empty(self) -> None:
        """Protected patterns tuple should contain entries."""
        assert len(PROTECTED_CONFIG_PATTERNS) > 0

    def test_patterns_is_tuple(self) -> None:
        """Protected patterns should be an immutable tuple."""
        assert isinstance(PROTECTED_CONFIG_PATTERNS, tuple)

    def test_patterns_contain_shell_configs(self) -> None:
        """Shell config patterns should be in the protected list."""
        assert "~/.bashrc" in PROTECTED_CONFIG_PATTERNS
        assert "~/.zshrc" in PROTECTED_CONFIG_PATTERNS
        assert "~/.profile" in PROTECTED_CONFIG_PATTERNS
        assert "~/.bash_profile" in PROTECTED_CONFIG_PATTERNS
        assert "~/.zprofile" in PROTECTED_CONFIG_PATTERNS

    def test_patterns_contain_ssh(self) -> None:
        """SSH patterns should be in the protected list."""
        assert "~/.ssh" in PROTECTED_CONFIG_PATTERNS
        assert "~/.ssh/*" in PROTECTED_CONFIG_PATTERNS

    def test_patterns_contain_gnupg(self) -> None:
        """GnuPG patterns should be in the protected list."""
        assert "~/.gnupg" in PROTECTED_CONFIG_PATTERNS
        assert "~/.gnupg/*" in PROTECTED_CONFIG_PATTERNS

    def test_patterns_contain_gpg(self) -> None:
        """GPG patterns should be in the protected list."""
        assert "~/.gpg" in PROTECTED_CONFIG_PATTERNS
        assert "~/.gpg/*" in PROTECTED_CONFIG_PATTERNS

    def test_patterns_contain_flatpak(self) -> None:
        """Flatpak config pattern should be in the protected list."""
        assert "~/.config/flatpak" in PROTECTED_CONFIG_PATTERNS


class TestIsProtectedConfig:
    """Tests for is_protected_config function."""

    def _home(self) -> str:
        return str(Path.home())

    def test_cosmic_protected(self) -> None:
        """~/.config/cosmic* pattern matches cosmic config dirs."""
        home = self._home()
        assert is_protected_config(f"{home}/.config/cosmic") is True
        assert is_protected_config(f"{home}/.config/cosmic-comp") is True
        assert is_protected_config(f"{home}/.config/cosmic-settings") is True

    def test_dconf_protected(self) -> None:
        """~/.config/dconf should be protected."""
        path = f"{self._home()}/.config/dconf"
        assert is_protected_config(path) is True

    def test_gtk_protected(self) -> None:
        """~/.config/gtk-* pattern matches gtk config dirs."""
        home = self._home()
        assert is_protected_config(f"{home}/.config/gtk-3.0") is True
        assert is_protected_config(f"{home}/.config/gtk-4.0") is True

    def test_shell_bashrc_protected(self) -> None:
        """~/.bashrc should be protected."""
        path = f"{self._home()}/.bashrc"
        assert is_protected_config(path) is True

    def test_shell_zshrc_protected(self) -> None:
        """~/.zshrc should be protected."""
        path = f"{self._home()}/.zshrc"
        assert is_protected_config(path) is True

    def test_ssh_directory_protected(self) -> None:
        """~/.ssh should be protected (directory itself)."""
        path = f"{self._home()}/.ssh"
        assert is_protected_config(path) is True

    def test_ssh_contents_protected(self) -> None:
        """~/.ssh/id_rsa should be protected (wildcard match)."""
        path = f"{self._home()}/.ssh/id_rsa"
        assert is_protected_config(path) is True

    def test_gnupg_directory_protected(self) -> None:
        """~/.gnupg should be protected (directory itself)."""
        path = f"{self._home()}/.gnupg"
        assert is_protected_config(path) is True

    def test_gnupg_contents_protected(self) -> None:
        """~/.gnupg/pubring.kbx should be protected (wildcard match)."""
        path = f"{self._home()}/.gnupg/pubring.kbx"
        assert is_protected_config(path) is True

    def test_gpg_directory_protected(self) -> None:
        """~/.gpg should be protected (directory itself)."""
        path = f"{self._home()}/.gpg"
        assert is_protected_config(path) is True

    def test_gpg_contents_protected(self) -> None:
        """~/.gpg/keys should be protected (wildcard match)."""
        path = f"{self._home()}/.gpg/keys"
        assert is_protected_config(path) is True

    def test_popctl_protected(self) -> None:
        """~/.config/popctl should be protected."""
        path = f"{self._home()}/.config/popctl"
        assert is_protected_config(path) is True

    def test_docker_protected(self) -> None:
        """~/.config/docker should be protected."""
        path = f"{self._home()}/.config/docker"
        assert is_protected_config(path) is True

    def test_flatpak_protected(self) -> None:
        """~/.config/flatpak should be protected."""
        path = f"{self._home()}/.config/flatpak"
        assert is_protected_config(path) is True

    def test_systemd_protected(self) -> None:
        """~/.config/systemd should be protected."""
        path = f"{self._home()}/.config/systemd"
        assert is_protected_config(path) is True

    def test_autostart_protected(self) -> None:
        """~/.config/autostart should be protected."""
        path = f"{self._home()}/.config/autostart"
        assert is_protected_config(path) is True

    def test_mimeapps_protected(self) -> None:
        """~/.config/mimeapps.list should be protected."""
        path = f"{self._home()}/.config/mimeapps.list"
        assert is_protected_config(path) is True

    def test_random_config_not_protected(self) -> None:
        """~/.config/some-random-app should NOT be protected."""
        path = f"{self._home()}/.config/some-random-app"
        assert is_protected_config(path) is False

    def test_tilde_expansion(self) -> None:
        """Patterns with ~ should be expanded to the actual home directory."""
        home = self._home()
        # cosmic* pattern should match ~/.config/cosmic-settings
        path = f"{home}/.config/cosmic-settings"
        assert is_protected_config(path) is True

    def test_wildcard_matching(self) -> None:
        """Wildcard patterns should match correctly."""
        home = self._home()
        # gtk-* should match gtk-3.0 but not gtkrc
        assert is_protected_config(f"{home}/.config/gtk-3.0") is True
        assert is_protected_config(f"{home}/.config/gtk-4.0") is True
        # cosmic* should match cosmic and cosmic-anything
        assert is_protected_config(f"{home}/.config/cosmic") is True
        assert is_protected_config(f"{home}/.config/cosmic-panel") is True

    def test_with_mocked_home(self) -> None:
        """Verify ~ expansion uses Path.home() correctly with mocked home."""
        with patch.object(Path, "home", return_value=Path("/mock/home")):
            assert is_protected_config("/mock/home/.ssh/id_rsa") is True
            assert is_protected_config("/mock/home/.ssh") is True
            assert is_protected_config("/mock/home/.config/popctl") is True
            assert is_protected_config("/mock/home/.bashrc") is True
            assert is_protected_config("/mock/home/.gnupg") is True
            assert is_protected_config("/mock/home/.gpg") is True
            assert is_protected_config("/mock/home/.config/flatpak") is True
            # Original home should no longer match
            assert is_protected_config("/real/home/.ssh/id_rsa") is False
