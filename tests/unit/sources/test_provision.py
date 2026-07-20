from pathlib import Path
from unittest.mock import call, patch

import pytest
from popctl.models.package import PackageSource
from popctl.sources.keytrust import KeyTrustError, VerifiedPublicKey, capture_apt_keys
from popctl.sources.models import (
    AptKey,
    AptSource,
    AptSourceFormat,
    AptSources,
    FlatpakRemote,
    FlatpakScope,
    FlatpakSources,
    ReplayMode,
    SignedByBinding,
    SnapSources,
    SourcePlatform,
    SourcesConfig,
)
from popctl.sources.provision import (
    ProvisioningPaths,
    SourceProvisionChange,
    SourceProvisionStatus,
    provision_sources,
    render_managed_apt_stanza,
)
from popctl.utils.shell import CommandResult

FINGERPRINT = "A" * 40
SELECTED_FINGERPRINT = "B" * 40
ARMOR = "-----BEGIN PGP PUBLIC KEY BLOCK-----\nmaterial\n"
PRIMARY_WITH_SUBKEY_ARMOR = "-----BEGIN PGP PUBLIC KEY BLOCK-----\nprimary-with-subkey\n"


def _success() -> CommandResult:
    return CommandResult(stdout=ARMOR, stderr="", returncode=0)


def _paths(tmp_path: Path) -> ProvisioningPaths:
    return ProvisioningPaths(
        apt_keyrings_dir=tmp_path / "keyrings",
        apt_sources_dir=tmp_path / "sources.list.d",
    )


def _apt_key(paths: ProvisioningPaths, *, fingerprints: tuple[str, ...] = (FINGERPRINT,)) -> AptKey:
    return AptKey(
        id="vendor",
        target_path=str(paths.apt_keyrings_dir / "vendor.asc"),
        armor=ARMOR,
        fingerprints=fingerprints,
    )


def _apt_source(
    key: AptKey,
    *,
    replay_mode: ReplayMode = ReplayMode.REPLAY,
    source_format: AptSourceFormat = AptSourceFormat.DEB822,
    selectors: tuple[str, ...] = (),
    capture_path: str = "/etc/apt/sources.list.d/vendor.sources",
    managed_target: str = "popctl-vendor",
    ppa_display: str | None = None,
) -> AptSource:
    stanza = (
        "Types: deb\n"
        "URIs: https://packages.example.com/apt\n"
        "Suites: stable\n"
        "Components: main\n"
        "Signed-By: /etc/apt/keyrings/vendor.asc\n"
    )
    if source_format is AptSourceFormat.LEGACY:
        stanza = (
            "deb [signed-by=/etc/apt/keyrings/vendor.asc] "
            "https://packages.example.com/apt stable main\n"
        )
    return AptSource(
        id="vendor",
        capture_path=capture_path,
        format=source_format,
        ordinal=0,
        managed_target=managed_target,
        verbatim_stanza=stanza,
        key_ids=(key.id,),
        signed_by=SignedByBinding(
            key_paths=("/etc/apt/keyrings/vendor.asc",),
            fingerprint_selectors=selectors,
        ),
        replay_mode=replay_mode,
        ppa_display=ppa_display,
    )


def _sources(
    key: AptKey,
    source: AptSource,
    *,
    remotes: tuple[FlatpakRemote, ...] = (),
) -> SourcesConfig:
    return SourcesConfig(
        platform=SourcePlatform(distro_id="ubuntu", codename="noble"),
        apt=AptSources(entries=(source,), keys=(key,)),
        flatpak=FlatpakSources(remotes=remotes),
        snap=SnapSources(),
    )


def _verified(fingerprint: str = FINGERPRINT) -> VerifiedPublicKey:
    return VerifiedPublicKey(armor=ARMOR, fingerprints=(fingerprint,))


def _missing(source: AptSource) -> SourceProvisionChange:
    return SourceProvisionChange(
        locator=source.managed_target_locator,
        status=SourceProvisionStatus.MISSING,
    )


class TestAptProvisioning:
    def test_writes_root_owned_key_and_signed_by_stanza_then_strict_update(
        self, tmp_path: Path
    ) -> None:
        paths = _paths(tmp_path)
        key = _apt_key(paths)
        source = _apt_source(key)

        with (
            patch("popctl.sources.provision.verify_public_material", return_value=_verified()),
            patch("popctl.sources.provision.run_command", return_value=_success()) as run_command,
        ):
            result = provision_sources(
                _sources(key, source),
                changes=(_missing(source),),
                selected_managers=(PackageSource.APT,),
                paths=paths,
            )

        assert result.success is True
        key_install = run_command.call_args_list[0].args[0]
        assert key_install[:8] == ["sudo", "install", "-o", "root", "-g", "root", "-m", "0644"]
        assert key_install[-1] == str(paths.apt_keyrings_dir / "vendor.asc")
        assert run_command.call_args_list[-1] == call(
            ["sudo", "apt-get", "update", "--error-on=any"], timeout=300.0
        )
        assert str(paths.apt_sources_dir / "popctl-vendor.sources") in result.retained_artifacts

    def test_renders_legacy_and_deb822_signed_by_with_managed_key_path(
        self, tmp_path: Path
    ) -> None:
        paths = _paths(tmp_path)
        key = _apt_key(paths)
        deb822 = _apt_source(key)
        legacy = _apt_source(key, source_format=AptSourceFormat.LEGACY)

        assert f"Signed-By: {key.target_path}" in render_managed_apt_stanza(deb822, (key,))
        assert f"signed-by={key.target_path}" in render_managed_apt_stanza(legacy, (key,))

    def test_post_write_full_set_mismatch_fails_before_enabling_stanza(
        self, tmp_path: Path
    ) -> None:
        paths = _paths(tmp_path)
        key = _apt_key(paths)
        source = _apt_source(key)

        with (
            patch(
                "popctl.sources.provision.verify_public_material",
                side_effect=(_verified(), _verified(), _verified(SELECTED_FINGERPRINT)),
            ),
            patch("popctl.sources.provision.run_command", return_value=_success()) as run_command,
        ):
            result = provision_sources(
                _sources(key, source),
                changes=(_missing(source),),
                selected_managers=(PackageSource.APT,),
                paths=paths,
            )

        assert result.success is False
        assert result.error is not None
        assert "Installed APT key fingerprints" in result.error
        assert run_command.call_count == 2
        assert str(paths.apt_keyrings_dir / "vendor.asc") in result.retained_artifacts

    def test_post_write_selector_mismatch_fails_before_enabling_stanza(
        self, tmp_path: Path
    ) -> None:
        paths = _paths(tmp_path)
        key = _apt_key(paths)
        source = _apt_source(key, selectors=(SELECTED_FINGERPRINT + "!",))

        with (
            patch("popctl.sources.provision.verify_public_material", return_value=_verified()),
            patch("popctl.sources.provision.run_command", return_value=_success()) as run_command,
        ):
            result = provision_sources(
                _sources(key, source),
                changes=(_missing(source),),
                selected_managers=(PackageSource.APT,),
                paths=paths,
            )

        assert result.success is False
        assert result.error is not None
        assert "Signed-By binding" in result.error
        assert run_command.call_count == 2

    def test_selector_bound_key_records_full_export_and_passes_post_write_verification(
        self, tmp_path: Path
    ) -> None:
        paths = _paths(tmp_path)
        source_keyrings = tmp_path / "source-keyrings"
        source_keyrings.mkdir()
        source_key = source_keyrings / "vendor.gpg"
        source_key.write_text(PRIMARY_WITH_SUBKEY_ARMOR, encoding="utf-8")
        listing = (
            f"fpr:::::::::{FINGERPRINT}:\n"
            f"fpr:::::::::{SELECTED_FINGERPRINT}:\n"
        )

        def gpg_result(args: list[str]) -> CommandResult:
            if "--import" in args or "--export" in args:
                return CommandResult(stdout=PRIMARY_WITH_SUBKEY_ARMOR, stderr="", returncode=0)
            if "--list-keys" in args:
                return CommandResult(stdout=listing, stderr="", returncode=0)
            raise AssertionError(args)

        with patch("popctl.sources.keytrust.run_command", side_effect=gpg_result):
            binding, captured = capture_apt_keys(
                SignedByBinding(
                    key_paths=(str(source_key),), fingerprint_selectors=(FINGERPRINT + "!",)
                ),
                supported_roots=(source_keyrings,),
            )
        key = captured[0].model_copy(
            update={"target_path": str(paths.apt_keyrings_dir / "vendor.asc")}
        )
        source = _apt_source(key, selectors=binding.fingerprint_selectors)
        verified = VerifiedPublicKey(
            armor=PRIMARY_WITH_SUBKEY_ARMOR,
            fingerprints=(FINGERPRINT, SELECTED_FINGERPRINT),
        )

        with (
            patch("popctl.sources.provision.verify_public_material", return_value=verified),
            patch("popctl.sources.provision.run_command", return_value=_success()),
        ):
            result = provision_sources(
                _sources(key, source),
                changes=(_missing(source),),
                selected_managers=(PackageSource.APT,),
                paths=paths,
            )

        assert key.fingerprints == (FINGERPRINT, SELECTED_FINGERPRINT)
        assert result.success is True
        assert f"{FINGERPRINT}!" in render_managed_apt_stanza(source, (key,))

    def test_uses_one_writer_for_ppa_without_primary_file_or_global_trust(
        self, tmp_path: Path
    ) -> None:
        paths = _paths(tmp_path)
        key = _apt_key(paths)
        source = _apt_source(key, ppa_display="owner/ppa")

        with (
            patch("popctl.sources.provision.verify_public_material", return_value=_verified()),
            patch("popctl.sources.provision.run_command", return_value=_success()) as run_command,
        ):
            result = provision_sources(
                _sources(key, source),
                changes=(_missing(source),),
                selected_managers=(PackageSource.APT,),
                paths=paths,
            )

        assert result.success is True
        commands = [call.args[0] for call in run_command.call_args_list]
        assert all("add-apt-repository" not in command for command in commands)
        assert all("/etc/apt/sources.list" not in command for command in commands)
        assert all("trusted.gpg" not in " ".join(command) for command in commands)

    def test_matching_skips_writes_but_selected_apt_still_refreshes(self, tmp_path: Path) -> None:
        paths = _paths(tmp_path)
        key = _apt_key(paths)
        source = _apt_source(key)

        with patch("popctl.sources.provision.run_command", return_value=_success()) as run_command:
            result = provision_sources(
                _sources(key, source),
                changes=(),
                selected_managers=(PackageSource.APT,),
                paths=paths,
            )

        assert result.success is True
        run_command.assert_called_once_with(
            ["sudo", "apt-get", "update", "--error-on=any"], timeout=300.0
        )

    def test_changed_unmanaged_target_fails_without_duplicate_or_command(
        self, tmp_path: Path
    ) -> None:
        paths = _paths(tmp_path)
        key = _apt_key(paths)
        source = _apt_source(key)
        changed = SourceProvisionChange(
            locator=source.managed_target_locator,
            status=SourceProvisionStatus.CHANGED,
        )

        with patch("popctl.sources.provision.run_command") as run_command:
            result = provision_sources(
                _sources(key, source),
                changes=(changed,),
                selected_managers=(PackageSource.APT,),
                paths=paths,
            )

        assert result.success is False
        assert result.error is not None
        assert "unmanaged target" in result.error
        run_command.assert_not_called()

    def test_changed_operation_owned_target_replaces_both_managed_formats(
        self, tmp_path: Path
    ) -> None:
        paths = _paths(tmp_path)
        key = _apt_key(paths)
        source = _apt_source(key)
        changed = SourceProvisionChange(
            locator=source.managed_target_locator,
            status=SourceProvisionStatus.CHANGED,
            operation_owned=True,
        )

        with (
            patch("popctl.sources.provision.verify_public_material", return_value=_verified()),
            patch("popctl.sources.provision.run_command", return_value=_success()) as run_command,
        ):
            result = provision_sources(
                _sources(key, source),
                changes=(changed,),
                selected_managers=(PackageSource.APT,),
                paths=paths,
            )

        assert result.success is True
        commands = [call.args[0] for call in run_command.call_args_list]
        assert ["sudo", "rm", "-f", str(paths.apt_sources_dir / "popctl-vendor.list")] in commands
        assert [
            "sudo",
            "rm",
            "-f",
            str(paths.apt_sources_dir / "popctl-vendor.sources"),
        ] in commands
        assert commands.count(["sudo", "apt-get", "update", "--error-on=any"]) == 1

    def test_report_only_source_under_sources_list_d_never_reaches_writer(
        self, tmp_path: Path
    ) -> None:
        paths = _paths(tmp_path)
        key = _apt_key(paths)
        base = _apt_source(
            key,
            replay_mode=ReplayMode.REPORT_ONLY,
            capture_path="/etc/apt/sources.list.d/base.sources",
            managed_target="popctl-base",
        )
        vendor = _apt_source(key, managed_target="popctl-vendor")
        sources = SourcesConfig(
            platform=SourcePlatform(distro_id="ubuntu", codename="noble"),
            apt=AptSources(entries=(base, vendor), keys=(key,)),
            flatpak=FlatpakSources(),
            snap=SnapSources(),
        )

        with (
            patch("popctl.sources.provision.verify_public_material", return_value=_verified()),
            patch("popctl.sources.provision.run_command", return_value=_success()) as run_command,
        ):
            result = provision_sources(
                sources,
                changes=(_missing(base), _missing(vendor)),
                selected_managers=(PackageSource.APT,),
                paths=paths,
            )

        assert result.success is True
        commands = [" ".join(call.args[0]) for call in run_command.call_args_list]
        assert all("popctl-base" not in command for command in commands)
        assert any("popctl-vendor.sources" in command for command in commands)

    @pytest.mark.parametrize("failure_index", (0, 1, 2, 3))
    def test_apt_failures_report_owned_artifacts_at_each_command_boundary(
        self, tmp_path: Path, failure_index: int
    ) -> None:
        paths = _paths(tmp_path)
        key = _apt_key(paths)
        source = _apt_source(key)
        outcomes = [_success() for _ in range(failure_index)]
        outcomes.append(CommandResult(stdout="", stderr="failed", returncode=1))

        with (
            patch("popctl.sources.provision.verify_public_material", return_value=_verified()),
            patch("popctl.sources.provision.run_command", side_effect=outcomes),
        ):
            result = provision_sources(
                _sources(key, source),
                changes=(_missing(source),),
                selected_managers=(PackageSource.APT,),
                paths=paths,
            )

        assert result.success is False
        assert str(paths.apt_keyrings_dir / "vendor.asc") in result.retained_artifacts
        if failure_index >= 2:
            assert str(paths.apt_sources_dir / "popctl-vendor.sources") in result.retained_artifacts

    def test_insecure_and_legacy_sources_are_refused_without_commands(self, tmp_path: Path) -> None:
        paths = _paths(tmp_path)
        key = _apt_key(paths)
        insecure = _apt_source(key).model_copy(
            update={
                "verbatim_stanza": (
                    "Types: deb\n"
                    "Trusted: yes\n"
                    "Signed-By: /etc/apt/keyrings/vendor.asc\n"
                )
            }
        )
        legacy = _apt_source(key).model_copy(
            update={"key_ids": (), "signed_by": SignedByBinding()}
        )

        for source in (insecure, legacy):
            with (
                patch("popctl.sources.provision.verify_public_material", return_value=_verified()),
                patch("popctl.sources.provision.run_command") as run_command,
            ):
                result = provision_sources(
                    _sources(key, source),
                    changes=(_missing(source),),
                    selected_managers=(PackageSource.APT,),
                    paths=paths,
                )
            assert result.success is False
            run_command.assert_not_called()

    def test_allow_downgrade_to_insecure_is_refused_without_commands(self, tmp_path: Path) -> None:
        paths = _paths(tmp_path)
        key = _apt_key(paths)
        source = _apt_source(key).model_copy(
            update={
                "verbatim_stanza": (
                    f"{_apt_source(key).verbatim_stanza}Allow-Downgrade-To-Insecure: yes\n"
                )
            }
        )

        with patch("popctl.sources.provision.run_command") as run_command:
            result = provision_sources(
                _sources(key, source),
                changes=(_missing(source),),
                selected_managers=(PackageSource.APT,),
                paths=paths,
            )

        assert result.success is False
        assert "Insecure" in (result.error or "")
        run_command.assert_not_called()


class TestFlatpakProvisioning:
    def test_imports_exact_key_before_scoped_remote_add_without_apt(self) -> None:
        remote = FlatpakRemote(
            name="flathub-beta",
            scope=FlatpakScope.USER,
            url="https://example.com/flathub-beta.flatpakrepo",
            gpg_verify=True,
            gpg_key_armor=ARMOR,
            gpg_fingerprints=(FINGERPRINT,),
            replay_mode=ReplayMode.REPLAY,
        )
        sources = SourcesConfig(
            platform=SourcePlatform(distro_id="ubuntu", codename="noble"),
            apt=AptSources(),
            flatpak=FlatpakSources(remotes=(remote,)),
            snap=SnapSources(),
        )
        change = SourceProvisionChange(remote.locator, SourceProvisionStatus.MISSING)

        with (
            patch("popctl.sources.provision.verify_public_material", return_value=_verified()),
            patch("popctl.sources.provision.run_command", return_value=_success()) as run_command,
        ):
            result = provision_sources(
                sources,
                changes=(change,),
                selected_managers=(PackageSource.FLATPAK, PackageSource.SNAP),
            )

        assert result.success is True
        command = run_command.call_args.args[0]
        assert command[:3] == ["flatpak", "remote-add", "--if-not-exists"]
        assert "--user" in command
        import_index = next(
            index for index, item in enumerate(command) if item.startswith("--gpg-import=")
        )
        assert import_index < command.index(remote.name)
        assert all("apt-get" not in call.args[0] for call in run_command.call_args_list)

    def test_system_remote_uses_sudo_and_retains_remote_on_command_failure(self) -> None:
        remote = FlatpakRemote(
            name="vendor",
            scope=FlatpakScope.SYSTEM,
            url="https://example.com/vendor.flatpakrepo",
            gpg_verify=True,
            gpg_key_armor=ARMOR,
            gpg_fingerprints=(FINGERPRINT,),
            replay_mode=ReplayMode.REPLAY,
        )
        sources = SourcesConfig(
            platform=SourcePlatform(distro_id="ubuntu", codename="noble"),
            apt=AptSources(),
            flatpak=FlatpakSources(remotes=(remote,)),
            snap=SnapSources(),
        )

        with (
            patch("popctl.sources.provision.verify_public_material", return_value=_verified()),
            patch(
                "popctl.sources.provision.run_command",
                return_value=CommandResult(stdout="", stderr="failed", returncode=1),
            ) as run_command,
        ):
            result = provision_sources(
                sources,
                changes=(SourceProvisionChange(remote.locator, SourceProvisionStatus.MISSING),),
                selected_managers=(PackageSource.FLATPAK,),
            )

        assert result.success is False
        assert run_command.call_args.args[0][0] == "sudo"
        assert result.retained_artifacts == ("flatpak:system:vendor",)

    def test_raw_unverified_remote_is_refused_before_command(self) -> None:
        remote = FlatpakRemote(
            name="vendor",
            scope=FlatpakScope.USER,
            url="https://example.com/vendor.flatpakrepo",
            gpg_verify=True,
            gpg_key_armor=ARMOR,
            gpg_fingerprints=(FINGERPRINT,),
            replay_mode=ReplayMode.REPLAY,
        )
        sources = SourcesConfig(
            platform=SourcePlatform(distro_id="ubuntu", codename="noble"),
            apt=AptSources(),
            flatpak=FlatpakSources(remotes=(remote,)),
            snap=SnapSources(),
        )

        with (
            patch(
                "popctl.sources.provision.verify_public_material",
                side_effect=KeyTrustError("unverified"),
            ),
            patch("popctl.sources.provision.run_command") as run_command,
        ):
            result = provision_sources(
                sources,
                changes=(SourceProvisionChange(remote.locator, SourceProvisionStatus.MISSING),),
                selected_managers=(PackageSource.FLATPAK,),
            )

        assert result.success is False
        assert result.error is not None
        assert "verified public key" in result.error
        run_command.assert_not_called()
