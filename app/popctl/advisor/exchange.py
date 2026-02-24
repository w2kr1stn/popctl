"""Exchange models and import logic for AI-assisted classification.

This module defines the Pydantic models for the file-based communication
protocol between popctl and AI advisors, and provides import/validation
of advisor decisions.

The primary workflow uses workspace-based sessions (see advisor/workspace.py).
"""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from popctl.advisor.prompts import CATEGORIES
from popctl.core.state import record_action
from popctl.models.history import (
    HistoryActionType,
    HistoryItem,
    create_history_entry,
)
from popctl.models.manifest import (
    DomainConfig,
    DomainEntry,
    Manifest,
    PackageEntry,
    PackageSourceType,
)
from popctl.models.package import PACKAGE_SOURCE_KEYS, PackageSource
from popctl.utils.formatting import print_warning

logger = logging.getLogger(__name__)

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

    packages: dict[PackageSourceType, SourceDecisions] = Field(default_factory=lambda: {})
    filesystem: DomainDecisions | None = None
    configs: DomainDecisions | None = None


# =============================================================================
# Import Functions
# =============================================================================


def import_decisions(decisions_path: Path) -> DecisionsResult:
    """Import and validate a decisions.toml file.

    Reads the decisions.toml file created by the AI agent and validates
    it against the expected schema using Pydantic.

    Args:
        decisions_path: Path to the decisions.toml file.

    Returns:
        Validated DecisionsResult containing all package decisions.

    Raises:
        FileNotFoundError: If decisions.toml doesn't exist.
        ValueError: If TOML is invalid or doesn't match schema.

    Example:
        >>> decisions = import_decisions(Path("/tmp/decisions.toml"))
        >>> for pkg in decisions.packages["apt"].keep:
        ...     print(f"Keep: {pkg.name} ({pkg.reason})")
    """
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

    # Fill missing package sources with empty defaults (if section exists)
    if "packages" in data and isinstance(data["packages"], dict):
        for source in ("apt", "flatpak", "snap"):
            if source not in data["packages"]:
                data["packages"][source] = {"keep": [], "remove": [], "ask": []}

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
            manifest.packages.remove.pop(decision.name, None)
            manifest.packages.keep[decision.name] = PackageEntry(
                source=source,  # type: ignore[arg-type]
                reason=decision.reason,
            )
            stats[source]["keep"] += 1

        for decision in source_decisions.remove:
            manifest.packages.keep.pop(decision.name, None)
            manifest.packages.remove[decision.name] = PackageEntry(
                source=source,  # type: ignore[arg-type]
                reason=decision.reason,
            )
            stats[source]["remove"] += 1

        for decision in source_decisions.ask:
            ask_packages.append((decision.name, source, decision.reason, decision.confidence))
            stats[source]["ask"] += 1

    return stats, ask_packages


def apply_domain_decisions_to_manifest(
    manifest: Manifest,
    domain: Literal["filesystem", "configs"],
    decisions: DomainDecisions,
) -> list[PathDecision]:
    """Apply domain advisor decisions to a manifest.

    Merges keep/remove classifications into the manifest's domain section,
    preserving existing entries not reclassified by the advisor.

    Args:
        manifest: Manifest to update in place.
        domain: Which domain section to update.
        decisions: Domain decisions from the advisor.

    Returns:
        List of "ask" decisions requiring manual user input.
    """
    keep_entries: dict[str, DomainEntry] = {}
    remove_entries: dict[str, DomainEntry] = {}

    for decision in decisions.keep:
        keep_entries[decision.path] = DomainEntry(
            reason=decision.reason,
            category=decision.category,
        )
    for decision in decisions.remove:
        remove_entries[decision.path] = DomainEntry(
            reason=decision.reason,
            category=decision.category,
        )

    existing = getattr(manifest, domain)
    if existing:
        for path, entry in existing.keep.items():
            if path not in keep_entries and path not in remove_entries:
                keep_entries[path] = entry
        for path, entry in existing.remove.items():
            if path not in keep_entries and path not in remove_entries:
                remove_entries[path] = entry

    setattr(manifest, domain, DomainConfig(keep=keep_entries, remove=remove_entries))
    return list(decisions.ask)


def record_advisor_apply_to_history(
    decisions: DecisionsResult,
) -> None:
    """Record advisor apply decisions to history.

    Creates a single history entry for all classifications applied.
    Errors during recording are logged but do not interrupt the flow.

    Args:
        decisions: The decisions result that was applied.
    """
    try:
        items: list[HistoryItem] = []

        for source_str in PACKAGE_SOURCE_KEYS:
            source_decisions = decisions.packages.get(source_str)  # type: ignore[arg-type]
            if source_decisions is None:
                continue

            pkg_source = PackageSource(source_str)

            for decision in source_decisions.keep:
                items.append(HistoryItem(name=decision.name, source=pkg_source))

            for decision in source_decisions.remove:
                items.append(HistoryItem(name=decision.name, source=pkg_source))

        if items:
            entry = create_history_entry(
                action_type=HistoryActionType.ADVISOR_APPLY,
                items=items,
                metadata={"command": "popctl advisor apply"},
            )
            record_action(entry)
            logger.debug("Recorded %d advisor apply item(s) to history", len(items))

    except (OSError, RuntimeError) as e:
        logger.warning("Failed to record advisor apply to history: %s", str(e))
        print_warning(f"Could not record classifications to history: {e}")
