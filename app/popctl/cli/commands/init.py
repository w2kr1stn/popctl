"""Init command implementation.

Creates a manifest.toml file from the current system state.
"""

from pathlib import Path
from typing import Annotated

import typer

from popctl.core.manifest import (
    manifest_exists,
    save_manifest,
    scan_and_create_manifest,
)
from popctl.core.paths import get_manifest_path
from popctl.models.manifest import Manifest
from popctl.scanners import get_available_scanners
from popctl.utils.formatting import (
    console,
    print_error,
    print_info,
    print_success,
    print_warning,
)

app = typer.Typer(
    help="Initialize manifest from current system state.",
    invoke_without_command=True,
)


def _show_manifest_summary(
    manifest: Manifest,
    output_path: Path,
    skipped_protected: list[str] | None = None,
) -> None:
    """Display a summary of the created manifest.

    Args:
        manifest: The manifest to summarize.
        output_path: Path where manifest will be saved.
        skipped_protected: List of protected package names that were skipped.
    """
    apt_count = len(manifest.get_keep_packages("apt"))
    flatpak_count = len(manifest.get_keep_packages("flatpak"))
    total = apt_count + flatpak_count

    console.print()
    console.print("[bold]Manifest Summary[/bold]")
    console.print(f"  System: [info]{manifest.system.name}[/info]")
    console.print(f"  Base: [muted]{manifest.system.base}[/muted]")
    console.print(f"  Output: [muted]{output_path}[/muted]")
    console.print()
    console.print(f"  Total packages: [bold]{total}[/bold]")
    if apt_count > 0:
        console.print(f"    APT: [package_manual]{apt_count}[/package_manual]")
    if flatpak_count > 0:
        console.print(f"    Flatpak: [package_manual]{flatpak_count}[/package_manual]")

    # Show skipped protected packages for transparency
    if skipped_protected:
        skipped_list = ", ".join(sorted(skipped_protected))
        console.print(
            f"  [muted]Skipped {len(skipped_protected)} protected: {skipped_list}[/muted]"
        )

    console.print()


@app.callback(invoke_without_command=True)
def init_manifest(
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Output path for manifest file.",
        ),
    ] = None,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            "-f",
            help="Overwrite existing manifest without prompting.",
        ),
    ] = False,
    dry_run: Annotated[
        bool,
        typer.Option(
            "--dry-run",
            "-n",
            help="Show what would be created without writing files.",
        ),
    ] = False,
) -> None:
    """Initialize a new manifest from the current system state.

    Scans the system for installed packages (APT and Flatpak) and creates
    a manifest.toml file tracking manually installed packages.

    Protected system packages (kernels, systemd, Pop!_OS core, etc.) and
    auto-installed dependencies are excluded from the manifest.

    Examples:
        popctl init                    # Create manifest in default location
        popctl init --output my.toml   # Create manifest at custom path
        popctl init --force            # Overwrite existing manifest
        popctl init --dry-run          # Preview without writing
    """
    # Determine output path
    output_path = output or get_manifest_path()

    # Check for existing manifest
    if manifest_exists(output_path):
        if dry_run:
            print_warning(f"Manifest already exists: {output_path}")
            print_info("Would be overwritten with --force.")
        elif not force:
            print_error(f"Manifest already exists: {output_path}")
            print_info("Use --force to overwrite or specify a different path with --output.")
            raise typer.Exit(code=1)
        else:
            print_warning(f"Overwriting existing manifest: {output_path}")

    # Get available scanners
    scanners = get_available_scanners()
    if not scanners:
        print_error("No package managers available (APT or Flatpak required).")
        raise typer.Exit(code=1)

    # Report which scanners are available
    source_names = [s.source.value.upper() for s in scanners]
    print_info(f"Scanning system packages: {', '.join(source_names)}")

    # Collect packages and create manifest
    try:
        manifest, packages, skipped_protected = scan_and_create_manifest(scanners)
    except RuntimeError as e:
        print_error(f"Scan failed: {e}")
        raise typer.Exit(code=1) from e

    if not packages:
        print_warning("No manually installed packages found (excluding protected system packages).")

    # Show summary
    _show_manifest_summary(manifest, output_path, skipped_protected)

    # Handle dry-run
    if dry_run:
        print_info("[DRY-RUN] No files were written.")
        return

    # Save manifest
    try:
        saved_path = save_manifest(manifest, output_path)
        print_success(f"Manifest created: {saved_path}")
    except (OSError, PermissionError) as e:
        print_error(f"Failed to save manifest: {e}")
        raise typer.Exit(code=1) from e
