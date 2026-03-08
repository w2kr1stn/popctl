"""Backup configuration management.

Configuration is stored in ~/.config/popctl/backup.toml with the
following optional fields:

    target = "/mnt/external/backups"     # or "gdrive:popctl-backups/"
    recipients = "~/.config/popctl/backup.age-recipients"
    identity = "~/.config/popctl/backup.age-key"
    max_backups = 1                      # 0 = keep all
"""

from __future__ import annotations

import logging
import tomllib
from dataclasses import dataclass
from pathlib import Path

from popctl.core.paths import get_config_dir

logger = logging.getLogger(__name__)

_CONFIG_FILENAME = "backup.toml"


@dataclass(frozen=True, slots=True)
class BackupConfig:
    """Backup configuration with defaults from backup.toml.

    Attributes:
        target: Destination path or rclone remote. Empty = default local dir.
        recipients: age recipients file or public key for encryption.
        identity: age identity (private key) file for decryption.
        max_backups: Maximum number of backups to retain. 0 = keep all.
    """

    target: str = ""
    recipients: str = ""
    identity: str = ""
    max_backups: int = 1


def load_backup_config(path: Path | None = None) -> BackupConfig:
    """Load backup configuration from backup.toml.

    Falls back to empty defaults if the file does not exist or is invalid.

    Args:
        path: Optional explicit path to config file.

    Returns:
        BackupConfig with values from file or defaults.
    """
    config_path = path or get_config_dir() / _CONFIG_FILENAME

    if not config_path.exists():
        return BackupConfig()

    try:
        with config_path.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError) as e:
        logger.warning("Could not load %s: %s", config_path, e)
        return BackupConfig()

    return BackupConfig(
        target=str(data.get("target", "")),
        recipients=str(data.get("recipients", "")),
        identity=str(data.get("identity", "")),
        max_backups=int(data.get("max_backups", 1)),
    )
