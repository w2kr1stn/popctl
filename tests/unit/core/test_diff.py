"""Unit tests for diff engine.

Tests for the DiffEngine class and related functions.
"""

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from popctl.core.diff import DiffEngine, DiffEntry, DiffResult, DiffType
from popctl.models.manifest import (
    Manifest,
    ManifestMeta,
    PackageConfig,
    PackageEntry,
    SystemConfig,
)
from popctl.models.package import PackageSource, PackageStatus, ScannedPackage
from popctl.scanners.base import Scanner


class MockScanner(Scanner):
    """Mock scanner for testing."""

    def __init__(
        self,
        source: PackageSource,
        packages: list[ScannedPackage],
        available: bool = True,
    ) -> None:
        self._source = source
        self._packages = packages
        self._available = available

    @property
    def source(self) -> PackageSource:
        return self._source

    def scan(self) -> Iterator[ScannedPackage]:
        yield from self._packages

    def is_available(self) -> bool:
        return self._available


@pytest.fixture
def base_manifest() -> Manifest:
    """Create a basic manifest for testing."""
    now = datetime.now(UTC)
    return Manifest(
        meta=ManifestMeta(version="1.0", created=now, updated=now),
        system=SystemConfig(name="test-machine"),
        packages=PackageConfig(
            keep={
                "firefox": PackageEntry(source="apt"),
                "neovim": PackageEntry(source="apt"),
                "com.spotify.Client": PackageEntry(source="flatpak"),
            },
            remove={
                "bloatware": PackageEntry(source="apt", status="remove"),
            },
        ),
    )


@pytest.fixture
def empty_manifest() -> Manifest:
    """Create an empty manifest for testing."""
    now = datetime.now(UTC)
    return Manifest(
        meta=ManifestMeta(version="1.0", created=now, updated=now),
        system=SystemConfig(name="test-machine"),
        packages=PackageConfig(keep={}, remove={}),
    )


class TestDiffType:
    """Tests for DiffType enum."""

    def test_diff_type_values(self) -> None:
        """DiffType has expected string values."""
        assert DiffType.NEW.value == "new"
        assert DiffType.MISSING.value == "missing"
        assert DiffType.EXTRA.value == "extra"


class TestDiffEntry:
    """Tests for DiffEntry dataclass."""

    def test_create_entry(self) -> None:
        """DiffEntry can be created with all fields."""
        entry = DiffEntry(
            name="firefox",
            source="apt",
            diff_type=DiffType.NEW,
            version="128.0",
            description="Mozilla Firefox",
        )

        assert entry.name == "firefox"
        assert entry.source == "apt"
        assert entry.diff_type == DiffType.NEW
        assert entry.version == "128.0"
        assert entry.description == "Mozilla Firefox"

    def test_create_entry_minimal(self) -> None:
        """DiffEntry can be created with minimal fields."""
        entry = DiffEntry(
            name="neovim",
            source="apt",
            diff_type=DiffType.MISSING,
        )

        assert entry.name == "neovim"
        assert entry.version is None
        assert entry.description is None

    def test_entry_is_immutable(self) -> None:
        """DiffEntry is frozen (immutable)."""
        entry = DiffEntry(name="test", source="apt", diff_type=DiffType.NEW)

        with pytest.raises(AttributeError):
            entry.name = "changed"  # type: ignore[misc]


class TestDiffResult:
    """Tests for DiffResult dataclass."""

    def test_is_in_sync_when_empty(self) -> None:
        """is_in_sync returns True when all lists are empty."""
        result = DiffResult(new=(), missing=(), extra=())

        assert result.is_in_sync is True
        assert result.total_changes == 0

    def test_is_in_sync_false_with_new(self) -> None:
        """is_in_sync returns False when there are new packages."""
        entry = DiffEntry(name="htop", source="apt", diff_type=DiffType.NEW)
        result = DiffResult(new=(entry,), missing=(), extra=())

        assert result.is_in_sync is False
        assert result.total_changes == 1

    def test_is_in_sync_false_with_missing(self) -> None:
        """is_in_sync returns False when there are missing packages."""
        entry = DiffEntry(name="vim", source="apt", diff_type=DiffType.MISSING)
        result = DiffResult(new=(), missing=(entry,), extra=())

        assert result.is_in_sync is False
        assert result.total_changes == 1

    def test_is_in_sync_false_with_extra(self) -> None:
        """is_in_sync returns False when there are extra packages."""
        entry = DiffEntry(name="bloat", source="apt", diff_type=DiffType.EXTRA)
        result = DiffResult(new=(), missing=(), extra=(entry,))

        assert result.is_in_sync is False
        assert result.total_changes == 1

    def test_total_changes_counts_all(self) -> None:
        """total_changes sums all categories."""
        new = DiffEntry(name="new", source="apt", diff_type=DiffType.NEW)
        missing = DiffEntry(name="missing", source="apt", diff_type=DiffType.MISSING)
        extra = DiffEntry(name="extra", source="apt", diff_type=DiffType.EXTRA)

        result = DiffResult(
            new=(new, new),
            missing=(missing,),
            extra=(extra, extra, extra),
        )

        assert result.total_changes == 6

    def test_to_dict(self) -> None:
        """to_dict returns proper dictionary structure."""
        entry = DiffEntry(name="htop", source="apt", diff_type=DiffType.NEW, version="3.2.2")
        result = DiffResult(new=(entry,), missing=(), extra=())

        data = result.to_dict()

        assert data["in_sync"] is False
        assert data["summary"]["new"] == 1
        assert data["summary"]["missing"] == 0
        assert data["summary"]["extra"] == 0
        assert data["summary"]["total"] == 1
        assert len(data["new"]) == 1
        assert data["new"][0]["name"] == "htop"
        assert data["new"][0]["source"] == "apt"
        assert data["new"][0]["version"] == "3.2.2"

    def test_to_dict_excludes_none_values(self) -> None:
        """to_dict excludes None fields from entries."""
        entry = DiffEntry(name="vim", source="apt", diff_type=DiffType.MISSING)
        result = DiffResult(new=(), missing=(entry,), extra=())

        data = result.to_dict()

        assert "version" not in data["missing"][0]
        assert "description" not in data["missing"][0]


class TestDiffEngine:
    """Tests for DiffEngine class."""

    def test_in_sync_system(self, base_manifest: Manifest) -> None:
        """System in sync with manifest shows no changes."""
        # System has exactly what manifest expects (keep packages installed)
        scanner = MockScanner(
            source=PackageSource.APT,
            packages=[
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
            ],
        )

        flatpak_scanner = MockScanner(
            source=PackageSource.FLATPAK,
            packages=[
                ScannedPackage(
                    name="com.spotify.Client",
                    source=PackageSource.FLATPAK,
                    version="1.2.31",
                    status=PackageStatus.MANUAL,
                ),
            ],
        )

        engine = DiffEngine(base_manifest)
        result = engine.compute_diff([scanner, flatpak_scanner])

        assert result.is_in_sync is True
        assert result.total_changes == 0

    def test_detects_new_packages(self, base_manifest: Manifest) -> None:
        """Packages installed but not in manifest are detected as NEW."""
        scanner = MockScanner(
            source=PackageSource.APT,
            packages=[
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
                # Extra package not in manifest
                ScannedPackage(
                    name="htop",
                    source=PackageSource.APT,
                    version="3.2.2",
                    status=PackageStatus.MANUAL,
                    description="Interactive process viewer",
                ),
            ],
        )

        engine = DiffEngine(base_manifest)
        result = engine.compute_diff([scanner])

        assert len(result.new) == 1
        assert result.new[0].name == "htop"
        assert result.new[0].diff_type == DiffType.NEW
        assert result.new[0].version == "3.2.2"

    def test_detects_missing_packages(self, base_manifest: Manifest) -> None:
        """Packages in manifest but not installed are detected as MISSING."""
        scanner = MockScanner(
            source=PackageSource.APT,
            packages=[
                ScannedPackage(
                    name="firefox",
                    source=PackageSource.APT,
                    version="128.0",
                    status=PackageStatus.MANUAL,
                ),
                # neovim is missing (in manifest but not installed)
            ],
        )

        engine = DiffEngine(base_manifest)
        # Filter to APT only since we only mock APT scanner
        result = engine.compute_diff([scanner], source_filter="apt")

        assert len(result.missing) == 1
        assert result.missing[0].name == "neovim"
        assert result.missing[0].diff_type == DiffType.MISSING

    def test_detects_extra_packages(self, base_manifest: Manifest) -> None:
        """Packages marked for removal but still installed are detected as EXTRA."""
        scanner = MockScanner(
            source=PackageSource.APT,
            packages=[
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
                # bloatware is marked for removal but still installed
                ScannedPackage(
                    name="bloatware",
                    source=PackageSource.APT,
                    version="1.0.0",
                    status=PackageStatus.MANUAL,
                ),
            ],
        )

        engine = DiffEngine(base_manifest)
        result = engine.compute_diff([scanner])

        assert len(result.extra) == 1
        assert result.extra[0].name == "bloatware"
        assert result.extra[0].diff_type == DiffType.EXTRA

    def test_ignores_auto_installed_packages(self, empty_manifest: Manifest) -> None:
        """Auto-installed packages are not considered in diff."""
        scanner = MockScanner(
            source=PackageSource.APT,
            packages=[
                ScannedPackage(
                    name="libfoo",
                    source=PackageSource.APT,
                    version="1.0.0",
                    status=PackageStatus.AUTO_INSTALLED,  # Auto-installed
                ),
            ],
        )

        engine = DiffEngine(empty_manifest)
        result = engine.compute_diff([scanner])

        # Auto-installed packages should not appear as NEW
        assert result.is_in_sync is True
        assert result.total_changes == 0

    def test_ignores_protected_packages(self, empty_manifest: Manifest) -> None:
        """Protected system packages are not considered in diff."""
        scanner = MockScanner(
            source=PackageSource.APT,
            packages=[
                ScannedPackage(
                    name="systemd",
                    source=PackageSource.APT,
                    version="256.0",
                    status=PackageStatus.MANUAL,
                ),
                ScannedPackage(
                    name="linux-image-generic",
                    source=PackageSource.APT,
                    version="6.5.0",
                    status=PackageStatus.MANUAL,
                ),
            ],
        )

        engine = DiffEngine(empty_manifest)
        result = engine.compute_diff([scanner])

        # Protected packages should not appear as NEW
        assert result.is_in_sync is True
        assert result.total_changes == 0

    def test_source_filter_apt(self, base_manifest: Manifest) -> None:
        """Source filter correctly filters to APT only."""
        apt_scanner = MockScanner(
            source=PackageSource.APT,
            packages=[
                ScannedPackage(
                    name="htop",  # New APT package
                    source=PackageSource.APT,
                    version="3.2.2",
                    status=PackageStatus.MANUAL,
                ),
            ],
        )

        flatpak_scanner = MockScanner(
            source=PackageSource.FLATPAK,
            packages=[
                ScannedPackage(
                    name="io.new.App",  # New Flatpak - should be filtered
                    source=PackageSource.FLATPAK,
                    version="1.0",
                    status=PackageStatus.MANUAL,
                ),
            ],
        )

        engine = DiffEngine(base_manifest)
        result = engine.compute_diff([apt_scanner, flatpak_scanner], source_filter="apt")

        # Only APT package should appear (htop, firefox missing, neovim missing)
        new_names = [e.name for e in result.new]
        missing_names = [e.name for e in result.missing]

        assert "htop" in new_names
        assert "io.new.App" not in new_names  # Flatpak filtered out
        # Firefox and neovim should be missing (APT packages in manifest)
        assert "firefox" in missing_names
        assert "neovim" in missing_names
        # Flatpak packages in manifest should not appear in missing
        assert "com.spotify.Client" not in missing_names

    def test_source_filter_flatpak(self, base_manifest: Manifest) -> None:
        """Source filter correctly filters to Flatpak only."""
        apt_scanner = MockScanner(
            source=PackageSource.APT,
            packages=[
                ScannedPackage(
                    name="htop",
                    source=PackageSource.APT,
                    version="3.2.2",
                    status=PackageStatus.MANUAL,
                ),
            ],
        )

        flatpak_scanner = MockScanner(
            source=PackageSource.FLATPAK,
            packages=[
                ScannedPackage(
                    name="io.new.App",
                    source=PackageSource.FLATPAK,
                    version="1.0",
                    status=PackageStatus.MANUAL,
                ),
            ],
        )

        engine = DiffEngine(base_manifest)
        result = engine.compute_diff([apt_scanner, flatpak_scanner], source_filter="flatpak")

        new_names = [e.name for e in result.new]
        missing_names = [e.name for e in result.missing]

        assert "io.new.App" in new_names
        assert "htop" not in new_names  # APT filtered out
        assert "com.spotify.Client" in missing_names
        assert "firefox" not in missing_names  # APT filtered out

    def test_skips_unavailable_scanners(self, base_manifest: Manifest) -> None:
        """Unavailable scanners are skipped."""
        available_scanner = MockScanner(
            source=PackageSource.APT,
            packages=[
                ScannedPackage(
                    name="firefox",
                    source=PackageSource.APT,
                    version="128.0",
                    status=PackageStatus.MANUAL,
                ),
            ],
        )

        unavailable_scanner = MockScanner(
            source=PackageSource.FLATPAK,
            packages=[
                ScannedPackage(
                    name="should.not.appear",
                    source=PackageSource.FLATPAK,
                    version="1.0",
                    status=PackageStatus.MANUAL,
                ),
            ],
            available=False,  # Not available
        )

        engine = DiffEngine(base_manifest)
        result = engine.compute_diff([available_scanner, unavailable_scanner])

        # Only APT packages should be processed
        all_names = [e.name for e in result.new + result.missing + result.extra]
        assert "should.not.appear" not in all_names

    def test_results_are_sorted(self, empty_manifest: Manifest) -> None:
        """Results are sorted by source and name."""
        scanner = MockScanner(
            source=PackageSource.APT,
            packages=[
                ScannedPackage(
                    name="zzz", source=PackageSource.APT, version="1.0", status=PackageStatus.MANUAL
                ),
                ScannedPackage(
                    name="aaa", source=PackageSource.APT, version="1.0", status=PackageStatus.MANUAL
                ),
                ScannedPackage(
                    name="mmm", source=PackageSource.APT, version="1.0", status=PackageStatus.MANUAL
                ),
            ],
        )

        engine = DiffEngine(empty_manifest)
        result = engine.compute_diff([scanner])

        names = [e.name for e in result.new]
        assert names == ["aaa", "mmm", "zzz"]

    def test_combined_scenario(self, base_manifest: Manifest) -> None:
        """Complex scenario with new, missing, and extra packages."""
        scanner = MockScanner(
            source=PackageSource.APT,
            packages=[
                # firefox exists (in sync)
                ScannedPackage(
                    name="firefox",
                    source=PackageSource.APT,
                    version="128.0",
                    status=PackageStatus.MANUAL,
                ),
                # neovim missing (not in this list)
                # bloatware still installed (marked for removal)
                ScannedPackage(
                    name="bloatware",
                    source=PackageSource.APT,
                    version="1.0.0",
                    status=PackageStatus.MANUAL,
                ),
                # htop is new (not in manifest)
                ScannedPackage(
                    name="htop",
                    source=PackageSource.APT,
                    version="3.2.2",
                    status=PackageStatus.MANUAL,
                ),
            ],
        )

        engine = DiffEngine(base_manifest)
        # Filter to APT only since we only mock APT scanner
        result = engine.compute_diff([scanner], source_filter="apt")

        assert not result.is_in_sync
        assert len(result.new) == 1
        assert result.new[0].name == "htop"
        assert len(result.missing) == 1
        assert result.missing[0].name == "neovim"
        assert len(result.extra) == 1
        assert result.extra[0].name == "bloatware"
        assert result.total_changes == 3

    def test_source_filter_snap(self, base_manifest: Manifest) -> None:
        """Source filter correctly filters to Snap only."""
        apt_scanner = MockScanner(
            source=PackageSource.APT,
            packages=[
                ScannedPackage(
                    name="htop",
                    source=PackageSource.APT,
                    version="3.2.2",
                    status=PackageStatus.MANUAL,
                ),
            ],
        )

        snap_scanner = MockScanner(
            source=PackageSource.SNAP,
            packages=[
                ScannedPackage(
                    name="firefox",
                    source=PackageSource.SNAP,
                    version="128.0",
                    status=PackageStatus.MANUAL,
                ),
            ],
        )

        engine = DiffEngine(base_manifest)
        result = engine.compute_diff([apt_scanner, snap_scanner], source_filter="snap")

        new_names = [e.name for e in result.new]
        assert "firefox" in new_names
        assert "htop" not in new_names

    def test_source_filter_invalid_raises(self, empty_manifest: Manifest) -> None:
        """Invalid source_filter raises ValueError."""
        scanner = MockScanner(source=PackageSource.APT, packages=[])
        engine = DiffEngine(empty_manifest)

        with pytest.raises(ValueError, match="Invalid source filter"):
            engine.compute_diff([scanner], source_filter="brew")
