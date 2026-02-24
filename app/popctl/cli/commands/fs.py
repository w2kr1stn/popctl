"""Filesystem scanning and cleanup commands.

Provides commands to scan for orphaned directories and files,
and clean up entries marked for removal in the manifest.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from popctl.cli.display import (
    export_orphan_results,
    print_deletion_plan,
    print_deletion_results,
    print_orphan_table,
)
from popctl.cli.types import OutputFormat, collect_domain_orphans, require_manifest
from popctl.core.manifest import save_manifest
from popctl.core.state import record_domain_deletions
from popctl.filesystem import FilesystemOperator
from popctl.utils.formatting import (
    console,
    format_size,
    print_info,
    print_success,
    print_warning,
)

app = typer.Typer(
    help="Filesystem scanning and cleanup.",
    invoke_without_command=True,
    no_args_is_help=True,
)


@app.command()
def scan(
    files: Annotated[
        bool,
        typer.Option("--files", help="Include individual stale files."),
    ] = False,
    include_etc: Annotated[
        bool,
        typer.Option("--include-etc", help="Include /etc in scan targets."),
    ] = False,
    output_format: Annotated[
        OutputFormat,
        typer.Option(
            "--format",
            "-f",
            help="Output format.",
            case_sensitive=False,
        ),
    ] = OutputFormat.TABLE,
    export_path: Annotated[
        Path | None,
        typer.Option(
            "--export",
            "-e",
            help="Export results to JSON file.",
        ),
    ] = None,
    limit: Annotated[
        int | None,
        typer.Option(
            "--limit",
            "-l",
            help="Limit number of results.",
        ),
    ] = None,
) -> None:
    """Scan filesystem for orphaned directories and files."""
    orphans = collect_domain_orphans("filesystem", include_files=files, include_etc=include_etc)

    if not orphans:
        print_success("Filesystem is clean. No orphaned entries found.")
        return

    # Apply limit
    display_orphans = orphans[:limit] if limit else orphans

    # Handle export
    if export_path is not None:
        export_orphan_results([p.to_dict() for p in orphans], export_path)

    # JSON output
    if output_format == OutputFormat.JSON:
        console.print_json(json.dumps([p.to_dict() for p in display_orphans]))
        return

    # Table output (default)
    print_orphan_table("Orphaned Filesystem Entries", display_orphans)

    # Summary
    total_size = sum(p.size_bytes or 0 for p in orphans)
    size_str = format_size(total_size)
    console.print(f"\n[dim]Found {len(orphans)} orphaned entries ({size_str} total)[/dim]")
    if limit and len(display_orphans) < len(orphans):
        console.print(
            f"[dim](showing {len(display_orphans)} of {len(orphans)}, limited to {limit})[/dim]"
        )


@app.command()
def clean(
    dry_run: Annotated[
        bool,
        typer.Option("--dry-run", help="Show what would be deleted."),
    ] = False,
    yes: Annotated[
        bool,
        typer.Option("--yes", "-y", help="Skip confirmation prompt."),
    ] = False,
    include_etc: Annotated[
        bool,
        typer.Option("--include-etc", help="Include /etc paths."),
    ] = False,
) -> None:
    """Clean up filesystem entries marked for removal in manifest."""
    manifest = require_manifest()

    remove_paths = manifest.get_fs_remove_paths()
    if not remove_paths:
        print_info("No filesystem entries marked for removal in manifest.")
        return

    # Filter out /etc paths unless --include-etc
    paths_to_delete: list[str] = []
    for path_str in remove_paths:
        if path_str.startswith("/etc") and not include_etc:
            print_warning(f"Skipping /etc path (use --include-etc): {path_str}")
            continue
        paths_to_delete.append(path_str)

    if not paths_to_delete:
        print_info("No filesystem entries to clean (all filtered out).")
        return

    # Display planned deletions
    print_deletion_plan(paths_to_delete, remove_paths, dry_run)

    # Confirm unless --yes or --dry-run
    if not dry_run and not yes:
        confirmed = typer.confirm(
            f"\nProceed with deleting {len(paths_to_delete)} path(s)?",
            default=False,
        )
        if not confirmed:
            print_info("Aborted.")
            raise typer.Exit(code=0)

    # Execute deletions
    operator = FilesystemOperator(dry_run=dry_run)
    results = operator.delete(paths_to_delete)

    # Display results
    print_deletion_results(results)

    # Record to history and update manifest (only actual deletions, not dry-run)
    if not dry_run:
        successful_paths = [r.path for r in results if r.success]
        if successful_paths:
            # Remove deleted paths from manifest using original tilde keys
            if manifest.filesystem:
                for result, original_path in zip(results, paths_to_delete, strict=True):
                    if result.success:
                        manifest.filesystem.remove.pop(original_path, None)
                manifest.meta.updated = datetime.now(UTC)
                try:
                    save_manifest(manifest)
                except OSError as e:
                    print_warning(f"Could not update manifest after cleanup: {e}")

            try:
                record_domain_deletions("filesystem", successful_paths, command="popctl fs clean")
                print_info("Deletions recorded to history.")
            except (OSError, RuntimeError) as e:
                print_warning(f"Could not record to history: {e}")

    # Exit with error if any deletion failed
    if any(not r.success for r in results):
        raise typer.Exit(code=1)
