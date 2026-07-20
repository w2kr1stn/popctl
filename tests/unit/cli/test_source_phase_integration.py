from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from popctl.cli.main import app
from popctl.core.diff import DiffEntry, DiffResult, DiffType
from popctl.models.action import Action, ActionResult, ActionType
from popctl.models.manifest import Manifest, ManifestMeta, PackageConfig, PackageEntry, SystemConfig
from popctl.models.package import PackageSource
from popctl.sources.keytrust import VerifiedPublicKey
from popctl.sources.models import (
    AptKey,
    AptSource,
    AptSourceFormat,
    AptSources,
    ReplayMode,
    SignedByBinding,
    SnapChannel,
    SnapSources,
    SourcePlatform,
    SourcesConfig,
)
from popctl.sources.phase import SourcePhaseResult, SourceRefreshResult
from popctl.sources.provision import SourceProvisionResult
from typer.testing import CliRunner

runner = CliRunner()


def _manifest(*, sources: SourcesConfig | None = None) -> Manifest:
    now = datetime.now(UTC)
    return Manifest(
        meta=ManifestMeta(created=now, updated=now),
        system=SystemConfig(name="test-machine"),
        packages=PackageConfig(keep={"vim": PackageEntry(source="apt")}),
        sources=sources,
    )


def _snap_sources() -> SourcesConfig:
    return SourcesConfig(
        platform=SourcePlatform(distro_id="ubuntu", codename="noble"),
        snap=SnapSources(
            packages=(
                SnapChannel(name="hello", channel="latest/edge", replay_mode=ReplayMode.REPLAY),
            )
        ),
    )


def _base_sources(uri: str) -> SourcesConfig:
    key = AptKey(
        id="ubuntu",
        target_path="/etc/apt/keyrings/ubuntu.asc",
        armor="ubuntu-key",
        fingerprints=("A" * 40,),
    )
    entry = AptSource(
        id="ubuntu",
        capture_path="/etc/apt/sources.list",
        format=AptSourceFormat.DEB822,
        ordinal=0,
        managed_target="popctl-ubuntu",
        verbatim_stanza=(
            "Types: deb\n"
            f"URIs: {uri}\n"
            "Suites: noble\n"
            "Components: main\n"
            "Signed-By: /etc/apt/keyrings/ubuntu.asc\n"
        ),
        key_ids=("ubuntu",),
        signed_by=SignedByBinding(key_paths=("/etc/apt/keyrings/ubuntu.asc",)),
        replay_mode=ReplayMode.REPORT_ONLY,
    )
    return SourcesConfig(
        platform=SourcePlatform(distro_id="ubuntu", codename="noble"),
        apt=AptSources(entries=(entry,), keys=(key,)),
    )


def test_apply_runs_source_phase_before_package_execution() -> None:
    manifest = _manifest(sources=_snap_sources())
    diff = DiffResult(
        new=(),
        missing=(DiffEntry(name="vim", source=PackageSource.APT, diff_type=DiffType.MISSING),),
        extra=(),
    )
    action = Action(ActionType.INSTALL, "vim", PackageSource.APT)
    events: list[str] = []
    operator = MagicMock()
    operator.source = PackageSource.APT

    def source_phase(*_args: object, **_kwargs: object) -> SourcePhaseResult:
        events.extend(("preflight", "source-writes", "apt-update"))
        return SourcePhaseResult(success=True)

    def execute(*_args: object, **_kwargs: object) -> list[ActionResult]:
        events.append("package")
        return [ActionResult(action=action, success=True)]

    with (
        patch("popctl.cli.commands.apply.require_manifest", return_value=manifest),
        patch("popctl.cli.commands.apply.run_source_phase", side_effect=source_phase),
        patch("popctl.cli.commands.apply.compute_system_diff", return_value=diff),
        patch("popctl.cli.commands.apply.diff_to_actions", return_value=[action]),
        patch("popctl.cli.commands.apply.get_available_operators", return_value=[operator]),
        patch("popctl.cli.commands.apply.execute_actions", side_effect=execute),
        patch("popctl.cli.commands.apply.record_actions_to_history"),
    ):
        result = runner.invoke(app, ["apply", "--yes"])

    assert result.exit_code == 0
    assert events == ["preflight", "source-writes", "apt-update", "package"]


def test_apply_source_failure_stops_package_execution() -> None:
    manifest = _manifest(sources=_snap_sources())
    with (
        patch("popctl.cli.commands.apply.require_manifest", return_value=manifest),
        patch(
            "popctl.cli.commands.apply.run_source_phase",
            return_value=SourcePhaseResult(success=False, error="strict index update failed"),
        ),
        patch("popctl.cli.commands.apply.compute_system_diff") as diff,
    ):
        result = runner.invoke(app, ["apply", "--yes"])

    assert result.exit_code == 1
    diff.assert_not_called()


def test_apply_yes_continues_to_packages_for_report_only_base_drift() -> None:
    manifest = _manifest(sources=_base_sources("https://archive.ubuntu.com/ubuntu"))
    live = _base_sources("http://archive.ubuntu.com/ubuntu")
    action = Action(ActionType.INSTALL, "vim", PackageSource.APT)
    operator = MagicMock()
    operator.source = PackageSource.APT

    with (
        patch("popctl.cli.commands.apply.require_manifest", return_value=manifest),
        patch("popctl.sources.preflight.get_available_operators", return_value=[operator]),
        patch(
            "popctl.sources.preflight.verify_public_material",
            return_value=VerifiedPublicKey(armor="ubuntu-key", fingerprints=("A" * 40,)),
        ),
        patch(
            "popctl.sources.phase.capture_platform",
            return_value=SourcePlatform(distro_id="ubuntu", codename="noble"),
        ),
        patch("popctl.sources.phase.capture_sources", return_value=live),
        patch(
            "popctl.sources.phase.provision_sources",
            return_value=SourceProvisionResult(success=True, retained_artifacts=()),
        ),
        patch(
            "popctl.cli.commands.apply.compute_system_diff",
            return_value=DiffResult(
                new=(),
                missing=(
                    DiffEntry(name="vim", source=PackageSource.APT, diff_type=DiffType.MISSING),
                ),
                extra=(),
            ),
        ),
        patch("popctl.cli.commands.apply.diff_to_actions", return_value=[action]),
        patch("popctl.cli.commands.apply.get_available_operators", return_value=[operator]),
        patch(
            "popctl.cli.commands.apply.execute_actions",
            return_value=[ActionResult(action=action, success=True)],
        ) as execute,
        patch("popctl.cli.commands.apply.record_actions_to_history"),
    ):
        result = runner.invoke(app, ["apply", "--yes"])

    assert result.exit_code == 0
    execute.assert_called_once()


def test_sync_stops_before_packages_and_home_after_a_source_failure() -> None:
    manifest = _manifest(sources=_snap_sources())
    with (
        patch("popctl.cli.commands.sync._ensure_manifest", return_value=(manifest, False)),
        patch(
            "popctl.cli.commands.sync.refresh_manifest_sources",
            return_value=SourceRefreshResult(success=True, manifest=manifest),
        ),
        patch(
            "popctl.cli.commands.sync.run_source_phase",
            return_value=SourcePhaseResult(success=False, error="strict index update failed"),
        ),
        patch("popctl.cli.commands.sync._sync_packages") as packages,
        patch("popctl.cli.commands.sync._run_both_orphan_phases") as home,
    ):
        result = runner.invoke(app, ["sync", "--yes"])

    assert result.exit_code == 1
    packages.assert_not_called()
    home.assert_not_called()


def test_sync_yes_continues_to_packages_for_report_only_base_drift() -> None:
    manifest = _manifest(sources=_base_sources("https://archive.ubuntu.com/ubuntu"))
    live = _base_sources("http://archive.ubuntu.com/ubuntu")
    operator = MagicMock()
    operator.source = PackageSource.APT

    with (
        patch("popctl.cli.commands.sync._ensure_manifest", return_value=(manifest, False)),
        patch("popctl.sources.preflight.get_available_operators", return_value=[operator]),
        patch(
            "popctl.sources.preflight.verify_public_material",
            return_value=VerifiedPublicKey(armor="ubuntu-key", fingerprints=("A" * 40,)),
        ),
        patch(
            "popctl.sources.phase.capture_platform",
            return_value=SourcePlatform(distro_id="ubuntu", codename="noble"),
        ),
        patch("popctl.sources.phase.capture_sources", return_value=live),
        patch(
            "popctl.sources.phase.provision_sources",
            return_value=SourceProvisionResult(success=True, retained_artifacts=()),
        ),
        patch("popctl.cli.commands.sync._sync_packages", return_value=(False, False)) as packages,
        patch("popctl.cli.commands.sync._run_both_orphan_phases"),
        patch("popctl.cli.commands.sync.save_manifest") as save,
    ):
        result = runner.invoke(app, ["sync", "--yes"])

    assert result.exit_code == 0
    packages.assert_called_once()
    save.assert_not_called()


def test_sync_persists_only_a_confirmed_existing_manifest_refresh() -> None:
    manifest = _manifest(sources=_snap_sources())
    refreshed = _manifest(sources=_snap_sources())
    refresh_result = SourceRefreshResult(success=True, manifest=refreshed, changed=True)
    with (
        patch("popctl.cli.commands.sync._ensure_manifest", return_value=(manifest, False)),
        patch("popctl.cli.commands.sync.refresh_manifest_sources", return_value=refresh_result),
        patch("popctl.cli.commands.sync.save_manifest") as save,
        patch(
            "popctl.cli.commands.sync.run_source_phase",
            return_value=SourcePhaseResult(success=True),
        ),
        patch("popctl.cli.commands.sync._sync_packages", return_value=(False, False)),
        patch("popctl.cli.commands.sync._run_both_orphan_phases"),
    ):
        result = runner.invoke(app, ["sync", "--yes"])

    assert result.exit_code == 0
    save.assert_called_once_with(refreshed)


def test_sync_does_not_persist_a_blocked_live_refresh_source() -> None:
    manifest = _manifest(sources=_snap_sources())
    blocked = manifest.sources.snap.packages[0].model_copy(
        update={"replay_mode": ReplayMode.BLOCKED}
    )
    live = manifest.sources.model_copy(
        update={"snap": SnapSources(packages=(blocked,))}
    )

    with (
        patch("popctl.cli.commands.sync._ensure_manifest", return_value=(manifest, False)),
        patch("popctl.sources.phase.capture_sources", return_value=live),
        patch("popctl.cli.commands.sync.save_manifest") as save,
        patch("popctl.cli.commands.sync.run_source_phase") as phase,
    ):
        result = runner.invoke(app, ["sync", "--yes"])

    assert result.exit_code == 1
    save.assert_not_called()
    phase.assert_not_called()


def test_sync_missing_manifest_dry_run_never_saves_the_ephemeral_manifest() -> None:
    manifest = _manifest()
    scanner = MagicMock()
    scanner.source = PackageSource.APT
    with (
        patch("popctl.cli.commands.sync.manifest_exists", return_value=False),
        patch("popctl.cli.commands.sync.get_available_scanners", return_value=[scanner]),
        patch(
            "popctl.cli.commands.sync.capture_manifest",
            return_value=(manifest, {"vim": PackageEntry(source="apt")}, []),
        ) as capture,
        patch("popctl.cli.commands.sync.save_manifest") as save,
        patch(
            "popctl.cli.commands.sync.run_source_phase",
            return_value=SourcePhaseResult(success=True),
        ),
        patch("popctl.cli.commands.sync.compute_system_diff", return_value=DiffResult((), (), ())),
    ):
        result = runner.invoke(
            app,
            ["sync", "--dry-run", "--no-filesystem", "--no-configs"],
        )

    assert result.exit_code == 0
    assert capture.call_args.args[1].value == "all"
    assert capture.call_args.kwargs["dry_run"] is True
    save.assert_not_called()
    assert "ephemeral" in result.stdout.lower()
