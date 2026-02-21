"""CLI-layer manifest utilities.

Functions that bridge manifest loading with CLI error handling (typer.Exit).
"""

from pathlib import Path

import typer

from popctl.core import manifest as core_manifest
from popctl.core.manifest import ManifestError, ManifestNotFoundError
from popctl.core.paths import get_manifest_path
from popctl.models.manifest import Manifest
from popctl.utils.formatting import print_error, print_info


def require_manifest(manifest_path: Path | None = None) -> Manifest:
    """Load manifest or exit with helpful error message.

    This is a convenience wrapper around load_manifest() that handles
    common error cases by printing user-friendly messages and exiting.

    Args:
        manifest_path: Optional custom manifest path.

    Returns:
        Loaded and validated Manifest.

    Raises:
        typer.Exit: If manifest cannot be loaded.
    """
    path = manifest_path or get_manifest_path()
    try:
        return core_manifest.load_manifest(path)
    except ManifestNotFoundError as e:
        print_error(f"Manifest not found: {path}")
        print_info("Run 'popctl init' to create a manifest from your current system.")
        raise typer.Exit(code=1) from e
    except ManifestError as e:
        print_error(f"Failed to load manifest: {e}")
        raise typer.Exit(code=1) from e
