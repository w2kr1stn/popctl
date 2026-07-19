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
    ref = importlib.resources.files("popctl.data.prompts").joinpath(name)
    return ref.read_text(encoding="utf-8")


# Module-level template constants (loaded from external files).
SESSION_CLAUDE_MD = _load_template("session_claude_md.txt")
SESSION_CLAUDE_MD_FILESYSTEM = _load_template("session_claude_md_filesystem.txt")
SESSION_CLAUDE_MD_CONFIGS = _load_template("session_claude_md_configs.txt")
SESSION_CLAUDE_MD_DOTFILES = _load_template("session_claude_md_dotfiles.txt")
INITIAL_PROMPT = _load_template("initial_prompt.txt")
DOTFILES_INITIAL_PROMPT = SESSION_CLAUDE_MD_DOTFILES

_DOMAIN_TEMPLATES: dict[str, str] = {
    "packages": SESSION_CLAUDE_MD,
    "filesystem": SESSION_CLAUDE_MD_FILESYSTEM,
    "configs": SESSION_CLAUDE_MD_CONFIGS,
    "dotfiles": SESSION_CLAUDE_MD_DOTFILES,
}

REVIEW_ADDENDUM = """

## Review Mode (ACTIVE)

This is a **review session**. The system is already in sync with the manifest.
Your task is NOT to classify new packages, but to **critically review existing
classifications**:

1. Read `manifest.toml` completely — it contains all existing KEEP/REMOVE decisions
2. Question every decision: Is the package still needed? Has the context changed?
3. Evaluate development tools by the same standard as every other package: manifest,
   observable usage evidence, and the user's answers; prefer ASK when uncertain.
   Also question orphaned dependencies and packages that have been replaced by alternatives
4. Write only **changed** decisions to `output/decisions.toml` — correctly
   classified packages do NOT need to be listed again
"""


def build_session_claude_md(
    system_info: dict[str, str] | None = None,
    summary: dict[str, int] | None = None,
    domain: str = "packages",
    review: bool = False,
) -> str:
    """Selects domain-specific template and renders CLAUDE.md content."""
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

    template = _DOMAIN_TEMPLATES.get(domain, SESSION_CLAUDE_MD)
    if domain == "dotfiles":
        return template

    content = template.format(
        system_context=system_context,
        categories=categories_list,
    )

    if review:
        content += REVIEW_ADDENDUM

    return content
