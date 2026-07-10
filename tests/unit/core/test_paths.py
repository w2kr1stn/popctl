"""Unit tests for XDG path management.

Tests for the paths module that provides XDG-compliant directory paths.
"""

import os
from pathlib import Path
from unittest.mock import patch

import pytest
from popctl.core.paths import (
    APP_NAME,
    get_config_dir,
    get_manifest_path,
    get_state_dir,
)


class TestGetConfigDir:
    """Tests for get_config_dir function."""

    def test_default_config_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_config_dir returns default path when XDG_CONFIG_HOME not set."""
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

        result = get_config_dir()

        assert result == Path(os.environ["HOME"]) / ".config" / APP_NAME

    def test_respects_xdg_config_home(self, tmp_path: Path) -> None:
        """get_config_dir respects XDG_CONFIG_HOME environment variable."""
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(tmp_path)}):
            result = get_config_dir()

        assert result == tmp_path / APP_NAME


class TestGetStateDir:
    """Tests for get_state_dir function."""

    def test_default_state_dir(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_state_dir returns default path when XDG_STATE_HOME not set."""
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)

        result = get_state_dir()

        assert result == Path(os.environ["HOME"]) / ".local" / "state" / APP_NAME

    def test_respects_xdg_state_home(self, tmp_path: Path) -> None:
        """get_state_dir respects XDG_STATE_HOME environment variable."""
        with patch.dict(os.environ, {"XDG_STATE_HOME": str(tmp_path)}):
            result = get_state_dir()

        assert result == tmp_path / APP_NAME


class TestConveniencePaths:
    """Tests for convenience path functions."""

    def test_get_manifest_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """get_manifest_path returns manifest.toml in config dir."""
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)

        result = get_manifest_path()

        assert result == Path(os.environ["HOME"]) / ".config" / APP_NAME / "manifest.toml"
