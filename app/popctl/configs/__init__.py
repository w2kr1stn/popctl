"""Config scanning and cleanup module.

This module provides config orphan detection, protected config
management, manifest models, and deletion operations for the
configs domain.
"""

from popctl.configs.models import ConfigOrphanReason, ConfigStatus, ConfigType, ScannedConfig
from popctl.configs.protected import PROTECTED_CONFIG_PATTERNS, is_protected_config

__all__ = [
    "PROTECTED_CONFIG_PATTERNS",
    "ConfigOrphanReason",
    "ConfigStatus",
    "ConfigType",
    "ScannedConfig",
    "is_protected_config",
]
