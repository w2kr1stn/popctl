"""Unit tests for Scanner ABC.

Tests for the abstract Scanner base class.
"""

from collections.abc import Iterator

import pytest
from popctl.models.package import PackageSource, PackageStatus, ScannedPackage
from popctl.scanners.base import Scanner


class ConcreteScanner(Scanner):
    """Concrete implementation for testing the ABC."""

    def __init__(self, packages: list[ScannedPackage], available: bool = True) -> None:
        self._packages = packages
        self._available = available

    @property
    def source(self) -> PackageSource:
        return PackageSource.APT

    def scan(self) -> Iterator[ScannedPackage]:
        yield from self._packages

    def is_available(self) -> bool:
        return self._available


class TestScanner:
    """Tests for Scanner ABC."""

    @pytest.fixture
    def sample_packages(self) -> list[ScannedPackage]:
        """Create sample packages for testing."""
        return [
            ScannedPackage(
                name="firefox",
                source=PackageSource.APT,
                version="128.0",
                status=PackageStatus.MANUAL,
            ),
            ScannedPackage(
                name="neovim",
                source=PackageSource.APT,
                version="0.9.5",
                status=PackageStatus.MANUAL,
            ),
            ScannedPackage(
                name="libgtk-3-0",
                source=PackageSource.APT,
                version="3.24.41",
                status=PackageStatus.AUTO_INSTALLED,
            ),
            ScannedPackage(
                name="python3",
                source=PackageSource.APT,
                version="3.11",
                status=PackageStatus.AUTO_INSTALLED,
            ),
        ]

    def test_source_property(self, sample_packages: list[ScannedPackage]) -> None:
        """Scanner returns correct source."""
        scanner = ConcreteScanner(sample_packages)
        assert scanner.source == PackageSource.APT

    def test_scan_yields_all_packages(self, sample_packages: list[ScannedPackage]) -> None:
        """Scan yields all packages."""
        scanner = ConcreteScanner(sample_packages)
        packages = list(scanner.scan())
        assert len(packages) == 4
        assert packages == sample_packages

    def test_is_available_true(self, sample_packages: list[ScannedPackage]) -> None:
        """is_available returns True when available."""
        scanner = ConcreteScanner(sample_packages, available=True)
        assert scanner.is_available() is True

    def test_is_available_false(self, sample_packages: list[ScannedPackage]) -> None:
        """is_available returns False when not available."""
        scanner = ConcreteScanner(sample_packages, available=False)
        assert scanner.is_available() is False

    def test_scan_manual_only(self, sample_packages: list[ScannedPackage]) -> None:
        """scan_manual_only yields only manually installed packages."""
        scanner = ConcreteScanner(sample_packages)
        manual_packages = list(scanner.scan_manual_only())
        assert len(manual_packages) == 2
        assert all(pkg.status == PackageStatus.MANUAL for pkg in manual_packages)
        assert manual_packages[0].name == "firefox"
        assert manual_packages[1].name == "neovim"

    def test_scan_manual_only_empty(self) -> None:
        """scan_manual_only handles no manual packages."""
        auto_only = [
            ScannedPackage(
                name="libfoo",
                source=PackageSource.APT,
                version="1.0",
                status=PackageStatus.AUTO_INSTALLED,
            )
        ]
        scanner = ConcreteScanner(auto_only)
        manual_packages = list(scanner.scan_manual_only())
        assert len(manual_packages) == 0

    def test_count_returns_totals(self, sample_packages: list[ScannedPackage]) -> None:
        """count returns correct total and manual counts."""
        scanner = ConcreteScanner(sample_packages)
        total, manual = scanner.count()
        assert total == 4
        assert manual == 2

    def test_count_empty_scanner(self) -> None:
        """count handles empty package list."""
        scanner = ConcreteScanner([])
        total, manual = scanner.count()
        assert total == 0
        assert manual == 0

    def test_scanner_is_abstract(self) -> None:
        """Scanner cannot be instantiated directly."""
        with pytest.raises(TypeError):
            Scanner()  # type: ignore[abstract]
