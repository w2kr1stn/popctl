"""Backup metadata model for system snapshots.

Defines the minimal metadata embedded in every backup archive,
used for identification and confirmation during restore.
"""

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class BackupMetadata:
    """Metadata embedded in every backup archive.

    Stored as ``metadata.json`` at the root of the tar archive.
    Kept intentionally minimal — the archive itself is the file catalog,
    and the manifest inside provides package/path details.

    Attributes:
        created: ISO 8601 timestamp of backup creation.
        hostname: Source machine hostname.
        popctl_version: popctl version that created the backup.
    """

    created: str
    hostname: str
    popctl_version: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dictionary for JSON storage."""
        return {
            "created": self.created,
            "hostname": self.hostname,
            "popctl_version": self.popctl_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BackupMetadata":
        """Deserialize from dictionary.

        Raises:
            KeyError: If required fields are missing.
        """
        return cls(
            created=data["created"],
            hostname=data["hostname"],
            popctl_version=data["popctl_version"],
        )

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_json(cls, text: str) -> "BackupMetadata":
        """Deserialize from JSON string.

        Raises:
            json.JSONDecodeError: If text is not valid JSON.
            KeyError: If required fields are missing.
        """
        return cls.from_dict(json.loads(text))
