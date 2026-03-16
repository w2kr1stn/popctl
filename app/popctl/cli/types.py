from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Literal

import typer

from popctl.configs import ConfigScanner
from popctl.core import manifest as core_manifest
from popctl.core.diff import DiffResult, compute_diff
from popctl.core.manifest import ManifestError, ManifestNotFoundError, save_manifest
from popctl.core.paths import get_manifest_path
from popctl.core.state import record_domain_deletions
from popctl.domain.models import DomainActionResult, OrphanStatus, ScannedEntry
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
    "post_clean_update",
    "require_manifest",
]


class SourceChoice(str, Enum):

    APT = "apt"
    FLATPAK = "flatpak"
    SNAP = "snap"
    ALL = "all"

    def to_package_source(self) -> PackageSource | None:
        if self == SourceChoice.ALL:
            return None
        return PackageSource(self.value)

    def to_source_filter(self) -> PackageSourceType | None:
        ps = self.to_package_source()
        if ps is None:
            return None
        return ps.value  # type: ignore[return-value]


class OutputFormat(str, Enum):

    TABLE = "table"
    JSON = "json"


def get_checked_scanners(
    source: SourceChoice = SourceChoice.ALL,
    *,
    silent: bool = False,
) -> list[Scanner]:
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
    scanner: FilesystemScanner | ConfigScanner
    if domain == "filesystem":
        scanner = FilesystemScanner(include_files=include_files, include_etc=include_etc)
    else:
        scanner = ConfigScanner()

    orphans = [item for item in scanner.scan() if item.status == OrphanStatus.ORPHAN]
    orphans.sort(key=lambda e: e.confidence, reverse=True)
    return orphans


def post_clean_update(
    manifest: Manifest,
    domain: Literal["filesystem", "configs"],
    results: Sequence[DomainActionResult],
    paths_to_delete: list[str],
    *,
    command: str = "popctl",
) -> list[str]:
    successful_paths = [r.path for r in results if r.success]

    if not successful_paths:
        return []

    section = manifest.filesystem if domain == "filesystem" else manifest.configs
    if section:
        for result, original_path in zip(results, paths_to_delete, strict=True):
            if result.success:
                section.remove.pop(original_path, None)
        manifest.meta.updated = datetime.now(UTC)
        try:
            save_manifest(manifest)
        except (OSError, ManifestError) as e:
            print_warning(f"Could not update manifest after cleanup: {e}")

    try:
        record_domain_deletions(domain, successful_paths, command=command)
        print_info("Deletions recorded to history.")
    except (OSError, RuntimeError) as e:
        print_warning(f"Could not record to history: {e}")

    return successful_paths
