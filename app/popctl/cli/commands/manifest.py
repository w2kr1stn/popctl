"""Manifest management commands.

Provides commands to manually move packages between keep and remove
sections of the manifest.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

import typer

from popctl.core.manifest import ManifestError, load_manifest, save_manifest
from popctl.utils.formatting import print_info, print_success, print_warning

app = typer.Typer(
    help="Manage the system manifest.",
    no_args_is_help=True,
)


@app.command()
def keep(
    packages: Annotated[
        list[str],
        typer.Argument(help="Package names to mark as keep."),
    ],
) -> None:
    """Move packages from remove to keep.

    Packages already in keep or not in the manifest are skipped.

    Examples:
        popctl manifest keep libllvm19 slirp4netns
    """
    _move_packages(packages, direction="keep")


@app.command()
def remove(
    packages: Annotated[
        list[str],
        typer.Argument(help="Package names to mark for removal."),
    ],
) -> None:
    """Move packages from keep to remove.

    Packages already in remove or not in the manifest are skipped.

    Examples:
        popctl manifest remove libllvm19 slirp4netns
    """
    _move_packages(packages, direction="remove")


def _move_packages(packages: list[str], *, direction: str) -> None:
    """Move packages between keep and remove sections."""
    try:
        manifest = load_manifest()
    except ManifestError as e:
        print_warning(f"Could not load manifest: {e}")
        raise typer.Exit(code=1) from e

    if direction == "keep":
        source_dict = manifest.packages.remove
        target_dict = manifest.packages.keep
    else:
        source_dict = manifest.packages.keep
        target_dict = manifest.packages.remove

    moved: list[str] = []
    for name in packages:
        if name in target_dict:
            print_info(f"{name}: already in {direction}")
            continue
        entry = source_dict.pop(name, None)
        if entry is None:
            print_warning(f"{name}: not found in manifest")
            continue
        target_dict[name] = entry
        moved.append(name)

    if not moved:
        print_info("No changes made.")
        return

    manifest.meta.updated = datetime.now(UTC)
    try:
        save_manifest(manifest)
    except (OSError, ManifestError) as e:
        print_warning(f"Could not save manifest: {e}")
        raise typer.Exit(code=1) from e

    for name in moved:
        print_success(f"{name}: moved to {direction}")
    print_success(f"{len(moved)} package(s) updated.")
