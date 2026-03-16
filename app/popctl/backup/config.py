from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass
from pathlib import Path

from popctl.backup.backup import BackupError
from popctl.core.paths import get_config_dir

logger = logging.getLogger(__name__)

_CONFIG_FILENAME = "backup.toml"


@dataclass(frozen=True, slots=True)
class BackupConfig:

    target: str = ""
    recipients: str = ""
    identity: str = ""
    max_backups: int = 1


def load_backup_config(path: Path | None = None) -> BackupConfig:
    config_path = path or get_config_dir() / _CONFIG_FILENAME

    if not config_path.exists():
        return BackupConfig()

    try:
        with config_path.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise BackupError(f"Corrupt backup config {config_path}: {e}") from e
    except OSError as e:
        raise BackupError(f"Cannot read backup config {config_path}: {e}") from e

    raw_max = data.get("max_backups", 1)
    if not isinstance(raw_max, int):
        raise BackupError(f"Invalid max_backups: expected integer, got {type(raw_max).__name__}")

    return BackupConfig(
        target=str(data.get("target", "")),
        recipients=str(data.get("recipients", "")),
        identity=str(data.get("identity", "")),
        max_backups=raw_max,
    )
