"""Unit tests for advisor-specific path management.

Tests for the advisor paths module that provides exchange directories,
advisor configuration, session storage, and memory file paths.
"""

import os
from pathlib import Path
from unittest.mock import patch

from popctl.advisor.paths import (
    ensure_advisor_memory_dir,
    ensure_advisor_sessions_dir,
    get_advisor_config_path,
    get_advisor_memory_path,
    get_advisor_sessions_dir,
    get_exchange_dir,
)
from popctl.core.paths import APP_NAME


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

    def test_get_advisor_sessions_dir(self) -> None:
        """get_advisor_sessions_dir returns sessions dir under state."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("XDG_STATE_HOME", None)

            result = get_advisor_sessions_dir()

        assert result == Path.home() / ".local" / "state" / APP_NAME / "advisor-sessions"

    def test_get_advisor_sessions_dir_respects_xdg(self, tmp_path: Path) -> None:
        """get_advisor_sessions_dir respects XDG_STATE_HOME."""
        with patch.dict(os.environ, {"XDG_STATE_HOME": str(tmp_path)}):
            result = get_advisor_sessions_dir()

        assert result == tmp_path / APP_NAME / "advisor-sessions"

    def test_ensure_advisor_sessions_dir_creates_directory(self, tmp_path: Path) -> None:
        """ensure_advisor_sessions_dir creates the sessions directory."""
        with patch.dict(os.environ, {"XDG_STATE_HOME": str(tmp_path / "state")}):
            result = ensure_advisor_sessions_dir()

        assert result == tmp_path / "state" / APP_NAME / "advisor-sessions"
        assert result.exists()
        assert result.is_dir()

    def test_ensure_advisor_sessions_dir_idempotent(self, tmp_path: Path) -> None:
        """ensure_advisor_sessions_dir can be called multiple times safely."""
        with patch.dict(os.environ, {"XDG_STATE_HOME": str(tmp_path / "state")}):
            result1 = ensure_advisor_sessions_dir()
            result2 = ensure_advisor_sessions_dir()

        assert result1 == result2
        assert result1.exists()

    def test_get_advisor_memory_path(self) -> None:
        """get_advisor_memory_path returns memory.md under state/advisor."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("XDG_STATE_HOME", None)

            result = get_advisor_memory_path()

        assert result == Path.home() / ".local" / "state" / APP_NAME / "advisor" / "memory.md"

    def test_get_advisor_memory_path_respects_xdg(self, tmp_path: Path) -> None:
        """get_advisor_memory_path respects XDG_STATE_HOME."""
        with patch.dict(os.environ, {"XDG_STATE_HOME": str(tmp_path)}):
            result = get_advisor_memory_path()

        assert result == tmp_path / APP_NAME / "advisor" / "memory.md"

    def test_ensure_advisor_memory_dir_creates_directory(self, tmp_path: Path) -> None:
        """ensure_advisor_memory_dir creates the advisor directory."""
        with patch.dict(os.environ, {"XDG_STATE_HOME": str(tmp_path / "state")}):
            result = ensure_advisor_memory_dir()

        assert result == tmp_path / "state" / APP_NAME / "advisor"
        assert result.exists()
        assert result.is_dir()

    def test_ensure_advisor_memory_dir_idempotent(self, tmp_path: Path) -> None:
        """ensure_advisor_memory_dir can be called multiple times safely."""
        with patch.dict(os.environ, {"XDG_STATE_HOME": str(tmp_path / "state")}):
            result1 = ensure_advisor_memory_dir()
            result2 = ensure_advisor_memory_dir()

        assert result1 == result2
        assert result1.exists()
