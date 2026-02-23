"""Scan command implementation.

Lists installed packages from various package managers.
"""

import json
from enum import Enum
from pathlib import Path
from typing import Annotated

import typer

from popctl.cli.types import SourceChoice, get_scanners
from popctl.models.package import PackageStatus, ScannedPackage
from popctl.models.scan_result import ScanResult
from popctl.utils.formatting import (
    console,
    create_package_table,
    format_package_row,
    print_error,
    print_info,
    print_warning,
)

app = typer.Typer(
    help="Scan system for installed packages.",
    invoke_without_command=True,
)


class OutputFormat(str, Enum):
    """Output format options."""

    TABLE = "table"
    JSON = "json"


def _get_source_title(source: SourceChoice, manual_only: bool) -> str:
    """Generate table title based on source and filter options.

    Args:
        source: The source choice.
        manual_only: Whether only manual packages are shown.

    Returns:
        Title string for the table.
    """
    prefix = "Manually Installed Packages" if manual_only else "Installed Packages"

    if source == SourceChoice.ALL:
        return prefix
    return f"{prefix} ({source.value.upper()})"


@app.callback(invoke_without_command=True)
def scan_packages(
    ctx: typer.Context,
    source: Annotated[
        SourceChoice,
        typer.Option(
            "--source",
            "-s",
            help="Package source to scan: apt, flatpak, or all.",
            case_sensitive=False,
        ),
    ] = SourceChoice.ALL,
    manual_only: Annotated[
        bool,
        typer.Option(
            "--manual-only",
            "-m",
            help="Only show manually installed packages.",
        ),
    ] = False,
    count_only: Annotated[
        bool,
        typer.Option(
            "--count",
            "-c",
            help="Only show package counts.",
        ),
    ] = False,
    limit: Annotated[
        int | None,
        typer.Option(
            "--limit",
            "-n",
            help="Limit number of packages to display.",
        ),
    ] = None,
    export_path: Annotated[
        Path | None,
        typer.Option(
            "--export",
            "-e",
            help="Export scan results to JSON file.",
        ),
    ] = None,
    output_format: Annotated[
        OutputFormat,
        typer.Option(
            "--format",
            "-f",
            help="Output format: table or json.",
            case_sensitive=False,
        ),
    ] = OutputFormat.TABLE,
) -> None:
    """Scan and display installed packages.

    By default, scans all package sources and displays results in a table.

    Examples:
        popctl scan                         # Scan all sources, show table
        popctl scan --source apt            # Scan APT only
        popctl scan --source flatpak        # Scan Flatpak only
        popctl scan --manual-only           # Show only manually installed
        popctl scan --format json           # Output as JSON
        popctl scan --export scan.json      # Export to JSON file
        popctl scan --limit 20              # Show first 20 packages
    """
    # Skip if a subcommand is being invoked
    if ctx.invoked_subcommand is not None:
        return

    scanners = get_scanners(source)
    available_sources: list[str] = []

    # Check availability of requested scanners
    for scanner in scanners:
        if scanner.is_available():
            available_sources.append(scanner.source.value)
        else:
            print_warning(f"{scanner.source.value.upper()} package manager is not available.")

    if not available_sources:
        print_error("No package managers are available on this system.")
        raise typer.Exit(code=1)

    # Collect packages from all available sources
    packages: list[ScannedPackage] = []
    total_count = 0
    manual_count = 0
    auto_count = 0
    counts_by_source: dict[str, dict[str, int]] = {}

    for scanner in scanners:
        if not scanner.is_available():
            continue

        source_name = scanner.source.value
        counts_by_source[source_name] = {"total": 0, "manual": 0, "auto": 0}

        try:
            for pkg in scanner.scan():
                total_count += 1
                counts_by_source[source_name]["total"] += 1

                if pkg.status == PackageStatus.MANUAL:
                    manual_count += 1
                    counts_by_source[source_name]["manual"] += 1
                else:
                    auto_count += 1
                    counts_by_source[source_name]["auto"] += 1

                if manual_only and pkg.status != PackageStatus.MANUAL:
                    continue

                packages.append(pkg)

        except RuntimeError as e:
            print_error(str(e))
            raise typer.Exit(code=1) from e

    # Sort packages by source and name for consistent output
    packages.sort(key=lambda p: (p.source.value, p.name))

    # Handle export (always JSON regardless of format option)
    if export_path is not None:
        # Validate export path
        export_path = export_path.resolve()
        if export_path.is_dir():
            print_error(f"Export path is a directory: {export_path}")
            raise typer.Exit(code=1)

        scan_result = ScanResult.create(
            packages=packages,
            sources=available_sources,
            manual_only=manual_only,
        )
        try:
            export_path.parent.mkdir(parents=True, exist_ok=True)
            export_path.write_text(json.dumps(scan_result.to_dict(), indent=2))
            print_info(f"Scan results exported to {export_path}")
        except OSError as e:
            print_error(f"Failed to export: {e}")
            raise typer.Exit(code=1) from e

    # Show counts only if requested
    if count_only:
        print_info(f"Total packages: {total_count}")
        console.print(f"  [package_manual]Manual:[/] {manual_count}")
        console.print(f"  [package_auto]Auto:[/] {auto_count}")

        if len(counts_by_source) > 1:
            console.print("\n[dim]By source:[/]")
            for source_name, counts in counts_by_source.items():
                console.print(
                    f"  {source_name.upper()}: {counts['total']} "
                    f"({counts['manual']} manual, {counts['auto']} auto)"
                )
        return

    # JSON output format
    if output_format == OutputFormat.JSON:
        scan_result = ScanResult.create(
            packages=packages[:limit] if limit else packages,
            sources=available_sources,
            manual_only=manual_only,
        )
        console.print_json(json.dumps(scan_result.to_dict()))
        return

    # Table output format (default)
    display_packages = packages[:limit] if limit else packages

    title = _get_source_title(source, manual_only)
    table = create_package_table(title)

    for pkg in display_packages:
        table.add_row(*format_package_row(pkg))

    console.print(table)

    # Print summary
    displayed = len(display_packages)
    filtered_count = len(packages)

    summary_parts: list[str] = []
    if manual_only:
        summary_parts.append(f"Showing {displayed} of {manual_count} manual packages")
    else:
        summary_parts.append(f"Showing {displayed} of {total_count} packages")
        summary_parts.append(f"({manual_count} manual, {auto_count} auto)")

    if limit and displayed < filtered_count:
        summary_parts.append(f"(limited to {limit})")

    # Show source breakdown if scanning multiple sources
    if len(counts_by_source) > 1:
        source_parts = [
            f"{name.upper()}: {counts['total']}" for name, counts in counts_by_source.items()
        ]
        summary_parts.append(f"[{', '.join(source_parts)}]")

    console.print(f"\n[dim]{' '.join(summary_parts)}[/]")
