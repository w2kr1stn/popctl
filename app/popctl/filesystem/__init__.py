"""Filesystem scanning and cleanup module.

This module provides filesystem orphan detection, protected path
management, and deletion operations for the filesystem domain.
"""

from popctl.filesystem.operator import FilesystemOperator
from popctl.filesystem.scanner import FilesystemScanner

__all__ = [
    "FilesystemOperator",
    "FilesystemScanner",
]
