"""File exchange for AI-assisted package classification.

This module handles the file-based communication protocol between popctl
and AI advisors (Claude Code / Gemini CLI). It provides functions to:

- Export scan data to scan.json for the AI agent
- Export prompt files (prompt.txt)
- Import and validate decisions.toml from the AI agent
- Clean up exchange directory after processing

File Exchange Protocol:
    popctl writes:
      - /tmp/popctl-exchange/scan.json       (package data)
      - /tmp/popctl-exchange/prompt.txt      (headless mode prompt)

    AI agent writes:
      - /tmp/popctl-exchange/decisions.toml  (classification results)
"""

from __future__ import annotations

import json
import socket
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, field_validator

from popctl.advisor.prompts import (
    CATEGORIES,
    build_headless_prompt,
    get_prompt_file_path,
)
from popctl.core.paths import get_exchange_dir

if TYPE_CHECKING:
    from popctl.models.scan_result import ScanResult

# Type alias for package source keys in decisions
PackageSourceKey = Literal["apt", "flatpak", "snap"]


# =============================================================================
# Export Models
# =============================================================================


class PackageScanEntry(BaseModel):
    """Single package entry in scan export.

    Represents a package in the scan.json export format, containing
    the essential information needed for AI classification.

    Attributes:
        name: Package name (e.g., "firefox", "com.spotify.Client").
        source: Package source ("apt" or "flatpak").
        version: Installed version string.
        status: Installation status ("manual" or "auto").
        description: Human-readable package description.
        size_bytes: Installed size in bytes.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    source: str  # "apt" | "flatpak"
    version: str
    status: str  # "manual" | "auto"
    description: str | None = None
    size_bytes: int | None = None


class FilesystemOrphanEntry(BaseModel):
    """Single filesystem orphan entry in scan export.

    Represents an orphaned filesystem path in the scan.json export format,
    containing the information needed for AI classification.

    Attributes:
        path: Filesystem path (tilde-prefixed, e.g., "~/.config/vlc").
        path_type: Type of path ("directory", "file", "symlink", "dead_symlink").
        size_bytes: Size in bytes (None if unavailable).
        mtime: Last modification time in ISO 8601 format (None if unavailable).
        parent_target: Scan target root directory (e.g., "~/.config").
        orphan_reason: Reason for orphan classification.
        confidence: Orphan confidence score (0.0 to 1.0).
    """

    model_config = ConfigDict(frozen=True)

    path: str
    path_type: str  # "directory", "file", "symlink", "dead_symlink"
    size_bytes: int | None = None
    mtime: str | None = None
    parent_target: str
    orphan_reason: str
    confidence: float


class FilesystemScanSection(BaseModel):
    """Filesystem section in scan.json export.

    Attributes:
        orphans: List of orphaned filesystem entries.
    """

    model_config = ConfigDict(frozen=True)

    orphans: list[FilesystemOrphanEntry] = Field(default_factory=lambda: [])


class ScanExport(BaseModel):
    """Complete scan export for AI agent.

    This model defines the structure of scan.json that is written to
    the exchange directory for the AI agent to read.

    Attributes:
        scan_date: ISO format timestamp of the scan.
        system: System information (hostname, os, manifest_path).
        summary: Package count summary.
        packages: Packages grouped by classification status.
        filesystem: Optional filesystem orphan data for classification.
    """

    model_config = ConfigDict(frozen=True)

    scan_date: str  # ISO format
    system: dict[str, str]  # hostname, os, manifest_path
    summary: dict[str, int]  # total_packages, manual_apt, etc.
    packages: dict[str, list[PackageScanEntry]]  # "unknown", "new_since_manifest"
    filesystem: FilesystemScanSection | None = None


# =============================================================================
# Import Models (decisions.toml)
# =============================================================================


class PackageDecision(BaseModel):
    """Single package classification decision from AI agent.

    Represents one package's classification in the decisions.toml file.

    Attributes:
        name: Package name.
        reason: Explanation for the classification.
        confidence: Classification confidence (0.0 - 1.0).
        category: Package category from CATEGORIES.
    """

    model_config = ConfigDict(frozen=True)

    name: str
    reason: str
    confidence: float = Field(ge=0.0, le=1.0)
    category: str

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: str) -> str:
        """Validate that category is in CATEGORIES."""
        if v not in CATEGORIES:
            valid = ", ".join(CATEGORIES)
            msg = f"Invalid category '{v}'. Must be one of: {valid}"
            raise ValueError(msg)
        return v


class SourceDecisions(BaseModel):
    """Decisions for one package source (apt or flatpak).

    Groups package decisions by classification: keep, remove, or ask.

    Attributes:
        keep: Packages that should be kept.
        remove: Packages that should be removed.
        ask: Packages that need user decision.
    """

    model_config = ConfigDict(frozen=True)

    keep: list[PackageDecision] = Field(default_factory=lambda: [])
    remove: list[PackageDecision] = Field(default_factory=lambda: [])
    ask: list[PackageDecision] = Field(default_factory=lambda: [])


class FilesystemPathDecision(BaseModel):
    """Single filesystem path decision from AI agent.

    Represents one path's classification in the decisions.toml file.

    Attributes:
        path: Filesystem path (tilde-prefixed, e.g., "~/.config/vlc").
        reason: Explanation for the classification.
        confidence: Classification confidence (0.0 - 1.0).
        category: Path category (e.g., "config", "obsolete", "other").
    """

    model_config = ConfigDict(frozen=True)

    path: str
    reason: str
    confidence: float = Field(ge=0.0, le=1.0)
    category: str


class FilesystemDecisions(BaseModel):
    """Filesystem decisions from AI agent.

    Groups filesystem path decisions by classification: keep, remove, or ask.

    Attributes:
        keep: Paths that should be kept.
        remove: Paths that should be removed.
        ask: Paths that need user decision.
    """

    model_config = ConfigDict(frozen=True)

    keep: list[FilesystemPathDecision] = Field(default_factory=lambda: [])
    remove: list[FilesystemPathDecision] = Field(default_factory=lambda: [])
    ask: list[FilesystemPathDecision] = Field(default_factory=lambda: [])


class DecisionsResult(BaseModel):
    """Complete decisions from AI agent.

    Root model for parsing decisions.toml from the AI agent.

    Attributes:
        packages: Decisions organized by package source.
        filesystem: Optional filesystem path decisions.
    """

    model_config = ConfigDict(frozen=True)

    packages: dict[PackageSourceKey, SourceDecisions]
    filesystem: FilesystemDecisions | None = None


# =============================================================================
# Export Functions
# =============================================================================


def export_scan_for_advisor(
    scan_result: ScanResult,
    exchange_dir: Path,
    manifest_path: Path | None = None,
    filesystem_orphans: list[FilesystemOrphanEntry] | None = None,
) -> Path:
    """Export scan results to exchange directory for AI agent.

    Creates a scan.json file in the exchange directory containing
    package data and optional filesystem orphan data for the AI
    agent to classify.

    Args:
        scan_result: Scan result from popctl scan command.
        exchange_dir: Directory for file exchange with AI agent.
        manifest_path: Optional path to manifest file for reference.
        filesystem_orphans: Optional list of filesystem orphan entries.

    Returns:
        Path to the created scan.json file.

    Raises:
        RuntimeError: If the file cannot be written.

    Example:
        >>> from popctl.models.scan_result import ScanResult
        >>> from popctl.core.paths import ensure_exchange_dir
        >>> scan = ScanResult.create(packages, ["apt", "flatpak"])
        >>> path = export_scan_for_advisor(scan, ensure_exchange_dir())
    """
    # Ensure exchange directory exists
    exchange_dir.mkdir(parents=True, exist_ok=True)

    # Build package entries grouped by status/need
    packages_by_group: dict[str, list[PackageScanEntry]] = {
        "unknown": [],
        "new_since_manifest": [],
    }

    # Convert ScannedPackages to PackageScanEntry
    for pkg in scan_result.packages:
        entry = PackageScanEntry(
            name=pkg.name,
            source=pkg.source.value,
            version=pkg.version,
            status=pkg.status.value,
            description=pkg.description,
            size_bytes=pkg.size_bytes,
        )
        # All scanned packages go to "unknown" for classification
        packages_by_group["unknown"].append(entry)

    # Build summary
    summary: dict[str, int] = {
        "total_packages": len(scan_result.packages),
        "manual_apt": 0,
        "auto_apt": 0,
        "flatpak": 0,
        "snap": 0,
        "unknown": len(packages_by_group["unknown"]),
    }

    for pkg in scan_result.packages:
        if pkg.source.value == "apt":
            if pkg.is_manual:
                summary["manual_apt"] += 1
            else:
                summary["auto_apt"] += 1
        elif pkg.source.value == "flatpak":
            summary["flatpak"] += 1
        elif pkg.source.value == "snap":
            summary["snap"] += 1

    # Build system info
    system_info: dict[str, str] = {
        "hostname": socket.gethostname(),
        "os": "Pop!_OS 24.04 LTS",
    }
    if manifest_path:
        system_info["manifest_path"] = str(manifest_path)

    # Build filesystem section if orphans provided
    filesystem_section: FilesystemScanSection | None = None
    if filesystem_orphans:
        filesystem_section = FilesystemScanSection(orphans=filesystem_orphans)

    # Create export model
    scan_export = ScanExport(
        scan_date=datetime.now(UTC).isoformat(),
        system=system_info,
        summary=summary,
        packages={
            "unknown": packages_by_group["unknown"],
            "new_since_manifest": packages_by_group["new_since_manifest"],
        },
        filesystem=filesystem_section,
    )

    # Write to file
    scan_json_path = exchange_dir / "scan.json"
    try:
        with scan_json_path.open("w", encoding="utf-8") as f:
            json.dump(
                scan_export.model_dump(),
                f,
                indent=2,
                ensure_ascii=False,
            )
    except OSError as e:
        msg = f"Failed to write scan.json to {scan_json_path}: {e}"
        raise RuntimeError(msg) from e

    return scan_json_path


def export_prompt_files(
    exchange_dir: Path,
    manifest_path: Path | None = None,
) -> Path:
    """Export prompt file to exchange directory.

    Creates prompt.txt for the AI agent in headless mode.

    Args:
        exchange_dir: Directory for file exchange with AI agent.
        manifest_path: Optional path to manifest file for reference.

    Returns:
        Path to the created prompt.txt file.

    Raises:
        RuntimeError: If file cannot be written.
    """
    # Ensure exchange directory exists
    exchange_dir.mkdir(parents=True, exist_ok=True)

    # Standard file paths
    scan_json_path = str(exchange_dir / "scan.json")
    decisions_path = str(exchange_dir / "decisions.toml")

    # Build system info for prompt
    system_info: dict[str, str] = {
        "hostname": socket.gethostname(),
        "os": "Pop!_OS 24.04 LTS",
    }

    # Create headless prompt
    prompt_content = build_headless_prompt(
        scan_json_path=scan_json_path,
        decisions_output_path=decisions_path,
        system_info=system_info,
    )

    # Write prompt.txt
    prompt_path = get_prompt_file_path(exchange_dir)
    try:
        prompt_path.write_text(prompt_content, encoding="utf-8")
    except OSError as e:
        msg = f"Failed to write prompt.txt to {prompt_path}: {e}"
        raise RuntimeError(msg) from e

    return prompt_path


# =============================================================================
# Import Functions
# =============================================================================


def import_decisions(exchange_dir: Path) -> DecisionsResult:
    """Import and validate decisions.toml from exchange directory.

    Reads the decisions.toml file created by the AI agent and validates
    it against the expected schema using Pydantic.

    Args:
        exchange_dir: Directory containing the decisions.toml file.

    Returns:
        Validated DecisionsResult containing all package decisions.

    Raises:
        FileNotFoundError: If decisions.toml doesn't exist.
        ValueError: If TOML is invalid or doesn't match schema.

    Example:
        >>> from popctl.core.paths import get_exchange_dir
        >>> decisions = import_decisions(get_exchange_dir())
        >>> for pkg in decisions.packages["apt"].keep:
        ...     print(f"Keep: {pkg.name} ({pkg.reason})")
    """
    decisions_path = exchange_dir / "decisions.toml"

    # Check if file exists
    if not decisions_path.exists():
        msg = f"decisions.toml not found at {decisions_path}"
        raise FileNotFoundError(msg)

    # Read and parse TOML
    try:
        content = decisions_path.read_text(encoding="utf-8")
        data = tomllib.loads(content)
    except tomllib.TOMLDecodeError as e:
        msg = f"Invalid TOML in decisions.toml: {e}"
        raise ValueError(msg) from e
    except OSError as e:
        msg = f"Failed to read decisions.toml: {e}"
        raise ValueError(msg) from e

    # Validate and convert to model
    try:
        return _parse_decisions_data(data)
    except (ValueError, KeyError, TypeError) as e:
        msg = f"Invalid decisions.toml schema: {e}"
        raise ValueError(msg) from e


def _parse_decisions_data(data: dict[str, Any]) -> DecisionsResult:
    """Parse and validate decisions data from TOML.

    Internal function to convert raw TOML data to DecisionsResult model.

    Args:
        data: Parsed TOML data dictionary.

    Returns:
        Validated DecisionsResult.

    Raises:
        ValueError: If data doesn't match expected schema.
    """
    # Extract packages section
    packages_data: Any = data.get("packages", {})

    if not isinstance(packages_data, dict):
        msg = "decisions.toml must have a 'packages' section"
        raise ValueError(msg)

    # Type-narrow packages_data to dict[str, Any] - cast needed for pyright
    packages_dict: dict[str, Any] = cast(dict[str, Any], packages_data)

    # Parse each source's decisions
    parsed_packages: dict[str, SourceDecisions] = {}

    for source in ("apt", "flatpak", "snap"):
        source_data: Any = packages_dict.get(source, {})

        if not isinstance(source_data, dict):
            # Empty or missing source - use defaults
            parsed_packages[source] = SourceDecisions()
            continue

        # Type-narrow source_data - cast for pyright
        source_dict: dict[str, Any] = cast(dict[str, Any], source_data)

        # Parse each classification list
        keep_raw: Any = source_dict.get("keep", [])
        remove_raw: Any = source_dict.get("remove", [])
        ask_raw: Any = source_dict.get("ask", [])

        keep_list = _parse_decision_list(keep_raw)
        remove_list = _parse_decision_list(remove_raw)
        ask_list = _parse_decision_list(ask_raw)

        parsed_packages[source] = SourceDecisions(
            keep=keep_list,
            remove=remove_list,
            ask=ask_list,
        )

    # Parse filesystem decisions (optional, for backward compatibility)
    filesystem_decisions: FilesystemDecisions | None = None
    fs_data: Any = data.get("filesystem")
    if isinstance(fs_data, dict):
        fs_dict: dict[str, Any] = cast(dict[str, Any], fs_data)
        filesystem_decisions = FilesystemDecisions(
            keep=_parse_fs_decision_list(fs_dict.get("keep", [])),
            remove=_parse_fs_decision_list(fs_dict.get("remove", [])),
            ask=_parse_fs_decision_list(fs_dict.get("ask", [])),
        )

    return DecisionsResult(
        packages=cast(dict[PackageSourceKey, SourceDecisions], parsed_packages),
        filesystem=filesystem_decisions,
    )


def _parse_decision_list(items: Any) -> list[PackageDecision]:
    """Parse a list of package decisions.

    Args:
        items: List of decision dictionaries from TOML.

    Returns:
        List of validated PackageDecision objects.

    Raises:
        ValueError: If any decision is invalid.
    """
    if not isinstance(items, list):
        return []

    decisions: list[PackageDecision] = []
    items_list: list[Any] = cast(list[Any], items)

    for item in items_list:
        if not isinstance(item, dict):
            continue

        # Type-narrow item - cast for pyright
        item_dict: dict[str, Any] = cast(dict[str, Any], item)

        # Pydantic will validate the fields
        decision = PackageDecision(
            name=str(item_dict.get("name", "")),
            reason=str(item_dict.get("reason", "")),
            confidence=float(item_dict.get("confidence", 0.0)),
            category=str(item_dict.get("category", "other")),
        )
        decisions.append(decision)

    return decisions


def _parse_fs_decision_list(items: Any) -> list[FilesystemPathDecision]:
    """Parse a list of filesystem path decisions.

    Args:
        items: List of decision dictionaries from TOML.

    Returns:
        List of validated FilesystemPathDecision objects.
    """
    if not isinstance(items, list):
        return []

    decisions: list[FilesystemPathDecision] = []
    items_list: list[Any] = cast(list[Any], items)

    for item in items_list:
        if not isinstance(item, dict):
            continue

        # Type-narrow item - cast for pyright
        item_dict: dict[str, Any] = cast(dict[str, Any], item)

        decision = FilesystemPathDecision(
            path=str(item_dict.get("path", "")),
            reason=str(item_dict.get("reason", "")),
            confidence=float(item_dict.get("confidence", 0.0)),
            category=str(item_dict.get("category", "other")),
        )
        decisions.append(decision)

    return decisions


# =============================================================================
# Cleanup Functions
# =============================================================================


def cleanup_exchange_dir(exchange_dir: Path) -> None:
    """Remove all files from exchange directory.

    Cleans up the exchange directory after processing is complete.
    Only removes known exchange files, not the directory itself.

    Args:
        exchange_dir: Exchange directory to clean.

    Example:
        >>> from popctl.core.paths import get_exchange_dir
        >>> cleanup_exchange_dir(get_exchange_dir())
    """
    if not exchange_dir.exists():
        return

    # Known exchange files to clean up
    exchange_files = [
        "scan.json",
        "decisions.toml",
        "prompt.txt",
    ]

    import logging

    logger = logging.getLogger(__name__)

    for filename in exchange_files:
        file_path = exchange_dir / filename
        try:
            file_path.unlink(missing_ok=True)
        except PermissionError as e:
            logger.warning("Permission denied when deleting %s: %s", file_path, e)
        except OSError as e:
            logger.warning("Failed to delete %s: %s", file_path, e)


def get_scan_json_path(exchange_dir: Path | None = None) -> Path:
    """Get the standard path for scan.json in exchange directory.

    Args:
        exchange_dir: Exchange directory path. If None, uses default.

    Returns:
        Path to scan.json in the exchange directory.
    """
    if exchange_dir is None:
        exchange_dir = get_exchange_dir()
    return exchange_dir / "scan.json"


def get_decisions_path(exchange_dir: Path | None = None) -> Path:
    """Get the standard path for decisions.toml in exchange directory.

    Args:
        exchange_dir: Exchange directory path. If None, uses default.

    Returns:
        Path to decisions.toml in the exchange directory.
    """
    if exchange_dir is None:
        exchange_dir = get_exchange_dir()
    return exchange_dir / "decisions.toml"
