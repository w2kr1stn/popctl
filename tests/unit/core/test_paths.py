"""Unit tests for XDG path management.

Tests for the paths module that provides XDG-compliant directory paths.
"""

import os
from pathlib import Path
from unittest.mock import patch

from popctl.core.paths import (
    APP_NAME,
    ensure_cache_dir,
    ensure_config_dir,
    ensure_dirs,
    ensure_exchange_dir,
    ensure_state_dir,
    get_advisor_config_path,
    get_cache_dir,
    get_config_dir,
    get_exchange_dir,
    get_history_path,
    get_last_scan_path,
    get_manifest_path,
    get_state_dir,
)


class TestGetConfigDir:
    """Tests for get_config_dir function."""

    def test_default_config_dir(self) -> None:
        """get_config_dir returns default path when XDG_CONFIG_HOME not set."""
        with patch.dict(os.environ, {}, clear=True):
            # Remove XDG_CONFIG_HOME if present
            os.environ.pop("XDG_CONFIG_HOME", None)

            result = get_config_dir()

        assert result == Path.home() / ".config" / APP_NAME

    def test_respects_xdg_config_home(self, tmp_path: Path) -> None:
        """get_config_dir respects XDG_CONFIG_HOME environment variable."""
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(tmp_path)}):
            result = get_config_dir()

        assert result == tmp_path / APP_NAME


class TestGetStateDir:
    """Tests for get_state_dir function."""

    def test_default_state_dir(self) -> None:
        """get_state_dir returns default path when XDG_STATE_HOME not set."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("XDG_STATE_HOME", None)

            result = get_state_dir()

        assert result == Path.home() / ".local" / "state" / APP_NAME

    def test_respects_xdg_state_home(self, tmp_path: Path) -> None:
        """get_state_dir respects XDG_STATE_HOME environment variable."""
        with patch.dict(os.environ, {"XDG_STATE_HOME": str(tmp_path)}):
            result = get_state_dir()

        assert result == tmp_path / APP_NAME


class TestGetCacheDir:
    """Tests for get_cache_dir function."""

    def test_default_cache_dir(self) -> None:
        """get_cache_dir returns default path when XDG_CACHE_HOME not set."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("XDG_CACHE_HOME", None)

            result = get_cache_dir()

        assert result == Path.home() / ".cache" / APP_NAME

    def test_respects_xdg_cache_home(self, tmp_path: Path) -> None:
        """get_cache_dir respects XDG_CACHE_HOME environment variable."""
        with patch.dict(os.environ, {"XDG_CACHE_HOME": str(tmp_path)}):
            result = get_cache_dir()

        assert result == tmp_path / APP_NAME


class TestConveniencePaths:
    """Tests for convenience path functions."""

    def test_get_manifest_path(self) -> None:
        """get_manifest_path returns manifest.toml in config dir."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("XDG_CONFIG_HOME", None)

            result = get_manifest_path()

        assert result == Path.home() / ".config" / APP_NAME / "manifest.toml"

    def test_get_history_path(self) -> None:
        """get_history_path returns history.jsonl in state dir."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("XDG_STATE_HOME", None)

            result = get_history_path()

        assert result == Path.home() / ".local" / "state" / APP_NAME / "history.jsonl"

    def test_get_last_scan_path(self) -> None:
        """get_last_scan_path returns last-scan.json in state dir."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("XDG_STATE_HOME", None)

            result = get_last_scan_path()

        assert result == Path.home() / ".local" / "state" / APP_NAME / "last-scan.json"


class TestEnsureDirs:
    """Tests for directory creation functions."""

    def test_ensure_config_dir_creates_directory(self, tmp_path: Path) -> None:
        """ensure_config_dir creates the config directory."""
        config_dir = tmp_path / "config" / APP_NAME

        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(tmp_path / "config")}):
            result = ensure_config_dir()

        assert result == config_dir
        assert config_dir.exists()
        assert config_dir.is_dir()

    def test_ensure_config_dir_idempotent(self, tmp_path: Path) -> None:
        """ensure_config_dir is idempotent (can be called multiple times)."""
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(tmp_path / "config")}):
            result1 = ensure_config_dir()
            result2 = ensure_config_dir()

        assert result1 == result2
        assert result1.exists()

    def test_ensure_state_dir_creates_directory(self, tmp_path: Path) -> None:
        """ensure_state_dir creates the state directory."""
        state_dir = tmp_path / "state" / APP_NAME

        with patch.dict(os.environ, {"XDG_STATE_HOME": str(tmp_path / "state")}):
            result = ensure_state_dir()

        assert result == state_dir
        assert state_dir.exists()

    def test_ensure_cache_dir_creates_directory(self, tmp_path: Path) -> None:
        """ensure_cache_dir creates the cache directory."""
        cache_dir = tmp_path / "cache" / APP_NAME

        with patch.dict(os.environ, {"XDG_CACHE_HOME": str(tmp_path / "cache")}):
            result = ensure_cache_dir()

        assert result == cache_dir
        assert cache_dir.exists()

    def test_ensure_dirs_creates_all_directories(self, tmp_path: Path) -> None:
        """ensure_dirs creates config, state, and cache directories."""
        with patch.dict(
            os.environ,
            {
                "XDG_CONFIG_HOME": str(tmp_path / "config"),
                "XDG_STATE_HOME": str(tmp_path / "state"),
                "XDG_CACHE_HOME": str(tmp_path / "cache"),
            },
        ):
            ensure_dirs()

        assert (tmp_path / "config" / APP_NAME).exists()
        assert (tmp_path / "state" / APP_NAME).exists()
        assert (tmp_path / "cache" / APP_NAME).exists()


class TestAdvisorPaths:
    """Tests for advisor-related path functions."""

    def test_get_exchange_dir_returns_fixed_path(self) -> None:
        """get_exchange_dir returns fixed /tmp path."""
        result = get_exchange_dir()

        assert result == Path("/tmp/popctl-exchange")

    def test_get_advisor_config_path(self) -> None:
        """get_advisor_config_path returns advisor.toml in config dir."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("XDG_CONFIG_HOME", None)

            result = get_advisor_config_path()

        assert result == Path.home() / ".config" / APP_NAME / "advisor.toml"

    def test_get_advisor_config_path_respects_xdg(self, tmp_path: Path) -> None:
        """get_advisor_config_path respects XDG_CONFIG_HOME."""
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(tmp_path)}):
            result = get_advisor_config_path()

        assert result == tmp_path / APP_NAME / "advisor.toml"

    def test_ensure_exchange_dir_creates_directory(self, tmp_path: Path) -> None:
        """ensure_exchange_dir creates the exchange directory."""
        test_dir = tmp_path / "popctl-exchange"

        with (
            patch("popctl.core.paths.EXCHANGE_DIR", test_dir),
            patch("popctl.core.paths.get_exchange_dir", return_value=test_dir),
        ):
            result = ensure_exchange_dir()

        assert result == test_dir
        assert test_dir.exists()
        assert test_dir.is_dir()

    def test_ensure_exchange_dir_idempotent(self, tmp_path: Path) -> None:
        """ensure_exchange_dir can be called multiple times safely."""
        test_dir = tmp_path / "popctl-exchange"

        with (
            patch("popctl.core.paths.EXCHANGE_DIR", test_dir),
            patch("popctl.core.paths.get_exchange_dir", return_value=test_dir),
        ):
            result1 = ensure_exchange_dir()
            result2 = ensure_exchange_dir()

        assert result1 == result2
        assert test_dir.exists()
