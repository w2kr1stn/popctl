"""Data models for popctl.

This module exports the core data structures used throughout the application.
"""

from popctl.models.package import PackageSource, PackageStatus, ScannedPackage
from popctl.models.scan_result import ScanMetadata, ScanResult

__all__ = [
    "PackageSource",
    "PackageStatus",
    "ScannedPackage",
    "ScanMetadata",
    "ScanResult",
]
