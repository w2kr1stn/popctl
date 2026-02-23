"""Pytest configuration and shared fixtures.

This module contains fixtures used across all test modules.
"""

import pytest


@pytest.fixture
def mock_dpkg_output() -> str:
    """Sample dpkg-query output for testing."""
    return """firefox\t128.0\t204800\tMozilla Firefox web browser
neovim\t0.9.5\t51200\tVim-based text editor
libgtk-3-0\t3.24.41\t10240\tGTK graphical toolkit
python3\t3.11.4\t25600\tInteractive high-level object-oriented language
curl\t8.5.0\t512\tCommand line tool for transferring data"""


@pytest.fixture
def mock_apt_mark_output() -> str:
    """Sample apt-mark showauto output for testing."""
    return """libgtk-3-0
python3"""


@pytest.fixture
def mock_empty_output() -> str:
    """Empty output for testing edge cases."""
    return ""


@pytest.fixture
def mock_malformed_output() -> str:
    """Malformed output for testing error handling."""
    return """firefox
incomplete_line\t
\t\t\t"""
