from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

from popctl.cli.main import app
from popctl.core.diff import DiffEntry, DiffResult, DiffType
from popctl.models.action import Action, ActionResult, ActionType
from popctl.models.manifest import Manifest, ManifestMeta, PackageConfig, PackageEntry, SystemConfig
from popctl.models.package import PackageSource
from popctl.sources.models import (
    ReplayMode,
    SnapChannel,
    SnapSources,
    SourcePlatform,
    SourcesConfig,
)
from popctl.sources.phase import SourcePhaseResult, SourceRefreshResult
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


def test_sync_missing_manifest_dry_run_never_saves_the_ephemeral_manifest() -> None:
    manifest = _manifest()
    scanner = MagicMock()
    scanner.source = PackageSource.APT
    with (
        patch("popctl.cli.commands.sync.manifest_exists", return_value=False),
        patch("popctl.cli.commands.sync.get_available_scanners", return_value=[scanner]),
        patch(
            "popctl.cli.commands.sync.scan_and_create_manifest",
            return_value=(manifest, {"vim": PackageEntry(source="apt")}, []),
        ),
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
    save.assert_not_called()
    assert "ephemeral" in result.stdout.lower()
