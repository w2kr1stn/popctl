from pathlib import Path
from typing import Annotated

import typer

from popctl.cli.display import (
    display_orphan_scan,
    print_deletion_plan,
    print_deletion_results,
)
from popctl.cli.types import (
    OutputFormat,
    collect_domain_orphans,
    post_clean_update,
    require_manifest,
)
from popctl.filesystem import FilesystemOperator
from popctl.utils.formatting import (
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

    display_orphan_scan(
        "filesystem",
        orphans,
        output_format=output_format.value,
        export_path=export_path,
        limit=limit,
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

    remove_paths = manifest.get_domain_remove("filesystem")
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
        post_clean_update(
            manifest, "filesystem", results, paths_to_delete, command="popctl fs clean"
        )

    # Exit with error if any deletion failed
    if any(not r.success for r in results):
        raise typer.Exit(code=1)
