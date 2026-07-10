import json
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer
from rich.table import Table

from popctl.domain.models import DomainActionResult, ScannedEntry
from popctl.models.action import Action, ActionResult, ActionType
from popctl.models.manifest import DomainEntry
from popctl.models.package import PackageSource
from popctl.utils.formatting import (
    console,
    format_size,
    print_error,
    print_info,
    print_success,
    print_warning,
)

if TYPE_CHECKING:
    from popctl.models.package import ScannedPackage

# Source icons for package display
SOURCE_ICONS: dict[PackageSource, str] = {
    PackageSource.APT: "📦",
    PackageSource.FLATPAK: "📀",
    PackageSource.SNAP: "📥",
}


def create_actions_table(actions: list[Action], dry_run: bool = False) -> Table:
    title = "Planned Actions (Dry Run)" if dry_run else "Planned Actions"

    table = Table(
        title=title,
        show_header=True,
        header_style="bold_header",
        border_style="border",
    )
    table.add_column("Action", width=8, justify="center")
    table.add_column("Source", width=8)
    table.add_column("Package", no_wrap=True)

    for action in actions:
        # Style based on action type
        if action.action_type == ActionType.INSTALL:
            action_text = "[added]+install[/added]"
            pkg_style = "added"
        elif action.action_type == ActionType.PURGE:
            action_text = "[removed]-purge[/removed]"
            pkg_style = "removed"
        else:  # REMOVE
            action_text = "[warning]-remove[/warning]"
            pkg_style = "warning"

        table.add_row(
            action_text,
            action.source.value,
            f"[{pkg_style}]{action.package}[/{pkg_style}]",
        )

    return table


def create_results_table(results: list[ActionResult]) -> Table:
    table = Table(
        title="Results",
        show_header=True,
        header_style="bold_header",
        border_style="border",
    )
    table.add_column("Status", width=8, justify="center")
    table.add_column("Action", width=8)
    table.add_column("Package", no_wrap=True)
    table.add_column("Message")

    for result in results:
        if result.success:
            status = "[success]OK[/success]"
            message = result.detail or ""
        else:
            status = "[error]FAIL[/error]"
            message = result.detail or "Unknown error"

        action_type = result.action.action_type.value

        table.add_row(
            status,
            action_type,
            result.action.package,
            f"[muted]{message}[/muted]",
        )

    return table


def print_actions_summary(actions: list[Action]) -> None:
    install_count = sum(1 for a in actions if a.action_type == ActionType.INSTALL)
    remove_count = sum(1 for a in actions if a.action_type == ActionType.REMOVE)
    purge_count = sum(1 for a in actions if a.action_type == ActionType.PURGE)

    parts: list[str] = []
    if install_count:
        parts.append(f"[added]{install_count} to install[/added]")
    if remove_count:
        parts.append(f"[warning]{remove_count} to remove[/warning]")
    if purge_count:
        parts.append(f"[removed]{purge_count} to purge[/removed]")

    if parts:
        summary = ", ".join(parts)
        console.print(f"\nSummary: {summary}")


def print_results_summary(results: list[ActionResult]) -> None:
    success_count = sum(1 for r in results if r.success)
    fail_count = sum(1 for r in results if r.failed)

    if fail_count == 0:
        print_success(f"All {success_count} action(s) completed successfully.")
    else:
        console.print(
            f"\n[success]{success_count} succeeded[/success], [error]{fail_count} failed[/error]"
        )


def print_orphan_table(
    title: str,
    orphans: Sequence[ScannedEntry],
    *,
    limit: int | None = None,
    hint_cmd: str | None = None,
) -> None:
    display = orphans[:limit] if limit else orphans

    table = Table(title=title, show_lines=False)
    table.add_column("Path", style="bold")
    table.add_column("Type", width=10)
    table.add_column("Size", justify="right", width=10)
    table.add_column("Confidence", justify="right", width=10)
    table.add_column("Reason", style="dim")

    for item in display:
        size_str = format_size(item.size_bytes) if item.size_bytes else "-"
        conf_str = f"{item.confidence:.0%}"
        reason = item.orphan_reason.value if item.orphan_reason else "-"
        table.add_row(item.path, item.path_type.value, size_str, conf_str, reason)

    console.print(table)

    if limit and len(orphans) > limit and hint_cmd:
        console.print(
            f"[dim]... and {len(orphans) - limit} more. Use '{hint_cmd}' for full list.[/dim]"
        )


def print_deletion_plan(
    paths: list[str],
    entries: dict[str, DomainEntry],
    dry_run: bool,
) -> None:
    label = "Planned Deletions (dry-run)" if dry_run else "Planned Deletions"
    table = Table(title=label, show_lines=False)
    table.add_column("Path", style="bold")
    table.add_column("Reason", style="dim")

    for path_str in paths:
        entry = entries.get(path_str)
        reason = "-"
        if entry is not None and entry.reason:
            reason = entry.reason
        table.add_row(path_str, reason)

    console.print(table)


def export_orphan_results(data: list[dict[str, Any]], export_path: Path) -> None:
    export_path = export_path.resolve()
    if export_path.is_dir():
        print_error(f"Export path is a directory: {export_path}")
        raise typer.Exit(code=1)

    try:
        export_path.parent.mkdir(parents=True, exist_ok=True)
        export_path.write_text(json.dumps(data, indent=2))
        print_info(f"Results exported to {export_path}")
    except OSError as e:
        print_error(f"Failed to export: {e}")
        raise typer.Exit(code=1) from e


def display_orphan_scan(
    domain: str,
    orphans: Sequence[ScannedEntry],
    *,
    output_format: str,
    export_path: Path | None,
    limit: int | None,
    summary_noun: str = "entries",
) -> None:
    display_orphans = orphans[:limit] if limit else list(orphans)

    if export_path is not None:
        export_orphan_results([e.to_dict() for e in orphans], export_path)

    if output_format == "json":
        console.print_json(json.dumps([e.to_dict() for e in display_orphans]))
        return

    print_orphan_table(f"Orphaned {domain.capitalize()} Entries", display_orphans)

    total_size = sum(e.size_bytes or 0 for e in orphans)
    size_str = format_size(total_size)
    console.print(
        f"\n[dim]Found {len(orphans)} orphaned {summary_noun} ({size_str} total)[/dim]"
    )
    if limit and len(display_orphans) < len(orphans):
        console.print(
            f"[dim](showing {len(display_orphans)} of {len(orphans)}, limited to {limit})[/dim]"
        )


def print_deletion_results(
    results: Sequence[DomainActionResult],
    show_backup: bool = False,
) -> None:
    third_col = "Backup" if show_backup else "Details"
    table = Table(title="Deletion Results", show_lines=False)
    table.add_column("Path", style="bold")
    table.add_column("Status", width=10)
    table.add_column(third_col, style="dim")

    for r in results:
        backup = r.backup_path
        if r.dry_run:
            status = "[info]dry-run[/]"
            detail = (backup or "-") if show_backup else "Would delete"
        elif r.success:
            status = "[success]deleted[/]"
            detail = (backup or "no backup") if show_backup else ""
        else:
            status = "[error]failed[/]"
            detail = r.error or "Unknown error"
        table.add_row(r.path, status, detail)

    console.print(table)

    success_count = sum(1 for r in results if r.success)
    fail_count = sum(1 for r in results if not r.success and not r.dry_run)
    dry_count = sum(1 for r in results if r.dry_run)

    if dry_count:
        print_info(f"Dry-run: {dry_count} path(s) would be deleted.")
    elif fail_count:
        print_warning(f"{success_count} succeeded, {fail_count} failed")
    else:
        print_success(f"All {success_count} path(s) processed successfully.")


def create_package_table(title: str = "Installed Packages") -> Table:
    table = Table(
        title=title,
        show_header=True,
        header_style="bold_header",
        border_style="border",
        row_styles=["", "on grey7"],  # Zebra striping for readability
    )
    # Status column: minimal width, icon only, no header text
    table.add_column("", width=2, justify="center")
    # Source column: emoji icon for package source
    table.add_column("", width=2, justify="center")
    table.add_column("Package", no_wrap=True)  # Style set per row
    table.add_column("Version", style="muted")
    table.add_column("Size", style="info", justify="right")
    table.add_column("Description", style="text", overflow="ellipsis")
    return table


def format_package_row(pkg: ScannedPackage) -> tuple[str, str, str, str, str, str]:
    if pkg.is_manual:
        status_icon = "[package_manual]\u25cf[/]"  # Filled circle
        name = f"[package_manual]{pkg.name}[/]"  # bold is in the style
    else:
        status_icon = "[package_auto]\u25cb[/]"  # Empty circle
        name = f"[package_auto]{pkg.name}[/]"

    source_icon = SOURCE_ICONS.get(pkg.source, "?")

    version = f"[muted]{pkg.version}[/]"
    size_str = format_size(pkg.size_bytes) if pkg.size_bytes is not None else "unknown"
    size = f"[info]{size_str}[/]"
    desc = f"[text]{pkg.description or '-'}[/]"

    return (status_icon, source_icon, name, version, size, desc)
