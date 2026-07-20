from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from popctl.cli.types import SourceChoice
from popctl.models.manifest import Manifest, ManifestMeta, PackageConfig, SystemConfig
from popctl.models.package import PackageSource
from popctl.sources.capture import SourceCaptureError
from popctl.sources.diff import SourceDiffType
from popctl.sources.keytrust import KeyTrustError, VerifiedPublicKey
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
from popctl.sources.phase import (
    SourceInteractionPolicy,
    capture_and_trust_sources,
    refresh_manifest_sources,
    run_source_phase,
)
from popctl.sources.preflight import preflight_sources, selected_managers
from popctl.sources.provision import SourceProvisionResult

FINGERPRINT = "A" * 40
CHANGED_FINGERPRINT = "B" * 40


def _platform(codename: str = "noble") -> SourcePlatform:
    return SourcePlatform(distro_id="ubuntu", codename=codename)


def _manifest(sources: SourcesConfig | None) -> Manifest:
    now = datetime.now(UTC)
    return Manifest(
        meta=ManifestMeta(created=now, updated=now),
        system=SystemConfig(name="test-machine"),
        packages=PackageConfig(),
        sources=sources,
    )


def _apt_sources(
    *,
    fingerprint: str = FINGERPRINT,
    uri: str = "https://vendor.example/apt",
) -> SourcesConfig:
    key = AptKey(
        id="vendor",
        target_path="/etc/apt/keyrings/vendor.asc",
        armor="vendor-key",
        fingerprints=(fingerprint,),
    )
    source = AptSource(
        id="vendor",
        capture_path="/etc/apt/sources.list.d/popctl-vendor.sources",
        format=AptSourceFormat.DEB822,
        ordinal=0,
        managed_target="popctl-vendor",
        verbatim_stanza=(
            "Types: deb\n"
            f"URIs: {uri}\n"
            "Suites: stable\n"
            "Components: main\n"
            "Signed-By: /etc/apt/keyrings/vendor.asc\n"
        ),
        key_ids=("vendor",),
        signed_by=SignedByBinding(key_paths=("/etc/apt/keyrings/vendor.asc",)),
        replay_mode=ReplayMode.REPLAY,
    )
    return SourcesConfig(platform=_platform(), apt=AptSources(entries=(source,), keys=(key,)))


def _snap_sources(*channels: SnapChannel) -> SourcesConfig:
    return SourcesConfig(platform=_platform(), snap=SnapSources(packages=channels))


def _verified(fingerprint: str = FINGERPRINT) -> VerifiedPublicKey:
    return VerifiedPublicKey(armor="verified", fingerprints=(fingerprint,))


def _available(manager: PackageSource) -> list[MagicMock]:
    operator = MagicMock()
    operator.source = manager
    return [operator]


def test_selected_managers_only_requires_sources_in_the_selected_filter() -> None:
    sources = _apt_sources().model_copy(
        update={
            "flatpak": FlatpakSources(
                apps=(
                    FlatpakApp(
                        id="org.example.App",
                        origin="flathub",
                        scope=FlatpakScope.USER,
                        arch="x86_64",
                        branch="stable",
                    ),
                ),
            ),
            "snap": SnapSources(
                packages=(
                    SnapChannel(
                        name="hello",
                        channel="latest/stable",
                        replay_mode=ReplayMode.REPLAY,
                    ),
                )
            ),
        }
    )

    assert selected_managers(sources, PackageSource.APT) == (PackageSource.APT,)
    assert selected_managers(sources, PackageSource.FLATPAK) == (PackageSource.FLATPAK,)
    assert selected_managers(sources, PackageSource.SNAP) == (PackageSource.SNAP,)


def test_preflight_collects_all_selected_failures_before_any_write() -> None:
    sources = _apt_sources().model_copy(
        update={
            "flatpak": FlatpakSources(
                apps=(
                    FlatpakApp(
                        id="org.example.App",
                        origin="missing",
                        scope=FlatpakScope.USER,
                        arch="x86_64",
                        branch="stable",
                    ),
                ),
            ),
            "snap": SnapSources(
                packages=(SnapChannel(name="hello", channel="", replay_mode=ReplayMode.REPLAY),)
            ),
        }
    )

    with (
        patch("popctl.sources.preflight.get_available_operators", side_effect=_available),
        patch("popctl.sources.preflight.verify_public_material", return_value=_verified()),
    ):
        result = preflight_sources(
            sources,
            source_filter=None,
            target_platform=SourcePlatform(distro_id="debian", codename="bookworm"),
        )

    assert result.success is False
    failures = {check.subject for check in result.checks if not check.success}
    assert {"platform", "flatpak-app:user:org.example.App@stable", "snap:hello"} <= failures
    assert {"manager:apt", "manager:flatpak", "manager:snap"} <= {
        check.subject for check in result.checks
    }


def test_preflight_rejects_unverified_key_material() -> None:
    sources = _apt_sources()
    with (
        patch("popctl.sources.preflight.get_available_operators", side_effect=_available),
        patch(
            "popctl.sources.preflight.verify_public_material",
            side_effect=KeyTrustError("secret material"),
        ),
    ):
        result = preflight_sources(
            sources,
            source_filter=PackageSource.APT,
            target_platform=_platform(),
        )

    assert result.success is False
    assert "verified public material" in (result.error or "")


def test_source_phase_is_a_legacy_noop_without_sources() -> None:
    with patch("popctl.sources.phase.capture_sources") as capture:
        result = run_source_phase(
            _manifest(None),
            SourceChoice.ALL,
            dry_run=False,
            interaction=SourceInteractionPolicy(),
        )

    assert result.success is True
    assert result.selected_managers == ()
    capture.assert_not_called()


def test_source_phase_filters_to_snap_without_an_apt_command() -> None:
    sources = _snap_sources(
        SnapChannel(name="hello", channel="latest/edge", replay_mode=ReplayMode.REPLAY)
    )
    with (
        patch("popctl.sources.preflight.get_available_operators", side_effect=_available),
        patch("popctl.sources.phase.capture_platform", return_value=_platform()),
        patch("popctl.sources.phase.capture_sources", return_value=sources),
        patch("popctl.sources.phase.provision_sources") as provision,
    ):
        provision.return_value = SourceProvisionResult(success=True, retained_artifacts=())
        result = run_source_phase(
            _manifest(sources),
            SourceChoice.SNAP,
            dry_run=False,
            interaction=SourceInteractionPolicy(yes=True),
        )

    assert result.success is True
    assert provision.call_args.kwargs["selected_managers"] == (PackageSource.SNAP,)


def test_source_phase_fails_closed_for_changed_trust_with_yes() -> None:
    expected = _apt_sources()
    live = _apt_sources(fingerprint=CHANGED_FINGERPRINT)
    with (
        patch("popctl.sources.preflight.get_available_operators", side_effect=_available),
        patch("popctl.sources.preflight.verify_public_material", return_value=_verified()),
        patch("popctl.sources.phase.capture_platform", return_value=_platform()),
        patch("popctl.sources.phase.capture_sources", return_value=live),
        patch("popctl.sources.phase.provision_sources") as provision,
    ):
        result = run_source_phase(
            _manifest(expected),
            SourceChoice.APT,
            dry_run=False,
            interaction=SourceInteractionPolicy(yes=True),
        )

    assert result.success is False
    assert result.source_diff.changed[0].diff_type is SourceDiffType.CHANGED
    assert "--yes" in (result.error or "")
    provision.assert_not_called()


def test_source_phase_confirms_changed_managed_target_then_provisions() -> None:
    expected = _apt_sources()
    live = _apt_sources(fingerprint=CHANGED_FINGERPRINT)
    with (
        patch("popctl.sources.preflight.get_available_operators", side_effect=_available),
        patch("popctl.sources.preflight.verify_public_material", return_value=_verified()),
        patch("popctl.sources.phase.capture_platform", return_value=_platform()),
        patch("popctl.sources.phase.capture_sources", return_value=live),
        patch("popctl.sources.phase.typer.confirm", return_value=True),
        patch("popctl.sources.phase.provision_sources") as provision,
    ):
        provision.return_value = SourceProvisionResult(success=True, retained_artifacts=())
        result = run_source_phase(
            _manifest(expected),
            SourceChoice.APT,
            dry_run=False,
            interaction=SourceInteractionPolicy(interactive=True),
        )

    assert result.success is True
    change = provision.call_args.kwargs["changes"][0]
    assert change.operation_owned is True


def test_source_phase_blocks_incompatible_suite_before_provisioning() -> None:
    sources = _apt_sources()
    apt_source = sources.apt.entries[0].model_copy(
        update={
            "verbatim_stanza": sources.apt.entries[0].verbatim_stanza.replace("stable", "noble")
        }
    )
    sources = sources.model_copy(
        update={"apt": sources.apt.model_copy(update={"entries": (apt_source,)})}
    )
    with (
        patch("popctl.sources.preflight.get_available_operators", side_effect=_available),
        patch("popctl.sources.preflight.verify_public_material", return_value=_verified()),
        patch("popctl.sources.phase.capture_platform", return_value=_platform("oracular")),
        patch("popctl.sources.phase.capture_sources", return_value=sources),
        patch("popctl.sources.phase.provision_sources") as provision,
    ):
        result = run_source_phase(
            _manifest(sources),
            SourceChoice.APT,
            dry_run=False,
            interaction=SourceInteractionPolicy(yes=True),
        )

    assert result.success is False
    assert "incompatible" in (result.error or "")
    provision.assert_not_called()


def test_source_phase_stops_before_capture_when_manager_is_missing() -> None:
    sources = _snap_sources(
        SnapChannel(name="hello", channel="latest/edge", replay_mode=ReplayMode.REPLAY)
    )
    with (
        patch("popctl.sources.preflight.get_available_operators", return_value=[]),
        patch("popctl.sources.phase.capture_sources") as capture,
    ):
        result = run_source_phase(
            _manifest(sources),
            SourceChoice.SNAP,
            dry_run=False,
            interaction=SourceInteractionPolicy(),
        )

    assert result.success is False
    assert "unavailable" in (result.error or "")
    capture.assert_not_called()


def test_source_phase_dry_run_previews_without_confirmation_or_provisioning() -> None:
    expected = _apt_sources()
    live = SourcesConfig(platform=_platform())
    with (
        patch("popctl.sources.preflight.get_available_operators", side_effect=_available),
        patch("popctl.sources.preflight.verify_public_material", return_value=_verified()),
        patch("popctl.sources.phase.capture_platform", return_value=_platform()),
        patch("popctl.sources.phase.capture_sources", return_value=live),
        patch("popctl.sources.phase.typer.confirm") as confirm,
        patch("popctl.sources.phase.provision_sources") as provision,
    ):
        result = run_source_phase(
            _manifest(expected),
            SourceChoice.APT,
            dry_run=True,
            interaction=SourceInteractionPolicy(),
        )

    assert result.success is True
    assert len(result.source_diff.missing) == 1
    confirm.assert_not_called()
    provision.assert_not_called()


def test_source_phase_preview_shows_fingerprints_and_exact_apt_commands() -> None:
    expected = _apt_sources()
    live = SourcesConfig(platform=_platform())
    lines: list[str] = []
    with (
        patch("popctl.sources.preflight.get_available_operators", side_effect=_available),
        patch("popctl.sources.preflight.verify_public_material", return_value=_verified()),
        patch("popctl.sources.phase.capture_platform", return_value=_platform()),
        patch("popctl.sources.phase.capture_sources", return_value=live),
        patch("popctl.sources.phase.print_info", side_effect=lines.append),
    ):
        result = run_source_phase(
            _manifest(expected),
            SourceChoice.APT,
            dry_run=True,
            interaction=SourceInteractionPolicy(),
        )

    assert result.success is True
    assert any("fingerprints:" in line and FINGERPRINT in line for line in lines)
    assert any("sudo install -o root -g root -m 0644 <public-key>" in line for line in lines)
    assert "  command: sudo apt-get update --error-on=any" in lines


def test_source_phase_keeps_provision_failure_and_retained_artifacts() -> None:
    expected = _apt_sources()
    live = SourcesConfig(platform=_platform())
    with (
        patch("popctl.sources.preflight.get_available_operators", side_effect=_available),
        patch("popctl.sources.preflight.verify_public_material", return_value=_verified()),
        patch("popctl.sources.phase.capture_platform", return_value=_platform()),
        patch("popctl.sources.phase.capture_sources", return_value=live),
        patch("popctl.sources.phase.provision_sources") as provision,
    ):
        provision.return_value = SourceProvisionResult(
            success=False,
            retained_artifacts=("/etc/apt/keyrings/vendor.asc",),
            error="strict update failed",
        )
        result = run_source_phase(
            _manifest(expected),
            SourceChoice.APT,
            dry_run=False,
            interaction=SourceInteractionPolicy(yes=True),
        )

    assert result.success is False
    assert result.retained_artifacts == ("/etc/apt/keyrings/vendor.asc",)
    assert result.error == "strict update failed"


def test_capture_and_trust_filters_bootstrap_and_shows_fingerprint_before_prompt() -> None:
    sources = _apt_sources().model_copy(
        update={
            "flatpak": FlatpakSources(
                remotes=(
                    FlatpakRemote(
                        name="vendor",
                        scope=FlatpakScope.USER,
                        url="https://vendor.example/flatpak",
                        gpg_verify=True,
                        gpg_key_armor="vendor-key",
                        gpg_fingerprints=(FINGERPRINT,),
                        replay_mode=ReplayMode.REPLAY,
                    ),
                ),
            ),
            "snap": _snap_sources(
                SnapChannel(name="hello", channel="latest/edge", replay_mode=ReplayMode.REPLAY)
            ).snap,
        }
    )
    events: list[str] = []

    def confirm_source(*_args: object, **_kwargs: object) -> bool:
        events.append("confirm")
        return True

    with (
        patch("popctl.sources.phase.capture_sources", return_value=sources) as capture,
        patch("popctl.sources.phase.print_info", side_effect=events.append),
        patch("popctl.sources.phase.typer.confirm", side_effect=confirm_source) as confirm,
    ):
        result = capture_and_trust_sources(
            SourceChoice.APT,
            dry_run=False,
            interaction=SourceInteractionPolicy(interactive=True),
    )

    assert result.success is True
    assert result.sources is not None
    assert result.sources.apt == sources.apt
    assert result.sources.flatpak == FlatpakSources()
    assert result.sources.snap == SnapSources()
    assert capture.call_args.kwargs["managers"] == (PackageSource.APT,)
    assert confirm.call_count == 1
    assert any("Third-party APT source: https://vendor.example/apt" in event for event in events)
    assert any(FINGERPRINT in event for event in events)
    assert events.index("confirm") > next(
        index for index, event in enumerate(events) if FINGERPRINT in event
    )


def test_capture_and_trust_rejects_blocked_sources_before_persisting() -> None:
    sources = _apt_sources()
    blocked = sources.apt.entries[0].model_copy(update={"replay_mode": ReplayMode.BLOCKED})
    sources = sources.model_copy(
        update={"apt": sources.apt.model_copy(update={"entries": (blocked,)})}
    )

    with (
        patch("popctl.sources.phase.capture_sources", return_value=sources),
        patch("popctl.sources.phase.typer.confirm") as confirm,
    ):
        result = capture_and_trust_sources(
            SourceChoice.APT,
            dry_run=False,
            interaction=SourceInteractionPolicy(interactive=True),
        )

    assert result.success is False
    assert result.sources is None
    assert "blocked" in (result.error or "")
    confirm.assert_not_called()


def test_capture_and_trust_refuses_rejected_authenticated_and_noninteractive_sources() -> None:
    sources = _apt_sources()
    with (
        patch("popctl.sources.phase.capture_sources", return_value=sources),
        patch("popctl.sources.phase.typer.confirm", return_value=False) as confirm,
    ):
        rejected = capture_and_trust_sources(
            SourceChoice.APT,
            dry_run=False,
            interaction=SourceInteractionPolicy(interactive=True),
        )

    with (
        patch("popctl.sources.phase.capture_sources", return_value=sources),
        patch("popctl.sources.phase.typer.confirm") as noninteractive_confirm,
    ):
        noninteractive = capture_and_trust_sources(
            SourceChoice.APT,
            dry_run=False,
            interaction=SourceInteractionPolicy(interactive=False),
        )

    with patch(
        "popctl.sources.phase.capture_sources",
        side_effect=SourceCaptureError("authenticated source"),
    ):
        authenticated = capture_and_trust_sources(
            SourceChoice.APT,
            dry_run=False,
            interaction=SourceInteractionPolicy(interactive=True),
        )

    assert rejected.success is False
    assert rejected.sources is None
    assert noninteractive.success is False
    assert noninteractive.sources is None
    assert authenticated.success is False
    assert authenticated.sources is None
    confirm.assert_called_once()
    noninteractive_confirm.assert_not_called()


def test_refresh_merges_only_confirmed_additions_and_never_removes_extras() -> None:
    old = SnapChannel(name="old", channel="latest/stable", replay_mode=ReplayMode.REPLAY)
    added = SnapChannel(name="added", channel="latest/edge", replay_mode=ReplayMode.REPLAY)
    manifest = _manifest(_snap_sources(old))
    live = _snap_sources(added)
    with (
        patch("popctl.sources.phase.capture_sources", return_value=live),
        patch("popctl.sources.phase.typer.confirm", return_value=True),
    ):
        result = refresh_manifest_sources(
            manifest,
            SourceChoice.SNAP,
            interaction=SourceInteractionPolicy(interactive=True),
        )

    assert result.success is True
    assert result.changed is True
    assert result.manifest.sources is not None
    assert {channel.name for channel in result.manifest.sources.snap.packages} == {"old", "added"}


def test_refresh_yes_refuses_a_new_source_without_mutating_manifest() -> None:
    manifest = _manifest(_snap_sources())
    live = _snap_sources(
        SnapChannel(name="added", channel="latest/edge", replay_mode=ReplayMode.REPLAY)
    )
    with patch("popctl.sources.phase.capture_sources", return_value=live):
        result = refresh_manifest_sources(
            manifest,
            SourceChoice.SNAP,
            interaction=SourceInteractionPolicy(yes=True),
        )

    assert result.success is False
    assert result.manifest is manifest
    assert result.changed is False


def test_refresh_atomically_merges_only_the_individually_confirmed_sources() -> None:
    existing = SnapChannel(name="existing", channel="latest/stable", replay_mode=ReplayMode.REPLAY)
    changed = SnapChannel(name="existing", channel="latest/edge", replay_mode=ReplayMode.REPLAY)
    added = SnapChannel(name="added", channel="latest/stable", replay_mode=ReplayMode.REPLAY)
    manifest = _manifest(_snap_sources(existing))
    live = _snap_sources(changed, added)
    with (
        patch("popctl.sources.phase.capture_sources", return_value=live),
        patch("popctl.sources.phase.typer.confirm", side_effect=(False, True)) as confirm,
    ):
        result = refresh_manifest_sources(
            manifest,
            SourceChoice.SNAP,
            interaction=SourceInteractionPolicy(interactive=True),
        )

    assert result.success is True
    assert result.changed is True
    assert result.manifest.sources is not None
    assert result.manifest.sources.snap.packages == (changed,)
    assert confirm.call_count == 2


def test_refresh_noninteractive_refuses_a_new_source_without_mutating_manifest() -> None:
    manifest = _manifest(_snap_sources())
    live = _snap_sources(
        SnapChannel(name="added", channel="latest/edge", replay_mode=ReplayMode.REPLAY)
    )
    with patch("popctl.sources.phase.capture_sources", return_value=live):
        result = refresh_manifest_sources(
            manifest,
            SourceChoice.SNAP,
            interaction=SourceInteractionPolicy(interactive=False),
        )

    assert result.success is False
    assert result.manifest is manifest
    assert result.changed is False
