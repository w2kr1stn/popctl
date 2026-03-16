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
    source = PackageSource.SNAP

    def is_available(self) -> bool:
        return command_exists("snap")

    def scan(self) -> Iterator[ScannedPackage]:
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
        """Runtime snaps: cores, bases, snapd, bare, and GNOME platform snaps."""
        if notes in _RUNTIME_NOTES:
            return True

        if name in _RUNTIME_NAMES:
            return True

        if name.startswith("core"):
            return True

        return name.startswith("gnome-") and name.endswith("-platform")
