"""File exchange for AI-assisted package classification.

This module handles the file-based communication protocol between popctl
and AI advisors (Claude Code / Gemini CLI). It provides functions to:

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

import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from popctl.advisor.prompts import CATEGORIES
from popctl.models.manifest import Manifest, PackageEntry, PackageSourceType
from popctl.models.package import PACKAGE_SOURCE_KEYS

# Exchange directory for file-based communication with AI advisors
EXCHANGE_DIR = Path("/tmp/popctl-exchange")  # noqa: S108

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


class OrphanEntry(BaseModel):
    """Single orphan entry in scan export (filesystem or config).

    Represents an orphaned path in the scan.json export format,
    containing the information needed for AI classification.

    Attributes:
        path: Path (tilde-prefixed, e.g., "~/.config/vlc").
        path_type: Type of path ("directory", "file", "symlink", "dead_symlink").
        size_bytes: Size in bytes (None if unavailable).
        mtime: Last modification time in ISO 8601 format (None if unavailable).
        parent_target: Scan target root directory (filesystem only, None for configs).
        orphan_reason: Reason for orphan classification.
        confidence: Orphan confidence score (0.0 to 1.0).
    """

    model_config = ConfigDict(frozen=True)

    path: str
    path_type: str
    size_bytes: int | None = None
    mtime: str | None = None
    parent_target: str | None = None
    orphan_reason: str
    confidence: float


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
        configs: Optional config orphan data for classification.
    """

    model_config = ConfigDict(frozen=True)

    scan_date: str  # ISO format
    system: dict[str, str]  # hostname, os, manifest_path
    summary: dict[str, int]  # total_packages, manual_apt, etc.
    packages: dict[str, list[PackageScanEntry]]  # "unknown"
    filesystem_orphans: list[OrphanEntry] = Field(default_factory=lambda: [])
    config_orphans: list[OrphanEntry] = Field(default_factory=lambda: [])


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


class PathDecision(BaseModel):
    """Single path decision from AI agent (filesystem or config).

    Represents one path's classification in the decisions.toml file.

    Attributes:
        path: Path (tilde-prefixed, e.g., "~/.config/vlc").
        reason: Explanation for the classification.
        confidence: Classification confidence (0.0 - 1.0).
        category: Path category (e.g., "config", "obsolete", "other").
    """

    model_config = ConfigDict(frozen=True)

    path: str
    reason: str
    confidence: float = Field(ge=0.0, le=1.0)
    category: str | None = None


class DomainDecisions(BaseModel):
    """Path decisions grouped by classification (keep/remove/ask).

    Used for both filesystem and config domain decisions from the AI agent.

    Attributes:
        keep: Paths that should be kept.
        remove: Paths that should be removed.
        ask: Paths that need user decision.
    """

    model_config = ConfigDict(frozen=True)

    keep: list[PathDecision] = Field(default_factory=lambda: [])
    remove: list[PathDecision] = Field(default_factory=lambda: [])
    ask: list[PathDecision] = Field(default_factory=lambda: [])


class DecisionsResult(BaseModel):
    """Complete decisions from AI agent.

    Root model for parsing decisions.toml from the AI agent.

    Attributes:
        packages: Decisions organized by package source.
        filesystem: Optional filesystem path decisions.
        configs: Optional config path decisions.
    """

    model_config = ConfigDict(frozen=True)

    packages: dict[PackageSourceType, SourceDecisions]
    filesystem: DomainDecisions | None = None
    configs: DomainDecisions | None = None


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
        >>> from popctl.advisor.exchange import EXCHANGE_DIR
        >>> decisions = import_decisions(EXCHANGE_DIR)
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

    # Validate packages section exists
    if not isinstance(data.get("packages"), dict):
        msg = "decisions.toml must have a 'packages' section"
        raise ValueError(msg)

    # Fill missing sources with empty defaults
    packages: dict[str, Any] = data["packages"]
    for source in ("apt", "flatpak", "snap"):
        if source not in packages:
            packages[source] = {"keep": [], "remove": [], "ask": []}

    # Let Pydantic validate the full structure
    try:
        return DecisionsResult.model_validate(data)
    except ValidationError as e:
        msg = f"Invalid decisions.toml schema: {e}"
        raise ValueError(msg) from e


def apply_decisions_to_manifest(
    manifest: Manifest,
    decisions: DecisionsResult,
) -> tuple[dict[str, dict[str, int]], list[tuple[str, str, str, float]]]:
    """Apply advisor package decisions to a manifest.

    Mutates the manifest's ``packages.keep`` and ``packages.remove``
    dicts by inserting entries derived from the advisor decisions.

    Args:
        manifest: Manifest to update in place.
        decisions: Validated advisor decisions.

    Returns:
        Tuple of ``(stats_by_source, ask_packages)`` where:
        - ``stats_by_source`` maps each source to
          ``{"keep": N, "remove": N, "ask": N}`` counts.
        - ``ask_packages`` is a list of
          ``(name, source, reason, confidence)`` tuples for decisions
          that require manual user input.
    """
    stats: dict[str, dict[str, int]] = {}
    ask_packages: list[tuple[str, str, str, float]] = []

    for source in PACKAGE_SOURCE_KEYS:
        source_decisions = decisions.packages.get(source)  # type: ignore[arg-type]
        if source_decisions is None:
            continue

        stats[source] = {"keep": 0, "remove": 0, "ask": 0}

        for decision in source_decisions.keep:
            manifest.packages.keep[decision.name] = PackageEntry(
                source=source,  # type: ignore[arg-type]
                status="keep",
                reason=decision.reason,
            )
            stats[source]["keep"] += 1

        for decision in source_decisions.remove:
            manifest.packages.remove[decision.name] = PackageEntry(
                source=source,  # type: ignore[arg-type]
                status="remove",
                reason=decision.reason,
            )
            stats[source]["remove"] += 1

        for decision in source_decisions.ask:
            ask_packages.append((decision.name, source, decision.reason, decision.confidence))
            stats[source]["ask"] += 1

    return stats, ask_packages
