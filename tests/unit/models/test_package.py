"""Unit tests for package models.

Tests for PackageSource, PackageStatus, and ScannedPackage.
"""

import pytest
from popctl.models.package import PackageSource, PackageStatus, ScannedPackage


class TestScannedPackage:
    """Tests for ScannedPackage dataclass."""

    def test_minimal_package(self) -> None:
        """Package can be created with only required fields."""
        pkg = ScannedPackage(
            name="firefox",
            source=PackageSource.APT,
            version="128.0",
            status=PackageStatus.MANUAL,
        )
        assert pkg.name == "firefox"
        assert pkg.source == PackageSource.APT
        assert pkg.version == "128.0"
        assert pkg.status == PackageStatus.MANUAL
        assert pkg.description is None
        assert pkg.size_bytes is None

    def test_full_package(self) -> None:
        """Package can be created with all fields."""
        pkg = ScannedPackage(
            name="neovim",
            source=PackageSource.APT,
            version="0.9.5",
            status=PackageStatus.MANUAL,
            description="Vim-based text editor",
            size_bytes=51200,
        )
        assert pkg.description == "Vim-based text editor"
        assert pkg.size_bytes == 51200

    def test_package_is_immutable(self) -> None:
        """Package is frozen (immutable)."""
        pkg = ScannedPackage(
            name="firefox",
            source=PackageSource.APT,
            version="128.0",
            status=PackageStatus.MANUAL,
        )
        with pytest.raises(AttributeError):
            pkg.name = "chrome"  # type: ignore[misc]

    def test_empty_name_raises_error(self) -> None:
        """Empty package name raises ValueError."""
        with pytest.raises(ValueError, match="name cannot be empty"):
            ScannedPackage(
                name="",
                source=PackageSource.APT,
                version="1.0",
                status=PackageStatus.MANUAL,
            )

    def test_empty_version_raises_error(self) -> None:
        """Empty package version raises ValueError."""
        with pytest.raises(ValueError, match="version cannot be empty"):
            ScannedPackage(
                name="firefox",
                source=PackageSource.APT,
                version="",
                status=PackageStatus.MANUAL,
            )

    def test_is_manual_property(self) -> None:
        """is_manual property returns correct value."""
        manual_pkg = ScannedPackage(
            name="firefox",
            source=PackageSource.APT,
            version="1.0",
            status=PackageStatus.MANUAL,
        )
        auto_pkg = ScannedPackage(
            name="libfoo",
            source=PackageSource.APT,
            version="1.0",
            status=PackageStatus.AUTO_INSTALLED,
        )
        assert manual_pkg.is_manual is True
        assert auto_pkg.is_manual is False

    def test_package_equality(self) -> None:
        """Two packages with same values are equal."""
        pkg1 = ScannedPackage(
            name="firefox",
            source=PackageSource.APT,
            version="128.0",
            status=PackageStatus.MANUAL,
        )
        pkg2 = ScannedPackage(
            name="firefox",
            source=PackageSource.APT,
            version="128.0",
            status=PackageStatus.MANUAL,
        )
        assert pkg1 == pkg2

    def test_package_hashable(self) -> None:
        """Package can be used in sets and as dict keys."""
        pkg = ScannedPackage(
            name="firefox",
            source=PackageSource.APT,
            version="128.0",
            status=PackageStatus.MANUAL,
        )
        package_set = {pkg}
        assert pkg in package_set
