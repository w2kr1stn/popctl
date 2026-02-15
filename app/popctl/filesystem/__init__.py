"""Filesystem scanning and cleanup module.

This module provides filesystem orphan detection, protected path
management, and manifest models for the filesystem domain.
Scanner and operator implementations are added by subsequent tasks.
"""

from popctl.filesystem.manifest import FilesystemConfig, FilesystemEntry
from popctl.filesystem.models import OrphanReason, PathStatus, PathType, ScannedPath
from popctl.filesystem.protected import PROTECTED_PATH_PATTERNS, is_protected_path

__all__ = [
    "PROTECTED_PATH_PATTERNS",
    "FilesystemConfig",
    "FilesystemEntry",
    "OrphanReason",
    "PathStatus",
    "PathType",
    "ScannedPath",
    "is_protected_path",
]
