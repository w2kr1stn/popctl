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
from popctl.configs import ConfigOperator
from popctl.domain.protected import is_protected
from popctl.utils.formatting import (
    print_info,
    print_success,
    print_warning,
)

app = typer.Typer(
    help="Scan and clean orphaned configuration files.",
    invoke_without_command=True,
    no_args_is_help=True,
)


@app.command()
def scan(
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
    """Scan ~/.config/ and shell dotfiles for orphaned configurations."""
    orphans = collect_domain_orphans("configs")

    if not orphans:
        print_success("Configs are clean. No orphaned configurations found.")
        return

    display_orphan_scan(
        "configuration",
        orphans,
        output_format=output_format.value,
        export_path=export_path,
        limit=limit,
        summary_noun="configs",
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
) -> None:
    """Clean up config entries marked for removal in manifest."""
    manifest = require_manifest()

    remove_paths = manifest.get_domain_remove("configs")
    if not remove_paths:
        print_info("No config entries marked for removal in manifest.")
        return

    # Check for protected configs
    paths_to_delete: list[str] = []
    for path_str in remove_paths:
        if is_protected(path_str, "configs"):
            print_warning(f"Skipping protected config: {path_str}")
            continue
        paths_to_delete.append(path_str)

    if not paths_to_delete:
        print_info("No config entries to clean (all protected or filtered out).")
        return

    # Display planned deletions
    print_deletion_plan(paths_to_delete, remove_paths, dry_run)

    # Confirm unless --yes or --dry-run
    if not dry_run and not yes:
        confirmed = typer.confirm(
            f"\nProceed with deleting {len(paths_to_delete)} config(s)?",
            default=False,
        )
        if not confirmed:
            print_info("Aborted.")
            raise typer.Exit(code=0)

    # Execute deletions
    operator = ConfigOperator(dry_run=dry_run)
    results = operator.delete(paths_to_delete)

    # Display results
    print_deletion_results(results, show_backup=True)

    # Record to history and update manifest (only actual deletions, not dry-run)
    if not dry_run:
        post_clean_update(
            manifest, "configs", results, paths_to_delete, command="popctl config clean"
        )

    # Exit with error if any deletion failed
    if any(not r.success for r in results):
        raise typer.Exit(code=1)
