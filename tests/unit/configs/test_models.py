"""Tests for config domain models."""

import pytest
from popctl.configs.models import ConfigOrphanReason, ConfigStatus, ConfigType, ScannedConfig


class TestConfigType:
    """Tests for ConfigType enum."""

    def test_config_type_values(self) -> None:
        """Verify all 2 ConfigType enum values exist with correct string values."""
        assert ConfigType.DIRECTORY == "directory"
        assert ConfigType.FILE == "file"
        assert len(ConfigType) == 2

    def test_config_type_is_str_enum(self) -> None:
        """ConfigType values are usable as strings."""
        assert isinstance(ConfigType.DIRECTORY, str)
        assert ConfigType.FILE.value == "file"


class TestConfigStatus:
    """Tests for ConfigStatus enum."""

    def test_config_status_values(self) -> None:
        """Verify all 4 ConfigStatus enum values exist with correct string values."""
        assert ConfigStatus.ORPHAN == "orphan"
        assert ConfigStatus.OWNED == "owned"
        assert ConfigStatus.PROTECTED == "protected"
        assert ConfigStatus.UNKNOWN == "unknown"
        assert len(ConfigStatus) == 4


class TestConfigOrphanReason:
    """Tests for ConfigOrphanReason enum."""

    def test_config_orphan_reason_values(self) -> None:
        """Verify all 3 ConfigOrphanReason enum values exist with correct string values."""
        assert ConfigOrphanReason.APP_NOT_INSTALLED == "app_not_installed"
        assert ConfigOrphanReason.NO_PACKAGE_MATCH == "no_package_match"
        assert ConfigOrphanReason.DEAD_LINK == "dead_link"
        assert len(ConfigOrphanReason) == 3


class TestScannedConfig:
    """Tests for ScannedConfig frozen dataclass."""

    def test_scanned_config_creation(self) -> None:
        """Create a valid ScannedConfig with all fields populated."""
        sc = ScannedConfig(
            path="/home/user/.config/vlc",
            config_type=ConfigType.DIRECTORY,
            status=ConfigStatus.ORPHAN,
            size_bytes=4096,
            mtime="2024-01-15T10:00:00Z",
            orphan_reason=ConfigOrphanReason.APP_NOT_INSTALLED,
            confidence=0.70,
            description="VLC media player config directory",
        )
        assert sc.path == "/home/user/.config/vlc"
        assert sc.config_type == ConfigType.DIRECTORY
        assert sc.status == ConfigStatus.ORPHAN
        assert sc.size_bytes == 4096
        assert sc.mtime == "2024-01-15T10:00:00Z"
        assert sc.orphan_reason == ConfigOrphanReason.APP_NOT_INSTALLED
        assert sc.confidence == 0.70
        assert sc.description == "VLC media player config directory"

    def test_scanned_config_frozen(self) -> None:
        """Verify immutability raises FrozenInstanceError on assignment."""
        sc = ScannedConfig(
            path="/home/user/.config/vlc",
            config_type=ConfigType.DIRECTORY,
            status=ConfigStatus.ORPHAN,
            size_bytes=4096,
            mtime="2024-01-15T10:00:00Z",
            orphan_reason=ConfigOrphanReason.APP_NOT_INSTALLED,
            confidence=0.70,
            description="VLC config",
        )
        with pytest.raises(AttributeError):
            sc.path = "/other/path"  # type: ignore[misc]

    def test_scanned_config_with_none_optionals(self) -> None:
        """Create ScannedConfig with None for all optional fields."""
        sc = ScannedConfig(
            path="/home/user/.config/old-app",
            config_type=ConfigType.DIRECTORY,
            status=ConfigStatus.ORPHAN,
            size_bytes=None,
            mtime=None,
            orphan_reason=None,
            confidence=0.95,
            description=None,
        )
        assert sc.size_bytes is None
        assert sc.mtime is None
        assert sc.orphan_reason is None
        assert sc.description is None

    def test_scanned_config_empty_path_rejected(self) -> None:
        """Empty path string should raise ValueError."""
        with pytest.raises(ValueError, match="Path cannot be empty"):
            ScannedConfig(
                path="",
                config_type=ConfigType.FILE,
                status=ConfigStatus.UNKNOWN,
                size_bytes=None,
                mtime=None,
                orphan_reason=None,
                confidence=0.5,
                description=None,
            )

    def test_scanned_config_invalid_confidence_rejected(self) -> None:
        """Confidence above 1.0 should raise ValueError."""
        with pytest.raises(ValueError, match="Confidence must be between"):
            ScannedConfig(
                path="/home/user/.config/test",
                config_type=ConfigType.DIRECTORY,
                status=ConfigStatus.ORPHAN,
                size_bytes=None,
                mtime=None,
                orphan_reason=None,
                confidence=1.5,
                description=None,
            )

    def test_scanned_config_negative_confidence_rejected(self) -> None:
        """Negative confidence should raise ValueError."""
        with pytest.raises(ValueError, match="Confidence must be between"):
            ScannedConfig(
                path="/home/user/.config/test",
                config_type=ConfigType.DIRECTORY,
                status=ConfigStatus.ORPHAN,
                size_bytes=None,
                mtime=None,
                orphan_reason=None,
                confidence=-0.1,
                description=None,
            )

    def test_scanned_config_boundary_confidence(self) -> None:
        """Confidence at exact boundaries (0.0 and 1.0) should be accepted."""
        sc_zero = ScannedConfig(
            path="/home/user/.config/a",
            config_type=ConfigType.FILE,
            status=ConfigStatus.UNKNOWN,
            size_bytes=None,
            mtime=None,
            orphan_reason=None,
            confidence=0.0,
            description=None,
        )
        sc_one = ScannedConfig(
            path="/home/user/.config/b",
            config_type=ConfigType.FILE,
            status=ConfigStatus.UNKNOWN,
            size_bytes=None,
            mtime=None,
            orphan_reason=None,
            confidence=1.0,
            description=None,
        )
        assert sc_zero.confidence == 0.0
        assert sc_one.confidence == 1.0
