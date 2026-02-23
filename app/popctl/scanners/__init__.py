"""Package scanners for different package managers.

Provides a scanner registry and factory functions for creating scanner
instances based on package source selection.
"""

from popctl.models.package import PackageSource
from popctl.scanners.apt import AptScanner
from popctl.scanners.base import Scanner
from popctl.scanners.flatpak import FlatpakScanner
from popctl.scanners.snap import SnapScanner

_SCANNER_CLASSES: dict[PackageSource, type[Scanner]] = {
    PackageSource.APT: AptScanner,
    PackageSource.FLATPAK: FlatpakScanner,
    PackageSource.SNAP: SnapScanner,
}


def get_scanners(source: PackageSource | None = None) -> list[Scanner]:
    """Get scanner instances, optionally filtered by source.

    Args:
        source: Filter to a specific package source, or None for all.

    Returns:
        List of scanner instances (not filtered by availability).
    """
    classes = _SCANNER_CLASSES if source is None else {source: _SCANNER_CLASSES[source]}
    return [cls() for cls in classes.values()]


def get_available_scanners(source: PackageSource | None = None) -> list[Scanner]:
    """Get available scanner instances (only those installed on the system).

    Args:
        source: Filter to a specific package source, or None for all.

    Returns:
        List of available scanner instances.
    """
    return [s for s in get_scanners(source) if s.is_available()]
