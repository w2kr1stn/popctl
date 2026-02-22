"""Diff command implementation.

Compares the manifest with the current system state to show differences.
"""

import json
from typing import Annotated

import typer
from rich.table import Table

from popctl.cli.types import SourceChoice, get_checked_scanners, require_manifest
from popctl.core.diff import DiffResult, DiffType, compute_diff
from popctl.utils.formatting import (
    console,
    print_error,
    print_success,
)

app = typer.Typer(
    help="Compare manifest with current system state.",
    invoke_without_command=True,
)

# (icon, style, note) per diff type
_DIFF_DISPLAY: dict[DiffType, tuple[str, str, str]] = {
    DiffType.NEW: ("[+]", "added", "Not in manifest"),
    DiffType.MISSING: ("[-]", "warning", "Not installed"),
    DiffType.EXTRA: ("[x]", "removed", "Marked removal"),
}


@app.callback(invoke_without_command=True)
def diff_packages(
    source: Annotated[
        SourceChoice,
        typer.Option(
            "--source",
            "-s",
            help="Package source to diff: apt, flatpak, or all.",
            case_sensitive=False,
        ),
    ] = SourceChoice.ALL,
    brief: Annotated[
        bool,
        typer.Option(
            "--brief",
            "-b",
            help="Show summary counts only.",
        ),
    ] = False,
    json_output: Annotated[
        bool,
        typer.Option(
            "--json",
            "-j",
            help="Output as JSON for scripting.",
        ),
    ] = False,
) -> None:
    """Compare manifest with current system state.

    Shows differences between what the manifest declares and what is
    actually installed on the system.

    Difference types:
      [+] NEW: Package installed but not in manifest
      [-] MISSING: Package in manifest but not installed
      [x] EXTRA: Package marked for removal but still installed

    Examples:
        popctl diff                    # Show all differences
        popctl diff --brief            # Summary counts only
        popctl diff --source apt       # Filter to APT packages
        popctl diff --json             # JSON output for scripting
    """
    # Load manifest (exits with helpful message if not found)
    manifest = require_manifest()

    # Get scanners
    available_scanners = get_checked_scanners(source, silent=json_output)

    # Compute diff
    source_filter = source.to_source_filter()
    try:
        result = compute_diff(manifest, available_scanners, source_filter)
    except RuntimeError as e:
        print_error(f"Scan failed: {e}")
        raise typer.Exit(code=1) from e

    # JSON output
    if json_output:
        console.print_json(json.dumps(result.to_dict()))
        return

    # Brief output (summary only)
    if brief:
        _print_brief(result)
        return

    # Full output (table)
    if result.is_in_sync:
        print_success("System is in sync with manifest.")
        return

    # Build and populate table
    table = Table(
        title="System Differences",
        show_header=True,
        header_style="bold_header",
        border_style="border",
    )
    table.add_column("Status", width=6, justify="center")
    table.add_column("Source", width=8)
    table.add_column("Package", no_wrap=True)
    table.add_column("Note")

    for entry in (*result.new, *result.missing, *result.extra):
        icon, style, note = _DIFF_DISPLAY[entry.diff_type]
        table.add_row(
            f"[{style}]{icon}[/{style}]",
            entry.source,
            f"[{style}]{entry.name}[/{style}]",
            f"[muted]{note}[/muted]",
        )

    console.print(table)

    # Summary line
    parts: list[str] = []
    if result.new:
        parts.append(f"[added]{len(result.new)} new[/added]")
    if result.missing:
        parts.append(f"[warning]{len(result.missing)} missing[/warning]")
    if result.extra:
        parts.append(f"[removed]{len(result.extra)} extra[/removed]")

    if parts:
        summary = ", ".join(parts)
        console.print(f"\nSummary: {summary} ({result.total_changes} total changes)")
    else:
        console.print("\n[muted]No differences found.[/muted]")


def _print_brief(result: DiffResult) -> None:
    """Print brief summary counts."""
    if result.is_in_sync:
        print_success("System is in sync with manifest.")
    else:
        console.print(f"[added]New:[/added] {len(result.new)}")
        console.print(f"[warning]Missing:[/warning] {len(result.missing)}")
        console.print(f"[removed]Extra:[/removed] {len(result.extra)}")
        console.print(f"[muted]Total changes: {result.total_changes}[/muted]")
