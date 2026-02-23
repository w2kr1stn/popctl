"""Unit tests for manifest models.

Tests for the Pydantic models representing manifest.toml structure.
"""

from datetime import UTC, datetime

import pytest
from popctl.configs.manifest import ConfigEntry, ConfigsConfig
from popctl.filesystem.manifest import FilesystemConfig, FilesystemEntry
from popctl.models.manifest import (
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
        meta = ManifestMeta(version="1.0", created=now, updated=now)

        assert meta.version == "1.0"
        assert meta.created == now
        assert meta.updated == now

    def test_default_version(self) -> None:
        """ManifestMeta has default version."""
        now = datetime.now(UTC)
        meta = ManifestMeta(created=now, updated=now)

        assert meta.version == "1.0"

    def test_rejects_extra_fields(self) -> None:
        """ManifestMeta rejects unknown fields."""
        now = datetime.now(UTC)
        with pytest.raises(ValidationError):
            ManifestMeta(version="1.0", created=now, updated=now, unknown="field")


class TestSystemConfig:
    """Tests for SystemConfig model."""

    def test_valid_system_config(self) -> None:
        """SystemConfig accepts valid data."""
        config = SystemConfig(
            name="test-machine",
            base="pop-os-24.04",
            description="Test machine",
        )

        assert config.name == "test-machine"
        assert config.base == "pop-os-24.04"
        assert config.description == "Test machine"

    def test_default_base(self) -> None:
        """SystemConfig has default base OS."""
        config = SystemConfig(name="test")

        assert config.base == "pop-os-24.04"

    def test_optional_description(self) -> None:
        """SystemConfig description is optional."""
        config = SystemConfig(name="test")

        assert config.description is None

    def test_rejects_extra_fields(self) -> None:
        """SystemConfig rejects unknown fields."""
        with pytest.raises(ValidationError):
            SystemConfig(name="test", unknown="field")


class TestPackageEntry:
    """Tests for PackageEntry model."""

    def test_valid_apt_package(self) -> None:
        """PackageEntry accepts valid APT package."""
        entry = PackageEntry(source="apt")

        assert entry.source == "apt"
        assert entry.status == "keep"
        assert entry.reason is None

    def test_valid_flatpak_package(self) -> None:
        """PackageEntry accepts valid Flatpak package."""
        entry = PackageEntry(source="flatpak", status="remove", reason="Not needed")

        assert entry.source == "flatpak"
        assert entry.status == "remove"
        assert entry.reason == "Not needed"

    def test_rejects_invalid_source(self) -> None:
        """PackageEntry rejects invalid source."""
        with pytest.raises(ValidationError):
            PackageEntry(source="invalid")

    def test_rejects_invalid_status(self) -> None:
        """PackageEntry rejects invalid status."""
        with pytest.raises(ValidationError):
            PackageEntry(source="apt", status="invalid")

    def test_default_status_is_keep(self) -> None:
        """PackageEntry default status is 'keep'."""
        entry = PackageEntry(source="apt")

        assert entry.status == "keep"


class TestPackageConfig:
    """Tests for PackageConfig model."""

    def test_valid_package_config(self) -> None:
        """PackageConfig accepts valid data."""
        config = PackageConfig(
            keep={"firefox": PackageEntry(source="apt")},
            remove={"bloatware": PackageEntry(source="apt", status="remove")},
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

    @pytest.fixture
    def sample_manifest(self) -> Manifest:
        """Create a sample manifest for testing."""
        now = datetime.now(UTC)
        return Manifest(
            meta=ManifestMeta(version="1.0", created=now, updated=now),
            system=SystemConfig(name="test-machine"),
            packages=PackageConfig(
                keep={
                    "firefox": PackageEntry(source="apt"),
                    "com.spotify.Client": PackageEntry(source="flatpak"),
                },
                remove={
                    "bloatware": PackageEntry(source="apt", status="remove"),
                },
            ),
        )

    def test_valid_manifest(self, sample_manifest: Manifest) -> None:
        """Manifest accepts valid data."""
        assert sample_manifest.meta.version == "1.0"
        assert sample_manifest.system.name == "test-machine"
        assert len(sample_manifest.packages.keep) == 2
        assert len(sample_manifest.packages.remove) == 1

    def test_get_keep_packages_all(self, sample_manifest: Manifest) -> None:
        """get_keep_packages returns all keep packages when no source filter."""
        packages = sample_manifest.get_keep_packages()

        assert len(packages) == 2
        assert "firefox" in packages
        assert "com.spotify.Client" in packages

    def test_get_keep_packages_apt_only(self, sample_manifest: Manifest) -> None:
        """get_keep_packages filters by APT source."""
        packages = sample_manifest.get_keep_packages("apt")

        assert len(packages) == 1
        assert "firefox" in packages
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

    def test_package_count(self, sample_manifest: Manifest) -> None:
        """package_count returns total tracked packages."""
        assert sample_manifest.package_count == 3


class TestManifestFilesystem:
    """Tests for filesystem integration in Manifest model."""

    @pytest.fixture
    def fs_config(self) -> FilesystemConfig:
        """Create a sample FilesystemConfig for testing."""
        return FilesystemConfig(
            keep={
                "~/.config/nvim": FilesystemEntry(reason="User config", category="config"),
                "~/.config/git": FilesystemEntry(reason="Version control"),
            },
            remove={
                "~/.config/old-app": FilesystemEntry(reason="App uninstalled", category="stale"),
                "~/.cache/stale": FilesystemEntry(),
            },
        )

    @pytest.fixture
    def manifest_with_fs(self, fs_config: FilesystemConfig) -> Manifest:
        """Create a manifest with filesystem section."""
        now = datetime.now(UTC)
        return Manifest(
            meta=ManifestMeta(version="1.0", created=now, updated=now),
            system=SystemConfig(name="test-machine"),
            packages=PackageConfig(
                keep={"firefox": PackageEntry(source="apt")},
                remove={},
            ),
            filesystem=fs_config,
        )

    @pytest.fixture
    def manifest_without_fs(self) -> Manifest:
        """Create a manifest without filesystem section."""
        now = datetime.now(UTC)
        return Manifest(
            meta=ManifestMeta(version="1.0", created=now, updated=now),
            system=SystemConfig(name="test-machine"),
            packages=PackageConfig(keep={}, remove={}),
        )

    def test_manifest_with_filesystem_section(self, manifest_with_fs: Manifest) -> None:
        """Manifest accepts a filesystem section with keep/remove entries."""
        assert manifest_with_fs.filesystem is not None
        assert len(manifest_with_fs.filesystem.keep) == 2
        assert len(manifest_with_fs.filesystem.remove) == 2
        assert "~/.config/nvim" in manifest_with_fs.filesystem.keep
        assert "~/.config/old-app" in manifest_with_fs.filesystem.remove

    def test_manifest_without_filesystem_backward_compat(
        self, manifest_without_fs: Manifest
    ) -> None:
        """Manifest without filesystem section loads with None default."""
        assert manifest_without_fs.filesystem is None
        assert manifest_without_fs.system.name == "test-machine"

    def test_manifest_filesystem_defaults_to_none(self) -> None:
        """Filesystem field defaults to None when not provided."""
        now = datetime.now(UTC)
        manifest = Manifest(
            meta=ManifestMeta(created=now, updated=now),
            system=SystemConfig(name="test"),
            packages=PackageConfig(keep={}, remove={}),
        )
        assert manifest.filesystem is None

    def test_get_fs_keep_paths(self, manifest_with_fs: Manifest) -> None:
        """get_fs_keep_paths returns keep dict when filesystem is present."""
        paths = manifest_with_fs.get_fs_keep_paths()

        assert len(paths) == 2
        assert "~/.config/nvim" in paths
        assert "~/.config/git" in paths
        assert paths["~/.config/nvim"].reason == "User config"
        assert paths["~/.config/nvim"].category == "config"

    def test_get_fs_remove_paths(self, manifest_with_fs: Manifest) -> None:
        """get_fs_remove_paths returns remove dict when filesystem is present."""
        paths = manifest_with_fs.get_fs_remove_paths()

        assert len(paths) == 2
        assert "~/.config/old-app" in paths
        assert "~/.cache/stale" in paths
        assert paths["~/.config/old-app"].reason == "App uninstalled"
        assert paths["~/.config/old-app"].category == "stale"

    def test_get_fs_keep_paths_when_none(self, manifest_without_fs: Manifest) -> None:
        """get_fs_keep_paths returns empty dict when filesystem is None."""
        paths = manifest_without_fs.get_fs_keep_paths()
        assert paths == {}

    def test_get_fs_remove_paths_when_none(self, manifest_without_fs: Manifest) -> None:
        """get_fs_remove_paths returns empty dict when filesystem is None."""
        paths = manifest_without_fs.get_fs_remove_paths()
        assert paths == {}


class TestManifestConfigs:
    """Tests for configs integration in Manifest model."""

    @pytest.fixture
    def configs_config(self) -> ConfigsConfig:
        """Create a sample ConfigsConfig for testing."""
        return ConfigsConfig(
            keep={
                "~/.config/Code": ConfigEntry(reason="VS Code settings", category="editor"),
                "~/.config/nvim": ConfigEntry(reason="User config"),
            },
            remove={
                "~/.config/vlc": ConfigEntry(reason="VLC not installed", category="obsolete"),
                "~/.config/sublime-text": ConfigEntry(reason="Switched editor"),
            },
        )

    @pytest.fixture
    def manifest_with_configs(self, configs_config: ConfigsConfig) -> Manifest:
        """Create a manifest with configs section."""
        now = datetime.now(UTC)
        return Manifest(
            meta=ManifestMeta(version="1.0", created=now, updated=now),
            system=SystemConfig(name="test-machine"),
            packages=PackageConfig(
                keep={"firefox": PackageEntry(source="apt")},
                remove={},
            ),
            configs=configs_config,
        )

    @pytest.fixture
    def manifest_without_configs(self) -> Manifest:
        """Create a manifest without configs section."""
        now = datetime.now(UTC)
        return Manifest(
            meta=ManifestMeta(version="1.0", created=now, updated=now),
            system=SystemConfig(name="test-machine"),
            packages=PackageConfig(keep={}, remove={}),
        )

    def test_manifest_with_configs_section(self, manifest_with_configs: Manifest) -> None:
        """Manifest accepts a configs section with keep/remove entries."""
        assert manifest_with_configs.configs is not None
        assert len(manifest_with_configs.configs.keep) == 2
        assert len(manifest_with_configs.configs.remove) == 2
        assert "~/.config/Code" in manifest_with_configs.configs.keep
        assert "~/.config/vlc" in manifest_with_configs.configs.remove

    def test_manifest_without_configs_backward_compat(
        self, manifest_without_configs: Manifest
    ) -> None:
        """Manifest without configs section loads with None default."""
        assert manifest_without_configs.configs is None
        assert manifest_without_configs.system.name == "test-machine"

    def test_manifest_configs_defaults_to_none(self) -> None:
        """Configs field defaults to None when not provided."""
        now = datetime.now(UTC)
        manifest = Manifest(
            meta=ManifestMeta(created=now, updated=now),
            system=SystemConfig(name="test"),
            packages=PackageConfig(keep={}, remove={}),
        )
        assert manifest.configs is None

    def test_get_config_keep_paths(self, manifest_with_configs: Manifest) -> None:
        """get_config_keep_paths returns keep dict when configs is present."""
        paths = manifest_with_configs.get_config_keep_paths()

        assert len(paths) == 2
        assert "~/.config/Code" in paths
        assert "~/.config/nvim" in paths
        assert paths["~/.config/Code"].reason == "VS Code settings"
        assert paths["~/.config/Code"].category == "editor"

    def test_get_config_remove_paths(self, manifest_with_configs: Manifest) -> None:
        """get_config_remove_paths returns remove dict when configs is present."""
        paths = manifest_with_configs.get_config_remove_paths()

        assert len(paths) == 2
        assert "~/.config/vlc" in paths
        assert "~/.config/sublime-text" in paths
        assert paths["~/.config/vlc"].reason == "VLC not installed"
        assert paths["~/.config/vlc"].category == "obsolete"

    def test_get_config_keep_paths_when_none(self, manifest_without_configs: Manifest) -> None:
        """get_config_keep_paths returns empty dict when configs is None."""
        paths = manifest_without_configs.get_config_keep_paths()
        assert paths == {}

    def test_get_config_remove_paths_when_none(self, manifest_without_configs: Manifest) -> None:
        """get_config_remove_paths returns empty dict when configs is None."""
        paths = manifest_without_configs.get_config_remove_paths()
        assert paths == {}
