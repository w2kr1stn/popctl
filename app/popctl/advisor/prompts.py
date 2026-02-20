"""Prompt templates for AI-assisted package classification.

This module provides prompt templates for the Claude Advisor to classify
packages as keep, remove, or ask. It supports headless (autonomous) mode
and workspace-based interactive sessions via CLAUDE.md.

Templates are loaded from external files in the ``popctl.data.prompts``
package. Builder functions fill in placeholders (system context, paths,
categories) and return the final prompt strings.
"""

import importlib.resources
from datetime import UTC, datetime
from pathlib import Path

# Classification categories for packages
CATEGORIES = (
    "system",
    "desktop",
    "drivers",
    "development",
    "server",
    "media",
    "gaming",
    "office",
    "network",
    "security",
    "obsolete",
    "telemetry",
    "other",
)


def _load_template(name: str) -> str:
    """Load a prompt template from the data package.

    Args:
        name: Filename inside ``popctl.data.prompts`` (e.g. ``"headless.txt"``).

    Returns:
        Raw template string with ``{placeholders}`` intact.
    """
    ref = importlib.resources.files("popctl.data.prompts").joinpath(name)
    return ref.read_text(encoding="utf-8")


# Module-level template constants (loaded from external files).
# Preserved for backward compatibility — tests and consumers import these directly.
DECISIONS_SCHEMA = _load_template("decisions_schema.txt")
HEADLESS_PROMPT = _load_template("headless.txt")
SESSION_CLAUDE_MD = _load_template("session_claude_md.txt")
INITIAL_PROMPT = _load_template("initial_prompt.txt")


def build_headless_prompt(
    scan_json_path: str,
    decisions_output_path: str,
    system_info: dict[str, str] | None = None,
) -> str:
    """Build prompt for headless (autonomous) classification.

    Constructs a complete prompt for the AI agent to perform autonomous
    package classification. The prompt includes paths to input/output files,
    classification rules, and output format specification.

    Args:
        scan_json_path: Path to scan.json file containing package data.
        decisions_output_path: Path where agent should write decisions.toml.
        system_info: Optional system context (hostname, os, machine_id).

    Returns:
        Complete prompt string for the AI agent.

    Example:
        >>> prompt = build_headless_prompt(
        ...     "/tmp/popctl-exchange/scan.json",
        ...     "/tmp/popctl-exchange/decisions.toml",
        ...     {"hostname": "pop-desktop", "os": "Pop!_OS 24.04"}
        ... )
    """
    # Build system context section
    system_context = ""
    if system_info:
        context_lines: list[str] = []
        if "hostname" in system_info:
            context_lines.append(f"- **Hostname**: {system_info['hostname']}")
        if "os" in system_info:
            context_lines.append(f"- **OS Version**: {system_info['os']}")
        if "machine_id" in system_info:
            context_lines.append(f"- **Machine ID**: {system_info['machine_id']}")
        if context_lines:
            system_context = "\n" + "\n".join(context_lines)

    # Format categories as bullet list
    categories_list = "\n".join(f"- {cat}" for cat in CATEGORIES)

    # Generate timestamp
    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    return HEADLESS_PROMPT.format(
        scan_json_path=scan_json_path,
        decisions_output_path=decisions_output_path,
        system_context=system_context,
        categories=categories_list,
        timestamp=timestamp,
    )


def build_session_claude_md(
    system_info: dict[str, str] | None = None,
    summary: dict[str, int] | None = None,
) -> str:
    """Build CLAUDE.md content for an interactive session workspace.

    Creates a comprehensive CLAUDE.md file that Claude Code picks up
    automatically from the working directory. Contains classification
    rules, output format, and system context.

    Args:
        system_info: Optional system context (hostname, os).
        summary: Optional package count summary (total, manual, auto).

    Returns:
        CLAUDE.md content string.
    """
    # Build system context section
    context_lines: list[str] = []
    if system_info:
        if "hostname" in system_info:
            context_lines.append(f"- **Hostname**: {system_info['hostname']}")
        if "os" in system_info:
            context_lines.append(f"- **OS Version**: {system_info['os']}")
    if summary:
        if "total" in summary:
            context_lines.append(f"- **Total packages scanned**: {summary['total']}")
        if "manual" in summary:
            context_lines.append(f"- **Manually installed**: {summary['manual']}")

    timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    context_lines.append(f"- **Scan date**: {timestamp}")

    system_context = "\n".join(context_lines) if context_lines else "No system context available."

    # Format categories as bullet list
    categories_list = "\n".join(f"- `{cat}`" for cat in CATEGORIES)

    return SESSION_CLAUDE_MD.format(
        system_context=system_context,
        categories=categories_list,
    )


def get_prompt_file_path(exchange_dir: Path) -> Path:
    """Get the standard path for the prompt file in exchange directory.

    Args:
        exchange_dir: Exchange directory path.

    Returns:
        Path to prompt.txt in the exchange directory.
    """
    return exchange_dir / "prompt.txt"
