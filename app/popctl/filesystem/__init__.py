"""Filesystem scanning and cleanup module.

This module provides filesystem orphan detection, protected path
management, manifest models, and deletion operations for the
filesystem domain.
"""

from popctl.filesystem.manifest import FilesystemConfig, FilesystemEntry
from popctl.filesystem.models import OrphanReason, PathStatus, PathType, ScannedPath
from popctl.filesystem.operator import FilesystemActionResult, FilesystemOperator
from popctl.filesystem.protected import PROTECTED_PATH_PATTERNS, is_protected_path
from popctl.filesystem.scanner import FilesystemScanner

__all__ = [
    "PROTECTED_PATH_PATTERNS",
    "FilesystemActionResult",
    "FilesystemConfig",
    "FilesystemEntry",
    "FilesystemOperator",
    "FilesystemScanner",
    "OrphanReason",
    "PathStatus",
    "PathType",
    "ScannedPath",
    "is_protected_path",
]
