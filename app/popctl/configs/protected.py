"""Protected config paths — delegates to domain.protected."""

from popctl.domain.protected import PROTECTED_PATTERNS, is_protected

PROTECTED_CONFIG_PATTERNS = PROTECTED_PATTERNS["configs"]


def is_protected_config(path: str) -> bool:
    """Check if a config path is protected and should not be deleted."""
    return is_protected(path, "configs")
