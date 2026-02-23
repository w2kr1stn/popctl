"""APT package scanner implementation.

Scans installed packages using dpkg-query and determines
installation status using apt-mark.
"""

import logging
from collections.abc import Iterator

from popctl.models.package import PackageSource, PackageStatus, ScannedPackage
from popctl.scanners.base import Scanner
from popctl.utils.shell import command_exists, run_command

logger = logging.getLogger(__name__)


class AptScanner(Scanner):
    """Scanner for APT/dpkg packages.

    Uses dpkg-query to list installed packages and apt-mark
    to distinguish between manually and automatically installed packages.
    """

    # dpkg-query format string: Package, Version, Installed-Size (KB), Description
    _DPKG_FORMAT = "${Package}\\t${Version}\\t${Installed-Size}\\t${binary:Summary}\\n"

    @property
    def source(self) -> PackageSource:
        """Return APT as the package source."""
        return PackageSource.APT

    def is_available(self) -> bool:
        """Check if dpkg and apt-mark are available."""
        return command_exists("dpkg-query") and command_exists("apt-mark")

    def scan(self) -> Iterator[ScannedPackage]:
        """Scan all installed APT packages.

        Yields:
            ScannedPackage for each installed package.

        Raises:
            RuntimeError: If dpkg-query or apt-mark commands fail.
        """
        if not self.is_available():
            msg = "APT package manager is not available on this system"
            raise RuntimeError(msg)

        # Get set of auto-installed packages
        auto_packages = self._get_auto_installed()

        # Query all installed packages
        result = run_command(
            ["dpkg-query", "-W", "-f", self._DPKG_FORMAT],
        )

        if not result.success:
            msg = f"dpkg-query failed: {result.stderr}"
            raise RuntimeError(msg)

        for line in result.stdout.strip().split("\n"):
            if not line:
                continue

            package = self._parse_dpkg_line(line, auto_packages)
            if package is not None:
                yield package

    def _get_auto_installed(self) -> set[str]:
        """Get set of package names that were auto-installed.

        Returns:
            Set of package names marked as automatically installed.

        Raises:
            RuntimeError: If apt-mark showauto command fails.
        """
        result = run_command(["apt-mark", "showauto"])
        if not result.success:
            # Do not silently continue - the data would be unreliable
            msg = f"apt-mark showauto failed: {result.stderr.strip() or 'unknown error'}"
            raise RuntimeError(msg)

        return {pkg.strip() for pkg in result.stdout.strip().split("\n") if pkg.strip()}

    def _parse_dpkg_line(
        self,
        line: str,
        auto_packages: set[str],
    ) -> ScannedPackage | None:
        """Parse a single line of dpkg-query output.

        Args:
            line: Tab-separated line from dpkg-query.
            auto_packages: Set of auto-installed package names.

        Returns:
            ScannedPackage if parsing succeeds, None otherwise.
        """
        parts = line.split("\t")
        if len(parts) < 2:
            logger.debug("Skipping malformed dpkg line (parts=%d): %r", len(parts), line[:100])
            return None

        name = parts[0].strip()
        version = parts[1].strip()

        if not name or not version:
            logger.debug("Skipping dpkg line with empty name/version: %r", line[:100])
            return None

        # Parse optional fields
        size_bytes: int | None = None
        description: str | None = None

        if len(parts) >= 3:
            size_str = parts[2].strip()
            if size_str.isdigit():
                # dpkg-query reports size in KB
                size_bytes = int(size_str) * 1024

        if len(parts) >= 4:
            description = parts[3].strip() or None

        # Determine installation status
        status = PackageStatus.AUTO_INSTALLED if name in auto_packages else PackageStatus.MANUAL

        return ScannedPackage(
            name=name,
            source=PackageSource.APT,
            version=version,
            status=status,
            description=description,
            size_bytes=size_bytes,
        )
