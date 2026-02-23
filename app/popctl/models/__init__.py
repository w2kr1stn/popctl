"""Data models for popctl.

This module exports the core data structures used throughout the application.
"""

from popctl.models.history import (
    HistoryActionType,
    HistoryEntry,
    HistoryItem,
    create_history_entry,
)
from popctl.models.manifest import (
    Manifest,
    ManifestMeta,
    PackageConfig,
    PackageEntry,
    SystemConfig,
)
from popctl.models.package import PackageSource, PackageStatus, ScannedPackage
from popctl.models.scan_result import ScanMetadata, ScanResult

__all__ = [
    "HistoryActionType",
    "HistoryEntry",
    "HistoryItem",
    "Manifest",
    "ManifestMeta",
    "PackageConfig",
    "PackageEntry",
    "PackageSource",
    "PackageStatus",
    "ScannedPackage",
    "ScanMetadata",
    "ScanResult",
    "SystemConfig",
    "create_history_entry",
]
