"""Shared fixtures for unit tests."""

import re
from datetime import UTC, datetime

import pytest
from popctl.models.manifest import (
    Manifest,
    ManifestMeta,
    PackageConfig,
    PackageEntry,
    SystemConfig,
)

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    return _ANSI_RE.sub("", text)


@pytest.fixture
def sample_manifest() -> Manifest:
    """Create a sample manifest for testing."""
    now = datetime.now(UTC)
    return Manifest(
        meta=ManifestMeta(created=now, updated=now),
        system=SystemConfig(name="test-machine"),
        packages=PackageConfig(
            keep={
                "firefox": PackageEntry(source="apt"),
                "neovim": PackageEntry(source="apt"),
                "com.spotify.Client": PackageEntry(source="flatpak"),
            },
            remove={
                "bloatware": PackageEntry(source="apt"),
            },
        ),
    )
