"""Flatpak package scanner implementation.

Scans installed Flatpak applications using the flatpak CLI.
"""

import re
from collections.abc import Iterator

from popctl.models.package import PackageSource, PackageStatus, ScannedPackage
from popctl.scanners.base import Scanner
from popctl.utils.shell import command_exists, run_command


class FlatpakScanner(Scanner):
    """Scanner for Flatpak applications.

    Uses `flatpak list` to enumerate installed applications.
    Note: All Flatpak apps are considered "manual" since there is no
    automatic dependency installation like APT has.
    """

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

    @property
    def source(self) -> PackageSource:
        """Return FLATPAK as the package source."""
        return PackageSource.FLATPAK

    def is_available(self) -> bool:
        """Check if flatpak CLI is available."""
        return command_exists("flatpak")

    def scan(self) -> Iterator[ScannedPackage]:
        """Scan all installed Flatpak applications.

        Note: Only scans applications, not runtimes. Runtimes are considered
        dependencies and are not relevant for user-facing package management.

        Yields:
            ScannedPackage for each installed Flatpak app.

        Raises:
            RuntimeError: If flatpak command fails.
        """
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
        """Parse a single line of flatpak list output.

        Args:
            line: Tab-separated line from flatpak list.

        Returns:
            ScannedPackage if parsing succeeds, None otherwise.
        """
        parts = line.split("\t")
        if len(parts) < 2:
            return None

        name = parts[0].strip()
        version = parts[1].strip()

        if not name or not version:
            return None

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
        """Parse a human-readable size string to bytes.

        Args:
            size_str: Size string like "1.2 GB", "500 MB", "100 KB".

        Returns:
            Size in bytes, or None if parsing fails.
        """
        if not size_str:
            return None

        match = self._SIZE_PATTERN.match(size_str)
        if not match:
            return None

        try:
            value = float(match.group(1))
            unit = match.group(2).upper()
            multiplier = self._SIZE_MULTIPLIERS.get(unit, 1)
            return int(value * multiplier)
        except (ValueError, OverflowError):
            return None
