"""Shared domain logic for filesystem and config modules."""

from popctl.domain.manifest import DomainConfig, DomainEntry
from popctl.domain.protected import PROTECTED_PATTERNS, is_protected

__all__ = [
    "PROTECTED_PATTERNS",
    "DomainConfig",
    "DomainEntry",
    "is_protected",
]
