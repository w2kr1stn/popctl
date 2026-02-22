"""Tests for config domain models."""

import pytest
from popctl.configs.models import ScannedConfig
from popctl.domain.models import OrphanReason, OrphanStatus, PathType


class TestPathType:
    """Tests for PathType enum."""

    def test_path_type_values(self) -> None:
        """Verify all 4 PathType enum values exist with correct string values."""
        assert PathType.DIRECTORY == "directory"
        assert PathType.FILE == "file"
        assert PathType.SYMLINK == "symlink"
        assert PathType.DEAD_SYMLINK == "dead_symlink"
        assert len(PathType) == 4

    def test_path_type_is_str_enum(self) -> None:
        """PathType values are usable as strings."""
        assert isinstance(PathType.DIRECTORY, str)
        assert PathType.FILE.value == "file"


class TestOrphanStatus:
    """Tests for OrphanStatus enum."""

    def test_config_status_values(self) -> None:
        """Verify all 3 OrphanStatus enum values exist with correct string values."""
        assert OrphanStatus.ORPHAN == "orphan"
        assert OrphanStatus.OWNED == "owned"
        assert OrphanStatus.PROTECTED == "protected"
        assert len(OrphanStatus) == 3


class TestOrphanReason:
    """Tests for OrphanReason enum."""

    def test_orphan_reason_values(self) -> None:
        """Verify all 3 OrphanReason enum values exist with correct string values."""
        assert OrphanReason.NO_PACKAGE_MATCH == "no_package_match"
        assert OrphanReason.STALE_CACHE == "stale_cache"
        assert OrphanReason.DEAD_LINK == "dead_link"
        assert len(OrphanReason) == 3


class TestScannedConfig:
    """Tests for ScannedConfig frozen dataclass."""

    def test_scanned_config_creation(self) -> None:
        """Create a valid ScannedConfig with all fields populated."""
        sc = ScannedConfig(
            path="/home/user/.config/vlc",
            path_type=PathType.DIRECTORY,
            status=OrphanStatus.ORPHAN,
            size_bytes=4096,
            mtime="2024-01-15T10:00:00Z",
            orphan_reason=OrphanReason.NO_PACKAGE_MATCH,
            confidence=0.70,
        )
        assert sc.path == "/home/user/.config/vlc"
        assert sc.path_type == PathType.DIRECTORY
        assert sc.status == OrphanStatus.ORPHAN
        assert sc.size_bytes == 4096
        assert sc.mtime == "2024-01-15T10:00:00Z"
        assert sc.orphan_reason == OrphanReason.NO_PACKAGE_MATCH
        assert sc.confidence == 0.70

    def test_scanned_config_frozen(self) -> None:
        """Verify immutability raises FrozenInstanceError on assignment."""
        sc = ScannedConfig(
            path="/home/user/.config/vlc",
            path_type=PathType.DIRECTORY,
            status=OrphanStatus.ORPHAN,
            size_bytes=4096,
            mtime="2024-01-15T10:00:00Z",
            orphan_reason=OrphanReason.NO_PACKAGE_MATCH,
            confidence=0.70,
        )
        with pytest.raises(AttributeError):
            sc.path = "/other/path"  # type: ignore[misc]

    def test_scanned_config_with_none_optionals(self) -> None:
        """Create ScannedConfig with None for all optional fields."""
        sc = ScannedConfig(
            path="/home/user/.config/old-app",
            path_type=PathType.DIRECTORY,
            status=OrphanStatus.ORPHAN,
            size_bytes=None,
            mtime=None,
            orphan_reason=None,
            confidence=0.95,
        )
        assert sc.size_bytes is None
        assert sc.mtime is None
        assert sc.orphan_reason is None

    def test_scanned_config_empty_path_rejected(self) -> None:
        """Empty path string should raise ValueError."""
        with pytest.raises(ValueError, match="Path cannot be empty"):
            ScannedConfig(
                path="",
                path_type=PathType.FILE,
                status=OrphanStatus.ORPHAN,
                size_bytes=None,
                mtime=None,
                orphan_reason=None,
                confidence=0.5,
            )

    def test_scanned_config_invalid_confidence_rejected(self) -> None:
        """Confidence above 1.0 should raise ValueError."""
        with pytest.raises(ValueError, match="Confidence must be between"):
            ScannedConfig(
                path="/home/user/.config/test",
                path_type=PathType.DIRECTORY,
                status=OrphanStatus.ORPHAN,
                size_bytes=None,
                mtime=None,
                orphan_reason=None,
                confidence=1.5,
            )

    def test_scanned_config_negative_confidence_rejected(self) -> None:
        """Negative confidence should raise ValueError."""
        with pytest.raises(ValueError, match="Confidence must be between"):
            ScannedConfig(
                path="/home/user/.config/test",
                path_type=PathType.DIRECTORY,
                status=OrphanStatus.ORPHAN,
                size_bytes=None,
                mtime=None,
                orphan_reason=None,
                confidence=-0.1,
            )

    def test_scanned_config_boundary_confidence(self) -> None:
        """Confidence at exact boundaries (0.0 and 1.0) should be accepted."""
        sc_zero = ScannedConfig(
            path="/home/user/.config/a",
            path_type=PathType.FILE,
            status=OrphanStatus.ORPHAN,
            size_bytes=None,
            mtime=None,
            orphan_reason=None,
            confidence=0.0,
        )
        sc_one = ScannedConfig(
            path="/home/user/.config/b",
            path_type=PathType.FILE,
            status=OrphanStatus.ORPHAN,
            size_bytes=None,
            mtime=None,
            orphan_reason=None,
            confidence=1.0,
        )
        assert sc_zero.confidence == 0.0
        assert sc_one.confidence == 1.0
