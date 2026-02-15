"""Tests for filesystem domain models."""

import pytest
from popctl.filesystem.models import OrphanReason, PathStatus, PathType, ScannedPath


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


class TestPathStatus:
    """Tests for PathStatus enum."""

    def test_path_status_values(self) -> None:
        """Verify all 4 PathStatus enum values exist with correct string values."""
        assert PathStatus.ORPHAN == "orphan"
        assert PathStatus.OWNED == "owned"
        assert PathStatus.PROTECTED == "protected"
        assert PathStatus.UNKNOWN == "unknown"
        assert len(PathStatus) == 4


class TestOrphanReason:
    """Tests for OrphanReason enum."""

    def test_orphan_reason_values(self) -> None:
        """Verify all 4 OrphanReason enum values exist with correct string values."""
        assert OrphanReason.NO_PACKAGE_MATCH == "no_package_match"
        assert OrphanReason.PACKAGE_UNINSTALLED == "package_removed"
        assert OrphanReason.STALE_CACHE == "stale_cache"
        assert OrphanReason.DEAD_LINK == "dead_link"
        assert len(OrphanReason) == 4


class TestScannedPath:
    """Tests for ScannedPath frozen dataclass."""

    def test_scanned_path_creation(self) -> None:
        """Create a valid ScannedPath with all fields populated."""
        sp = ScannedPath(
            path="/home/user/.config/vlc",
            path_type=PathType.DIRECTORY,
            status=PathStatus.ORPHAN,
            size_bytes=4096,
            mtime="2024-01-15T10:00:00Z",
            parent_target="~/.config",
            orphan_reason=OrphanReason.NO_PACKAGE_MATCH,
            confidence=0.70,
            description="VLC media player config directory",
        )
        assert sp.path == "/home/user/.config/vlc"
        assert sp.path_type == PathType.DIRECTORY
        assert sp.status == PathStatus.ORPHAN
        assert sp.size_bytes == 4096
        assert sp.mtime == "2024-01-15T10:00:00Z"
        assert sp.parent_target == "~/.config"
        assert sp.orphan_reason == OrphanReason.NO_PACKAGE_MATCH
        assert sp.confidence == 0.70
        assert sp.description == "VLC media player config directory"

    def test_scanned_path_frozen(self) -> None:
        """Verify immutability raises FrozenInstanceError on assignment."""
        sp = ScannedPath(
            path="/home/user/.config/vlc",
            path_type=PathType.DIRECTORY,
            status=PathStatus.ORPHAN,
            size_bytes=4096,
            mtime="2024-01-15T10:00:00Z",
            parent_target="~/.config",
            orphan_reason=OrphanReason.NO_PACKAGE_MATCH,
            confidence=0.70,
            description="VLC config",
        )
        with pytest.raises(AttributeError):
            sp.path = "/other/path"  # type: ignore[misc]

    def test_scanned_path_with_none_optionals(self) -> None:
        """Create ScannedPath with None for all optional fields."""
        sp = ScannedPath(
            path="/home/user/.cache/old-app",
            path_type=PathType.DIRECTORY,
            status=PathStatus.ORPHAN,
            size_bytes=None,
            mtime=None,
            parent_target="~/.cache",
            orphan_reason=None,
            confidence=0.95,
            description=None,
        )
        assert sp.size_bytes is None
        assert sp.mtime is None
        assert sp.orphan_reason is None
        assert sp.description is None

    def test_scanned_path_empty_path_rejected(self) -> None:
        """Empty path string should raise ValueError."""
        with pytest.raises(ValueError, match="Path cannot be empty"):
            ScannedPath(
                path="",
                path_type=PathType.FILE,
                status=PathStatus.UNKNOWN,
                size_bytes=None,
                mtime=None,
                parent_target="~/.config",
                orphan_reason=None,
                confidence=0.5,
                description=None,
            )

    def test_scanned_path_invalid_confidence_rejected(self) -> None:
        """Confidence outside 0.0-1.0 range should raise ValueError."""
        with pytest.raises(ValueError, match="Confidence must be between"):
            ScannedPath(
                path="/home/user/.config/test",
                path_type=PathType.DIRECTORY,
                status=PathStatus.ORPHAN,
                size_bytes=None,
                mtime=None,
                parent_target="~/.config",
                orphan_reason=None,
                confidence=1.5,
                description=None,
            )

    def test_scanned_path_negative_confidence_rejected(self) -> None:
        """Negative confidence should raise ValueError."""
        with pytest.raises(ValueError, match="Confidence must be between"):
            ScannedPath(
                path="/home/user/.config/test",
                path_type=PathType.DIRECTORY,
                status=PathStatus.ORPHAN,
                size_bytes=None,
                mtime=None,
                parent_target="~/.config",
                orphan_reason=None,
                confidence=-0.1,
                description=None,
            )

    def test_scanned_path_boundary_confidence(self) -> None:
        """Confidence at exact boundaries (0.0 and 1.0) should be accepted."""
        sp_zero = ScannedPath(
            path="/home/user/.config/a",
            path_type=PathType.FILE,
            status=PathStatus.UNKNOWN,
            size_bytes=None,
            mtime=None,
            parent_target="~/.config",
            orphan_reason=None,
            confidence=0.0,
            description=None,
        )
        sp_one = ScannedPath(
            path="/home/user/.config/b",
            path_type=PathType.FILE,
            status=PathStatus.UNKNOWN,
            size_bytes=None,
            mtime=None,
            parent_target="~/.config",
            orphan_reason=None,
            confidence=1.0,
            description=None,
        )
        assert sp_zero.confidence == 0.0
        assert sp_one.confidence == 1.0
