import logging
import re
from collections.abc import Iterator

from popctl.models.package import PackageSource, PackageStatus, ScannedPackage
from popctl.scanners.base import Scanner, parse_tab_fields
from popctl.utils.shell import command_exists, run_command

logger = logging.getLogger(__name__)


class FlatpakScanner(Scanner):
    # Regex pattern for parsing size strings like "1.2 GB", "500 MB", "100 KB"
    _SIZE_PATTERN = re.compile(r"^\s*([\d.]+)\s*(B|KB|MB|GB|TB)\s*$", re.IGNORECASE)

    # Size multipliers for converting to bytes
    _SIZE_MULTIPLIERS: dict[str, int] = {
        "B": 1,
        "KB": 1024,
        "MB": 1024 * 1024,
        "GB": 1024 * 1024 * 1024,
        "TB": 1024 * 1024 * 1024 * 1024,
    }

    source = PackageSource.FLATPAK

    def is_available(self) -> bool:
        return command_exists("flatpak")

    def scan(self) -> Iterator[ScannedPackage]:
        if not self.is_available():
            msg = "Flatpak is not available on this system"
            raise RuntimeError(msg)

        # Query installed apps (not runtimes)
        # Format: application, version, size, description (tab-separated)
        result = run_command(
            [
                "flatpak",
                "list",
                "--app",
                "--columns=application,version,size,description",
            ],
        )

        if not result.success:
            msg = f"flatpak list failed: {result.stderr}"
            raise RuntimeError(msg)

        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue

            package = self._parse_flatpak_line(line)
            if package is not None:
                yield package

    def _parse_flatpak_line(self, line: str) -> ScannedPackage | None:
        parsed = parse_tab_fields(line, "flatpak")
        if parsed is None:
            return None

        name, version, parts = parsed

        # Parse optional fields
        size_bytes: int | None = None
        description: str | None = None

        if len(parts) >= 3:
            size_bytes = self._parse_size(parts[2].strip())

        if len(parts) >= 4:
            description = parts[3].strip() or None

        # All Flatpak apps are considered manually installed
        # (there's no auto-dependency installation like APT)
        return ScannedPackage(
            name=name,
            source=PackageSource.FLATPAK,
            version=version,
            status=PackageStatus.MANUAL,
            description=description,
            size_bytes=size_bytes,
        )

    def _parse_size(self, size_str: str) -> int | None:
        if not size_str:
            return None

        match = self._SIZE_PATTERN.match(size_str)
        if not match:
            logger.debug("Could not parse size string: %r", size_str)
            return None

        try:
            value = float(match.group(1))
            unit = match.group(2).upper()
            multiplier = self._SIZE_MULTIPLIERS.get(unit, 1)
            return int(value * multiplier)
        except (ValueError, OverflowError) as e:
            logger.warning("Failed to parse size %r: %s", size_str, e)
            return None
