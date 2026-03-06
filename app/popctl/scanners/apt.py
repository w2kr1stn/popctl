"""APT package scanner implementation.

Scans installed packages using dpkg-query and determines
installation status using apt-mark.
"""

import logging
from collections.abc import Iterator

from popctl.models.package import PackageSource, PackageStatus, ScannedPackage
from popctl.scanners.base import Scanner, parse_tab_fields
from popctl.utils.shell import command_exists, run_command

logger = logging.getLogger(__name__)


class AptScanner(Scanner):
    """Scanner for APT/dpkg packages.

    Uses dpkg-query to list installed packages and apt-mark
    to distinguish between manually and automatically installed packages.
    """

    # dpkg-query format: Status, Package, Version, Installed-Size (KB), Description
    # Status field filters out packages in 'config-files' or 'not-installed' state
    # that linger in the dpkg database after apt-get remove (but not purge).
    _DPKG_FORMAT = (
        "${db:Status-Status}\\t${Package}\\t${Version}\\t${Installed-Size}\\t${binary:Summary}\\n"
    )

    source = PackageSource.APT

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
        # First field is dpkg status — skip anything not fully installed
        dpkg_status, _, remainder = line.partition("\t")
        if dpkg_status.strip() != "installed":
            return None

        parsed = parse_tab_fields(remainder, "dpkg")
        if parsed is None:
            return None

        name, version, parts = parsed

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


def get_reverse_deps(packages: list[str]) -> dict[str, list[str]]:
    """Get installed reverse dependencies for a list of APT packages.

    Calls ``apt-cache rdepends --installed`` for each package and parses
    the output. Skips virtual-package markers and self-references.

    Args:
        packages: Package names to query.

    Returns:
        Mapping of package name → list of installed packages that depend on it.
        Packages with no reverse deps or query failures are omitted.
    """
    if not command_exists("apt-cache"):
        return {}

    rdeps: dict[str, list[str]] = {}

    for pkg in packages:
        result = run_command(["apt-cache", "rdepends", "--installed", pkg], timeout=10.0)
        if not result.success:
            continue

        dependents: list[str] = []
        for line in result.stdout.strip().splitlines()[2:]:  # skip header lines
            dep = line.strip().lstrip("|").strip()
            if dep and dep != pkg:
                dependents.append(dep)

        if dependents:
            rdeps[pkg] = dependents

    return rdeps
