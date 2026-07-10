import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class BackupMetadata:
    """Stored as ``metadata.json`` at the root of the tar archive.

    Kept intentionally minimal -- the archive itself is the file catalog,
    and the manifest inside provides package/path details.
    """

    created: str
    hostname: str
    popctl_version: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "created": self.created,
            "hostname": self.hostname,
            "popctl_version": self.popctl_version,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> BackupMetadata:
        return cls(
            created=data["created"],
            hostname=data["hostname"],
            popctl_version=data["popctl_version"],
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_json(cls, text: str) -> BackupMetadata:
        return cls.from_dict(json.loads(text))
