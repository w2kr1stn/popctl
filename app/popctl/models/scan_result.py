"""Scan result model for package scan data.

This module defines the data structure for holding scan results
used by the advisor and CLI scan commands.
"""

from dataclasses import dataclass

from popctl.models.package import ScannedPackage


@dataclass(frozen=True, slots=True)
class ScanResult:
    """Complete scan result with package data."""

    packages: tuple[ScannedPackage, ...]
