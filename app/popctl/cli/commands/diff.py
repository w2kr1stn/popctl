"""Diff command implementation.

Compares the manifest with the current system state to show differences.
"""

import json
from enum import Enum
from typing import Annotated

import typer
from rich.table import Table

from popctl.core.diff import DiffEngine, DiffEntry, DiffResult, DiffType
from popctl.core.manifest import (
    ManifestError,
    ManifestNotFoundError,
    load_manifest,
    manifest_exists,
)
from popctl.core.paths import get_manifest_path
from popctl.scanners.apt import AptScanner
from popctl.scanners.base import Scanner
from popctl.scanners.flatpak import FlatpakScanner
from popctl.utils.formatting import (
    console,
    print_error,
    print_info,
    print_success,
    print_warning,
)

app = typer.Typer(
    help="Compare manifest with current system state.",
    invoke_without_command=True,
)


class SourceChoice(str, Enum):
    """Available package sources for diff filtering."""

    APT = "apt"
    FLATPAK = "flatpak"
    ALL = "all"


def _get_scanners(source: SourceChoice) -> list[Scanner]:
    """Get scanner instances based on source selection.

    Args:
        source: The source choice (apt, flatpak, or all).

    Returns:
        List of scanner instances.
    """
    scanners: list[Scanner] = []

    if source in (SourceChoice.APT, SourceChoice.ALL):
        scanners.append(AptScanner())

    if source in (SourceChoice.FLATPAK, SourceChoice.ALL):
        scanners.append(FlatpakScanner())

    return scanners


def _get_status_display(diff_type: DiffType) -> tuple[str, str]:
    """Get status icon and style for a diff type.

    Args:
        diff_type: The type of difference.

    Returns:
        Tuple of (status_icon, style_name).
    """
    if diff_type == DiffType.NEW:
        return "[+]", "added"
    if diff_type == DiffType.MISSING:
        return "[-]", "warning"
    # EXTRA
    return "[x]", "removed"


def _get_note(diff_type: DiffType) -> str:
    """Get explanatory note for a diff type.

    Args:
        diff_type: The type of difference.

    Returns:
        Human-readable explanation of the difference.
    """
    if diff_type == DiffType.NEW:
        return "Not in manifest"
    if diff_type == DiffType.MISSING:
        return "Not installed"
    # EXTRA
    return "Marked removal"


def _create_diff_table() -> Table:
    """Create a table for displaying diff results.

    Returns:
        Rich Table configured for diff display.
    """
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
    return table


def _add_entries_to_table(table: Table, entries: tuple[DiffEntry, ...]) -> None:
    """Add diff entries to the table.

    Args:
        table: The Rich Table to add rows to.
        entries: Tuple of DiffEntry objects to add.
    """
    for entry in entries:
        status_icon, style = _get_status_display(entry.diff_type)
        note = _get_note(entry.diff_type)

        table.add_row(
            f"[{style}]{status_icon}[/{style}]",
            entry.source,
            f"[{style}]{entry.name}[/{style}]",
            f"[muted]{note}[/muted]",
        )


def _print_summary(result: DiffResult) -> None:
    """Print summary line for diff results.

    Args:
        result: The DiffResult to summarize.
    """
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


@app.callback(invoke_without_command=True)
def diff_packages(
    ctx: typer.Context,
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
    # Skip if a subcommand is being invoked
    if ctx.invoked_subcommand is not None:
        return

    # Check if manifest exists
    manifest_path = get_manifest_path()
    if not manifest_exists():
        print_error(f"Manifest not found: {manifest_path}")
        print_info("Run 'popctl init' to create a manifest from your current system.")
        raise typer.Exit(code=1)

    # Load manifest
    try:
        manifest = load_manifest()
    except ManifestNotFoundError as e:
        print_error(f"Manifest not found: {manifest_path}")
        print_info("Run 'popctl init' to create a manifest from your current system.")
        raise typer.Exit(code=1) from e
    except ManifestError as e:
        print_error(f"Failed to load manifest: {e}")
        raise typer.Exit(code=1) from e

    # Get scanners
    scanners = _get_scanners(source)
    available_scanners: list[Scanner] = []

    for scanner in scanners:
        if scanner.is_available():
            available_scanners.append(scanner)
        elif not json_output:
            print_warning(f"{scanner.source.value.upper()} package manager is not available.")

    if not available_scanners:
        print_error("No package managers are available on this system.")
        raise typer.Exit(code=1)

    # Compute diff
    source_filter = source.value if source != SourceChoice.ALL else None
    engine = DiffEngine(manifest)

    try:
        result = engine.compute_diff(available_scanners, source_filter)
    except RuntimeError as e:
        print_error(f"Scan failed: {e}")
        raise typer.Exit(code=1) from e

    # JSON output
    if json_output:
        console.print_json(json.dumps(result.to_dict()))
        return

    # Brief output (summary only)
    if brief:
        if result.is_in_sync:
            print_success("System is in sync with manifest.")
        else:
            console.print(f"[added]New:[/added] {len(result.new)}")
            console.print(f"[warning]Missing:[/warning] {len(result.missing)}")
            console.print(f"[removed]Extra:[/removed] {len(result.extra)}")
            console.print(f"[muted]Total changes: {result.total_changes}[/muted]")
        return

    # Full output (table)
    if result.is_in_sync:
        print_success("System is in sync with manifest.")
        return

    # Create and populate table
    table = _create_diff_table()

    # Add entries in order: new, missing, extra
    _add_entries_to_table(table, result.new)
    _add_entries_to_table(table, result.missing)
    _add_entries_to_table(table, result.extra)

    console.print(table)
    _print_summary(result)
