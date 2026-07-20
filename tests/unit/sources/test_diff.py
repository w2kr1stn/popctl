from unittest.mock import patch

import pytest
from popctl.models.package import PackageSource
from popctl.sources.diff import SourceDiffType, compute_source_diff
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
    SourcePlatform,
    SourcesConfig,
)
from pydantic import ValidationError

FINGERPRINT = "A" * 40
CHANGED_FINGERPRINT = "B" * 40


def _apt_key(identifier: str = "vendor", fingerprint: str = FINGERPRINT) -> AptKey:
    return AptKey(
        id=identifier,
        target_path=f"/etc/apt/keyrings/{identifier}.asc",
        armor="-----BEGIN PGP PUBLIC KEY BLOCK-----\nkey\n",
        fingerprints=(fingerprint,),
    )


def _apt_source(
    key: AptKey,
    *,
    identifier: str = "vendor",
    capture_path: str = "/etc/apt/sources.list.d/vendor.list",
    ordinal: int = 0,
    managed_target: str = "popctl-vendor",
    source_format: AptSourceFormat = AptSourceFormat.LEGACY,
    suite: str = "stable",
    uri: str = "https://vendor.example/apt",
    replay_mode: ReplayMode = ReplayMode.REPLAY,
) -> AptSource:
    if source_format is AptSourceFormat.LEGACY:
        stanza = f"deb [signed-by=/etc/apt/keyrings/{identifier}.gpg] {uri} {suite} main\n"
    else:
        stanza = (
            "Types: deb\n"
            f"URIs: {uri}\n"
            f"Suites: {suite}\n"
            "Components: main\n"
            f"Signed-By: /etc/apt/keyrings/{identifier}.gpg\n"
        )
    return AptSource(
        id=identifier,
        capture_path=capture_path,
        format=source_format,
        ordinal=ordinal,
        managed_target=managed_target,
        verbatim_stanza=stanza,
        key_ids=(key.id,),
        signed_by=SignedByBinding(key_paths=(f"/etc/apt/keyrings/{identifier}.gpg",)),
        replay_mode=replay_mode,
    )


def _remote(
    *,
    url: str = "https://vendor.example/repo.flatpakrepo",
    fingerprint: str = FINGERPRINT,
) -> FlatpakRemote:
    return FlatpakRemote(
        name="vendor",
        scope=FlatpakScope.USER,
        url=url,
        gpg_verify=True,
        gpg_key_armor="-----BEGIN PGP PUBLIC KEY BLOCK-----\nkey\n",
        gpg_fingerprints=(fingerprint,),
        replay_mode=ReplayMode.REPLAY,
    )


def _app(*, branch: str = "stable", origin: str = "vendor") -> FlatpakApp:
    return FlatpakApp(
        id="org.example.App",
        origin=origin,
        scope=FlatpakScope.USER,
        arch="x86_64",
        branch=branch,
    )


def _snap(channel: str = "latest/stable") -> SnapChannel:
    return SnapChannel(name="vendor", channel=channel, replay_mode=ReplayMode.REPLAY)


def _sources(
    *,
    apt_entries: tuple[AptSource, ...] = (),
    apt_keys: tuple[AptKey, ...] = (),
    remotes: tuple[FlatpakRemote, ...] = (),
    apps: tuple[FlatpakApp, ...] = (),
    snaps: tuple[SnapChannel, ...] = (),
) -> SourcesConfig:
    return SourcesConfig(
        platform=SourcePlatform(distro_id="ubuntu", codename="noble"),
        apt=AptSources(entries=apt_entries, keys=apt_keys),
        flatpak=FlatpakSources(remotes=remotes, apps=apps),
        snap=SnapSources(packages=snaps),
    )


@pytest.mark.parametrize("state", ["missing", "extra", "changed"])
def test_apt_diff_states_are_keyed_by_managed_target(state: str) -> None:
    key = _apt_key()
    source = _apt_source(key)
    expected = _sources(apt_entries=(source,), apt_keys=(key,))
    live = _sources(apt_entries=(source,), apt_keys=(key,))
    if state == "missing":
        live = _sources()
    elif state == "extra":
        expected = _sources()
    else:
        live = _sources(
            apt_entries=(_apt_source(key, uri="https://changed.example/apt"),),
            apt_keys=(key,),
        )

    result = compute_source_diff(expected, live)

    entries = getattr(result, state)
    assert len(entries) == 1
    assert entries[0].locator == source.managed_target_locator
    assert entries[0].diff_type.value == state


@pytest.mark.parametrize("state", ["missing", "extra", "changed"])
def test_flatpak_remote_diff_states(state: str) -> None:
    remote = _remote()
    expected = _sources(remotes=(remote,))
    live = _sources(remotes=(remote,))
    if state == "missing":
        live = _sources()
    elif state == "extra":
        expected = _sources()
    else:
        live = _sources(remotes=(_remote(url="https://changed.example/repo"),))

    result = compute_source_diff(expected, live)

    assert len(getattr(result, state)) == 1
    assert getattr(result, state)[0].locator == remote.locator


@pytest.mark.parametrize("state", ["missing", "extra", "changed"])
def test_snap_diff_states(state: str) -> None:
    channel = _snap()
    expected = _sources(snaps=(channel,))
    live = _sources(snaps=(channel,))
    if state == "missing":
        live = _sources()
    elif state == "extra":
        expected = _sources()
    else:
        live = _sources(snaps=(_snap("latest/edge"),))

    result = compute_source_diff(expected, live)

    assert len(getattr(result, state)) == 1
    assert getattr(result, state)[0].locator == channel.locator


@pytest.mark.parametrize("change", ["uri", "suite", "key"])
def test_apt_attribute_changes_are_not_reclassified_as_missing_and_extra(change: str) -> None:
    key = _apt_key()
    source = _apt_source(key)
    expected = _sources(apt_entries=(source,), apt_keys=(key,))
    if change == "uri":
        live = _sources(
            apt_entries=(_apt_source(key, uri="https://changed.example/apt"),), apt_keys=(key,)
        )
    elif change == "suite":
        live = _sources(apt_entries=(_apt_source(key, suite="edge"),), apt_keys=(key,))
    else:
        changed_key = _apt_key(fingerprint=CHANGED_FINGERPRINT)
        live = _sources(apt_entries=(_apt_source(changed_key),), apt_keys=(changed_key,))

    result = compute_source_diff(expected, live)

    assert result.missing == ()
    assert result.extra == ()
    assert result.changed[0].locator == source.managed_target_locator


def test_restore_rescan_has_no_apt_drift_for_legacy_deb822_and_primary_inputs() -> None:
    legacy_key = _apt_key("legacy")
    deb822_key = _apt_key("deb822")
    primary_key = _apt_key("primary")
    legacy = _apt_source(
        legacy_key,
        identifier="legacy",
        capture_path="/etc/apt/sources.list",
        ordinal=3,
        managed_target="popctl-legacy",
    )
    deb822 = _apt_source(
        deb822_key,
        identifier="deb822",
        capture_path="/etc/apt/sources.list.d/vendor.sources",
        ordinal=2,
        managed_target="popctl-deb822",
        source_format=AptSourceFormat.DEB822,
    )
    primary = _apt_source(
        primary_key,
        identifier="primary",
        capture_path="/etc/apt/sources.list",
        ordinal=4,
        managed_target="popctl-primary",
        replay_mode=ReplayMode.REPORT_ONLY,
    )
    expected = _sources(
        apt_entries=(legacy, deb822, primary),
        apt_keys=(legacy_key, deb822_key, primary_key),
    )

    def rescan(source: AptSource, key: AptKey) -> tuple[AptSource, AptKey]:
        installed_key = _apt_key(f"rescanned-{key.id}")
        installed_stanza = source.verbatim_stanza.replace(
            f"/etc/apt/keyrings/{source.id}.gpg", installed_key.target_path
        )
        return (
            source.model_copy(
                update={
                    "id": f"rescanned-{source.id}",
                    "capture_path": f"/etc/apt/sources.list.d/{source.managed_target}.list",
                    "ordinal": 0,
                    "verbatim_stanza": installed_stanza,
                    "key_ids": (installed_key.id,),
                    "signed_by": SignedByBinding(key_paths=(installed_key.target_path,)),
                }
            ),
            installed_key,
        )

    rescanned = tuple(
        rescan(source, key)
        for source, key in zip(expected.apt.entries[:2], expected.apt.keys[:2], strict=True)
    )
    live = _sources(
        apt_entries=tuple(source for source, _ in rescanned) + (primary,),
        apt_keys=tuple(key for _, key in rescanned) + (primary_key,),
    )

    assert compute_source_diff(expected, live).is_in_sync


def test_manager_discriminated_locators_do_not_collide() -> None:
    key = _apt_key()
    apt = _apt_source(key, managed_target="shared")
    snap = SnapChannel(name="shared", channel="latest/stable", replay_mode=ReplayMode.REPLAY)

    result = compute_source_diff(
        _sources(apt_entries=(apt,), apt_keys=(key,)),
        _sources(snaps=(snap,)),
    )

    assert result.changed == ()
    assert result.missing[0].locator.manager is PackageSource.APT
    assert result.extra[0].locator.manager is PackageSource.SNAP


def test_duplicate_apt_locator_fails_at_model_validation() -> None:
    key = _apt_key()
    first = _apt_source(key)
    second = _apt_source(key, identifier="second")

    with pytest.raises(ValidationError, match="Duplicate source locator"):
        _sources(apt_entries=(first, second), apt_keys=(key,))


def test_extra_sources_are_report_only() -> None:
    result = compute_source_diff(_sources(), _sources(snaps=(_snap(),)))

    assert result.extra[0].diff_type is SourceDiffType.EXTRA


def test_flatpak_apps_keep_same_scope_id_arch_distinct_by_branch() -> None:
    stable = _app(branch="stable")
    beta = _app(branch="beta")
    expected = _sources(apps=(stable, beta))
    live = _sources(apps=(stable, _app(branch="beta", origin="vendor-beta")))

    result = compute_source_diff(expected, live)

    assert result.missing == ()
    assert result.extra == ()
    assert result.changed[0].locator == beta.locator


def test_apt_unrecorded_source_diagnostics_report_mapped_and_unknown_provenance() -> None:
    key = _apt_key()
    source = _apt_source(key)
    live = _sources(apt_entries=(source,), apt_keys=(key,))

    with patch(
        "popctl.sources.diff.resolve_apt_candidate_origins",
        return_value={"mapped": source.capture_locator, "unknown": "unknown"},
    ):
        result = compute_source_diff(
            _sources(),
            live,
            apt_package_names=("mapped", "unknown"),
        )

    assert result.unrecorded_apt_packages[0].locator == source.capture_locator
    assert result.unrecorded_apt_packages[0].provenance == "unrecorded"
    assert result.unrecorded_apt_packages[1].locator is None
    assert result.unrecorded_apt_packages[1].provenance == "unknown"
