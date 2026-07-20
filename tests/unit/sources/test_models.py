from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from popctl.backup.backup import collect_backup_files
from popctl.cli.types import SourceChoice as CliSourceChoice
from popctl.core.manifest import load_manifest, save_manifest
from popctl.models.manifest import Manifest, ManifestMeta, PackageConfig, SystemConfig
from popctl.models.package import PackageSource, SourceChoice
from popctl.sources.models import (
    AptKey,
    AptSource,
    AptSourceFormat,
    AptSources,
    FlatpakApp,
    FlatpakRemote,
    FlatpakScope,
    FlatpakSources,
    ReplayMode,
    SignedByBinding,
    SnapChannel,
    SnapSources,
    SourceLocator,
    SourcePlatform,
    SourcesConfig,
)
from pydantic import ValidationError


@pytest.fixture
def sources_config() -> SourcesConfig:
    return SourcesConfig(
        platform=SourcePlatform(distro_id="ubuntu", codename="noble"),
        apt=AptSources(
            entries=(
                AptSource(
                    id="vendor",
                    capture_path="/etc/apt/sources.list.d/vendor.sources",
                    format=AptSourceFormat.DEB822,
                    ordinal=1,
                    managed_target="popctl-vendor",
                    verbatim_stanza="Types: deb\nURIs: https://packages.example.com\n",
                    key_ids=("vendor",),
                    signed_by=SignedByBinding(
                        key_paths=("/etc/apt/keyrings/vendor.asc",),
                        fingerprint_selectors=("0123456789ABCDEF!",),
                        embedded_armor="-----BEGIN PGP PUBLIC KEY BLOCK-----\nkey\n",
                    ),
                    replay_mode=ReplayMode.REPLAY,
                    ppa_display="vendor/ppa",
                ),
            ),
            keys=(
                AptKey(
                    id="vendor",
                    target_path="/etc/apt/keyrings/vendor.asc",
                    armor="-----BEGIN PGP PUBLIC KEY BLOCK-----\nkey\n",
                    fingerprints=("0123456789ABCDEF",),
                ),
            ),
        ),
        flatpak=FlatpakSources(
            remotes=(
                FlatpakRemote(
                    name="flathub-beta",
                    scope=FlatpakScope.USER,
                    url="https://dl.flathub.org/beta-repo/flathub-beta.flatpakrepo",
                    gpg_verify=True,
                    gpg_key_armor="-----BEGIN PGP PUBLIC KEY BLOCK-----\nflatpak\n",
                    gpg_fingerprints=("FEDCBA9876543210",),
                    replay_mode=ReplayMode.REPLAY,
                ),
            ),
            apps=(
                FlatpakApp(
                    id="org.example.App",
                    origin="flathub-beta",
                    scope=FlatpakScope.USER,
                    arch="x86_64",
                    branch="beta",
                ),
            ),
        ),
        snap=SnapSources(
            packages=(
                SnapChannel(
                    name="hello-world",
                    channel="latest/edge",
                    replay_mode=ReplayMode.BLOCKED,
                ),
            ),
        ),
    )


@pytest.fixture
def manifest_with_sources(sources_config: SourcesConfig) -> Manifest:
    now = datetime.now(UTC)
    return Manifest(
        meta=ManifestMeta(created=now, updated=now),
        system=SystemConfig(name="test-machine"),
        packages=PackageConfig(keep={}, remove={}),
        sources=sources_config,
    )


def test_sources_round_trip_every_manifest_field(
    tmp_path: Path,
    manifest_with_sources: Manifest,
) -> None:
    manifest_path = tmp_path / "manifest.toml"

    save_manifest(manifest_with_sources, manifest_path)
    loaded = load_manifest(manifest_path)

    assert loaded.sources == manifest_with_sources.sources
    assert loaded.sources is not None
    assert loaded.sources.platform == SourcePlatform(distro_id="ubuntu", codename="noble")
    assert loaded.sources.apt.entries[0].replay_mode is ReplayMode.REPLAY
    assert loaded.sources.apt.entries[0].signed_by.fingerprint_selectors == (
        "0123456789ABCDEF!",
    )
    assert loaded.sources.apt.keys[0].armor.startswith("-----BEGIN PGP")
    assert loaded.sources.flatpak.remotes[0].gpg_key_armor.startswith("-----BEGIN PGP")
    assert loaded.sources.flatpak.remotes[0].gpg_fingerprints == ("FEDCBA9876543210",)
    assert loaded.sources.snap.packages[0].replay_mode is ReplayMode.BLOCKED


def test_apt_source_capture_and_managed_target_locators(sources_config: SourcesConfig) -> None:
    source = sources_config.apt.entries[0]

    assert source.capture_locator == SourceLocator(
        manager=PackageSource.APT,
        parts=("/etc/apt/sources.list.d/vendor.sources", "1"),
    )
    assert source.managed_target_locator == SourceLocator(
        manager=PackageSource.APT,
        parts=("popctl-vendor",),
    )
    assert source.capture_locator != source.managed_target_locator
    assert sources_config.flatpak.remotes[0].locator == SourceLocator(
        manager=PackageSource.FLATPAK,
        parts=("user", "flathub-beta"),
    )
    assert sources_config.snap.packages[0].locator == SourceLocator(
        manager=PackageSource.SNAP,
        parts=("hello-world",),
    )


def test_cli_reexports_the_shared_source_choice() -> None:
    assert CliSourceChoice is SourceChoice
    assert SourceChoice.FLATPAK.to_package_source() is PackageSource.FLATPAK


def test_flatpak_app_locator_keeps_duplicate_id_contexts_distinct() -> None:
    stable = FlatpakApp(
        id="org.example.App",
        origin="flathub",
        scope=FlatpakScope.USER,
        arch="x86_64",
        branch="stable",
    )
    beta = stable.model_copy(update={"branch": "beta"})
    system = stable.model_copy(update={"scope": FlatpakScope.SYSTEM})

    assert len({stable.locator, beta.locator, system.locator}) == 3
    assert stable.locator.parts == ("user", "org.example.App", "x86_64", "stable")
    assert beta.locator.parts == ("user", "org.example.App", "x86_64", "beta")


def test_manifest_without_sources_remains_compatible(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    manifest = Manifest(
        meta=ManifestMeta(created=now, updated=now),
        system=SystemConfig(name="test-machine"),
        packages=PackageConfig(keep={}, remove={}),
    )
    manifest_path = tmp_path / "manifest.toml"

    save_manifest(manifest, manifest_path)
    loaded = load_manifest(manifest_path)

    assert loaded.sources is None
    assert "sources" not in manifest_path.read_text(encoding="utf-8")


def test_sources_reject_unknown_structural_fields(sources_config: SourcesConfig) -> None:
    data = sources_config.model_dump(mode="json")
    data["unknown"] = True

    with pytest.raises(ValidationError):
        SourcesConfig.model_validate(data)

    nested_data = sources_config.model_dump(mode="json")
    nested_data["apt"]["entries"][0]["unknown"] = True

    with pytest.raises(ValidationError):
        SourcesConfig.model_validate(nested_data)


def test_sources_reject_duplicate_flatpak_remote_locator(sources_config: SourcesConfig) -> None:
    data = sources_config.model_dump(mode="json")
    data["flatpak"]["remotes"].append(data["flatpak"]["remotes"][0].copy())

    with pytest.raises(ValidationError, match="Duplicate source locator"):
        SourcesConfig.model_validate(data)


def test_backup_carries_manifest_sources(
    tmp_path: Path,
    manifest_with_sources: Manifest,
) -> None:
    config_dir = tmp_path / "config"
    state_dir = tmp_path / "state"
    config_dir.mkdir()
    state_dir.mkdir()
    manifest_path = config_dir / "manifest.toml"
    save_manifest(manifest_with_sources, manifest_path)

    with (
        patch("popctl.backup.backup.get_config_dir", return_value=config_dir),
        patch("popctl.backup.backup.get_state_dir", return_value=state_dir),
        patch("popctl.backup.backup.Path.home", return_value=tmp_path),
    ):
        backup_files = collect_backup_files()

    assert (manifest_path, "files/popctl/manifest.toml") in backup_files
    assert load_manifest(manifest_path).sources == manifest_with_sources.sources
