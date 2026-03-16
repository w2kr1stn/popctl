import logging
from abc import ABC, abstractmethod
from collections.abc import Iterator

from popctl.models.package import PackageSource, ScannedPackage

logger = logging.getLogger(__name__)


def parse_tab_fields(
    line: str,
    source_label: str,
    min_fields: int = 2,
) -> tuple[str, str, list[str]] | None:
    """Parse a tab-separated line into (name, version, remaining_parts).

    Shared guard-clause logic for scanners that split on tabs, check
    minimum field count, and validate name/version. Returns None on
    malformed input.
    """
    parts = line.split("\t")
    if len(parts) < min_fields:
        logger.debug(
            "Skipping malformed %s line (parts=%d): %r",
            source_label,
            len(parts),
            line[:100],
        )
        return None

    name = parts[0].strip()
    version = parts[1].strip()

    if not name or not version:
        logger.debug(
            "Skipping %s line with empty name/version: %r",
            source_label,
            line[:100],
        )
        return None

    return name, version, parts


class Scanner(ABC):
    source: PackageSource

    @abstractmethod
    def scan(self) -> Iterator[ScannedPackage]: ...

    @abstractmethod
    def is_available(self) -> bool: ...
