from pathlib import Path
from unittest.mock import patch

import pytest
from popctl.sources.keytrust import (
    KeyTrustError,
    capture_apt_keys,
    resolve_key_path,
    verify_public_material,
)
from popctl.sources.models import SignedByBinding
from popctl.utils.shell import CommandResult

FINGERPRINT_ONE = "0123456789ABCDEF0123456789ABCDEF01234567"
FINGERPRINT_TWO = "89ABCDEF0123456789ABCDEF0123456789ABCDEF"
LISTING = f"fpr:::::::::{FINGERPRINT_ONE}:\nfpr:::::::::{FINGERPRINT_TWO}:\n"


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
    assert export_args[-1] == FINGERPRINT_TWO
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
