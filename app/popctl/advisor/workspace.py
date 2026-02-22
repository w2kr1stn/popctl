"""Advisor workspace management for AI-assisted classification sessions.

This module provides workspace creation and session discovery for the
CLAUDE.md-based advisor workflow. Each session creates an ephemeral
directory with all files needed for classification.

Workspace structure:
    <session_dir>/
        CLAUDE.md           — Agent instructions (auto-picked up by Claude Code)
        scan.json           — Package scan data
        manifest.toml       — Current manifest (if exists)
        memory.md           — Past session decisions (if exists)
        output/             — Directory for agent output
            decisions.toml  — Written by the agent
"""

from __future__ import annotations

import json
import logging
import platform
import shutil
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from popctl.advisor.exchange import PackageScanEntry, ScanExport
from popctl.advisor.prompts import build_session_claude_md
from popctl.core.paths import ensure_dir, get_state_dir

if TYPE_CHECKING:
    from popctl.advisor.exchange import OrphanEntry
    from popctl.models.package import ScannedPackage
    from popctl.models.scan_result import ScanResult


def ensure_advisor_sessions_dir() -> Path:
    """Create the advisor sessions directory if it doesn't exist.

    Returns:
        Path to the advisor sessions directory.

    Raises:
        RuntimeError: If the directory cannot be created.
    """
    return ensure_dir(get_state_dir() / "advisor-sessions", "advisor sessions")


def create_session_workspace(
    scan_result: ScanResult,
    sessions_dir: Path,
    manifest_path: Path | None = None,
    system_info: dict[str, str] | None = None,
    memory_path: Path | None = None,
    filesystem_orphans: list[OrphanEntry] | None = None,
    config_orphans: list[OrphanEntry] | None = None,
) -> Path:
    """Create an ephemeral workspace directory for a classification session.

    Creates a timestamped subdirectory containing all files needed for
    an interactive or headless classification session.

    Args:
        scan_result: Scan result with package data.
        sessions_dir: Base directory for session workspaces.
        manifest_path: Optional path to manifest file to copy.
        system_info: Optional system context for CLAUDE.md.
        memory_path: Optional path to persistent memory.md to copy.
        filesystem_orphans: Optional filesystem orphan entries for FS advisor.
        config_orphans: Optional config orphan entries for config advisor.

    Returns:
        Path to the created session workspace directory.

    Raises:
        RuntimeError: If workspace cannot be created.
    """
    # Create timestamped session directory
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S")
    session_dir = sessions_dir / timestamp

    try:
        session_dir.mkdir(parents=True, exist_ok=True)
        (session_dir / "output").mkdir(exist_ok=True)
    except OSError as e:
        msg = f"Cannot create session workspace at {session_dir}: {e}"
        raise RuntimeError(msg) from e

    # Build system info if not provided
    if system_info is None:
        try:
            os_name = platform.freedesktop_os_release()["PRETTY_NAME"]
        except OSError:
            os_name = "Unknown"

        system_info = {
            "hostname": socket.gethostname(),
            "os": os_name,
        }

    # Build summary from packages
    summary = _build_summary(scan_result.packages)

    # Write CLAUDE.md
    claude_md_content = build_session_claude_md(
        system_info=system_info,
        summary=summary,
    )
    try:
        (session_dir / "CLAUDE.md").write_text(claude_md_content, encoding="utf-8")
    except OSError as e:
        msg = f"Failed to write CLAUDE.md: {e}"
        raise RuntimeError(msg) from e

    # Write scan.json
    scan_data = _build_scan_data(
        scan_result, system_info, summary, filesystem_orphans, config_orphans
    )
    try:
        with (session_dir / "scan.json").open("w", encoding="utf-8") as f:
            json.dump(scan_data, f, indent=2, ensure_ascii=False)
    except OSError as e:
        msg = f"Failed to write scan.json: {e}"
        raise RuntimeError(msg) from e

    # Copy manifest if it exists
    if manifest_path is not None and manifest_path.exists():
        try:
            shutil.copy2(manifest_path, session_dir / "manifest.toml")
        except OSError as e:
            # Non-critical — log but don't fail
            logging.getLogger(__name__).warning("Could not copy manifest to workspace: %s", e)

    # Copy memory.md for cross-session learning
    _copy_memory_to_workspace(memory_path, sessions_dir, session_dir)

    return session_dir


def _build_scan_data(
    scan_result: ScanResult,
    system_info: dict[str, str],
    summary: dict[str, int],
    filesystem_orphans: list[OrphanEntry] | None,
    config_orphans: list[OrphanEntry] | None,
) -> dict[str, object]:
    """Build scan.json data using ScanExport format.

    Args:
        scan_result: Package scan data.
        system_info: System context dict.
        summary: Pre-computed package count summary.
        filesystem_orphans: Optional FS orphan entries.
        config_orphans: Optional config orphan entries.

    Returns:
        Dictionary ready for JSON serialization.
    """
    pkg_entries = [
        PackageScanEntry(
            name=p.name,
            source=p.source.value,
            version=p.version,
            status=p.status.value,
            description=p.description,
            size_bytes=p.size_bytes,
        )
        for p in scan_result.packages
    ]

    scan_export = ScanExport(
        scan_date=datetime.now(UTC).isoformat(),
        system=system_info,
        summary=summary,
        packages={"unknown": pkg_entries},
        filesystem_orphans=filesystem_orphans or [],
        config_orphans=config_orphans or [],
    )
    return scan_export.model_dump(mode="json", exclude_none=True)


def _build_summary(packages: tuple[ScannedPackage, ...]) -> dict[str, int]:
    """Build package count summary from packages.

    Args:
        packages: Tuple of scanned packages.

    Returns:
        Summary dict with source counts, total, manual, auto.
    """
    summary: dict[str, int] = {}
    manual_count = 0
    auto_count = 0

    for pkg in packages:
        source_key = pkg.source.value
        summary[source_key] = summary.get(source_key, 0) + 1
        if pkg.is_manual:
            manual_count += 1
        else:
            auto_count += 1

    summary["total"] = len(packages)
    summary["manual"] = manual_count
    summary["auto"] = auto_count
    return summary


_MEMORY_SIZE_WARN_KB = 50


def _copy_memory_to_workspace(
    memory_path: Path | None,
    sessions_dir: Path,
    session_dir: Path,
) -> None:
    """Copy memory.md into the session workspace.

    Tries the persistent memory path first, then falls back to the most
    recent previous session that contains a memory.md (for host-mode
    where post-processing cannot persist memory back).

    Args:
        memory_path: Persistent memory.md path (may be None or missing).
        sessions_dir: Base directory containing all session workspaces.
        session_dir: Current session workspace to copy into.
    """
    logger = logging.getLogger(__name__)

    # Try persistent memory path first
    if memory_path is not None and memory_path.exists():
        try:
            shutil.copy2(memory_path, session_dir / "memory.md")
        except OSError as e:
            logger.warning("Could not copy memory.md to workspace: %s", e)
            return
    else:
        # Fallback: chain from latest previous session
        _copy_memory_from_latest_session(sessions_dir, session_dir)

    # Warn if memory.md is getting large
    workspace_memory = session_dir / "memory.md"
    if workspace_memory.exists():
        size_kb = workspace_memory.stat().st_size / 1024
        if size_kb > _MEMORY_SIZE_WARN_KB:
            logger.warning(
                "memory.md is %.1f KB (recommended max: %d KB)", size_kb, _MEMORY_SIZE_WARN_KB
            )


def _copy_memory_from_latest_session(
    sessions_dir: Path,
    target_session_dir: Path,
) -> None:
    """Copy memory.md from the most recent previous session if available.

    Provides fallback chaining for host-mode where the interactive
    session exits before post-processing can persist memory.md back.

    Args:
        sessions_dir: Base directory containing session workspaces.
        target_session_dir: Current session directory to copy into.
    """
    logger = logging.getLogger(__name__)

    for session_dir in list_sessions(sessions_dir):
        if session_dir == target_session_dir:
            continue
        memory_file = session_dir / "memory.md"
        if memory_file.exists():
            try:
                shutil.copy2(memory_file, target_session_dir / "memory.md")
                logger.debug("Copied memory.md from previous session %s", session_dir.name)
            except OSError as e:
                logger.warning("Could not copy memory.md from previous session: %s", e)
            return


def find_latest_decisions(sessions_dir: Path) -> Path | None:
    """Find output/decisions.toml from the most recent session.

    Scans the sessions directory for the latest session that contains
    a decisions.toml file in its output subdirectory.

    Args:
        sessions_dir: Base directory containing session workspaces.

    Returns:
        Path to decisions.toml if found, None otherwise.
    """
    if not sessions_dir.exists():
        return None

    for session_dir in list_sessions(sessions_dir):
        decisions_path = session_dir / "output" / "decisions.toml"
        if decisions_path.exists():
            return decisions_path

    return None


def list_sessions(sessions_dir: Path) -> list[Path]:
    """List session directories sorted by name (newest first).

    Args:
        sessions_dir: Base directory containing session workspaces.

    Returns:
        List of session directory paths, newest first.
    """
    if not sessions_dir.exists():
        return []

    sessions = [d for d in sessions_dir.iterdir() if d.is_dir() and not d.name.startswith(".")]
    return sorted(sessions, key=lambda d: d.name, reverse=True)
