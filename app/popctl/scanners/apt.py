import logging
from collections.abc import Iterator

from popctl.models.package import PackageSource, PackageStatus, ScannedPackage
from popctl.scanners.base import Scanner, parse_tab_fields
from popctl.utils.shell import command_exists, run_command

logger = logging.getLogger(__name__)


class AptScanner(Scanner):
    # dpkg-query format: Status, Package, Version, Installed-Size (KB), Description
    # Status field filters out packages in 'config-files' or 'not-installed' state
    # that linger in the dpkg database after apt-get remove (but not purge).
    _DPKG_FORMAT = (
        "${db:Status-Status}\\t${Package}\\t${Version}\\t${Installed-Size}\\t${binary:Summary}\\n"
    )

    source = PackageSource.APT

    def is_available(self) -> bool:
        return command_exists("dpkg-query") and command_exists("apt-mark")

    def scan(self) -> Iterator[ScannedPackage]:
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
    if not command_exists("apt-cache"):
        logger.warning("apt-cache not available — skipping reverse dependency enrichment")
        return {}

    rdeps: dict[str, list[str]] = {}

    for pkg in packages:
        result = run_command(["apt-cache", "rdepends", "--installed", pkg], timeout=10.0)
        if not result.success:
            logger.debug("apt-cache rdepends failed for %s: %s", pkg, result.stderr.strip())
            continue

        dependents: list[str] = []
        for line in result.stdout.strip().splitlines()[2:]:  # skip header lines
            dep = line.strip().lstrip("|").strip()
            if dep and dep != pkg:
                dependents.append(dep)

        if dependents:
            rdeps[pkg] = dependents

    return rdeps
