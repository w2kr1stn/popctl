"""Snap package scanner implementation.

Scans installed Snap applications using the snap CLI.
"""

import logging
from collections.abc import Iterator

from popctl.models.package import PackageSource, PackageStatus, ScannedPackage
from popctl.scanners.base import Scanner
from popctl.utils.shell import command_exists, run_command

logger = logging.getLogger(__name__)

# Notes values that indicate runtime/infrastructure snaps
_RUNTIME_NOTES: frozenset[str] = frozenset({"base", "snapd"})

# Exact snap names that are always runtime infrastructure
_RUNTIME_NAMES: frozenset[str] = frozenset({"snapd", "bare"})


class SnapScanner(Scanner):
    """Scanner for Snap packages.

    Uses ``snap list`` to enumerate installed snaps. Runtime and
    infrastructure snaps (cores, bases, snapd) are filtered out;
    all remaining snaps are classified as MANUAL.
    """

    @property
    def source(self) -> PackageSource:
        """Return SNAP as the package source."""
        return PackageSource.SNAP

    def is_available(self) -> bool:
        """Check if snap CLI is available."""
        return command_exists("snap")

    def scan(self) -> Iterator[ScannedPackage]:
        """Scan all installed Snap packages.

        Filters out runtime/infrastructure snaps (cores, bases, snapd)
        and yields user-facing applications only.

        Yields:
            ScannedPackage for each installed user-facing snap.

        Raises:
            RuntimeError: If snap is not available or snap list fails.
        """
        if not self.is_available():
            msg = "Snap is not available on this system"
            raise RuntimeError(msg)

        result = run_command(["snap", "list"])

        if not result.success:
            msg = f"snap list failed: {result.stderr}"
            raise RuntimeError(msg)

        lines = result.stdout.strip().split("\n")

        # Skip header line ("Name  Version  Rev  Tracking  Publisher  Notes")
        for line in lines[1:]:
            if not line.strip():
                continue

            package = self._parse_snap_line(line)
            if package is not None:
                yield package

    def _parse_snap_line(self, line: str) -> ScannedPackage | None:
        """Parse a single line of snap list output.

        Args:
            line: Whitespace-separated line from snap list.

        Returns:
            ScannedPackage if the snap is a user-facing app, None if it
            is a runtime snap or the line is malformed.
        """
        parts = line.split()
        if len(parts) < 6:
            logger.debug("Skipping malformed snap line (parts=%d): %r", len(parts), line[:100])
            return None

        name = parts[0]
        version = parts[1]
        notes = parts[5]

        if self._is_runtime_snap(name, notes):
            return None

        return ScannedPackage(
            name=name,
            source=PackageSource.SNAP,
            version=version,
            status=PackageStatus.MANUAL,
            description=None,
            size_bytes=None,
        )

    @staticmethod
    def _is_runtime_snap(name: str, notes: str) -> bool:
        """Check whether a snap is a runtime/infrastructure snap.

        Runtime snaps include cores, bases, snapd itself, the bare snap,
        and GNOME platform snaps.

        Args:
            name: Snap package name.
            notes: Value from the Notes column of ``snap list``.

        Returns:
            True if the snap should be filtered out.
        """
        if notes in _RUNTIME_NOTES:
            return True

        if name in _RUNTIME_NAMES:
            return True

        if name.startswith("core"):
            return True

        return name.startswith("gnome-") and name.endswith("-platform")
