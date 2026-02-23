"""Package scanners for different package managers.

This module exports the scanner classes for querying installed packages.
"""

from popctl.scanners.apt import AptScanner
from popctl.scanners.base import Scanner

__all__ = ["AptScanner", "Scanner"]
