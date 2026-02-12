"""Advisor workspace management for AI-assisted classification sessions.

This module provides workspace creation and session discovery for the
CLAUDE.md-based advisor workflow. Each session creates an ephemeral
directory with all files needed for classification.

Workspace structure:
    <session_dir>/
        CLAUDE.md           — Agent instructions (auto-picked up by Claude Code)
        scan.json           — Package scan data
        manifest.toml       — Current manifest (if exists)
        output/             — Directory for agent output
            decisions.toml  — Written by the agent
"""

from __future__ import annotations

import json
import shutil
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from popctl.advisor.prompts import build_session_claude_md

if TYPE_CHECKING:
    from popctl.models.scan_result import ScanResult


def create_session_workspace(
    scan_result: ScanResult,
    sessions_dir: Path,
    manifest_path: Path | None = None,
    system_info: dict[str, str] | None = None,
) -> Path:
    """Create an ephemeral workspace directory for a classification session.

    Creates a timestamped subdirectory containing all files needed for
    an interactive or headless classification session.

    Args:
        scan_result: Scan result with package data.
        sessions_dir: Base directory for session workspaces.
        manifest_path: Optional path to manifest file to copy.
        system_info: Optional system context for CLAUDE.md.

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
        system_info = {
            "hostname": socket.gethostname(),
            "os": "Pop!_OS 24.04 LTS",
        }

    # Build summary from scan result
    summary = dict(scan_result.summary)

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
    scan_data = scan_result.to_dict()
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
            import logging

            logging.getLogger(__name__).warning("Could not copy manifest to workspace: %s", e)

    return session_dir


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
