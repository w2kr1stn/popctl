"""Manifest file I/O operations.

This module provides functions for loading and saving manifest files
in TOML format with proper validation using Pydantic models.
"""

import tomllib
from pathlib import Path
from typing import Any

import tomli_w
from pydantic import ValidationError

from popctl.core.paths import get_manifest_path
from popctl.models.manifest import Manifest


class ManifestError(Exception):
    """Base exception for manifest-related errors."""


class ManifestNotFoundError(ManifestError):
    """Raised when manifest file is not found."""


class ManifestParseError(ManifestError):
    """Raised when manifest file cannot be parsed."""


class ManifestValidationError(ManifestError):
    """Raised when manifest content is invalid."""


def load_manifest(path: Path | None = None) -> Manifest:
    """Load and validate a manifest from a TOML file.

    Args:
        path: Path to the manifest file. If None, uses default manifest path.

    Returns:
        Validated Manifest object.

    Raises:
        ManifestNotFoundError: If the manifest file doesn't exist.
        ManifestParseError: If the TOML syntax is invalid.
        ManifestValidationError: If the content doesn't match the schema.
    """
    manifest_path = path or get_manifest_path()

    if not manifest_path.exists():
        raise ManifestNotFoundError(f"Manifest not found: {manifest_path}")

    try:
        with open(manifest_path, "rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise ManifestParseError(f"Invalid TOML syntax: {e}") from e
    except OSError as e:
        raise ManifestError(f"Failed to read manifest: {e}") from e

    try:
        return Manifest.model_validate(data)
    except ValidationError as e:
        raise ManifestValidationError(f"Invalid manifest content: {e}") from e


def save_manifest(manifest: Manifest, path: Path | None = None) -> Path:
    """Save a manifest to a TOML file.

    The file is written atomically by first writing to a temporary file
    in the same directory and then using os.replace() for atomic rename.
    The temporary file is cleaned up on failure.

    Args:
        manifest: The Manifest object to save.
        path: Path to save the manifest. If None, uses default manifest path.

    Returns:
        Path where the manifest was saved.

    Raises:
        ManifestError: If the file cannot be written.
    """
    import os
    from tempfile import NamedTemporaryFile

    manifest_path = path or get_manifest_path()

    # Ensure parent directory exists
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    # Convert manifest to dictionary with proper serialization
    data = _manifest_to_dict(manifest)

    tmp_path: Path | None = None
    try:
        # Write atomically using a temporary file in the same directory
        with NamedTemporaryFile(
            mode="wb",
            dir=manifest_path.parent,
            delete=False,
            suffix=".tmp",
        ) as f:
            tmp_path = Path(f.name)
            tomli_w.dump(data, f)
        # os.replace() is atomic on POSIX and handles cross-filesystem moves
        os.replace(str(tmp_path), str(manifest_path))
    except OSError as e:
        # Cleanup temp file on failure
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink()
        raise ManifestError(f"Failed to write manifest: {e}") from e

    return manifest_path


def manifest_exists(path: Path | None = None) -> bool:
    """Check if a manifest file exists.

    Args:
        path: Path to check. If None, uses default manifest path.

    Returns:
        True if the manifest file exists, False otherwise.
    """
    manifest_path = path or get_manifest_path()
    return manifest_path.exists()


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
    import typer

    from popctl.utils.formatting import print_error, print_info

    path = manifest_path or get_manifest_path()
    try:
        return load_manifest(path)
    except ManifestNotFoundError as e:
        print_error(f"Manifest not found: {path}")
        print_info("Run 'popctl init' to create a manifest from your current system.")
        raise typer.Exit(code=1) from e
    except ManifestError as e:
        print_error(f"Failed to load manifest: {e}")
        raise typer.Exit(code=1) from e


def _manifest_to_dict(manifest: Manifest) -> dict[str, Any]:
    """Convert a Manifest to a dictionary suitable for TOML serialization.

    Handles datetime conversion and nested structures properly.

    Args:
        manifest: The Manifest object to convert.

    Returns:
        Dictionary ready for TOML serialization.
    """
    return {
        "meta": {
            "version": manifest.meta.version,
            "created": manifest.meta.created.isoformat(),
            "updated": manifest.meta.updated.isoformat(),
        },
        "system": {
            "name": manifest.system.name,
            "base": manifest.system.base,
            **({"description": manifest.system.description} if manifest.system.description else {}),
        },
        "packages": {
            "keep": {
                name: _package_entry_to_dict(entry)
                for name, entry in manifest.packages.keep.items()
            },
            "remove": {
                name: _package_entry_to_dict(entry)
                for name, entry in manifest.packages.remove.items()
            },
        },
    }


def _package_entry_to_dict(entry: Any) -> dict[str, Any]:
    """Convert a PackageEntry to a dictionary for TOML serialization.

    Args:
        entry: The PackageEntry object to convert.

    Returns:
        Dictionary with source and optional reason.
    """
    result: dict[str, Any] = {"source": entry.source}
    if entry.status != "keep":
        result["status"] = entry.status
    if entry.reason:
        result["reason"] = entry.reason
    return result
