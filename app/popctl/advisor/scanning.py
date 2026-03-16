import json
import logging
from pathlib import Path
from typing import Any, cast

from popctl.models.package import PackageSource, PackageStatus, ScannedPackage, ScanResult
from popctl.scanners import get_available_scanners

logger = logging.getLogger(__name__)


def scan_system(input_file: Path | None = None) -> ScanResult:
    if input_file is not None:
        return _load_from_file(input_file)

    return _scan_live()


def _load_from_file(input_file: Path) -> ScanResult:
    if not input_file.exists():
        msg = f"Input file not found: {input_file}"
        raise RuntimeError(msg)

    try:
        data: dict[str, Any] = json.loads(input_file.read_text())
        packages: list[ScannedPackage] = []

        # Support both nested format {"unknown": [...]} from workspace
        # and flat list format [...] for backward compatibility
        raw_packages: Any = data.get("packages", [])
        if isinstance(raw_packages, dict):
            raw_packages = cast("dict[str, Any]", raw_packages).get("unknown", [])

        for pkg_data in raw_packages:
            pkg = ScannedPackage(
                name=pkg_data["name"],
                source=PackageSource(pkg_data["source"]),
                version=pkg_data["version"],
                status=PackageStatus(pkg_data["status"]),
                description=pkg_data.get("description"),
                size_bytes=pkg_data.get("size_bytes"),
            )
            packages.append(pkg)

        return tuple(packages)
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        msg = f"Invalid scan file format: {e}"
        raise RuntimeError(msg) from e


def _scan_live() -> ScanResult:
    scanners = get_available_scanners()
    if not scanners:
        msg = "No package managers are available on this system."
        raise RuntimeError(msg)

    packages: list[ScannedPackage] = []
    sources: list[str] = []

    for scanner in scanners:
        sources.append(scanner.source.value)
        for pkg in scanner.scan():
            packages.append(pkg)

    logger.info("Scanned %d packages from %d source(s).", len(packages), len(sources))
    return tuple(packages)
