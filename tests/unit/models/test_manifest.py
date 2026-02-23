"""Unit tests for manifest models.

Tests for the Pydantic models representing manifest.toml structure.
"""

from datetime import UTC, datetime

import pytest
from popctl.models.manifest import (
    DomainConfig,
    DomainEntry,
    Manifest,
    ManifestMeta,
    PackageConfig,
    PackageEntry,
    SystemConfig,
)
from pydantic import ValidationError


class TestManifestMeta:
    """Tests for ManifestMeta model."""

    def test_valid_manifest_meta(self) -> None:
        """ManifestMeta accepts valid data."""
        now = datetime.now(UTC)
        meta = ManifestMeta(created=now, updated=now)

        assert meta.created == now
        assert meta.updated == now

    def test_ignores_legacy_version_field(self) -> None:
        """ManifestMeta silently ignores legacy 'version' field."""
        now = datetime.now(UTC)
        meta = ManifestMeta(created=now, updated=now)

        assert meta.created == now


class TestSystemConfig:
    """Tests for SystemConfig model."""

    def test_valid_system_config(self) -> None:
        """SystemConfig accepts valid data."""
        config = SystemConfig(
            name="test-machine",
            base="pop-os-24.04",
        )

        assert config.name == "test-machine"
        assert config.base == "pop-os-24.04"

    def test_default_base(self) -> None:
        """SystemConfig has default base OS."""
        config = SystemConfig(name="test")

        assert config.base == "pop-os-24.04"

    def test_ignores_legacy_description_field(self) -> None:
        """SystemConfig silently ignores legacy 'description' field."""
        config = SystemConfig(name="test", description="Test machine")

        assert config.name == "test"


class TestPackageEntry:
    """Tests for PackageEntry model."""

    def test_valid_apt_package(self) -> None:
        """PackageEntry accepts valid APT package."""
        entry = PackageEntry(source="apt")

        assert entry.source == "apt"
        assert entry.reason is None

    def test_valid_flatpak_package(self) -> None:
        """PackageEntry accepts valid Flatpak package."""
        entry = PackageEntry(source="flatpak", reason="Not needed")

        assert entry.source == "flatpak"
        assert entry.reason == "Not needed"

    def test_rejects_invalid_source(self) -> None:
        """PackageEntry rejects invalid source."""
        with pytest.raises(ValidationError):
            PackageEntry(source="invalid")

    def test_ignores_legacy_status_field(self) -> None:
        """PackageEntry silently ignores legacy 'status' field."""
        entry = PackageEntry(source="apt", status="remove")

        assert entry.source == "apt"


class TestPackageConfig:
    """Tests for PackageConfig model."""

    def test_valid_package_config(self) -> None:
        """PackageConfig accepts valid data."""
        config = PackageConfig(
            keep={"firefox": PackageEntry(source="apt")},
            remove={"bloatware": PackageEntry(source="apt")},
        )

        assert "firefox" in config.keep
        assert "bloatware" in config.remove

    def test_default_empty_dicts(self) -> None:
        """PackageConfig defaults to empty dictionaries."""
        config = PackageConfig(keep={}, remove={})

        assert config.keep == {}
        assert config.remove == {}


class TestManifest:
    """Tests for complete Manifest model."""

    def test_valid_manifest(self, sample_manifest: Manifest) -> None:
        """Manifest accepts valid data."""
        assert sample_manifest.system.name == "test-machine"
        assert len(sample_manifest.packages.keep) == 3
        assert len(sample_manifest.packages.remove) == 1

    def test_get_keep_packages_all(self, sample_manifest: Manifest) -> None:
        """get_keep_packages returns all keep packages when no source filter."""
        packages = sample_manifest.get_keep_packages()

        assert len(packages) == 3
        assert "firefox" in packages
        assert "neovim" in packages
        assert "com.spotify.Client" in packages

    def test_get_keep_packages_apt_only(self, sample_manifest: Manifest) -> None:
        """get_keep_packages filters by APT source."""
        packages = sample_manifest.get_keep_packages("apt")

        assert len(packages) == 2
        assert "firefox" in packages
        assert "neovim" in packages
        assert "com.spotify.Client" not in packages

    def test_get_keep_packages_flatpak_only(self, sample_manifest: Manifest) -> None:
        """get_keep_packages filters by Flatpak source."""
        packages = sample_manifest.get_keep_packages("flatpak")

        assert len(packages) == 1
        assert "com.spotify.Client" in packages
        assert "firefox" not in packages

    def test_get_remove_packages(self, sample_manifest: Manifest) -> None:
        """get_remove_packages returns removal packages."""
        packages = sample_manifest.get_remove_packages()

        assert len(packages) == 1
        assert "bloatware" in packages


_DOMAIN_GETTER = {
    "filesystem": "get_fs_remove_paths",
    "configs": "get_config_remove_paths",
}


@pytest.mark.parametrize("domain", ["filesystem", "configs"])
class TestManifestDomain:
    """Tests for domain (filesystem/configs) integration in Manifest model."""

    @pytest.fixture
    def domain_config(self) -> DomainConfig:
        """Create a sample DomainConfig for testing."""
        return DomainConfig(
            keep={
                "~/.config/nvim": DomainEntry(reason="User config", category="config"),
                "~/.config/git": DomainEntry(reason="Version control"),
            },
            remove={
                "~/.config/old-app": DomainEntry(reason="App uninstalled", category="stale"),
                "~/.cache/stale": DomainEntry(),
            },
        )

    @pytest.fixture
    def manifest_with_domain(self, domain_config: DomainConfig, domain: str) -> Manifest:
        """Create a manifest with the given domain section."""
        now = datetime.now(UTC)
        return Manifest(
            meta=ManifestMeta(created=now, updated=now),
            system=SystemConfig(name="test-machine"),
            packages=PackageConfig(
                keep={"firefox": PackageEntry(source="apt")},
                remove={},
            ),
            **{domain: domain_config},
        )

    @pytest.fixture
    def manifest_without_domain(self) -> Manifest:
        """Create a manifest without any domain section."""
        now = datetime.now(UTC)
        return Manifest(
            meta=ManifestMeta(created=now, updated=now),
            system=SystemConfig(name="test-machine"),
            packages=PackageConfig(keep={}, remove={}),
        )

    def test_manifest_with_domain_section(
        self, manifest_with_domain: Manifest, domain: str
    ) -> None:
        """Manifest accepts a domain section with keep/remove entries."""
        section = getattr(manifest_with_domain, domain)
        assert section is not None
        assert len(section.keep) == 2
        assert len(section.remove) == 2
        assert "~/.config/nvim" in section.keep
        assert "~/.config/old-app" in section.remove

    def test_manifest_without_domain_backward_compat(
        self, manifest_without_domain: Manifest, domain: str
    ) -> None:
        """Manifest without domain section loads with None default."""
        assert getattr(manifest_without_domain, domain) is None
        assert manifest_without_domain.system.name == "test-machine"

    def test_manifest_domain_defaults_to_none(self, domain: str) -> None:
        """Domain field defaults to None when not provided."""
        now = datetime.now(UTC)
        manifest = Manifest(
            meta=ManifestMeta(created=now, updated=now),
            system=SystemConfig(name="test"),
            packages=PackageConfig(keep={}, remove={}),
        )
        assert getattr(manifest, domain) is None

    def test_get_domain_remove_paths(self, manifest_with_domain: Manifest, domain: str) -> None:
        """Domain getter returns remove dict when section is present."""
        getter = getattr(manifest_with_domain, _DOMAIN_GETTER[domain])
        paths = getter()

        assert len(paths) == 2
        assert "~/.config/old-app" in paths
        assert "~/.cache/stale" in paths
        assert paths["~/.config/old-app"].reason == "App uninstalled"
        assert paths["~/.config/old-app"].category == "stale"

    def test_get_domain_remove_paths_when_none(
        self, manifest_without_domain: Manifest, domain: str
    ) -> None:
        """Domain getter returns empty dict when section is None."""
        getter = getattr(manifest_without_domain, _DOMAIN_GETTER[domain])
        paths = getter()
        assert paths == {}
