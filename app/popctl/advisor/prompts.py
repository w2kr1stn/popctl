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
SESSION_CLAUDE_MD = _load_template("session_claude_md.txt")
INITIAL_PROMPT = _load_template("initial_prompt.txt")


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
