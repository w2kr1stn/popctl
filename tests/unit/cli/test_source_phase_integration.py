from contextlib import ExitStack
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from popctl.cli.main import app
from popctl.core.diff import DiffEntry, DiffResult, DiffType
from popctl.core.paths import get_manifest_path
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
from popctl.sources.phase import SourceInteractionPolicy, SourcePhaseResult, SourceRefreshResult
from popctl.sources.provision import SourceProvisionResult
from popctl.utils.shell import CommandResult
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


def _apt_sources() -> SourcesConfig:
    key = AptKey(
        id="vendor",
        target_path="/etc/apt/keyrings/vendor.asc",
        armor="vendor-key",
        fingerprints=("A" * 40,),
    )
    entry = AptSource(
        id="vendor",
        capture_path="/etc/apt/sources.list.d/vendor.list",
        format=AptSourceFormat.LEGACY,
        ordinal=0,
        managed_target="popctl-vendor",
        verbatim_stanza=(
            "deb [signed-by=/etc/apt/keyrings/vendor.asc] "
            "https://vendor.example/apt stable main\n"
        ),
        key_ids=(key.id,),
        signed_by=SignedByBinding(key_paths=(key.target_path,)),
        replay_mode=ReplayMode.REPLAY,
    )
    return SourcesConfig(
        platform=SourcePlatform(distro_id="ubuntu", codename="noble"),
        apt=AptSources(entries=(entry,), keys=(key,)),
    )


def test_apply_runs_source_phase_before_package_execution() -> None:
    manifest = _manifest(sources=_apt_sources())
    diff = DiffResult(
        new=(),
        missing=(DiffEntry(name="vim", source=PackageSource.APT, diff_type=DiffType.MISSING),),
        extra=(),
    )
    action = Action(ActionType.INSTALL, "vim", PackageSource.APT)
    events: list[str] = []
    operator = MagicMock()
    operator.source = PackageSource.APT

    def execute(*_args: object, **_kwargs: object) -> list[ActionResult]:
        events.append("package")
        return [ActionResult(action=action, success=True)]

    def source_commands(args: list[str], *, timeout: float | None = None) -> CommandResult:
        if args[:2] == ["sudo", "install"]:
            if "source-writes" not in events:
                events.append("source-writes")
            return CommandResult(stdout="", stderr="", returncode=0)
        if args[:2] == ["sudo", "cat"]:
            return CommandResult(stdout="vendor-key", stderr="", returncode=0)
        if args == ["sudo", "apt-get", "update", "--error-on=any"]:
            events.append("apt-update")
            return CommandResult(stdout="", stderr="", returncode=0)
        raise AssertionError(args)

    from popctl.sources.phase import preflight_manager_availability

    def record_preflight(managers: tuple[PackageSource, ...]) -> tuple[object, ...]:
        if "preflight" not in events:
            events.append("preflight")
        return preflight_manager_availability(managers)

    with (
        patch("popctl.cli.commands.apply.require_manifest", return_value=manifest),
        patch("popctl.sources.phase.preflight_manager_availability", side_effect=record_preflight),
        patch("popctl.sources.preflight.get_available_operators", return_value=[operator]),
        patch(
            "popctl.sources.preflight.verify_public_material",
            return_value=VerifiedPublicKey(armor="vendor-key", fingerprints=("A" * 40,)),
        ),
        patch(
            "popctl.sources.provision.verify_public_material",
            return_value=VerifiedPublicKey(armor="vendor-key", fingerprints=("A" * 40,)),
        ),
        patch(
            "popctl.sources.phase.capture_platform",
            return_value=SourcePlatform(distro_id="ubuntu", codename="noble"),
        ),
        patch(
            "popctl.sources.phase.capture_sources",
            return_value=SourcesConfig(platform=manifest.sources.platform),
        ),
        patch("popctl.sources.provision.run_command", side_effect=source_commands),
        patch("popctl.cli.commands.apply.compute_system_diff", return_value=diff),
        patch("popctl.cli.commands.apply.diff_to_actions", return_value=[action]),
        patch("popctl.cli.commands.apply.get_available_operators", return_value=[operator]),
        patch("popctl.cli.commands.apply.execute_actions", side_effect=execute),
        patch("popctl.cli.commands.apply.record_actions_to_history"),
    ):
        result = runner.invoke(app, ["apply", "--yes"])

    assert result.exit_code == 0
    assert events == ["preflight", "source-writes", "apt-update", "package"]


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


@pytest.mark.parametrize("update_succeeds", (True, False))
def test_sync_real_source_phase_orders_or_stops_the_package_and_home_chain(
    update_succeeds: bool,
) -> None:
    manifest = _manifest(sources=_apt_sources())
    action = Action(ActionType.INSTALL, "vim", PackageSource.APT)
    operator = MagicMock()
    operator.source = PackageSource.APT
    events: list[str] = []
    phase_policies: list[SourceInteractionPolicy] = []
    diff = DiffResult(
        new=(),
        missing=(DiffEntry(name="vim", source=PackageSource.APT, diff_type=DiffType.MISSING),),
        extra=(),
    )

    from popctl.sources.phase import preflight_manager_availability, run_source_phase

    def record_preflight(managers: tuple[PackageSource, ...]) -> tuple[object, ...]:
        if "preflight" not in events:
            events.append("preflight")
        return preflight_manager_availability(managers)

    def record_phase(*args: object, **kwargs: object) -> SourcePhaseResult:
        interaction = kwargs["interaction"]
        assert isinstance(interaction, SourceInteractionPolicy)
        phase_policies.append(interaction)
        return run_source_phase(*args, **kwargs)

    def source_commands(args: list[str], *, timeout: float | None = None) -> CommandResult:
        if args[:2] == ["sudo", "install"]:
            if "source-writes" not in events:
                events.append("source-writes")
            return CommandResult(stdout="", stderr="", returncode=0)
        if args[:2] == ["sudo", "cat"]:
            return CommandResult(stdout="vendor-key", stderr="", returncode=0)
        if args == ["sudo", "apt-get", "update", "--error-on=any"]:
            events.append("apt-update")
            return CommandResult(
                stdout="",
                stderr="" if update_succeeds else "strict index update failed",
                returncode=0 if update_succeeds else 1,
            )
        raise AssertionError(args)

    def execute(*_args: object, **_kwargs: object) -> list[ActionResult]:
        events.append("packages")
        return [ActionResult(action=action, success=True)]

    def home(**_kwargs: object) -> None:
        events.append("home")

    with (
        patch("popctl.cli.commands.sync._ensure_manifest", return_value=(manifest, False)),
        patch(
            "popctl.cli.commands.sync.refresh_manifest_sources",
            return_value=SourceRefreshResult(success=True, manifest=manifest),
        ),
        patch("popctl.cli.commands.sync.run_source_phase", side_effect=record_phase),
        patch("popctl.sources.phase.preflight_manager_availability", side_effect=record_preflight),
        patch("popctl.sources.preflight.get_available_operators", return_value=[operator]),
        patch(
            "popctl.sources.preflight.verify_public_material",
            return_value=VerifiedPublicKey(armor="vendor-key", fingerprints=("A" * 40,)),
        ),
        patch(
            "popctl.sources.provision.verify_public_material",
            return_value=VerifiedPublicKey(armor="vendor-key", fingerprints=("A" * 40,)),
        ),
        patch(
            "popctl.sources.phase.capture_platform",
            return_value=SourcePlatform(distro_id="ubuntu", codename="noble"),
        ),
        patch(
            "popctl.sources.phase.capture_sources",
            return_value=SourcesConfig(platform=manifest.sources.platform),
        ),
        patch("popctl.sources.provision.run_command", side_effect=source_commands),
        patch("popctl.cli.commands.sync.compute_system_diff", return_value=diff),
        patch("popctl.cli.commands.sync.diff_to_actions", return_value=[action]),
        patch("popctl.cli.commands.sync.get_available_operators", return_value=[operator]),
        patch("popctl.cli.commands.sync.execute_actions", side_effect=execute) as packages,
        patch("popctl.cli.commands.sync.record_actions_to_history"),
        patch("popctl.cli.commands.sync._run_both_orphan_phases", side_effect=home) as home_phase,
    ):
        result = runner.invoke(app, ["sync", "--yes", "--no-advisor"])

    assert phase_policies == [SourceInteractionPolicy(yes=True, interactive=False)]
    if update_succeeds:
        assert result.exit_code == 0
        assert events == ["preflight", "source-writes", "apt-update", "packages", "home"]
        packages.assert_called_once()
        home_phase.assert_called_once()
    else:
        assert result.exit_code == 1
        assert events == ["preflight", "source-writes", "apt-update"]
        packages.assert_not_called()
        home_phase.assert_not_called()


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


@pytest.mark.parametrize(
    ("command", "expected_yes"),
    (
        ("apply", True),
        ("sync", True),
        ("init", False),
    ),
)
def test_source_bearing_dry_run_matrix_never_prompts_or_mutates(
    command: str,
    expected_yes: bool,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_home = tmp_path / "xdg-config"
    state_home = tmp_path / "xdg-state"
    config_home.mkdir()
    state_home.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    manifest = _manifest(sources=_apt_sources())
    operator = MagicMock()
    operator.source = PackageSource.APT
    policies: list[SourceInteractionPolicy] = []
    provision_commands: list[list[str]] = []
    system_diff = DiffResult(new=(), missing=(), extra=())
    live_sources = (
        manifest.sources
        if command == "init"
        else SourcesConfig(platform=manifest.sources.platform)
    )

    from popctl.sources.phase import capture_and_trust_sources, run_source_phase

    def record_phase(*args: object, **kwargs: object) -> SourcePhaseResult:
        interaction = kwargs["interaction"]
        assert isinstance(interaction, SourceInteractionPolicy)
        policies.append(interaction)
        return run_source_phase(*args, **kwargs)

    def record_capture(*args: object, **kwargs: object) -> SourcePhaseResult:
        interaction = kwargs["interaction"]
        assert isinstance(interaction, SourceInteractionPolicy)
        policies.append(interaction)
        return capture_and_trust_sources(*args, **kwargs)

    def record_provision_command(
        args: list[str], *, timeout: float | None = None
    ) -> CommandResult:
        provision_commands.append(args)
        return CommandResult(stdout="", stderr="", returncode=0)

    with ExitStack() as stack:
        confirm = stack.enter_context(patch("typer.confirm"))
        stack.enter_context(
            patch("popctl.sources.preflight.get_available_operators", return_value=[operator])
        )
        stack.enter_context(
            patch(
                "popctl.sources.preflight.verify_public_material",
                return_value=VerifiedPublicKey(armor="vendor-key", fingerprints=("A" * 40,)),
            )
        )
        stack.enter_context(
            patch(
                "popctl.sources.phase.capture_platform",
                return_value=SourcePlatform(distro_id="ubuntu", codename="noble"),
            )
        )
        stack.enter_context(
            patch(
                "popctl.sources.phase.capture_sources",
                return_value=live_sources,
            )
        )
        stack.enter_context(
            patch("popctl.sources.provision.run_command", side_effect=record_provision_command)
        )
        if command == "apply":
            history = stack.enter_context(
                patch("popctl.cli.commands.apply.record_actions_to_history")
            )
            stack.enter_context(
                patch("popctl.cli.commands.apply.require_manifest", return_value=manifest)
            )
            stack.enter_context(
                patch("popctl.cli.commands.apply.run_source_phase", side_effect=record_phase)
            )
            stack.enter_context(
                patch("popctl.cli.commands.apply.compute_system_diff", return_value=system_diff)
            )
            result = runner.invoke(app, ["apply", "--dry-run", "--yes"])
        elif command == "sync":
            history = stack.enter_context(
                patch("popctl.cli.commands.sync.record_actions_to_history")
            )
            stack.enter_context(
                patch("popctl.cli.commands.sync._ensure_manifest", return_value=(manifest, False))
            )
            stack.enter_context(
                patch("popctl.cli.commands.sync.run_source_phase", side_effect=record_phase)
            )
            stack.enter_context(
                patch("popctl.cli.commands.sync.compute_system_diff", return_value=system_diff)
            )
            result = runner.invoke(
                app,
                ["sync", "--dry-run", "--yes", "--no-filesystem", "--no-configs"],
            )
        else:
            history = stack.enter_context(patch("popctl.cli.commands.init.save_manifest"))
            stack.enter_context(
                patch("popctl.cli.commands.init.get_available_scanners", return_value=[operator])
            )
            stack.enter_context(
                patch(
                    "popctl.cli.commands.init.scan_and_create_manifest",
                    return_value=(manifest, {}, []),
                )
            )
            stack.enter_context(
                patch(
                    "popctl.cli.commands.init.capture_and_trust_sources",
                    side_effect=record_capture,
                )
            )
            result = runner.invoke(app, ["init", "--dry-run"])

    assert result.exit_code == 0
    assert policies == [SourceInteractionPolicy(yes=expected_yes, interactive=False)]
    assert provision_commands == []
    confirm.assert_not_called()
    history.assert_not_called()
    assert not get_manifest_path().exists()
