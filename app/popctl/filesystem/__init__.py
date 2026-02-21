"""Filesystem scanning and cleanup module.

This module provides filesystem orphan detection, protected path
management, manifest models, and deletion operations for the
filesystem domain.
"""

from popctl.domain.manifest import DomainConfig, DomainEntry
from popctl.domain.protected import PROTECTED_PATTERNS, is_protected
from popctl.filesystem.models import OrphanReason, PathStatus, PathType, ScannedPath
from popctl.filesystem.operator import FilesystemActionResult, FilesystemOperator
from popctl.filesystem.scanner import FilesystemScanner

PROTECTED_PATH_PATTERNS = PROTECTED_PATTERNS["filesystem"]


def is_protected_path(path: str) -> bool:
    """Check if a filesystem path is protected and should not be deleted."""
    return is_protected(path, "filesystem")


__all__ = [
    "PROTECTED_PATH_PATTERNS",
    "FilesystemActionResult",
    "DomainConfig",
    "DomainEntry",
    "FilesystemOperator",
    "FilesystemScanner",
    "OrphanReason",
    "PathStatus",
    "PathType",
    "ScannedPath",
    "is_protected_path",
]
