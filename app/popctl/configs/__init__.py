"""Config scanning and cleanup module.

This module provides config orphan detection, protected config
management, manifest models, and deletion operations for the
configs domain.
"""

from popctl.configs.models import ConfigOrphanReason, ConfigStatus, ConfigType, ScannedConfig
from popctl.configs.operator import ConfigActionResult, ConfigOperator
from popctl.configs.scanner import ConfigScanner
from popctl.domain.manifest import DomainConfig, DomainEntry
from popctl.domain.protected import PROTECTED_PATTERNS, is_protected

PROTECTED_CONFIG_PATTERNS = PROTECTED_PATTERNS["configs"]


def is_protected_config(path: str) -> bool:
    """Check if a config path is protected and should not be deleted."""
    return is_protected(path, "configs")


__all__ = [
    "PROTECTED_CONFIG_PATTERNS",
    "ConfigActionResult",
    "DomainEntry",
    "ConfigOperator",
    "ConfigOrphanReason",
    "ConfigScanner",
    "ConfigStatus",
    "ConfigType",
    "DomainConfig",
    "ScannedConfig",
    "is_protected_config",
]
