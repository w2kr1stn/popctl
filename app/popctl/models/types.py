"""Shared type definitions for package management."""

from enum import Enum


class SourceChoice(str, Enum):
    """Available package sources for CLI commands."""

    APT = "apt"
    FLATPAK = "flatpak"
    SNAP = "snap"
    ALL = "all"
