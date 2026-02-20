"""Filesystem manifest models — delegates to domain.manifest."""

from popctl.domain.manifest import DomainConfig as FilesystemConfig
from popctl.domain.manifest import DomainEntry as FilesystemEntry

__all__ = ["FilesystemConfig", "FilesystemEntry"]
