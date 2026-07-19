from __future__ import annotations

import logging
import os
import re
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from popctl.advisor.prompts import CATEGORIES
from popctl.core.state import record_action
from popctl.dotfiles.config import DotfilesConfig
from popctl.dotfiles.discovery import DiscoveryResult
from popctl.dotfiles.materialize import HomePathError, canonical_home_relative_path
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

_PKG_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9.+\-_]+$")

# =============================================================================
# Import Models (decisions.toml)
# =============================================================================


class PackageDecision(BaseModel):

    model_config = ConfigDict(frozen=True)

    name: str
    reason: str
    confidence: float = Field(ge=0.0, le=1.0)
    category: str

    @field_validator("category")
    @classmethod
    def validate_category(cls, v: str) -> str:
        if v not in CATEGORIES:
            valid = ", ".join(CATEGORIES)
            msg = f"Invalid category '{v}'. Must be one of: {valid}"
            raise ValueError(msg)
        return v

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        if not _PKG_NAME_RE.match(v):
            msg = f"Invalid package name: {v!r}"
            raise ValueError(msg)
        return v


class SourceDecisions(BaseModel):

    model_config = ConfigDict(frozen=True)

    keep: list[PackageDecision] = Field(default_factory=lambda: [])
    remove: list[PackageDecision] = Field(default_factory=lambda: [])
    ask: list[PackageDecision] = Field(default_factory=lambda: [])


class PathDecision(BaseModel):

    model_config = ConfigDict(frozen=True)

    path: str
    reason: str
    confidence: float = Field(ge=0.0, le=1.0)
    category: str | None = None

    @field_validator("path")
    @classmethod
    def validate_path(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            msg = "Path cannot be empty"
            raise ValueError(msg)
        if not (stripped.startswith("~/") or stripped.startswith("/")):
            msg = f"Path must be absolute or start with ~/: {stripped!r}"
            raise ValueError(msg)
        expanded = os.path.normpath(os.path.abspath(os.path.expanduser(stripped)))
        home = str(Path.home())
        if expanded in (home, "/"):
            msg = f"Refusing root-level path: {stripped!r}"
            raise ValueError(msg)
        p = Path(expanded)
        if not (p.is_relative_to(Path(home)) or p.is_relative_to(Path("/etc"))):
            msg = f"Path outside allowed prefixes (home, /etc): {stripped!r}"
            raise ValueError(msg)
        return stripped


class DomainDecisions(BaseModel):

    model_config = ConfigDict(frozen=True)

    keep: list[PathDecision] = Field(default_factory=lambda: [])
    remove: list[PathDecision] = Field(default_factory=lambda: [])
    ask: list[PathDecision] = Field(default_factory=lambda: [])


class DotfilesPathDecision(BaseModel):

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    reason: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        if value != value.strip():
            msg = "Dotfiles path must not have surrounding whitespace"
            raise ValueError(msg)
        try:
            canonical = canonical_home_relative_path(value)
        except HomePathError as e:
            raise ValueError(str(e)) from e
        if _contains_symlink(Path.home(), canonical):
            msg = f"Dotfiles path must not traverse a symlink: {canonical!r}"
            raise ValueError(msg)
        return canonical

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            msg = "Dotfiles decision reason cannot be empty"
            raise ValueError(msg)
        return stripped


class DotfilesDecisions(BaseModel):

    model_config = ConfigDict(frozen=True, extra="forbid")

    track: list[DotfilesPathDecision] = Field(default_factory=lambda: [])
    ignore: list[DotfilesPathDecision] = Field(default_factory=lambda: [])
    ask: list[DotfilesPathDecision] = Field(default_factory=lambda: [])


@dataclass(frozen=True, slots=True)
class DotfilesReviewFinalization:
    tracked_paths: tuple[str, ...]
    ignored_paths: tuple[str, ...]
    pending_paths: tuple[str, ...]


class DecisionsResult(BaseModel):

    model_config = ConfigDict(frozen=True)

    packages: dict[PackageSourceType, SourceDecisions] = Field(default_factory=lambda: {})
    filesystem: DomainDecisions | None = None
    configs: DomainDecisions | None = None
    dotfiles: DotfilesDecisions | None = None


# =============================================================================
# Import Functions
# =============================================================================


def import_decisions(
    decisions_path: Path,
    *,
    discovery: DiscoveryResult | None = None,
) -> DecisionsResult:
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
        decisions = DecisionsResult.model_validate(data)
    except ValidationError as e:
        msg = f"Invalid decisions.toml schema: {e}"
        raise ValueError(msg) from e
    if decisions.dotfiles is not None:
        if discovery is None:
            msg = "Dotfiles decisions require a fresh discovery snapshot"
            raise ValueError(msg)
        validate_dotfiles_decisions(decisions.dotfiles, discovery)
    return decisions


def validate_dotfiles_decisions(
    decisions: DotfilesDecisions,
    discovery: DiscoveryResult,
) -> None:
    _validated_dotfiles_paths(decisions, discovery)


def finalize_dotfiles_review(
    decisions: DotfilesDecisions,
    discovery: DiscoveryResult,
    config: DotfilesConfig,
    *,
    confirmed: bool,
    finalize_operation: Callable[[tuple[str, ...], DotfilesConfig], None],
) -> DotfilesReviewFinalization:
    track_paths, ignore_paths, ask_paths = _validated_dotfiles_paths(decisions, discovery)
    if not confirmed:
        return DotfilesReviewFinalization((), (), ask_paths)
    ignored = sorted(set(config.ignored) | set(ignore_paths))
    updated_config = config.model_copy(update={"ignored": ignored})
    finalize_operation(track_paths, updated_config)
    return DotfilesReviewFinalization(track_paths, ignore_paths, ask_paths)


def _validated_dotfiles_paths(
    decisions: DotfilesDecisions,
    discovery: DiscoveryResult,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    snapshot = set(discovery.candidate_paths)
    seen: set[str] = set()
    paths_by_decision: list[tuple[str, tuple[DotfilesPathDecision, ...]]] = [
        ("track", tuple(decisions.track)),
        ("ignore", tuple(decisions.ignore)),
        ("ask", tuple(decisions.ask)),
    ]
    validated: dict[str, tuple[str, ...]] = {}
    for action, path_decisions in paths_by_decision:
        paths: list[str] = []
        for decision in path_decisions:
            if decision.path not in snapshot:
                msg = (
                    "Dotfiles decision path is not in the fresh discovery snapshot: "
                    f"{decision.path}"
                )
                raise ValueError(msg)
            if decision.path in seen:
                msg = f"Dotfiles path is classified more than once: {decision.path}"
                raise ValueError(msg)
            seen.add(decision.path)
            paths.append(decision.path)
        validated[action] = tuple(paths)
    return validated["track"], validated["ignore"], validated["ask"]


def _contains_symlink(home: Path, canonical_path: str) -> bool:
    current = home
    for component in PurePosixPath(canonical_path).parts:
        current = current / component
        try:
            if current.is_symlink():
                return True
        except OSError:
            return True
    return False


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
                reversible=False,
                metadata={"command": "popctl advisor apply"},
            )
            record_action(entry)
            logger.debug("Recorded %d advisor apply item(s) to history", len(items))

    except (OSError, RuntimeError) as e:
        logger.warning("Failed to record advisor apply to history: %s", str(e))
        print_warning(f"Could not record classifications to history: {e}")
