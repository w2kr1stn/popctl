from pathlib import Path
from unittest.mock import patch

import pytest
from popctl.models.package import PackageSource
from popctl.sources.keytrust import (
    KeyTrustError,
    capture_apt_keys,
    decode_flatpakrepo_key,
    resolve_key_path,
    verify_public_material,
)
from popctl.sources.models import (
    AptKey,
    AptSource,
    AptSourceFormat,
    AptSources,
    ReplayMode,
    SignedByBinding,
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

FINGERPRINT_ONE = "0123456789ABCDEF0123456789ABCDEF01234567"
FINGERPRINT_TWO = "89ABCDEF0123456789ABCDEF0123456789ABCDEF"
LISTING = f"fpr:::::::::{FINGERPRINT_ONE}:\nfpr:::::::::{FINGERPRINT_TWO}:\n"
FIXTURE_DIRECTORY = Path(__file__).parents[2] / "fixtures" / "sources"
PUBLIC_KEY_FIXTURE = FIXTURE_DIRECTORY / "keytrust-multi-key-public.asc"
SECRET_KEY_FIXTURE = FIXTURE_DIRECTORY / "keytrust-multi-key-secret.asc"
PRIMARY_FINGERPRINT = "7035A19B6840739E8FFB99678B144BBE8E99DCE1"
SUBKEY_FINGERPRINT = "34EAD52484F43F953746ED268F61584EC465F895"


def _dearmor(fixture: Path, tmp_path: Path) -> bytes:
    binary = tmp_path / f"{fixture.stem}.gpg"
    result = run_command(
        ["gpg", "--batch", "--yes", "--dearmor", "--output", str(binary), str(fixture)]
    )

    assert result.success, result.stderr
    return binary.read_bytes()


def _gpg_result(args: list[str]) -> CommandResult:
    if "--import" in args:
        return CommandResult(stdout="", stderr="", returncode=0)
    if "--list-keys" in args:
        return CommandResult(stdout=LISTING, stderr="", returncode=0)
    if "--export" in args:
        return CommandResult(
            stdout="-----BEGIN PGP PUBLIC KEY BLOCK-----\nkey\n", stderr="", returncode=0
        )
    raise AssertionError(args)


def test_public_key_capture_exports_full_set_without_selector() -> None:
    with patch("popctl.sources.keytrust.run_command", side_effect=_gpg_result) as run:
        verified = verify_public_material(b"public material")

    assert verified.fingerprints == (FINGERPRINT_ONE, FINGERPRINT_TWO)
    export_args = next(call.args[0] for call in run.call_args_list if "--export" in call.args[0])
    assert export_args[-2:] == [FINGERPRINT_ONE, FINGERPRINT_TWO]


def test_public_key_capture_records_full_export_for_selector() -> None:
    with patch("popctl.sources.keytrust.run_command", side_effect=_gpg_result) as run:
        verified = verify_public_material(b"public material", selectors=(f"{FINGERPRINT_TWO}!",))

    assert verified.fingerprints == (FINGERPRINT_ONE, FINGERPRINT_TWO)
    export_args = next(call.args[0] for call in run.call_args_list if "--export" in call.args[0])
    assert export_args[-1] == FINGERPRINT_TWO + "!"
    assert FINGERPRINT_ONE not in export_args


def test_secret_packet_is_rejected_before_gpg_listing() -> None:
    with (
        patch("popctl.sources.keytrust.run_command") as run,
        pytest.raises(KeyTrustError, match="Secret OpenPGP"),
    ):
        verify_public_material(b"\xc5\x01\x04")

    run.assert_not_called()


def test_signed_by_symlink_must_resolve_to_regular_supported_keyring_file(tmp_path: Path) -> None:
    keyrings = tmp_path / "keyrings"
    keyrings.mkdir()
    target = keyrings / "vendor.gpg"
    target.write_bytes(b"public")
    accepted_link = keyrings / "accepted.gpg"
    accepted_link.symlink_to(target)
    external = tmp_path / "external.gpg"
    external.write_bytes(b"public")
    rejected_link = keyrings / "rejected.gpg"
    rejected_link.symlink_to(external)

    assert resolve_key_path(str(accepted_link), supported_roots=(keyrings,)) == target
    with pytest.raises(KeyTrustError, match="outside supported"):
        resolve_key_path(str(rejected_link), supported_roots=(keyrings,))


def test_capture_apt_keys_keeps_resolved_binding_and_verified_armor(tmp_path: Path) -> None:
    keyrings = tmp_path / "keyrings"
    keyrings.mkdir()
    key = keyrings / "vendor.gpg"
    key.write_bytes(b"public")
    binding = SignedByBinding(key_paths=(str(key),), fingerprint_selectors=(FINGERPRINT_ONE,))

    with patch("popctl.sources.keytrust.run_command", side_effect=_gpg_result):
        resolved, captured = capture_apt_keys(binding, supported_roots=(keyrings,))

    assert resolved.key_paths == (str(key),)
    assert resolved.fingerprint_selectors == (FINGERPRINT_ONE,)
    assert captured[0].fingerprints == (FINGERPRINT_ONE, FINGERPRINT_TWO)
    assert captured[0].armor.startswith("-----BEGIN PGP PUBLIC KEY BLOCK-----")


def test_selector_mismatch_fails_closed_for_post_write_verification() -> None:
    with (
        patch("popctl.sources.keytrust.run_command", side_effect=_gpg_result),
        pytest.raises(KeyTrustError, match="selector is absent"),
    ):
        verify_public_material(
            b"public material", selectors=("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA",)
        )


def test_real_gpg_fixture_exports_the_full_primary_and_subkey_set_in_armor_and_binary(
    tmp_path: Path, real_gpg: None
) -> None:
    armored = PUBLIC_KEY_FIXTURE.read_bytes()
    binary = _dearmor(PUBLIC_KEY_FIXTURE, tmp_path)

    assert verify_public_material(armored).fingerprints == (
        PRIMARY_FINGERPRINT,
        SUBKEY_FINGERPRINT,
    )
    assert verify_public_material(binary).fingerprints == (
        PRIMARY_FINGERPRINT,
        SUBKEY_FINGERPRINT,
    )


@pytest.mark.parametrize(
    ("selector", "expected_fingerprints"),
    (
        (
            PRIMARY_FINGERPRINT,
            (PRIMARY_FINGERPRINT, SUBKEY_FINGERPRINT),
        ),
        (PRIMARY_FINGERPRINT + "!", (PRIMARY_FINGERPRINT,)),
    ),
)
def test_real_gpg_fixture_selector_export_preserves_exact_selector_semantics(
    selector: str,
    expected_fingerprints: tuple[str, ...],
    real_gpg: None,
) -> None:
    verified = verify_public_material(PUBLIC_KEY_FIXTURE.read_bytes(), selectors=(selector,))

    assert verified.fingerprints == expected_fingerprints


def test_real_gpg_capture_resolves_accepted_symlinks_and_rejects_external_targets(
    tmp_path: Path, real_gpg: None
) -> None:
    keyrings = tmp_path / "keyrings"
    keyrings.mkdir()
    target = keyrings / "fixture.gpg"
    target.write_bytes(_dearmor(PUBLIC_KEY_FIXTURE, tmp_path))
    accepted_link = keyrings / "accepted.gpg"
    accepted_link.symlink_to(target)
    rejected_target = tmp_path / "external.gpg"
    rejected_target.write_bytes(target.read_bytes())
    rejected_link = keyrings / "rejected.gpg"
    rejected_link.symlink_to(rejected_target)

    binding, keys = capture_apt_keys(
        SignedByBinding(
            key_paths=(str(accepted_link),), fingerprint_selectors=(PRIMARY_FINGERPRINT + "!",)
        ),
        supported_roots=(keyrings,),
    )

    assert binding.key_paths == (str(target),)
    assert binding.fingerprint_selectors == (PRIMARY_FINGERPRINT + "!",)
    assert keys[0].fingerprints == (PRIMARY_FINGERPRINT,)
    with pytest.raises(KeyTrustError, match="outside supported"):
        capture_apt_keys(
            SignedByBinding(key_paths=(str(rejected_link),)), supported_roots=(keyrings,)
        )


def test_real_binary_mixed_public_and_secret_material_is_rejected_before_gpg_listing(
    tmp_path: Path, real_gpg: None
) -> None:
    mixed_material = _dearmor(PUBLIC_KEY_FIXTURE, tmp_path) + _dearmor(SECRET_KEY_FIXTURE, tmp_path)

    with (
        patch("popctl.sources.keytrust.run_command") as run,
        pytest.raises(KeyTrustError, match="Secret OpenPGP"),
    ):
        verify_public_material(mixed_material)

    run.assert_not_called()


def test_selector_variants_with_the_same_fingerprint_are_rejected_before_gpg() -> None:
    with (
        patch("popctl.sources.keytrust.run_command") as run,
        pytest.raises(KeyTrustError, match="unique"),
    ):
        verify_public_material(
            PUBLIC_KEY_FIXTURE.read_bytes(),
            selectors=(PRIMARY_FINGERPRINT, PRIMARY_FINGERPRINT + "!"),
        )

    run.assert_not_called()


def test_real_gpg_capture_embedded_public_material_uses_a_stable_embedded_key_id(
    real_gpg: None,
) -> None:
    binding, keys = capture_apt_keys(
        SignedByBinding(embedded_armor=PUBLIC_KEY_FIXTURE.read_text(encoding="utf-8"))
    )

    assert binding.key_paths == ()
    assert keys[0].id.startswith("embedded-")
    assert keys[0].fingerprints == (PRIMARY_FINGERPRINT, SUBKEY_FINGERPRINT)


def test_flatpak_repository_descriptor_key_decoding_rejects_invalid_base64() -> None:
    assert decode_flatpakrepo_key(" YWJj\nZA== ") == b"abcd"
    with pytest.raises(KeyTrustError, match="invalid GPGKey"):
        decode_flatpakrepo_key("not base64")


def test_real_gpg_post_write_fingerprint_mismatch_stops_before_stanza_write(
    tmp_path: Path, real_gpg: None
) -> None:
    paths = ProvisioningPaths(
        apt_keyrings_dir=tmp_path / "keyrings",
        apt_sources_dir=tmp_path / "sources.list.d",
    )
    full_key = verify_public_material(PUBLIC_KEY_FIXTURE.read_bytes())
    exact_key = verify_public_material(
        PUBLIC_KEY_FIXTURE.read_bytes(), selectors=(PRIMARY_FINGERPRINT + "!",)
    )
    key = AptKey(
        id="fixture",
        target_path=str(paths.apt_keyrings_dir / "fixture.asc"),
        armor=full_key.armor,
        fingerprints=full_key.fingerprints,
    )
    source = AptSource(
        id="fixture-source",
        capture_path="/etc/apt/sources.list.d/fixture.list",
        format=AptSourceFormat.LEGACY,
        ordinal=0,
        managed_target="popctl-fixture",
        verbatim_stanza=(
            f"deb [signed-by={key.target_path}] https://vendor.example/apt stable main\n"
        ),
        key_ids=(key.id,),
        signed_by=SignedByBinding(key_paths=(key.target_path,)),
        replay_mode=ReplayMode.REPLAY,
    )
    sources = SourcesConfig(
        platform=SourcePlatform(distro_id="ubuntu", codename="noble"),
        apt=AptSources(entries=(source,), keys=(key,)),
    )
    commands: list[list[str]] = []

    def command_recorder(args: list[str], *, timeout: float | None = None) -> CommandResult:
        commands.append(args)
        if args[:3] == ["sudo", "install", "-d"]:
            directory = Path(args[-1])
            directory.mkdir(parents=True, exist_ok=True)
            directory.chmod(0o755)
            return CommandResult(stdout="", stderr="", returncode=0)
        if args[:2] == ["sudo", "install"]:
            target = Path(args[-1])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(Path(args[-2]).read_text(encoding="utf-8"), encoding="utf-8")
            return CommandResult(stdout="", stderr="", returncode=0)
        if args[:2] == ["sudo", "cat"]:
            return CommandResult(stdout=exact_key.armor, stderr="", returncode=0)
        raise AssertionError(args)

    with patch("popctl.sources.provision.run_command", side_effect=command_recorder):
        result = provision_sources(
            sources,
            changes=(
                SourceProvisionChange(
                    locator=source.managed_target_locator,
                    status=SourceProvisionStatus.MISSING,
                ),
            ),
            selected_managers=(PackageSource.APT,),
            paths=paths,
        )

    assert result.success is False
    assert result.error == "Installed APT key fingerprints do not match the recorded key"
    assert paths.apt_keyrings_dir.is_dir()
    assert paths.apt_sources_dir.is_dir()
    assert commands[:2] == [
        [
            "sudo",
            "install",
            "-d",
            "-o",
            "root",
            "-g",
            "root",
            "-m",
            "0755",
            str(paths.apt_keyrings_dir),
        ],
        [
            "sudo",
            "install",
            "-d",
            "-o",
            "root",
            "-g",
            "root",
            "-m",
            "0755",
            str(paths.apt_sources_dir),
        ],
    ]
    assert not (paths.apt_sources_dir / "popctl-fixture.list").exists()
