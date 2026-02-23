"""Config scanning and cleanup module.

This module provides config orphan detection, protected config
management, and deletion operations for the configs domain.
"""

from popctl.configs.operator import ConfigOperator
from popctl.configs.scanner import ConfigScanner

__all__ = [
    "ConfigOperator",
    "ConfigScanner",
]
