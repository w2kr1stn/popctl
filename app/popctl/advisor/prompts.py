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
INITIAL_PROMPT = _load_template("initial_prompt.txt")

_DOMAIN_TEMPLATES: dict[str, str] = {
    "packages": SESSION_CLAUDE_MD,
    "filesystem": SESSION_CLAUDE_MD_FILESYSTEM,
    "configs": SESSION_CLAUDE_MD_CONFIGS,
}

REVIEW_ADDENDUM = """

## Review-Modus (AKTIV)

Dies ist eine **Review-Session**. Das System ist bereits in sync mit dem Manifest.
Deine Aufgabe ist NICHT neue Pakete zu klassifizieren, sondern **bestehende
Klassifikationen kritisch zu überprüfen**:

1. Lies `manifest.toml` vollständig — dort stehen alle bisherigen KEEP/REMOVE-Entscheidungen
2. Hinterfrage jede Entscheidung: Wird das Paket noch gebraucht? Hat sich der Kontext geändert?
3. Achte besonders auf: Dev-Tools die auf den Host gerutscht sind, verwaiste Abhängigkeiten,
   Pakete die inzwischen durch Alternativen ersetzt wurden
4. Schreibe nur **geänderte** Entscheidungen in `output/decisions.toml` — Pakete die korrekt
   klassifiziert sind, müssen NICHT erneut aufgelistet werden
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

    content = template.format(
        system_context=system_context,
        categories=categories_list,
    )

    if review:
        content += REVIEW_ADDENDUM

    return content
