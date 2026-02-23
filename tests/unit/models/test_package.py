"""Unit tests for package models.

Tests for PackageSource, PackageStatus, and ScannedPackage.
"""

import pytest
from popctl.models.package import PackageSource, PackageStatus, ScannedPackage


class TestPackageSource:
    """Tests for PackageSource enum."""

    def test_apt_value(self) -> None:
        """APT source has correct value."""
        assert PackageSource.APT.value == "apt"

    def test_flatpak_value(self) -> None:
        """FLATPAK source has correct value."""
        assert PackageSource.FLATPAK.value == "flatpak"

    def test_snap_value(self) -> None:
        """SNAP source has correct value."""
        assert PackageSource.SNAP.value == "snap"

    def test_all_sources_are_unique(self) -> None:
        """All source values are unique."""
        values = [s.value for s in PackageSource]
        assert len(values) == len(set(values))


class TestPackageStatus:
    """Tests for PackageStatus enum."""

    def test_manual_value(self) -> None:
        """MANUAL status has correct value."""
        assert PackageStatus.MANUAL.value == "manual"

    def test_auto_installed_value(self) -> None:
        """AUTO_INSTALLED status has correct value."""
        assert PackageStatus.AUTO_INSTALLED.value == "auto"


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
            install_date="2024-01-15",
            classification="keep",
            confidence=0.95,
            reason="User development tool",
            category="development",
        )
        assert pkg.description == "Vim-based text editor"
        assert pkg.size_bytes == 51200
        assert pkg.classification == "keep"
        assert pkg.confidence == 0.95

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

    def test_invalid_confidence_raises_error(self) -> None:
        """Invalid confidence value raises ValueError."""
        with pytest.raises(ValueError, match="Confidence must be between"):
            ScannedPackage(
                name="firefox",
                source=PackageSource.APT,
                version="1.0",
                status=PackageStatus.MANUAL,
                confidence=1.5,
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

    def test_is_auto_property(self) -> None:
        """is_auto property returns correct value."""
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
        assert manual_pkg.is_auto is False
        assert auto_pkg.is_auto is True

    def test_size_human_bytes(self) -> None:
        """size_human formats bytes correctly."""
        pkg = ScannedPackage(
            name="tiny",
            source=PackageSource.APT,
            version="1.0",
            status=PackageStatus.MANUAL,
            size_bytes=512,
        )
        assert pkg.size_human == "512.0 B"

    def test_size_human_kilobytes(self) -> None:
        """size_human formats kilobytes correctly."""
        pkg = ScannedPackage(
            name="small",
            source=PackageSource.APT,
            version="1.0",
            status=PackageStatus.MANUAL,
            size_bytes=2048,
        )
        assert pkg.size_human == "2.0 KB"

    def test_size_human_megabytes(self) -> None:
        """size_human formats megabytes correctly."""
        pkg = ScannedPackage(
            name="medium",
            source=PackageSource.APT,
            version="1.0",
            status=PackageStatus.MANUAL,
            size_bytes=5_242_880,  # 5 MB
        )
        assert pkg.size_human == "5.0 MB"

    def test_size_human_gigabytes(self) -> None:
        """size_human formats gigabytes correctly."""
        pkg = ScannedPackage(
            name="large",
            source=PackageSource.APT,
            version="1.0",
            status=PackageStatus.MANUAL,
            size_bytes=2_147_483_648,  # 2 GB
        )
        assert pkg.size_human == "2.0 GB"

    def test_size_human_unknown(self) -> None:
        """size_human returns unknown when size is None."""
        pkg = ScannedPackage(
            name="unknown",
            source=PackageSource.APT,
            version="1.0",
            status=PackageStatus.MANUAL,
        )
        assert pkg.size_human == "unknown"

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
