"""Tests for filesystem protected path patterns."""

from pathlib import Path
from unittest.mock import patch

from popctl.filesystem.protected import PROTECTED_PATH_PATTERNS, is_protected_path


class TestProtectedPathPatterns:
    """Tests for the PROTECTED_PATH_PATTERNS list."""

    def test_patterns_list_not_empty(self) -> None:
        """Protected patterns list should contain entries."""
        assert len(PROTECTED_PATH_PATTERNS) > 0

    def test_patterns_contain_ssh(self) -> None:
        """SSH patterns should be in the protected list."""
        assert "~/.ssh/*" in PROTECTED_PATH_PATTERNS

    def test_patterns_contain_etc_entries(self) -> None:
        """System /etc patterns should be in the protected list."""
        assert "/etc/fstab" in PROTECTED_PATH_PATTERNS
        assert "/etc/ssh/*" in PROTECTED_PATH_PATTERNS


class TestIsProtectedPath:
    """Tests for is_protected_path function."""

    def _home(self) -> str:
        return str(Path.home())

    def test_ssh_protected(self) -> None:
        """~/.ssh/id_rsa should be protected."""
        path = f"{self._home()}/.ssh/id_rsa"
        assert is_protected_path(path) is True

    def test_gnupg_protected(self) -> None:
        """~/.gnupg/pubring.kbx should be protected."""
        path = f"{self._home()}/.gnupg/pubring.kbx"
        assert is_protected_path(path) is True

    def test_popctl_protected(self) -> None:
        """~/.config/popctl should be protected."""
        path = f"{self._home()}/.config/popctl"
        assert is_protected_path(path) is True

    def test_random_path_not_protected(self) -> None:
        """~/.config/some-random-app should NOT be protected."""
        path = f"{self._home()}/.config/some-random-app"
        assert is_protected_path(path) is False

    def test_etc_system_files_protected(self) -> None:
        """/etc/fstab should be protected."""
        assert is_protected_path("/etc/fstab") is True

    def test_etc_ssh_protected(self) -> None:
        """/etc/ssh/sshd_config should be protected."""
        assert is_protected_path("/etc/ssh/sshd_config") is True

    def test_unprotected_etc_path(self) -> None:
        """/etc/some-random-dir should NOT be protected."""
        assert is_protected_path("/etc/some-random-dir") is False

    def test_tilde_expansion(self) -> None:
        """Patterns with ~ should be expanded to the actual home directory."""
        home = self._home()
        # cosmic* pattern should match ~/.config/cosmic-settings
        path = f"{home}/.config/cosmic-settings"
        assert is_protected_path(path) is True

    def test_cosmic_wildcard_matches(self) -> None:
        """~/.config/cosmic* pattern matches various cosmic dirs."""
        home = self._home()
        assert is_protected_path(f"{home}/.config/cosmic") is True
        assert is_protected_path(f"{home}/.config/cosmic-comp") is True
        assert is_protected_path(f"{home}/.config/cosmic-term") is True

    def test_gtk_wildcard_matches(self) -> None:
        """~/.config/gtk-* pattern matches gtk config dirs."""
        home = self._home()
        assert is_protected_path(f"{home}/.config/gtk-3.0") is True
        assert is_protected_path(f"{home}/.config/gtk-4.0") is True

    def test_etc_sudoers_wildcard(self) -> None:
        """/etc/sudoers* pattern matches sudoers and sudoers.d."""
        assert is_protected_path("/etc/sudoers") is True
        assert is_protected_path("/etc/sudoers.d") is True

    def test_local_share_popctl_protected(self) -> None:
        """~/.local/share/popctl should be protected."""
        path = f"{self._home()}/.local/share/popctl"
        assert is_protected_path(path) is True

    def test_local_state_popctl_protected(self) -> None:
        """~/.local/state/popctl should be protected."""
        path = f"{self._home()}/.local/state/popctl"
        assert is_protected_path(path) is True

    def test_keyrings_protected(self) -> None:
        """~/.local/share/keyrings should be protected."""
        path = f"{self._home()}/.local/share/keyrings"
        assert is_protected_path(path) is True

    def test_docker_config_protected(self) -> None:
        """~/.config/docker should be protected."""
        path = f"{self._home()}/.config/docker"
        assert is_protected_path(path) is True

    def test_with_mocked_home(self) -> None:
        """Verify ~ expansion uses Path.home() correctly with mocked home."""
        with patch.object(Path, "home", return_value=Path("/mock/home")):
            assert is_protected_path("/mock/home/.ssh/id_rsa") is True
            assert is_protected_path("/mock/home/.config/popctl") is True
            # Original home should no longer match
            assert is_protected_path("/real/home/.ssh/id_rsa") is False
