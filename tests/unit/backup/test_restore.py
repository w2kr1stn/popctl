"""Unit tests for backup restore module."""

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from popctl.backup.backup import BackupError
from popctl.backup.restore import (
    _fix_sensitive_permissions,
    _restore_home_files,
    _restore_popctl_state,
    list_backups,
    restore_backup,
)
from popctl.cli.types import SourceChoice
from popctl.core.executor import ActionResult
from popctl.core.manifest import load_manifest, save_manifest
from popctl.models.action import Action
from popctl.models.manifest import (
    Manifest,
    ManifestMeta,
    PackageConfig,
    PackageEntry,
    SystemConfig,
)
from popctl.models.package import PackageSource
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
    SourcePlatform,
    SourcesConfig,
)
from popctl.sources.phase import SourceInteractionPolicy, SourcePhaseResult
from popctl.sources.provision import SourceProvisionResult
from popctl.utils.shell import CommandResult

FINGERPRINT = "A" * 40
CHANGED_FINGERPRINT = "B" * 40


def _platform() -> SourcePlatform:
    return SourcePlatform(distro_id="ubuntu", codename="noble")


def _apt_sources(
    *,
    fingerprint: str = FINGERPRINT,
    fingerprints: tuple[str, ...] | None = None,
    stanza_suffix: str = "",
) -> SourcesConfig:
    key = AptKey(
        id="vendor",
        target_path="/etc/apt/keyrings/vendor.asc",
        armor="vendor-key",
        fingerprints=fingerprints if fingerprints is not None else (fingerprint,),
    )
    entry = AptSource(
        id="vendor",
        capture_path="/etc/apt/sources.list.d/popctl-vendor.list",
        format=AptSourceFormat.LEGACY,
        ordinal=0,
        managed_target="popctl-vendor",
        verbatim_stanza=(
            "deb [signed-by=/etc/apt/keyrings/vendor.asc] "
            f"https://vendor.example/apt stable main {stanza_suffix}".strip()
        ),
        key_ids=("vendor",),
        signed_by=SignedByBinding(key_paths=("/etc/apt/keyrings/vendor.asc",)),
        replay_mode=ReplayMode.REPLAY,
    )
    return SourcesConfig(platform=_platform(), apt=AptSources(entries=(entry,), keys=(key,)))


def _base_sources(uri: str) -> SourcesConfig:
    sources = _apt_sources()
    entry = sources.apt.entries[0].model_copy(
        update={
            "verbatim_stanza": (
                "deb [signed-by=/etc/apt/keyrings/vendor.asc] "
                f"{uri} noble main\n"
            ),
            "replay_mode": ReplayMode.REPORT_ONLY,
        }
    )
    return sources.model_copy(update={"apt": sources.apt.model_copy(update={"entries": (entry,)})})


def _flatpak_sources() -> SourcesConfig:
    remote = FlatpakRemote(
        name="vendor",
        scope=FlatpakScope.USER,
        url="https://vendor.example/repo",
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
        branch="stable",
    )
    return SourcesConfig(
        platform=_platform(),
        flatpak=FlatpakSources(remotes=(remote,), apps=(app,)),
    )


def _manifest(sources: SourcesConfig | None = None) -> Manifest:
    now = datetime.now(UTC)
    return Manifest(
        meta=ManifestMeta(created=now, updated=now),
        system=SystemConfig(name="backup-machine"),
        packages=PackageConfig(
            keep={
                "vim": PackageEntry(source="apt"),
                "org.example.App": PackageEntry(source="flatpak"),
                "hello": PackageEntry(source="snap"),
            },
        ),
        sources=sources,
    )


def _extract_manifest(manifest: Manifest, *, include_home_file: bool = True):
    def extract(_backup_path: Path, staging_dir: Path, _identity: str | None) -> None:
        (staging_dir / "metadata.json").write_text("{}")
        manifest_path = staging_dir / "files" / "popctl" / "manifest.toml"
        save_manifest(manifest, manifest_path)
        if include_home_file:
            home_file = staging_dir / "files" / "home" / ".config" / "restored"
            home_file.parent.mkdir(parents=True)
            home_file.write_text("from-backup")

    return extract


class TestRestorePopctlState:
    """Tests for popctl state file restoration."""

    def test_restores_manifest(self, tmp_path: Path) -> None:
        """Restores manifest.toml to config dir."""
        staging = tmp_path / "staging"
        popctl_dir = staging / "files" / "popctl"
        popctl_dir.mkdir(parents=True)
        (popctl_dir / "manifest.toml").write_text("[meta]\ncreated = '2026-01-01'")

        config_dir = tmp_path / "config"
        state_dir = tmp_path / "state"

        with (
            patch("popctl.backup.restore.get_config_dir", return_value=config_dir),
            patch("popctl.backup.restore.get_state_dir", return_value=state_dir),
        ):
            count = _restore_popctl_state(staging)

        assert count == 1
        assert (config_dir / "manifest.toml").exists()
        assert "[meta]" in (config_dir / "manifest.toml").read_text()

    def test_restores_history(self, tmp_path: Path) -> None:
        """Restores history.jsonl to state dir."""
        staging = tmp_path / "staging"
        popctl_dir = staging / "files" / "popctl"
        popctl_dir.mkdir(parents=True)
        (popctl_dir / "history.jsonl").write_text('{"id":"abc"}\n')

        config_dir = tmp_path / "config"
        state_dir = tmp_path / "state"

        with (
            patch("popctl.backup.restore.get_config_dir", return_value=config_dir),
            patch("popctl.backup.restore.get_state_dir", return_value=state_dir),
        ):
            count = _restore_popctl_state(staging)

        assert count == 1
        assert (state_dir / "history.jsonl").exists()

    def test_restores_advisor_memory(self, tmp_path: Path) -> None:
        """Restores advisor memory to nested state dir."""
        staging = tmp_path / "staging"
        popctl_dir = staging / "files" / "popctl"
        popctl_dir.mkdir(parents=True)
        (popctl_dir / "advisor-memory.md").write_text("# Memory")

        config_dir = tmp_path / "config"
        state_dir = tmp_path / "state"

        with (
            patch("popctl.backup.restore.get_config_dir", return_value=config_dir),
            patch("popctl.backup.restore.get_state_dir", return_value=state_dir),
        ):
            count = _restore_popctl_state(staging)

        assert count == 1
        assert (state_dir / "advisor" / "memory.md").exists()

    def test_returns_zero_if_no_popctl_dir(self, tmp_path: Path) -> None:
        """Returns 0 if staging has no popctl files."""
        staging = tmp_path / "staging"
        staging.mkdir()

        config_dir = tmp_path / "config"
        state_dir = tmp_path / "state"

        with (
            patch("popctl.backup.restore.get_config_dir", return_value=config_dir),
            patch("popctl.backup.restore.get_state_dir", return_value=state_dir),
        ):
            count = _restore_popctl_state(staging)

        assert count == 0


class TestRestoreHomeFiles:
    """Tests for home directory file restoration."""

    def test_restores_files_to_home(self, tmp_path: Path) -> None:
        """Files from staging/files/home/ are copied to current $HOME."""
        staging = tmp_path / "staging"
        home_dir = staging / "files" / "home"
        home_dir.mkdir(parents=True)
        (home_dir / ".bashrc").write_text("# bash config")

        subdir = home_dir / "projects" / "myapp"
        subdir.mkdir(parents=True)
        (subdir / "main.py").write_text("print('hello')")

        target_home = tmp_path / "target_home"
        target_home.mkdir()

        with patch("popctl.backup.restore.Path.home", return_value=target_home):
            count = _restore_home_files(staging)

        assert count == 2
        assert (target_home / ".bashrc").read_text() == "# bash config"
        assert (target_home / "projects" / "myapp" / "main.py").read_text() == "print('hello')"

    def test_returns_zero_if_no_home_dir(self, tmp_path: Path) -> None:
        """Returns 0 if staging has no home files."""
        staging = tmp_path / "staging"
        staging.mkdir()

        with patch("popctl.backup.restore.Path.home", return_value=tmp_path):
            count = _restore_home_files(staging)

        assert count == 0


class TestFixSensitivePermissions:
    """Tests for SSH/GPG permission fixing."""

    def test_fixes_ssh_permissions(self, tmp_path: Path) -> None:
        """Sets correct permissions on .ssh directory and files."""
        ssh_dir = tmp_path / ".ssh"
        ssh_dir.mkdir()
        key_file = ssh_dir / "id_ed25519"
        key_file.write_text("private key")
        key_file.chmod(0o644)  # wrong permission

        with patch("popctl.backup.restore.Path.home", return_value=tmp_path):
            _fix_sensitive_permissions()

        assert oct(ssh_dir.stat().st_mode)[-3:] == "700"
        assert oct(key_file.stat().st_mode)[-3:] == "600"

    def test_fixes_gnupg_permissions(self, tmp_path: Path) -> None:
        """Sets correct permissions on .gnupg directory."""
        gnupg_dir = tmp_path / ".gnupg"
        gnupg_dir.mkdir()
        gnupg_dir.chmod(0o755)  # wrong permission

        with patch("popctl.backup.restore.Path.home", return_value=tmp_path):
            _fix_sensitive_permissions()

        assert oct(gnupg_dir.stat().st_mode)[-3:] == "700"

    def test_handles_missing_dirs(self, tmp_path: Path) -> None:
        """Does not raise if .ssh/.gnupg don't exist."""
        with patch("popctl.backup.restore.Path.home", return_value=tmp_path):
            _fix_sensitive_permissions()  # should not raise


class TestListBackups:
    """Tests for backup listing."""

    def test_list_local_backups(self, tmp_path: Path) -> None:
        """Lists backup files from local directory."""
        (tmp_path / "popctl-backup-host-20260306-120000.tar.zst.age").write_text("")
        (tmp_path / "popctl-backup-host-20260307-120000.tar.zst.age").write_text("")
        (tmp_path / "other-file.txt").write_text("")

        result = list_backups(str(tmp_path))
        assert len(result) == 2
        assert "popctl-backup-host-20260306-120000.tar.zst.age" in result

    def test_list_empty_directory(self, tmp_path: Path) -> None:
        """Returns empty list for directory with no backups."""
        result = list_backups(str(tmp_path))
        assert result == []

    def test_list_nonexistent_directory(self, tmp_path: Path) -> None:
        """Returns empty list for non-existent directory."""
        result = list_backups(str(tmp_path / "nonexistent"))
        assert result == []

    def test_list_default_directory(self, tmp_path: Path) -> None:
        """Uses default backup dir when no target given."""
        with patch("popctl.backup.restore.get_backups_dir", return_value=tmp_path):
            result = list_backups()
        assert result == []


class TestRestoreSourceIntegration:
    def test_restore_yes_continues_to_packages_for_report_only_base_drift(
        self, tmp_path: Path
    ) -> None:
        manifest = _manifest(_base_sources("https://archive.ubuntu.com/ubuntu"))
        live = _base_sources("http://archive.ubuntu.com/ubuntu")
        operator = MagicMock()
        operator.source = PackageSource.APT

        with (
            patch("popctl.backup.restore._fetch_backup", return_value=tmp_path / "backup.age"),
            patch(
                "popctl.backup.restore._decrypt_and_decompress",
                side_effect=_extract_manifest(manifest),
            ),
            patch("popctl.backup.restore._restore_popctl_state", return_value=0),
            patch("popctl.backup.restore._install_packages", return_value=(1, 0)) as packages,
            patch("popctl.backup.restore._restore_home_files", return_value=0),
            patch("popctl.backup.restore._fix_sensitive_permissions"),
            patch("popctl.sources.preflight.get_available_operators", return_value=[operator]),
            patch(
                "popctl.sources.preflight.verify_public_material",
                return_value=VerifiedPublicKey(armor="verified-key", fingerprints=(FINGERPRINT,)),
            ),
            patch("popctl.sources.phase.capture_platform", return_value=_platform()),
            patch("popctl.sources.phase.capture_sources", return_value=live),
            patch(
                "popctl.sources.phase.provision_sources",
                return_value=SourceProvisionResult(success=True, retained_artifacts=()),
            ),
        ):
            counts = restore_backup(
                "backup.age",
                package_source=SourceChoice.APT,
                interaction=SourceInteractionPolicy(yes=True),
            )

        assert counts["packages_installed"] == 1
        packages.assert_called_once()

    @pytest.mark.parametrize("has_live_manifest", [False, True])
    def test_dry_run_uses_the_extracted_manifest_without_xdg_writes(
        self,
        tmp_path: Path,
        has_live_manifest: bool,
    ) -> None:
        backup_manifest = _manifest(_flatpak_sources())
        live_manifest = _manifest()
        config_dir = tmp_path / "config"
        state_dir = tmp_path / "state"
        target_home = tmp_path / "home"
        if has_live_manifest:
            save_manifest(live_manifest, config_dir / "manifest.toml")
        source_manifests: list[Manifest] = []
        package_manifests: list[Manifest] = []

        def source_phase(
            manifest: Manifest,
            source: SourceChoice,
            *,
            dry_run: bool,
            interaction: SourceInteractionPolicy,
        ) -> SourcePhaseResult:
            source_manifests.append(manifest)
            assert source is SourceChoice.ALL
            assert dry_run is True
            assert interaction == SourceInteractionPolicy()
            return SourcePhaseResult(success=True)

        def install_packages(
            manifest: Manifest,
            source: SourceChoice,
            *,
            dry_run: bool,
        ) -> tuple[int, int]:
            package_manifests.append(manifest)
            assert source is SourceChoice.ALL
            assert dry_run is True
            return 0, 0

        with (
            patch("popctl.backup.restore._fetch_backup", return_value=tmp_path / "backup.age"),
            patch(
                "popctl.backup.restore._decrypt_and_decompress",
                side_effect=_extract_manifest(backup_manifest),
            ),
            patch("popctl.backup.restore.get_config_dir", return_value=config_dir),
            patch("popctl.backup.restore.get_state_dir", return_value=state_dir),
            patch("popctl.backup.restore.Path.home", return_value=target_home),
            patch("popctl.backup.restore.run_source_phase", side_effect=source_phase),
            patch("popctl.backup.restore._install_packages", side_effect=install_packages),
            patch("popctl.backup.restore.load_manifest", wraps=load_manifest) as load,
        ):
            counts = restore_backup("backup.age", dry_run=True)

        assert counts == {
            "popctl_state": 0,
            "home_files": 0,
            "packages_installed": 0,
            "packages_failed": 0,
        }
        assert len(source_manifests) == 1
        assert source_manifests[0] is package_manifests[0]
        assert source_manifests[0].system.name == "backup-machine"
        assert source_manifests[0].sources == backup_manifest.sources
        load.assert_called_once()
        assert load.call_args.args[0].as_posix().endswith("files/popctl/manifest.toml")
        if has_live_manifest:
            assert load_manifest(config_dir / "manifest.toml") == live_manifest
        else:
            assert not (config_dir / "manifest.toml").exists()
        assert not state_dir.exists()
        assert not (target_home / ".config" / "restored").exists()

    @pytest.mark.parametrize(
        ("files_only", "packages_only", "expected_events"),
        [
            (False, False, ["state", "sources", "packages", "home", "permissions"]),
            (False, True, ["state", "sources", "packages"]),
            (True, False, ["state", "home", "permissions"]),
        ],
    )
    def test_restore_mode_ordering_and_source_phase_scope(
        self,
        tmp_path: Path,
        files_only: bool,
        packages_only: bool,
        expected_events: list[str],
    ) -> None:
        manifest = _manifest(_flatpak_sources())
        events: list[str] = []

        def state(_staging_dir: Path, *, dry_run: bool) -> int:
            assert dry_run is False
            events.append("state")
            return 1

        def source_phase(*_args: object, **_kwargs: object) -> SourcePhaseResult:
            events.append("sources")
            return SourcePhaseResult(success=True)

        def install(*_args: object, **_kwargs: object) -> tuple[int, int]:
            events.append("packages")
            return 1, 0

        def home(_staging_dir: Path, *, dry_run: bool) -> int:
            assert dry_run is False
            events.append("home")
            return 1

        def permissions(*, dry_run: bool) -> None:
            assert dry_run is False
            events.append("permissions")

        with (
            patch("popctl.backup.restore._fetch_backup", return_value=tmp_path / "backup.age"),
            patch(
                "popctl.backup.restore._decrypt_and_decompress",
                side_effect=_extract_manifest(manifest),
            ),
            patch("popctl.backup.restore._restore_popctl_state", side_effect=state),
            patch("popctl.backup.restore.run_source_phase", side_effect=source_phase) as phase,
            patch(
                "popctl.backup.restore._install_packages", side_effect=install
            ) as install_packages,
            patch("popctl.backup.restore._restore_home_files", side_effect=home),
            patch("popctl.backup.restore._fix_sensitive_permissions", side_effect=permissions),
        ):
            restore_backup(
                "backup.age",
                files_only=files_only,
                packages_only=packages_only,
            )

        assert events == expected_events
        assert phase.called is (not files_only)
        assert install_packages.called is (not files_only)

    def test_filtered_flatpak_dry_run_uses_no_other_manager_or_mutation(
        self,
        tmp_path: Path,
    ) -> None:
        manifest = _manifest(_flatpak_sources())
        config_dir = tmp_path / "config"
        state_dir = tmp_path / "state"
        target_home = tmp_path / "home"
        flatpak_operator = MagicMock()
        flatpak_operator.source = PackageSource.FLATPAK
        seen_actions: list[Action] = []

        def install(items: list[Action]) -> list[ActionResult]:
            seen_actions.extend(items)
            return [ActionResult(action=item, success=True) for item in items]

        flatpak_operator.install.side_effect = install
        available = MagicMock(return_value=[flatpak_operator])

        with (
            patch("popctl.backup.restore._fetch_backup", return_value=tmp_path / "backup.age"),
            patch(
                "popctl.backup.restore._decrypt_and_decompress",
                side_effect=_extract_manifest(manifest),
            ),
            patch("popctl.backup.restore.get_config_dir", return_value=config_dir),
            patch("popctl.backup.restore.get_state_dir", return_value=state_dir),
            patch("popctl.backup.restore.Path.home", return_value=target_home),
            patch(
                "popctl.backup.restore.run_source_phase",
                return_value=SourcePhaseResult(success=True),
            ) as phase,
            patch("popctl.operators.get_available_operators", available),
            patch("popctl.core.executor.record_actions_to_history") as history,
        ):
            counts = restore_backup(
                "backup.age",
                package_source=SourceChoice.FLATPAK,
                dry_run=True,
            )

        assert counts["packages_installed"] == 1
        phase.assert_called_once()
        assert phase.call_args.args[1] is SourceChoice.FLATPAK
        assert phase.call_args.kwargs["dry_run"] is True
        available.assert_called_once_with(PackageSource.FLATPAK, dry_run=True)
        assert len(seen_actions) == 1
        assert seen_actions[0].package == "org.example.App"
        assert seen_actions[0].source_install_context is not None
        flatpak_operator.remove.assert_not_called()
        history.assert_not_called()
        assert not state_dir.exists()
        assert not (target_home / ".config" / "restored").exists()

    @pytest.mark.parametrize(
        "case",
        [
            "changed fingerprint",
            "absent fingerprint",
            "secret keyring",
            "trusted=yes",
            "allow-insecure",
        ],
    )
    def test_restore_security_failures_stop_package_and_home_work(
        self,
        tmp_path: Path,
        case: str,
    ) -> None:
        expected = _apt_sources()
        live = expected
        verification: VerifiedPublicKey | KeyTrustError = VerifiedPublicKey(
            armor="verified-key",
            fingerprints=(FINGERPRINT,),
        )
        if case == "changed fingerprint":
            live = _apt_sources(fingerprint=CHANGED_FINGERPRINT)
        elif case == "absent fingerprint":
            expected = _apt_sources(fingerprints=())
        elif case == "secret keyring":
            verification = KeyTrustError("secret material")
        elif case == "trusted=yes":
            expected = _apt_sources(stanza_suffix="[trusted=yes]")
        else:
            expected = _apt_sources(stanza_suffix="allow-insecure=yes")

        manifest = _manifest(expected)
        available_operator = MagicMock()
        available_operator.source = PackageSource.APT
        source_commands: list[list[str]] = []

        def record_source_command(
            args: list[str], *, timeout: float | None = None
        ) -> CommandResult:
            source_commands.append(args)
            return CommandResult(stdout="", stderr="", returncode=0)

        with (
            patch("popctl.backup.restore._fetch_backup", return_value=tmp_path / "backup.age"),
            patch(
                "popctl.backup.restore._decrypt_and_decompress",
                side_effect=_extract_manifest(manifest),
            ),
            patch("popctl.backup.restore._restore_popctl_state", return_value=0),
            patch("popctl.backup.restore._install_packages") as install_packages,
            patch("popctl.backup.restore._restore_home_files") as restore_home,
            patch("popctl.backup.restore._fix_sensitive_permissions") as permissions,
            patch(
                "popctl.sources.preflight.get_available_operators",
                return_value=[available_operator],
            ),
            patch("popctl.sources.phase.capture_platform", return_value=_platform()),
            patch("popctl.sources.phase.capture_sources", return_value=live),
            patch(
                "popctl.sources.preflight.verify_public_material",
                side_effect=verification if isinstance(verification, KeyTrustError) else None,
                return_value=None if isinstance(verification, KeyTrustError) else verification,
            ),
            patch("popctl.sources.phase.provision_sources") as provision,
            patch("popctl.sources.provision.run_command", side_effect=record_source_command),
            patch("popctl.sources.phase.typer.confirm") as confirm,
            pytest.raises(BackupError),
        ):
            restore_backup(
                "backup.age",
                package_source=SourceChoice.APT,
                interaction=SourceInteractionPolicy(yes=True),
            )

        confirm.assert_not_called()
        provision.assert_not_called()
        install_packages.assert_not_called()
        restore_home.assert_not_called()
        permissions.assert_not_called()
        assert source_commands == []

    def test_restore_runs_source_then_packages_then_home_and_permissions(
        self, tmp_path: Path
    ) -> None:
        manifest = _manifest(_apt_sources())
        events: list[str] = []

        def state(_staging_dir: Path, *, dry_run: bool) -> int:
            assert dry_run is False
            events.append("state")
            return 1

        def source_phase(*_args: object, **_kwargs: object) -> SourcePhaseResult:
            events.extend(("preflight", "source-writes", "apt-update"))
            return SourcePhaseResult(success=True)

        def install(*_args: object, **_kwargs: object) -> tuple[int, int]:
            events.append("packages")
            return 1, 0

        def home(_staging_dir: Path, *, dry_run: bool) -> int:
            assert dry_run is False
            events.append("home")
            return 1

        def permissions(*, dry_run: bool) -> None:
            assert dry_run is False
            events.append("permissions")

        with (
            patch("popctl.backup.restore._fetch_backup", return_value=tmp_path / "backup.age"),
            patch(
                "popctl.backup.restore._decrypt_and_decompress",
                side_effect=_extract_manifest(manifest),
            ),
            patch("popctl.backup.restore._restore_popctl_state", side_effect=state),
            patch("popctl.backup.restore.run_source_phase", side_effect=source_phase),
            patch("popctl.backup.restore._install_packages", side_effect=install),
            patch("popctl.backup.restore._restore_home_files", side_effect=home),
            patch("popctl.backup.restore._fix_sensitive_permissions", side_effect=permissions),
        ):
            restore_backup("backup.age")

        assert events == [
            "state",
            "preflight",
            "source-writes",
            "apt-update",
            "packages",
            "home",
            "permissions",
        ]
