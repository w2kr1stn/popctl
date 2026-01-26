"""Shared types and utilities for CLI commands.

This module provides common enums and helper functions used across
multiple CLI command modules to avoid code duplication.
"""

from enum import Enum

from popctl.scanners.apt import AptScanner
from popctl.scanners.base import Scanner
from popctl.scanners.flatpak import FlatpakScanner


class SourceChoice(str, Enum):
    """Available package sources for CLI commands."""

    APT = "apt"
    FLATPAK = "flatpak"
    ALL = "all"


def get_scanners(source: SourceChoice) -> list[Scanner]:
    """Get scanner instances based on source selection.

    Args:
        source: The source choice (apt, flatpak, or all).

    Returns:
        List of scanner instances.
    """
    scanners: list[Scanner] = []

    if source in (SourceChoice.APT, SourceChoice.ALL):
        scanners.append(AptScanner())

    if source in (SourceChoice.FLATPAK, SourceChoice.ALL):
        scanners.append(FlatpakScanner())

    return scanners
