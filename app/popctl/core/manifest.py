import os
import socket
import tomllib
from datetime import UTC, datetime
from pathlib import Path
from tempfile import NamedTemporaryFile

import tomli_w
from pydantic import ValidationError

from popctl.core.baseline import is_package_protected
from popctl.core.paths import get_manifest_path
from popctl.models.manifest import (
    Manifest,
    ManifestMeta,
    PackageConfig,
    PackageEntry,
    SystemConfig,
)
from popctl.models.package import PackageStatus
from popctl.scanners.base import Scanner


class ManifestError(Exception): ...


class ManifestNotFoundError(ManifestError): ...


class ManifestParseError(ManifestError): ...


class ManifestValidationError(ManifestError): ...


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
    manifest_path = path or get_manifest_path()

    # Ensure parent directory exists
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    # Convert manifest to dictionary with proper serialization
    data = manifest.model_dump(mode="json", exclude_none=True)

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
        # os.replace() is atomic on POSIX (same filesystem guaranteed by temp file in same dir)
        os.replace(str(tmp_path), str(manifest_path))
    except OSError as e:
        # Cleanup temp file on failure
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink()
        raise ManifestError(f"Failed to write manifest: {e}") from e

    return manifest_path


def manifest_exists(path: Path | None = None) -> bool:
    manifest_path = path or get_manifest_path()
    return manifest_path.exists()


# ---------------------------------------------------------------------------
# Manifest creation helpers
# ---------------------------------------------------------------------------


def collect_manual_packages(
    scanners: list[Scanner],
) -> tuple[dict[str, PackageEntry], list[str]]:
    packages: dict[str, PackageEntry] = {}
    skipped_protected: list[str] = []

    for scanner in scanners:
        source_name = scanner.source.value

        for pkg in scanner.scan():
            # Skip auto-installed packages (dependencies)
            if pkg.status != PackageStatus.MANUAL:
                continue

            # Skip protected system packages (but track them)
            if is_package_protected(pkg.name):
                skipped_protected.append(pkg.name)
                continue

            packages[pkg.name] = PackageEntry(
                source=source_name,  # type: ignore[arg-type]
            )

    return packages, skipped_protected


def scan_and_create_manifest(
    scanners: list[Scanner],
) -> tuple[Manifest, dict[str, PackageEntry], list[str]]:
    packages, skipped = collect_manual_packages(scanners)
    manifest = create_manifest(packages)
    return manifest, packages, skipped


def create_manifest(packages: dict[str, PackageEntry]) -> Manifest:
    now = datetime.now(UTC)

    return Manifest(
        meta=ManifestMeta(
            created=now,
            updated=now,
        ),
        system=SystemConfig(
            name=socket.gethostname(),
        ),
        packages=PackageConfig(
            keep=packages,
            remove={},
        ),
    )
