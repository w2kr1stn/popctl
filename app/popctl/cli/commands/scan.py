"""Scan command implementation.

Lists installed packages from various package managers.
"""

from typing import Annotated

import typer

from popctl.models.package import PackageStatus, ScannedPackage
from popctl.scanners.apt import AptScanner
from popctl.utils.formatting import (
    console,
    create_package_table,
    format_package_row,
    print_error,
    print_info,
)

app = typer.Typer(
    help="Scan system for installed packages.",
    invoke_without_command=True,
)


@app.callback(invoke_without_command=True)
def scan_packages(
    ctx: typer.Context,
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
) -> None:
    """Scan and display installed packages.

    By default, shows all APT packages in a table format.

    Examples:
        popctl scan                    # Show all packages
        popctl scan --manual-only      # Show only manually installed
        popctl scan --count            # Show counts only
        popctl scan --limit 20         # Show first 20 packages
    """
    # Skip if a subcommand is being invoked
    if ctx.invoked_subcommand is not None:
        return

    scanner = AptScanner()

    if not scanner.is_available():
        print_error("APT package manager is not available on this system.")
        raise typer.Exit(code=1)

    # Collect packages
    packages: list[ScannedPackage] = []
    total_count = 0
    manual_count = 0
    auto_count = 0

    try:
        for pkg in scanner.scan():
            total_count += 1
            if pkg.status == PackageStatus.MANUAL:
                manual_count += 1
            else:
                auto_count += 1

            if manual_only and pkg.status != PackageStatus.MANUAL:
                continue

            packages.append(pkg)

    except RuntimeError as e:
        print_error(str(e))
        raise typer.Exit(code=1) from e

    # Show counts only if requested
    if count_only:
        print_info(f"Total packages: {total_count}")
        console.print(f"  [package.manual]Manual:[/] {manual_count}")
        console.print(f"  [package.auto]Auto:[/] {auto_count}")
        return

    # Apply limit if specified
    display_packages = packages[:limit] if limit else packages

    # Create and populate table
    title = "Manually Installed Packages" if manual_only else "Installed Packages (APT)"
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

    console.print(f"\n[dim]{' '.join(summary_parts)}[/]")
