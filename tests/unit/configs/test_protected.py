"""Tests for config protected path patterns."""

from pathlib import Path
from unittest.mock import patch

from popctl.configs.protected import PROTECTED_CONFIG_PATTERNS, is_protected_config


class TestProtectedConfigPatterns:
    """Tests for the PROTECTED_CONFIG_PATTERNS tuple."""

    def test_patterns_not_empty(self) -> None:
        """Protected patterns tuple should contain entries."""
        assert len(PROTECTED_CONFIG_PATTERNS) > 0

    def test_patterns_contain_shell_configs(self) -> None:
        """Shell config patterns should be in the protected list."""
        assert "~/.bashrc" in PROTECTED_CONFIG_PATTERNS
        assert "~/.zshrc" in PROTECTED_CONFIG_PATTERNS
        assert "~/.profile" in PROTECTED_CONFIG_PATTERNS
        assert "~/.bash_profile" in PROTECTED_CONFIG_PATTERNS
        assert "~/.zprofile" in PROTECTED_CONFIG_PATTERNS

    def test_patterns_contain_ssh(self) -> None:
        """SSH patterns should be in the protected list."""
        assert "~/.ssh/*" in PROTECTED_CONFIG_PATTERNS


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

    def test_ssh_protected(self) -> None:
        """~/.ssh/id_rsa should be protected."""
        path = f"{self._home()}/.ssh/id_rsa"
        assert is_protected_config(path) is True

    def test_gnupg_protected(self) -> None:
        """~/.gnupg/pubring.kbx should be protected."""
        path = f"{self._home()}/.gnupg/pubring.kbx"
        assert is_protected_config(path) is True

    def test_popctl_protected(self) -> None:
        """~/.config/popctl should be protected."""
        path = f"{self._home()}/.config/popctl"
        assert is_protected_config(path) is True

    def test_nvim_protected(self) -> None:
        """~/.config/nvim should be protected."""
        path = f"{self._home()}/.config/nvim"
        assert is_protected_config(path) is True

    def test_vimrc_protected(self) -> None:
        """~/.vimrc should be protected."""
        path = f"{self._home()}/.vimrc"
        assert is_protected_config(path) is True

    def test_gitconfig_protected(self) -> None:
        """~/.gitconfig should be protected."""
        path = f"{self._home()}/.gitconfig"
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

    def test_with_mocked_home(self) -> None:
        """Verify ~ expansion uses Path.home() correctly with mocked home."""
        with patch.object(Path, "home", return_value=Path("/mock/home")):
            assert is_protected_config("/mock/home/.ssh/id_rsa") is True
            assert is_protected_config("/mock/home/.config/popctl") is True
            assert is_protected_config("/mock/home/.bashrc") is True
            # Original home should no longer match
            assert is_protected_config("/real/home/.ssh/id_rsa") is False
