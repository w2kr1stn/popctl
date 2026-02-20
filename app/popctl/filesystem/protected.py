"""Protected filesystem paths — delegates to domain.protected."""

from popctl.domain.protected import PROTECTED_PATTERNS, is_protected

PROTECTED_PATH_PATTERNS = PROTECTED_PATTERNS["filesystem"]


def is_protected_path(path: str) -> bool:
    """Check if a filesystem path is protected and should not be deleted."""
    return is_protected(path, "filesystem")
