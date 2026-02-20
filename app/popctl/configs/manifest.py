"""Config manifest models — delegates to domain.manifest."""

from popctl.domain.manifest import DomainConfig as ConfigsConfig
from popctl.domain.manifest import DomainEntry as ConfigEntry

__all__ = ["ConfigEntry", "ConfigsConfig"]
