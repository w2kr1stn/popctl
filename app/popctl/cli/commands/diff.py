import json
from typing import Annotated

import typer
from rich.table import Table

from popctl.cli.types import SourceChoice, compute_source_system_diff, compute_system_diff
from popctl.core.diff import DiffResult, DiffType
from popctl.sources.diff import (
    AptPackageDiagnostic,
    SourceDiffEntry,
    SourceDiffResult,
    SourceDiffType,
)
from popctl.utils.formatting import (
    console,
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

_SOURCE_DIFF_DISPLAY: dict[SourceDiffType, tuple[str, str, str]] = {
    SourceDiffType.MISSING: ("[-]", "warning", "Not present on system"),
    SourceDiffType.EXTRA: ("[+]", "added", "Unrecorded live source (report only)"),
    SourceDiffType.CHANGED: ("[~]", "warning", "Live source differs"),
}


@app.callback(invoke_without_command=True)
def diff_packages(
    source: Annotated[
        SourceChoice,
        typer.Option(
            "--source",
            "-s",
            help="Package source to diff: apt, flatpak, snap, or all.",
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
    result = compute_system_diff(source, silent_warnings=json_output)
    source_result = compute_source_system_diff(source)
    in_sync = result.is_in_sync and source_result.is_in_sync

    # JSON output
    if json_output:
        console.print_json(json.dumps(_combined_json(result, source_result)))
        return

    # Brief output (summary only)
    if brief:
        _print_brief(result, source_result)
        return

    # Full output (table)
    if in_sync:
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
    table.add_column("Item", no_wrap=True)
    table.add_column("Note")

    for entry in (*result.new, *result.missing, *result.extra):
        icon, style, note = _DIFF_DISPLAY[entry.diff_type]
        table.add_row(
            f"[{style}]{icon}[/{style}]",
            entry.source.value,
            f"[{style}]{entry.name}[/{style}]",
            f"[muted]{note}[/muted]",
        )

    for entry in (*source_result.missing, *source_result.extra, *source_result.changed):
        _add_source_row(table, entry)
    for diagnostic in source_result.unrecorded_apt_packages:
        _add_apt_diagnostic_row(table, diagnostic)

    console.print(table)

    # Summary line
    parts: list[str] = []
    if result.new:
        parts.append(f"[added]{len(result.new)} new[/added]")
    if result.missing:
        parts.append(f"[warning]{len(result.missing)} missing[/warning]")
    if result.extra:
        parts.append(f"[removed]{len(result.extra)} extra[/removed]")
    if source_result.missing:
        parts.append(f"[warning]{len(source_result.missing)} source missing[/warning]")
    if source_result.extra:
        parts.append(f"[added]{len(source_result.extra)} source extra[/added]")
    if source_result.changed:
        parts.append(f"[warning]{len(source_result.changed)} source changed[/warning]")

    if parts:
        summary = ", ".join(parts)
        console.print(
            "\nSummary: "
            f"{summary} ({result.total_changes + source_result.total_changes} total changes)"
        )
    else:
        console.print("\n[muted]No differences found.[/muted]")


def _combined_json(result: DiffResult, source_result: SourceDiffResult) -> dict[str, object]:
    return {
        "in_sync": result.is_in_sync and source_result.is_in_sync,
        "summary": {
            "new": len(result.new),
            "missing": len(result.missing),
            "extra": len(result.extra),
            "source_missing": len(source_result.missing),
            "source_extra": len(source_result.extra),
            "source_changed": len(source_result.changed),
            "total": result.total_changes + source_result.total_changes,
        },
        "new": [entry.to_dict() for entry in result.new],
        "missing": [entry.to_dict() for entry in result.missing],
        "extra": [entry.to_dict() for entry in result.extra],
        "sources": source_result.to_dict(),
    }


def _add_source_row(table: Table, entry: SourceDiffEntry) -> None:
    icon, style, note = _SOURCE_DIFF_DISPLAY[entry.diff_type]
    table.add_row(
        f"[{style}]{icon}[/{style}]",
        entry.locator.manager.value,
        f"[{style}]{entry.label}[/{style}]",
        f"[muted]{note}[/muted]",
    )


def _add_apt_diagnostic_row(table: Table, diagnostic: AptPackageDiagnostic) -> None:
    if diagnostic.locator is None:
        note = "Candidate source is unknown"
    else:
        note = f"Uses unrecorded source: {'/'.join(diagnostic.locator.parts)}"
    table.add_row("[warning][?][/warning]", "apt", diagnostic.package, f"[muted]{note}[/muted]")


def _print_brief(result: DiffResult, source_result: SourceDiffResult) -> None:
    if result.is_in_sync and source_result.is_in_sync:
        print_success("System is in sync with manifest.")
    else:
        console.print(f"[added]New:[/added] {len(result.new)}")
        console.print(f"[warning]Missing:[/warning] {len(result.missing)}")
        console.print(f"[removed]Extra:[/removed] {len(result.extra)}")
        console.print(f"[warning]Source missing:[/warning] {len(source_result.missing)}")
        console.print(f"[added]Source extra:[/added] {len(source_result.extra)}")
        console.print(f"[warning]Source changed:[/warning] {len(source_result.changed)}")
        console.print(
            f"[muted]Total changes: {result.total_changes + source_result.total_changes}[/muted]"
        )
