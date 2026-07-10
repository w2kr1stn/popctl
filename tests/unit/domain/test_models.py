"""Tests for shared domain models.

ScannedEntry frozen dataclass used by both filesystem and configs domains.
"""

import pytest
from popctl.domain.models import OrphanReason, OrphanStatus, PathType, ScannedEntry


class TestScannedEntry:
    """Tests for ScannedEntry frozen dataclass."""

    def test_scanned_entry_creation(self) -> None:
        """Create a valid ScannedEntry with all fields populated."""
        entry = ScannedEntry(
            path="/home/user/.config/vlc",
            path_type=PathType.DIRECTORY,
            status=OrphanStatus.ORPHAN,
            size_bytes=4096,
            mtime="2024-01-15T10:00:00Z",
            parent_target="~/.config",
            orphan_reason=OrphanReason.NO_PACKAGE_MATCH,
            confidence=0.70,
        )
        assert entry.path == "/home/user/.config/vlc"
        assert entry.path_type == PathType.DIRECTORY
        assert entry.status == OrphanStatus.ORPHAN
        assert entry.size_bytes == 4096
        assert entry.mtime == "2024-01-15T10:00:00Z"
        assert entry.parent_target == "~/.config"
        assert entry.orphan_reason == OrphanReason.NO_PACKAGE_MATCH
        assert entry.confidence == 0.70

    def test_scanned_entry_frozen(self) -> None:
        """Verify immutability raises FrozenInstanceError on assignment."""
        entry = ScannedEntry(
            path="/home/user/.config/vlc",
            path_type=PathType.DIRECTORY,
            status=OrphanStatus.ORPHAN,
            size_bytes=4096,
            mtime="2024-01-15T10:00:00Z",
            parent_target="~/.config",
            orphan_reason=OrphanReason.NO_PACKAGE_MATCH,
            confidence=0.70,
        )
        with pytest.raises(AttributeError):
            entry.path = "/other/path"  # type: ignore[misc]

    def test_scanned_entry_with_none_optionals(self) -> None:
        """Create ScannedEntry with None for all optional fields."""
        entry = ScannedEntry(
            path="/home/user/.cache/old-app",
            path_type=PathType.DIRECTORY,
            status=OrphanStatus.ORPHAN,
            size_bytes=None,
            mtime=None,
            parent_target="~/.cache",
            orphan_reason=None,
            confidence=0.95,
        )
        assert entry.size_bytes is None
        assert entry.mtime is None
        assert entry.orphan_reason is None

    def test_scanned_entry_empty_path_rejected(self) -> None:
        """Empty path string should raise ValueError."""
        with pytest.raises(ValueError, match="Path cannot be empty"):
            ScannedEntry(
                path="",
                path_type=PathType.FILE,
                status=OrphanStatus.ORPHAN,
                size_bytes=None,
                mtime=None,
                parent_target="~/.config",
                orphan_reason=None,
                confidence=0.5,
            )

    def test_scanned_entry_invalid_confidence_rejected(self) -> None:
        """Confidence outside 0.0-1.0 range should raise ValueError."""
        with pytest.raises(ValueError, match="Confidence must be between"):
            ScannedEntry(
                path="/home/user/.config/test",
                path_type=PathType.DIRECTORY,
                status=OrphanStatus.ORPHAN,
                size_bytes=None,
                mtime=None,
                parent_target="~/.config",
                orphan_reason=None,
                confidence=1.5,
            )

    def test_scanned_entry_negative_confidence_rejected(self) -> None:
        """Negative confidence should raise ValueError."""
        with pytest.raises(ValueError, match="Confidence must be between"):
            ScannedEntry(
                path="/home/user/.config/test",
                path_type=PathType.DIRECTORY,
                status=OrphanStatus.ORPHAN,
                size_bytes=None,
                mtime=None,
                parent_target="~/.config",
                orphan_reason=None,
                confidence=-0.1,
            )

    def test_scanned_entry_boundary_confidence(self) -> None:
        """Confidence at exact boundaries (0.0 and 1.0) should be accepted."""
        entry_zero = ScannedEntry(
            path="/home/user/.config/a",
            path_type=PathType.FILE,
            status=OrphanStatus.ORPHAN,
            size_bytes=None,
            mtime=None,
            parent_target="~/.config",
            orphan_reason=None,
            confidence=0.0,
        )
        entry_one = ScannedEntry(
            path="/home/user/.config/b",
            path_type=PathType.FILE,
            status=OrphanStatus.ORPHAN,
            size_bytes=None,
            mtime=None,
            parent_target="~/.config",
            orphan_reason=None,
            confidence=1.0,
        )
        assert entry_zero.confidence == 0.0
        assert entry_one.confidence == 1.0
