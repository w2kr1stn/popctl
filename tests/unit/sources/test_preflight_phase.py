from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from popctl.core.diff import DiffEntry, DiffResult, DiffType, diff_to_actions
from popctl.models.manifest import Manifest, ManifestMeta, PackageConfig, SystemConfig
from popctl.models.package import PackageSource, SourceChoice
from popctl.sources.capture import SourceCaptureError
from popctl.sources.diff import SourceDiffError, SourceDiffType
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
from popctl.sources.preflight import (
    RECOGNIZED_STABLE_VENDOR_URIS,
    preflight_sources,
    selected_managers,
)
from popctl.sources.provision import SourceProvisionResult, render_managed_apt_stanza
from popctl.utils.shell import CommandResult

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


def _flatpak_remote_and_app() -> tuple[FlatpakRemote, FlatpakApp]:
    remote = FlatpakRemote(
        name="vendor",
        scope=FlatpakScope.USER,
        url="https://vendor.example/repo.flatpakrepo",
        gpg_verify=True,
        gpg_key_armor="vendor-key",
        gpg_fingerprints=(FINGERPRINT,),
        replay_mode=ReplayMode.REPLAY,
    )
    app = FlatpakApp(
        id="org.example.App",
        origin="vendor",
        scope=FlatpakScope.USER,
        arch="x86_64",
        branch="beta",
    )
    return remote, app


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


def test_report_only_apt_records_do_not_select_a_manager_or_preflight() -> None:
    sources = _apt_sources()
    report_only = sources.apt.entries[0].model_copy(
        update={
            "replay_mode": ReplayMode.REPORT_ONLY,
            "verbatim_stanza": sources.apt.entries[0].verbatim_stanza.replace("stable", "jammy"),
        }
    )
    sources = sources.model_copy(
        update={
            "platform": _platform("jammy"),
            "apt": sources.apt.model_copy(update={"entries": (report_only,)}),
        }
    )

    result = preflight_sources(
        sources,
        source_filter=PackageSource.APT,
        target_platform=_platform("oracular"),
    )

    assert selected_managers(sources, PackageSource.APT) == ()
    assert result.success is True
    assert result.checks == ()


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


def test_preflight_uses_capture_parser_for_multi_option_legacy_stanza() -> None:
    sources = _apt_sources()
    source = sources.apt.entries[0].model_copy(
        update={
            "format": AptSourceFormat.LEGACY,
            "verbatim_stanza": (
                "deb [arch=amd64 signed-by=/etc/apt/keyrings/vendor.asc] "
                "https://vendor.example/apt noble main\n"
            ),
        }
    )
    sources = sources.model_copy(
        update={"apt": sources.apt.model_copy(update={"entries": (source,)})}
    )

    with (
        patch("popctl.sources.preflight.get_available_operators", side_effect=_available),
        patch("popctl.sources.preflight.verify_public_material", return_value=_verified()),
    ):
        result = preflight_sources(
            sources,
            source_filter=PackageSource.APT,
            target_platform=_platform(),
        )

    assert result.success is True


def test_preflight_rejects_allow_downgrade_to_insecure_deb822_source() -> None:
    sources = _apt_sources()
    source = sources.apt.entries[0].model_copy(
        update={
            "verbatim_stanza": (
                f"{sources.apt.entries[0].verbatim_stanza}Allow-Downgrade-To-Insecure: yes\n"
            )
        }
    )
    sources = sources.model_copy(
        update={"apt": sources.apt.model_copy(update={"entries": (source,)})}
    )

    with patch("popctl.sources.preflight.get_available_operators", side_effect=_available):
        result = preflight_sources(
            sources,
            source_filter=PackageSource.APT,
            target_platform=_platform(),
        )

    assert result.success is False
    assert "insecure" in (result.error or "")


def test_preflight_rejects_unrecognized_stable_vendor_across_codenames() -> None:
    sources = _apt_sources(uri="https://unrecognized.example/apt")

    with (
        patch("popctl.sources.preflight.get_available_operators", side_effect=_available),
        patch("popctl.sources.preflight.verify_public_material", return_value=_verified()),
    ):
        result = preflight_sources(
            sources,
            source_filter=PackageSource.APT,
            target_platform=_platform("oracular"),
        )

    assert result.success is False
    assert "incompatible" in (result.error or "")


def test_preflight_allows_recognized_stable_vendor_across_codenames() -> None:
    uri = next(iter(RECOGNIZED_STABLE_VENDOR_URIS))
    sources = _apt_sources(uri=uri)

    with (
        patch("popctl.sources.preflight.get_available_operators", side_effect=_available),
        patch("popctl.sources.preflight.verify_public_material", return_value=_verified()),
    ):
        result = preflight_sources(
            sources,
            source_filter=PackageSource.APT,
            target_platform=_platform("oracular"),
        )

    assert result.success is True


def test_source_phase_is_a_legacy_noop_without_sources() -> None:
    with patch("popctl.sources.phase.capture_sources") as capture:
        result = run_source_phase(
            _manifest(None),
            SourceChoice.ALL,
            dry_run=False,
            interaction=SourceInteractionPolicy(),
        )

    assert result.success is True
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


def test_source_phase_report_only_base_drift_skips_source_preflight_and_writes() -> None:
    expected = _apt_sources(uri="https://archive.ubuntu.com/ubuntu")
    source = expected.apt.entries[0].model_copy(
        update={
            "verbatim_stanza": expected.apt.entries[0].verbatim_stanza.replace("stable", "noble"),
            "replay_mode": ReplayMode.REPORT_ONLY,
        }
    )
    expected = expected.model_copy(
        update={"apt": expected.apt.model_copy(update={"entries": (source,)})}
    )
    live_source = source.model_copy(
        update={"verbatim_stanza": source.verbatim_stanza.replace("https://", "http://")}
    )
    live = expected.model_copy(
        update={"apt": expected.apt.model_copy(update={"entries": (live_source,)})}
    )
    lines: list[str] = []

    with (
        patch("popctl.sources.preflight.get_available_operators", side_effect=_available),
        patch("popctl.sources.preflight.verify_public_material", return_value=_verified()),
        patch("popctl.sources.phase.capture_platform", return_value=_platform()),
        patch("popctl.sources.phase.capture_sources", return_value=live) as capture,
        patch("popctl.sources.phase.print_info", side_effect=lines.append),
        patch("popctl.sources.phase.typer.confirm") as confirm,
        patch("popctl.sources.phase.provision_sources") as provision,
    ):
        provision.return_value = SourceProvisionResult(success=True, retained_artifacts=())
        result = run_source_phase(
            _manifest(expected),
            SourceChoice.APT,
            dry_run=False,
            interaction=SourceInteractionPolicy(yes=True),
        )

    assert result.success is True
    assert result.source_diff.changed == ()
    assert lines == []
    capture.assert_not_called()
    confirm.assert_not_called()
    provision.assert_not_called()


def test_source_phase_returns_structured_failure_for_residual_diff_error() -> None:
    sources = _snap_sources(
        SnapChannel(name="hello", channel="latest/edge", replay_mode=ReplayMode.REPLAY)
    )
    with (
        patch("popctl.sources.preflight.get_available_operators", side_effect=_available),
        patch("popctl.sources.phase.capture_platform", return_value=_platform()),
        patch("popctl.sources.phase.capture_sources", return_value=sources),
        patch(
            "popctl.sources.phase.compute_source_diff",
            side_effect=SourceDiffError("Duplicate source locator: injected"),
        ),
        patch("popctl.sources.phase.provision_sources") as provision,
    ):
        result = run_source_phase(
            _manifest(sources),
            SourceChoice.SNAP,
            dry_run=False,
            interaction=SourceInteractionPolicy(yes=True),
        )

    assert result.success is False
    assert result.error == "Duplicate source locator: injected"
    provision.assert_not_called()


def test_source_phase_confirms_changed_managed_target_then_provisions() -> None:
    expected = _apt_sources()
    live = _apt_sources(fingerprint=CHANGED_FINGERPRINT)
    live_entry = live.apt.entries[0].model_copy(
        update={
            "verbatim_stanza": render_managed_apt_stanza(live.apt.entries[0], live.apt.keys)
        }
    )
    live = live.model_copy(
        update={
            "apt": live.apt.model_copy(update={"entries": (live_entry,)})
        }
    )
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


def test_source_phase_refuses_changed_popctl_named_unmanaged_target_without_commands() -> None:
    expected = _apt_sources()
    live = _apt_sources(fingerprint=CHANGED_FINGERPRINT)
    source_commands: list[list[str]] = []

    def record_source_command(
        args: list[str], *, timeout: float | None = None
    ) -> CommandResult:
        source_commands.append(args)
        return CommandResult(stdout="", stderr="", returncode=0)

    with (
        patch("popctl.sources.preflight.get_available_operators", side_effect=_available),
        patch("popctl.sources.preflight.verify_public_material", return_value=_verified()),
        patch("popctl.sources.phase.capture_platform", return_value=_platform()),
        patch("popctl.sources.phase.capture_sources", return_value=live),
        patch("popctl.sources.phase.typer.confirm", return_value=True),
        patch("popctl.sources.provision.run_command", side_effect=record_source_command),
    ):
        result = run_source_phase(
            _manifest(expected),
            SourceChoice.APT,
            dry_run=False,
            interaction=SourceInteractionPolicy(interactive=True),
        )

    assert result.success is False
    assert result.error == "Changed source conflicts with an unmanaged target"
    assert source_commands == []


def test_source_phase_confirms_each_missing_replay_source_before_provisioning() -> None:
    expected = _apt_sources()
    live = SourcesConfig(platform=_platform())
    with (
        patch("popctl.sources.preflight.get_available_operators", side_effect=_available),
        patch("popctl.sources.preflight.verify_public_material", return_value=_verified()),
        patch("popctl.sources.phase.capture_platform", return_value=_platform()),
        patch("popctl.sources.phase.capture_sources", return_value=live),
        patch("popctl.sources.phase.typer.confirm", return_value=True) as confirm,
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
    confirm.assert_called_once()
    assert provision.call_args.kwargs["changes"][0].status.value == "missing"


def test_source_phase_declines_missing_replay_source_without_provisioning() -> None:
    expected = _apt_sources()
    live = SourcesConfig(platform=_platform())
    with (
        patch("popctl.sources.preflight.get_available_operators", side_effect=_available),
        patch("popctl.sources.preflight.verify_public_material", return_value=_verified()),
        patch("popctl.sources.phase.capture_platform", return_value=_platform()),
        patch("popctl.sources.phase.capture_sources", return_value=live),
        patch("popctl.sources.phase.typer.confirm", return_value=False),
        patch("popctl.sources.phase.provision_sources") as provision,
    ):
        result = run_source_phase(
            _manifest(expected),
            SourceChoice.APT,
            dry_run=False,
            interaction=SourceInteractionPolicy(interactive=True),
        )

    assert result.success is False
    assert result.error == "source trust confirmation was declined"
    provision.assert_not_called()


def test_source_phase_yes_provisions_recorded_missing_source_without_confirmation() -> None:
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
        provision.return_value = SourceProvisionResult(success=True, retained_artifacts=())
        result = run_source_phase(
            _manifest(expected),
            SourceChoice.APT,
            dry_run=False,
            interaction=SourceInteractionPolicy(yes=True, interactive=False),
        )

    assert result.success is True
    confirm.assert_not_called()
    assert provision.call_args.kwargs["changes"][0].status.value == "missing"


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
    assert "  command: sudo install -d -o root -g root -m 0755 /etc/apt/keyrings" in lines
    assert "  command: sudo install -d -o root -g root -m 0755 /etc/apt/sources.list.d" in lines
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


def test_no_gpg_verify_flatpak_preflight_refuses_provisioning() -> None:
    remote, _ = _flatpak_remote_and_app()
    blocked_remote = remote.model_copy(
        update={"gpg_verify": False, "replay_mode": ReplayMode.BLOCKED}
    )
    sources = SourcesConfig(
        platform=_platform(),
        flatpak=FlatpakSources(remotes=(blocked_remote,)),
    )

    with patch("popctl.sources.preflight.get_available_operators", side_effect=_available):
        preflight = preflight_sources(
            sources,
            source_filter=PackageSource.FLATPAK,
            target_platform=_platform(),
        )

    assert preflight.success is False
    assert "blocked Flatpak" in (preflight.error or "")

    with (
        patch("popctl.sources.preflight.get_available_operators", side_effect=_available),
        patch("popctl.sources.phase.capture_platform", return_value=_platform()),
        patch("popctl.sources.phase.capture_sources", return_value=sources),
        patch("popctl.sources.phase.provision_sources") as provision,
        patch("popctl.sources.phase.typer.confirm") as confirm,
    ):
        result = run_source_phase(
            _manifest(sources),
            SourceChoice.FLATPAK,
            dry_run=False,
            interaction=SourceInteractionPolicy(yes=True, interactive=False),
        )

    assert result.success is False
    assert "blocked Flatpak" in (result.error or "")
    confirm.assert_not_called()
    provision.assert_not_called()


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


def test_refresh_rejects_blocked_live_source_before_preview_or_merge() -> None:
    manifest = _manifest(_apt_sources())
    live = _apt_sources()
    blocked = live.apt.entries[0].model_copy(
        update={
            "replay_mode": ReplayMode.BLOCKED,
            "verbatim_stanza": live.apt.entries[0].verbatim_stanza.replace(
                "Signed-By:", "Trusted: yes\nSigned-By:"
            ),
        }
    )
    live = live.model_copy(update={"apt": live.apt.model_copy(update={"entries": (blocked,)})})

    with (
        patch("popctl.sources.phase.capture_sources", return_value=live),
        patch("popctl.sources.phase._print_capture_trust_preview") as preview,
        patch("popctl.sources.phase.typer.confirm") as confirm,
    ):
        result = refresh_manifest_sources(
            manifest,
            SourceChoice.APT,
            interaction=SourceInteractionPolicy(interactive=True),
        )

    assert result.success is False
    assert result.manifest is manifest
    assert result.changed is False
    assert "blocked" in (result.error or "")
    preview.assert_not_called()
    confirm.assert_not_called()


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


def test_refresh_merges_flatpak_app_context_from_an_existing_trusted_remote() -> None:
    remote, app = _flatpak_remote_and_app()
    sources = SourcesConfig(
        platform=_platform(), flatpak=FlatpakSources(remotes=(remote,))
    )
    manifest = _manifest(sources)
    live = sources.model_copy(update={"flatpak": FlatpakSources(remotes=(remote,), apps=(app,))})

    with (
        patch("popctl.sources.phase.capture_sources", return_value=live) as capture,
        patch("popctl.sources.phase._print_capture_trust_preview") as preview,
        patch("popctl.sources.phase.typer.confirm") as confirm,
    ):
        result = refresh_manifest_sources(
            manifest,
            SourceChoice.FLATPAK,
            interaction=SourceInteractionPolicy(interactive=True),
        )

    assert result.success is True
    assert result.changed is True
    assert result.manifest.sources is not None
    assert result.manifest.sources.flatpak.apps == (app,)
    assert capture.call_args.kwargs["managers"] == (PackageSource.FLATPAK,)
    assert preview.call_args.args[1][0].live == app
    confirm.assert_not_called()

    actions = diff_to_actions(
        DiffResult(
            new=(),
            missing=(
                DiffEntry(
                    name=app.id,
                    source=PackageSource.FLATPAK,
                    diff_type=DiffType.MISSING,
                ),
            ),
            extra=(),
        ),
        sources=result.manifest.sources,
    )
    assert actions[0].source_install_context is not None
    assert actions[0].source_install_context.flatpak_remote == "vendor"
    assert actions[0].source_install_context.flatpak_scope is FlatpakScope.USER
    assert actions[0].source_install_context.flatpak_arch == "x86_64"
    assert actions[0].source_install_context.flatpak_branch == "beta"


def test_refresh_updates_flatpak_app_context_for_an_existing_trusted_remote() -> None:
    remote, app = _flatpak_remote_and_app()
    legacy_remote = remote.model_copy(update={"name": "legacy"})
    legacy_app = app.model_copy(update={"origin": "legacy"})
    sources = SourcesConfig(
        platform=_platform(),
        flatpak=FlatpakSources(remotes=(legacy_remote, remote), apps=(legacy_app,)),
    )
    manifest = _manifest(sources)
    live = sources.model_copy(
        update={"flatpak": FlatpakSources(remotes=(legacy_remote, remote), apps=(app,))}
    )

    with (
        patch("popctl.sources.phase.capture_sources", return_value=live),
        patch("popctl.sources.phase.typer.confirm") as confirm,
    ):
        result = refresh_manifest_sources(
            manifest,
            SourceChoice.FLATPAK,
            interaction=SourceInteractionPolicy(interactive=True),
        )

    assert result.success is True
    assert result.changed is True
    assert result.manifest.sources is not None
    assert result.manifest.sources.flatpak.apps == (app,)
    confirm.assert_not_called()


def test_refresh_merges_flatpak_app_only_with_an_approved_new_remote() -> None:
    remote, app = _flatpak_remote_and_app()
    manifest = _manifest(SourcesConfig(platform=_platform()))
    live = SourcesConfig(
        platform=_platform(), flatpak=FlatpakSources(remotes=(remote,), apps=(app,))
    )

    with (
        patch("popctl.sources.phase.capture_sources", return_value=live),
        patch("popctl.sources.phase.typer.confirm", return_value=True) as confirm,
    ):
        result = refresh_manifest_sources(
            manifest,
            SourceChoice.FLATPAK,
            interaction=SourceInteractionPolicy(interactive=True),
        )

    assert result.success is True
    assert result.changed is True
    assert result.manifest.sources is not None
    assert result.manifest.sources.flatpak.remotes == (remote,)
    assert result.manifest.sources.flatpak.apps == (app,)
    assert confirm.call_count == 1


def test_refresh_does_not_merge_flatpak_app_when_its_new_remote_is_rejected() -> None:
    remote, app = _flatpak_remote_and_app()
    manifest = _manifest(SourcesConfig(platform=_platform()))
    live = SourcesConfig(
        platform=_platform(), flatpak=FlatpakSources(remotes=(remote,), apps=(app,))
    )

    with (
        patch("popctl.sources.phase.capture_sources", return_value=live),
        patch("popctl.sources.phase.typer.confirm", return_value=False),
    ):
        result = refresh_manifest_sources(
            manifest,
            SourceChoice.FLATPAK,
            interaction=SourceInteractionPolicy(interactive=True),
        )

    assert result.success is True
    assert result.changed is False
    assert result.manifest.sources == manifest.sources


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
