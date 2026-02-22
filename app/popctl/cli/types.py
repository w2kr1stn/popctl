"""Shared types and utilities for CLI commands.

This module provides common enums and helper functions used across
multiple CLI command modules to avoid code duplication.
"""

from enum import Enum
from pathlib import Path

import typer

from popctl.core import manifest as core_manifest
from popctl.core.manifest import ManifestError, ManifestNotFoundError
from popctl.core.paths import get_manifest_path
from popctl.models.manifest import Manifest, PackageSourceType
from popctl.models.package import PackageSource
from popctl.scanners.apt import AptScanner
from popctl.scanners.base import Scanner
from popctl.scanners.flatpak import FlatpakScanner
from popctl.scanners.snap import SnapScanner
from popctl.utils.formatting import print_error, print_info, print_warning

__all__ = [
    "OutputFormat",
    "SourceChoice",
    "get_available_scanners",
    "get_checked_scanners",
    "require_manifest",
]


class SourceChoice(str, Enum):
    """Available package sources for CLI commands.

    Used by Typer for CLI argument choices. The ALL variant means
    all available package sources.
    """

    APT = "apt"
    FLATPAK = "flatpak"
    SNAP = "snap"
    ALL = "all"

    def to_package_source(self) -> PackageSource | None:
        """Convert to PackageSource (None for ALL)."""
        if self == SourceChoice.ALL:
            return None
        return PackageSource(self.value)

    def to_source_filter(self) -> PackageSourceType | None:
        """Convert to PackageSourceType string literal (None for ALL).

        Returns the source value as a typed literal suitable for
        manifest filtering and compute_diff().
        """
        if self == SourceChoice.ALL:
            return None
        # Cast is safe: non-ALL values are "apt", "flatpak", "snap"
        source: PackageSourceType = self.value  # type: ignore[assignment]
        return source


class OutputFormat(str, Enum):
    """Output format options for scan commands."""

    TABLE = "table"
    JSON = "json"


_SCANNER_CLASSES: dict[PackageSource, type[Scanner]] = {
    PackageSource.APT: AptScanner,
    PackageSource.FLATPAK: FlatpakScanner,
    PackageSource.SNAP: SnapScanner,
}


def _get_scanners(source: SourceChoice = SourceChoice.ALL) -> list[Scanner]:
    """Get scanner instances based on source selection.

    Args:
        source: The source choice (apt, flatpak, snap, or all).

    Returns:
        List of scanner instances.
    """
    pkg_source = source.to_package_source()
    classes = _SCANNER_CLASSES if pkg_source is None else {pkg_source: _SCANNER_CLASSES[pkg_source]}
    return [cls() for cls in classes.values()]


def get_available_scanners(source: SourceChoice = SourceChoice.ALL) -> list[Scanner]:
    """Get available scanner instances based on source selection.

    Only returns scanners that are available on the system.

    Args:
        source: The source choice (apt, flatpak, or all).

    Returns:
        List of available scanner instances.
    """
    return [s for s in _get_scanners(source) if s.is_available()]


def get_checked_scanners(
    source: SourceChoice = SourceChoice.ALL,
    *,
    silent: bool = False,
) -> list[Scanner]:
    """Get available scanners, warning about unavailable ones.

    Prints a warning for each unavailable scanner and exits
    if no scanners are available at all.

    Args:
        source: The source choice (apt, flatpak, snap, or all).
        silent: If True, suppress warnings about unavailable scanners.

    Returns:
        List of available scanner instances.

    Raises:
        typer.Exit: If no package managers are available.
    """
    scanners = _get_scanners(source)
    available: list[Scanner] = []

    for scanner in scanners:
        if scanner.is_available():
            available.append(scanner)
        elif not silent:
            print_warning(f"{scanner.source.value.upper()} package manager is not available.")

    if not available:
        print_error("No package managers are available on this system.")
        raise typer.Exit(code=1)

    return available


def require_manifest(manifest_path: Path | None = None) -> Manifest:
    """Load manifest or exit with helpful error message.

    This is a convenience wrapper around load_manifest() that handles
    common error cases by printing user-friendly messages and exiting.

    Args:
        manifest_path: Optional custom manifest path.

    Returns:
        Loaded and validated Manifest.

    Raises:
        typer.Exit: If manifest cannot be loaded.
    """
    path = manifest_path or get_manifest_path()
    try:
        return core_manifest.load_manifest(path)
    except ManifestNotFoundError as e:
        print_error(f"Manifest not found: {path}")
        print_info("Run 'popctl init' to create a manifest from your current system.")
        raise typer.Exit(code=1) from e
    except ManifestError as e:
        print_error(f"Failed to load manifest: {e}")
        raise typer.Exit(code=1) from e
