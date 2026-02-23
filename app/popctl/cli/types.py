"""Shared types and utilities for CLI commands.

This module provides common enums and helper functions used across
multiple CLI command modules to avoid code duplication.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Literal

import typer

from popctl.configs import ConfigScanner
from popctl.core import manifest as core_manifest
from popctl.core.diff import DiffResult, compute_diff
from popctl.core.manifest import ManifestError, ManifestNotFoundError
from popctl.core.paths import get_manifest_path
from popctl.domain.models import OrphanStatus, ScannedEntry
from popctl.filesystem import FilesystemScanner
from popctl.models.manifest import Manifest, PackageSourceType
from popctl.models.package import PackageSource
from popctl.scanners import get_scanners
from popctl.scanners.base import Scanner
from popctl.utils.formatting import print_error, print_info, print_warning

__all__ = [
    "OutputFormat",
    "SourceChoice",
    "collect_domain_orphans",
    "compute_system_diff",
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
        """Convert to PackageSourceType for diff filtering.

        Returns:
            The source as a PackageSourceType literal, or None for ALL.
        """
        ps = self.to_package_source()
        if ps is None:
            return None
        return ps.value  # type: ignore[return-value]


class OutputFormat(str, Enum):
    """Output format options for scan commands."""

    TABLE = "table"
    JSON = "json"


class ProviderChoice(str, Enum):
    """Available AI providers."""

    CLAUDE = "claude"
    GEMINI = "gemini"


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
    scanners = get_scanners(source.to_package_source())
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


def compute_system_diff(source: SourceChoice, *, silent_warnings: bool = False) -> DiffResult:
    """Load manifest, scan system, and compute diff.

    Convenience helper that combines require_manifest, get_checked_scanners,
    and compute_diff into a single call. Exits on failure.

    Args:
        source: Package source filter.
        silent_warnings: If True, suppress scanner unavailability warnings.

    Returns:
        Diff result with NEW, MISSING, and EXTRA entries.

    Raises:
        typer.Exit: If manifest or scan fails.
    """
    manifest = require_manifest()
    scanners = get_checked_scanners(source, silent=silent_warnings)
    try:
        return compute_diff(manifest, scanners, source.to_source_filter())
    except RuntimeError as e:
        print_error(f"Scan failed: {e}")
        raise typer.Exit(code=1) from e


def collect_domain_orphans(
    domain: Literal["filesystem", "configs"],
    *,
    include_files: bool = False,
    include_etc: bool = False,
) -> list[ScannedEntry]:
    """Scan a domain and return orphan entries sorted by confidence (desc).

    Args:
        domain: Which domain to scan ("filesystem" or "configs").
        include_files: Include individual stale files (filesystem only).
        include_etc: Include /etc in scan targets (filesystem only).

    Returns:
        List of orphan entries sorted by confidence descending.

    Raises:
        OSError: If the scan encounters filesystem errors.
        RuntimeError: If the scanner fails.
    """
    scanner: FilesystemScanner | ConfigScanner
    if domain == "filesystem":
        scanner = FilesystemScanner(include_files=include_files, include_etc=include_etc)
    else:
        scanner = ConfigScanner()

    orphans = [item for item in scanner.scan() if item.status == OrphanStatus.ORPHAN]
    orphans.sort(key=lambda e: e.confidence, reverse=True)
    return orphans
