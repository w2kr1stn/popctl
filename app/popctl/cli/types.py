"""Shared types and utilities for CLI commands.

This module provides common enums and helper functions used across
multiple CLI command modules to avoid code duplication.
"""

from enum import Enum

from popctl.scanners.apt import AptScanner
from popctl.scanners.base import Scanner
from popctl.scanners.flatpak import FlatpakScanner
from popctl.scanners.snap import SnapScanner


class SourceChoice(str, Enum):
    """Available package sources for CLI commands."""

    APT = "apt"
    FLATPAK = "flatpak"
    SNAP = "snap"
    ALL = "all"


def get_scanners(source: SourceChoice = SourceChoice.ALL) -> list[Scanner]:
    """Get scanner instances based on source selection.

    Args:
        source: The source choice (apt, flatpak, snap, or all).

    Returns:
        List of scanner instances.
    """
    scanners: list[Scanner] = []

    if source in (SourceChoice.APT, SourceChoice.ALL):
        scanners.append(AptScanner())

    if source in (SourceChoice.FLATPAK, SourceChoice.ALL):
        scanners.append(FlatpakScanner())

    if source in (SourceChoice.SNAP, SourceChoice.ALL):
        scanners.append(SnapScanner())

    return scanners


def get_available_scanners(source: SourceChoice = SourceChoice.ALL) -> list[Scanner]:
    """Get available scanner instances based on source selection.

    Only returns scanners that are available on the system.

    Args:
        source: The source choice (apt, flatpak, or all).

    Returns:
        List of available scanner instances.
    """
    return [s for s in get_scanners(source) if s.is_available()]
