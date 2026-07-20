from pathlib import Path
from unittest.mock import patch

import pytest
from popctl.models.package import PackageSource
from popctl.sources.capture import capture_apt_sources
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
from popctl.sources.provision import (
    ProvisioningPaths,
    SourceProvisionChange,
    SourceProvisionStatus,
    provision_sources,
)
from popctl.utils.shell import CommandResult, run_command
from pydantic import ValidationError

FINGERPRINT = "A" * 40
CHANGED_FINGERPRINT = "B" * 40
KEYTRUST_FIXTURE = (
    Path(__file__).parents[2] / "fixtures" / "sources" / "keytrust-multi-key-public.asc"
)


def _dearmor_key_fixture(tmp_path: Path) -> bytes:
    binary_path = tmp_path / "fixture.gpg"
    result = run_command(
        [
            "gpg",
            "--batch",
            "--yes",
            "--dearmor",
            "--output",
            str(binary_path),
            str(KEYTRUST_FIXTURE),
        ]
    )

    assert result.success, result.stderr
    return binary_path.read_bytes()


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


def test_capture_provision_rescan_has_no_apt_drift_for_legacy_deb822_and_primary_inputs(
    tmp_path: Path, real_gpg: None
) -> None:
    apt_root = tmp_path / "etc" / "apt"
    keyrings = apt_root / "keyrings"
    source_directory = apt_root / "sources.list.d"
    keyrings.mkdir(parents=True)
    source_directory.mkdir()
    source_key = keyrings / "fixture.gpg"
    source_key.write_bytes(_dearmor_key_fixture(tmp_path))
    apt_root.joinpath("sources.list").write_text(
        "deb [signed-by="
        f"{source_key}] https://archive.ubuntu.com/ubuntu noble main\n",
        encoding="utf-8",
    )
    legacy_path = source_directory / "vendor.list"
    legacy_path.write_text(
        f"deb [signed-by={source_key}] https://legacy.vendor.example/apt stable main\n",
        encoding="utf-8",
    )
    deb822_path = source_directory / "vendor.sources"
    deb822_path.write_text(
        "Types: deb\n"
        "URIs: https://deb822.vendor.example/apt\n"
        "Suites: stable\n"
        "Components: main\n"
        f"Signed-By: {source_key}\n",
        encoding="utf-8",
    )
    platform = SourcePlatform(distro_id="ubuntu", codename="noble")
    policy = CommandResult(
        stdout=(
            " 500 https://archive.ubuntu.com/ubuntu noble/main amd64 Packages\n"
            "     release o=Ubuntu,a=noble\n"
        ),
        stderr="",
        returncode=0,
    )
    paths = ProvisioningPaths(apt_keyrings_dir=keyrings, apt_sources_dir=source_directory)

    def command_recorder(args: list[str], *, timeout: float | None = None) -> CommandResult:
        if args[:3] == ["sudo", "install", "-d"]:
            Path(args[-1]).mkdir(parents=True, exist_ok=True)
            return CommandResult(stdout="", stderr="", returncode=0)
        if args[:2] == ["sudo", "install"]:
            target = Path(args[-1])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(Path(args[-2]).read_bytes())
            return CommandResult(stdout="", stderr="", returncode=0)
        if args[:2] == ["sudo", "cat"]:
            return CommandResult(
                stdout=Path(args[-1]).read_text(encoding="utf-8"), stderr="", returncode=0
            )
        if args == ["sudo", "apt-get", "update", "--error-on=any"]:
            return CommandResult(stdout="", stderr="", returncode=0)
        raise AssertionError(args)

    with patch("popctl.sources.capture.run_command", return_value=policy):
        captured = capture_apt_sources(apt_root, platform)

    expected = SourcesConfig(
        platform=platform,
        apt=captured.model_copy(
            update={
                "keys": tuple(
                    key.model_copy(
                        update={"target_path": str(keyrings / Path(key.target_path).name)}
                    )
                    for key in captured.keys
                )
            }
        ),
    )
    replay_entries = tuple(
        entry for entry in expected.apt.entries if entry.replay_mode is ReplayMode.REPLAY
    )
    report_only_entry = next(
        entry for entry in expected.apt.entries if entry.replay_mode is ReplayMode.REPORT_ONLY
    )

    legacy_path.unlink()
    deb822_path.unlink()
    with patch("popctl.sources.provision.run_command", side_effect=command_recorder):
        provisioned = provision_sources(
            expected,
            changes=tuple(
                SourceProvisionChange(
                    locator=entry.managed_target_locator,
                    status=SourceProvisionStatus.MISSING,
                )
                for entry in replay_entries
            ),
            selected_managers=(PackageSource.APT,),
            paths=paths,
        )

    assert provisioned.success is True
    with patch("popctl.sources.capture.run_command", return_value=policy):
        rescanned = SourcesConfig(platform=platform, apt=capture_apt_sources(apt_root, platform))

    assert compute_source_diff(expected, rescanned).is_in_sync
    assert {entry.managed_target_locator for entry in expected.apt.entries} == {
        entry.managed_target_locator for entry in rescanned.apt.entries
    }
    assert f"signed-by={expected.apt.keys[0].target_path}" in (
        source_directory / f"{replay_entries[0].managed_target}.list"
    ).read_text(encoding="utf-8")
    assert f"Signed-By: {expected.apt.keys[0].target_path}" in (
        source_directory / f"{replay_entries[1].managed_target}.sources"
    ).read_text(encoding="utf-8")
    assert not (source_directory / f"{report_only_entry.managed_target}.list").exists()


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
