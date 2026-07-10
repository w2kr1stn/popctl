"""Unit tests for BackupMetadata model."""

import json

import pytest
from popctl.models.backup import BackupMetadata


class TestBackupMetadata:
    """Tests for BackupMetadata dataclass."""

    def test_create_metadata(self) -> None:
        """Can create metadata with required fields."""
        meta = BackupMetadata(
            created="2026-03-06T12:00:00+00:00",
            hostname="myhost",
            popctl_version="0.1.0",
        )
        assert meta.created == "2026-03-06T12:00:00+00:00"
        assert meta.hostname == "myhost"
        assert meta.popctl_version == "0.1.0"

    def test_metadata_is_frozen(self) -> None:
        """BackupMetadata is immutable."""
        meta = BackupMetadata(
            created="2026-03-06T12:00:00+00:00",
            hostname="myhost",
            popctl_version="0.1.0",
        )
        with pytest.raises(AttributeError):
            meta.hostname = "other"  # type: ignore[misc]

    def test_to_dict(self) -> None:
        """to_dict serializes all fields."""
        meta = BackupMetadata(
            created="2026-03-06T12:00:00+00:00",
            hostname="myhost",
            popctl_version="0.1.0",
        )
        result = meta.to_dict()
        assert result == {
            "created": "2026-03-06T12:00:00+00:00",
            "hostname": "myhost",
            "popctl_version": "0.1.0",
        }

    def test_from_dict(self) -> None:
        """from_dict deserializes correctly."""
        data = {
            "created": "2026-03-06T12:00:00+00:00",
            "hostname": "myhost",
            "popctl_version": "0.1.0",
        }
        meta = BackupMetadata.from_dict(data)
        assert meta.hostname == "myhost"

    def test_from_dict_missing_field_raises(self) -> None:
        """from_dict raises KeyError on missing fields."""
        with pytest.raises(KeyError):
            BackupMetadata.from_dict({"created": "2026-03-06T12:00:00+00:00"})

    def test_to_json_roundtrip(self) -> None:
        """to_json -> from_json preserves all data."""
        meta = BackupMetadata(
            created="2026-03-06T12:00:00+00:00",
            hostname="myhost",
            popctl_version="0.1.0",
        )
        json_str = meta.to_json()
        restored = BackupMetadata.from_json(json_str)
        assert restored == meta

    def test_to_json_is_valid_json(self) -> None:
        """to_json produces valid JSON."""
        meta = BackupMetadata(
            created="2026-03-06T12:00:00+00:00",
            hostname="myhost",
            popctl_version="0.1.0",
        )
        parsed = json.loads(meta.to_json())
        assert parsed["hostname"] == "myhost"

    def test_from_json_invalid_raises(self) -> None:
        """from_json raises on invalid JSON."""
        with pytest.raises(json.JSONDecodeError):
            BackupMetadata.from_json("not json")
