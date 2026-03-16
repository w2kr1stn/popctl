from dataclasses import dataclass, field
from enum import Enum


class PackageSource(Enum):
    APT = "apt"
    FLATPAK = "flatpak"
    SNAP = "snap"


# Derived constant: all source key strings for iteration.
# Use this instead of hardcoding ("apt", "flatpak") to avoid missing sources.
PACKAGE_SOURCE_KEYS: tuple[str, ...] = tuple(s.value for s in PackageSource)


class PackageStatus(Enum):
    MANUAL = "manual"
    AUTO_INSTALLED = "auto"


@dataclass(frozen=True, slots=True)
class ScannedPackage:
    name: str
    source: PackageSource
    version: str
    status: PackageStatus
    description: str | None = field(default=None)
    size_bytes: int | None = field(default=None)

    def __post_init__(self) -> None:
        if not self.name:
            msg = "Package name cannot be empty"
            raise ValueError(msg)
        if not self.version:
            msg = "Package version cannot be empty"
            raise ValueError(msg)

    @property
    def is_manual(self) -> bool:
        return self.status == PackageStatus.MANUAL


# Type alias replacing the former ScanResult dataclass (models/scan_result.py)
ScanResult = tuple[ScannedPackage, ...]
