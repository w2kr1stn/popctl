"""Config scanning and cleanup module.

This module provides config orphan detection, protected config
management, manifest models, and deletion operations for the
configs domain.
"""

from popctl.configs.manifest import ConfigEntry, ConfigsConfig
from popctl.configs.models import ConfigOrphanReason, ConfigStatus, ConfigType, ScannedConfig
from popctl.configs.operator import ConfigActionResult, ConfigOperator
from popctl.configs.protected import PROTECTED_CONFIG_PATTERNS, is_protected_config
from popctl.configs.scanner import ConfigScanner

__all__ = [
    "PROTECTED_CONFIG_PATTERNS",
    "ConfigActionResult",
    "ConfigEntry",
    "ConfigOperator",
    "ConfigOrphanReason",
    "ConfigScanner",
    "ConfigStatus",
    "ConfigType",
    "ConfigsConfig",
    "ScannedConfig",
    "is_protected_config",
]
