"""Unit tests for manifest I/O operations.

Tests for loading and saving manifest files.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from popctl.core.manifest import (
    ManifestNotFoundError,
    ManifestParseError,
    ManifestValidationError,
    load_manifest,
    manifest_exists,
    save_manifest,
)
from popctl.models.manifest import (
    Manifest,
    ManifestMeta,
    PackageConfig,
    PackageEntry,
    SystemConfig,
)


@pytest.fixture
def sample_manifest() -> Manifest:
    """Create a sample manifest for testing."""
    now = datetime.now(UTC)
    return Manifest(
        meta=ManifestMeta(version="1.0", created=now, updated=now),
        system=SystemConfig(name="test-machine", description="Test"),
        packages=PackageConfig(
            keep={
                "firefox": PackageEntry(source="apt"),
                "com.spotify.Client": PackageEntry(source="flatpak"),
            },
            remove={},
        ),
    )


class TestSaveManifest:
    """Tests for save_manifest function."""

    def test_save_creates_file(self, tmp_path: Path, sample_manifest: Manifest) -> None:
        """save_manifest creates a TOML file."""
        manifest_path = tmp_path / "manifest.toml"

        result = save_manifest(sample_manifest, manifest_path)

        assert result == manifest_path
        assert manifest_path.exists()

    def test_save_creates_parent_directories(
        self, tmp_path: Path, sample_manifest: Manifest
    ) -> None:
        """save_manifest creates parent directories if needed."""
        manifest_path = tmp_path / "nested" / "dir" / "manifest.toml"

        save_manifest(sample_manifest, manifest_path)

        assert manifest_path.exists()

    def test_save_writes_valid_toml(self, tmp_path: Path, sample_manifest: Manifest) -> None:
        """save_manifest writes valid TOML content."""
        import tomllib

        manifest_path = tmp_path / "manifest.toml"
        save_manifest(sample_manifest, manifest_path)

        # Should be parseable TOML
        with open(manifest_path, "rb") as f:
            data = tomllib.load(f)

        assert "meta" in data
        assert "system" in data
        assert "packages" in data

    def test_save_preserves_data(self, tmp_path: Path, sample_manifest: Manifest) -> None:
        """save_manifest preserves all manifest data."""
        manifest_path = tmp_path / "manifest.toml"
        save_manifest(sample_manifest, manifest_path)

        loaded = load_manifest(manifest_path)

        assert loaded.meta.version == sample_manifest.meta.version
        assert loaded.system.name == sample_manifest.system.name
        assert loaded.system.description == sample_manifest.system.description
        assert set(loaded.packages.keep.keys()) == set(sample_manifest.packages.keep.keys())


class TestLoadManifest:
    """Tests for load_manifest function."""

    def test_load_valid_manifest(self, tmp_path: Path, sample_manifest: Manifest) -> None:
        """load_manifest loads a valid manifest file."""
        manifest_path = tmp_path / "manifest.toml"
        save_manifest(sample_manifest, manifest_path)

        loaded = load_manifest(manifest_path)

        assert loaded.system.name == "test-machine"
        assert len(loaded.packages.keep) == 2

    def test_load_raises_on_missing_file(self, tmp_path: Path) -> None:
        """load_manifest raises ManifestNotFoundError for missing file."""
        manifest_path = tmp_path / "nonexistent.toml"

        with pytest.raises(ManifestNotFoundError):
            load_manifest(manifest_path)

    def test_load_raises_on_invalid_toml(self, tmp_path: Path) -> None:
        """load_manifest raises ManifestParseError for invalid TOML."""
        manifest_path = tmp_path / "invalid.toml"
        manifest_path.write_text("invalid [ toml content")

        with pytest.raises(ManifestParseError):
            load_manifest(manifest_path)

    def test_load_raises_on_invalid_schema(self, tmp_path: Path) -> None:
        """load_manifest raises ManifestValidationError for invalid schema."""
        manifest_path = tmp_path / "invalid_schema.toml"
        manifest_path.write_text('[meta]\nversion = "1.0"\n')  # Missing required fields

        with pytest.raises(ManifestValidationError):
            load_manifest(manifest_path)


class TestManifestExists:
    """Tests for manifest_exists function."""

    def test_returns_true_for_existing_file(
        self, tmp_path: Path, sample_manifest: Manifest
    ) -> None:
        """manifest_exists returns True when file exists."""
        manifest_path = tmp_path / "manifest.toml"
        save_manifest(sample_manifest, manifest_path)

        assert manifest_exists(manifest_path) is True

    def test_returns_false_for_missing_file(self, tmp_path: Path) -> None:
        """manifest_exists returns False when file doesn't exist."""
        manifest_path = tmp_path / "nonexistent.toml"

        assert manifest_exists(manifest_path) is False


class TestManifestRoundTrip:
    """Integration tests for save/load round-trip."""

    def test_preserves_timestamps(self, tmp_path: Path) -> None:
        """Round-trip preserves datetime values."""
        created = datetime(2024, 1, 15, 10, 30, 0, tzinfo=UTC)
        updated = datetime(2024, 1, 20, 14, 45, 0, tzinfo=UTC)

        manifest = Manifest(
            meta=ManifestMeta(version="1.0", created=created, updated=updated),
            system=SystemConfig(name="test"),
            packages=PackageConfig(keep={}, remove={}),
        )

        manifest_path = tmp_path / "manifest.toml"
        save_manifest(manifest, manifest_path)
        loaded = load_manifest(manifest_path)

        # ISO format may lose microseconds, compare to second precision
        assert loaded.meta.created.replace(microsecond=0) == created.replace(microsecond=0)
        assert loaded.meta.updated.replace(microsecond=0) == updated.replace(microsecond=0)

    def test_preserves_package_entries(self, tmp_path: Path) -> None:
        """Round-trip preserves package entry details."""
        now = datetime.now(UTC)
        manifest = Manifest(
            meta=ManifestMeta(created=now, updated=now),
            system=SystemConfig(name="test"),
            packages=PackageConfig(
                keep={
                    "firefox": PackageEntry(source="apt", reason="Browser"),
                    "spotify": PackageEntry(source="flatpak"),
                },
                remove={
                    "bloat": PackageEntry(source="apt", status="remove"),
                },
            ),
        )

        manifest_path = tmp_path / "manifest.toml"
        save_manifest(manifest, manifest_path)
        loaded = load_manifest(manifest_path)

        assert loaded.packages.keep["firefox"].source == "apt"
        assert loaded.packages.keep["firefox"].reason == "Browser"
        assert loaded.packages.keep["spotify"].source == "flatpak"
        assert loaded.packages.remove["bloat"].status == "remove"
