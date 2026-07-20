from pathlib import Path
from unittest.mock import patch

import pytest
from popctl.models.package import PackageSource, SourceChoice
from popctl.sources.capture import (
    AptSourceParseError,
    CredentialedSourceError,
    FlatpakPaths,
    SourceCaptureError,
    capture_apt_sources,
    capture_flatpak_sources,
    capture_snap_sources,
    capture_sources,
    parse_apt_source_file,
    resolve_apt_candidate_origins,
)
from popctl.sources.keytrust import KeyTrustError, VerifiedPublicKey
from popctl.sources.models import (
    AptKey,
    AptSource,
    AptSourceFormat,
    FlatpakScope,
    ReplayMode,
    SignedByBinding,
    SnapChannel,
    SnapSources,
    SourcePlatform,
    SourcesConfig,
)
from popctl.sources.phase import SourceInteractionPolicy, capture_and_trust_sources
from popctl.utils.shell import CommandResult


@pytest.fixture
def platform() -> SourcePlatform:
    return SourcePlatform(distro_id="ubuntu", codename="noble")


@pytest.fixture
def apt_root(tmp_path: Path) -> Path:
    root = tmp_path / "etc" / "apt"
    (root / "sources.list.d").mkdir(parents=True)
    (root / "keyrings").mkdir()
    return root


def _captured_key(
    binding: SignedByBinding, **_: object
) -> tuple[SignedByBinding, tuple[AptKey, ...]]:
    return (
        binding,
        (
            AptKey(
                id="vendor-key",
                target_path="/etc/apt/keyrings/vendor-key.asc",
                armor="-----BEGIN PGP PUBLIC KEY BLOCK-----\nkey\n",
                fingerprints=("0123456789ABCDEF0123456789ABCDEF01234567",),
            ),
        ),
    )


def _archive_policy(uri: str, suite: str, *origins: str) -> str:
    return "".join(
        " 500 "
        f"{uri} {suite}/main amd64 Packages\n"
        f"     release o={origin},a={suite}\n"
        for origin in origins
    )


def test_apt_capture_preserves_legacy_options_comments_and_stanza(
    apt_root: Path, platform: SourcePlatform
) -> None:
    key_path = apt_root / "keyrings" / "vendor.gpg"
    key_path.write_bytes(b"public")
    source = apt_root / "sources.list"
    source.write_text(
        "# machine comment\n"
        f"deb [arch=amd64 signed-by={key_path}] https://packages.example.com stable main # keep\n",
        encoding="utf-8",
    )

    with patch("popctl.sources.capture.capture_apt_keys", side_effect=_captured_key):
        captured = capture_apt_sources(apt_root, platform)

    assert len(captured.entries) == 1
    entry = captured.entries[0]
    assert entry.format is AptSourceFormat.LEGACY
    assert entry.ordinal == 0
    assert entry.capture_path == str(source)
    assert entry.verbatim_stanza.endswith("# keep\n")
    assert entry.signed_by.key_paths == (str(key_path),)
    assert entry.managed_target == f"popctl-{entry.id}"
    assert entry.replay_mode is ReplayMode.REPLAY
    assert entry.key_ids == ("vendor-key",)


def test_legacy_exact_path_source_allows_omitted_component(tmp_path: Path) -> None:
    source = tmp_path / "vendor.list"
    source.write_text(
        "deb [signed-by=/etc/apt/keyrings/vendor.gpg] https://vendor.example/repo ./\n",
        encoding="utf-8",
    )

    descriptor = parse_apt_source_file(source)[0]

    assert descriptor.uris == ("https://vendor.example/repo",)
    assert descriptor.suites == ("./",)


def test_deb822_capture_classifies_base_and_replayable_sources_by_identity(
    apt_root: Path, platform: SourcePlatform
) -> None:
    key_path = apt_root / "keyrings" / "vendor.gpg"
    key_path.write_bytes(b"public")
    source = apt_root / "sources.list.d" / "system.sources"
    source.write_text(
        "# retained header comment\n"
        "Types: deb\n"
        "URIs: https://archive.ubuntu.com/ubuntu\n"
        "Suites: noble noble-updates\n"
        "Components: main\n"
        "Origin: Ubuntu\n"
        f"Signed-By: {key_path}\n"
        "\n"
        "Types: deb\n"
        "URIs: https://packages.example.com/linux\n"
        "Suites: stable\n"
        "Components: main\n"
        f"Signed-By: {key_path}\n"
        "\n"
        "Types: deb\n"
        "URIs: https://archive.ubuntu.com/ubuntu\n"
        "Suites: noble\n"
        "Components: main\n"
        "Origin: Unrecognized\n"
        f"Signed-By: {key_path}\n",
        encoding="utf-8",
    )

    policy = _archive_policy("https://archive.ubuntu.com/ubuntu", "noble", "Ubuntu")
    policy += _archive_policy("https://archive.ubuntu.com/ubuntu", "noble-updates", "Ubuntu")
    with (
        patch("popctl.sources.capture.capture_apt_keys", side_effect=_captured_key),
        patch(
            "popctl.sources.capture.run_command",
            return_value=CommandResult(stdout=policy, stderr="", returncode=0),
        ),
    ):
        captured = capture_apt_sources(apt_root, platform)

    assert [entry.replay_mode for entry in captured.entries] == [
        ReplayMode.REPORT_ONLY,
        ReplayMode.REPLAY,
        ReplayMode.REPORT_ONLY,
    ]
    assert captured.entries[0].verbatim_stanza.startswith("# retained header comment")
    assert captured.entries[0].ordinal == 0
    assert captured.entries[2].ordinal == 2
    assert captured.entries[0].capture_path.endswith("sources.list.d/system.sources")


@pytest.mark.parametrize(
    ("source_format", "stanza_origin", "policy_origins", "expected_mode"),
    [
        ("legacy", None, ("Ubuntu",), ReplayMode.REPORT_ONLY),
        ("deb822", None, ("Ubuntu",), ReplayMode.REPORT_ONLY),
        ("deb822", "Ubuntu", ("Unrecognized",), ReplayMode.REPLAY),
        ("deb822", "Ubuntu", ("Ubuntu", "Mirror"), ReplayMode.REPLAY),
        ("deb822", "Ubuntu", (), ReplayMode.REPLAY),
    ],
    ids=("legacy", "deb822", "wrong-origin", "ambiguous-origin", "absent-origin"),
)
def test_base_classification_uses_resolved_release_origin(
    apt_root: Path,
    platform: SourcePlatform,
    source_format: str,
    stanza_origin: str | None,
    policy_origins: tuple[str, ...],
    expected_mode: ReplayMode,
) -> None:
    key_path = apt_root / "keyrings" / "archive.gpg"
    key_path.write_bytes(b"public")
    if source_format == "legacy":
        source = apt_root / "sources.list"
        source.write_text(
            f"deb [signed-by={key_path}] https://archive.ubuntu.com/ubuntu noble main\n",
            encoding="utf-8",
        )
    else:
        source = apt_root / "sources.list.d" / "archive.sources"
        origin = f"Origin: {stanza_origin}\n" if stanza_origin is not None else ""
        source.write_text(
            "Types: deb\n"
            "URIs: https://archive.ubuntu.com/ubuntu\n"
            "Suites: noble\n"
            "Components: main\n"
            f"{origin}"
            f"Signed-By: {key_path}\n",
            encoding="utf-8",
        )

    policy = _archive_policy("https://archive.ubuntu.com/ubuntu", "noble", *policy_origins)
    with (
        patch("popctl.sources.capture.capture_apt_keys", side_effect=_captured_key),
        patch(
            "popctl.sources.capture.run_command",
            return_value=CommandResult(stdout=policy, stderr="", returncode=0),
        ) as run_command,
    ):
        captured = capture_apt_sources(apt_root, platform)

    assert captured.entries[0].replay_mode is expected_mode
    run_command.assert_called_once_with(["apt-cache", "policy"], timeout=10.0)


@pytest.mark.parametrize(
    ("distro_id", "codename", "uri", "suite", "origin"),
    [
        ("pop", "jammy", "https://apt.pop-os.org/ubuntu", "jammy", "Pop"),
        ("pop", "jammy", "https://apt.pop-os.org/release", "jammy", "Pop"),
        ("debian", "sid", "https://ftp.debian-ports.org/debian-ports", "sid", "Debian"),
    ],
    ids=("pop-ubuntu", "pop-release", "debian-ports"),
)
def test_canonical_pop_and_debian_ports_archives_are_report_only(
    apt_root: Path,
    distro_id: str,
    codename: str,
    uri: str,
    suite: str,
    origin: str,
) -> None:
    key_path = apt_root / "keyrings" / "archive.gpg"
    key_path.write_bytes(b"public")
    (apt_root / "sources.list").write_text(
        f"deb [signed-by={key_path}] {uri} {suite} main\n", encoding="utf-8"
    )
    platform = SourcePlatform(distro_id=distro_id, codename=codename)
    policy = _archive_policy(uri, suite, origin)

    with (
        patch("popctl.sources.capture.capture_apt_keys", side_effect=_captured_key),
        patch(
            "popctl.sources.capture.run_command",
            return_value=CommandResult(stdout=policy, stderr="", returncode=0),
        ),
    ):
        captured = capture_apt_sources(apt_root, platform)

    assert captured.entries[0].replay_mode is ReplayMode.REPORT_ONLY


def test_deb822_disabled_stanza_is_preserved_by_parser_and_not_captured(
    apt_root: Path, platform: SourcePlatform
) -> None:
    key_path = apt_root / "keyrings" / "vendor.gpg"
    key_path.write_bytes(b"public")
    source = apt_root / "sources.list.d" / "disabled.sources"
    source.write_text(
        "Types: deb\n"
        "URIs: https://disabled.example.com\n"
        "Suites: stable\n"
        "Components: main\n"
        "Enabled: no\n"
        "# preserved comment\n"
        f"Signed-By: {key_path}\n",
        encoding="utf-8",
    )

    parsed = parse_apt_source_file(source)
    with patch("popctl.sources.capture.capture_apt_keys", side_effect=_captured_key):
        captured = capture_apt_sources(apt_root, platform)

    assert parsed[0].enabled is False
    assert "Enabled: no" in parsed[0].verbatim
    assert captured.entries == ()


def test_deb822_embedded_signed_by_armor_preserves_blank_lines(tmp_path: Path) -> None:
    source = tmp_path / "embedded.sources"
    source.write_text(
        "Types: deb\n"
        "URIs: https://packages.example.com\n"
        "Suites: stable\n"
        "Components: main\n"
        "Signed-By:\n"
        " -----BEGIN PGP PUBLIC KEY BLOCK-----\n"
        " .\n"
        " key-material\n"
        " -----END PGP PUBLIC KEY BLOCK-----\n",
        encoding="utf-8",
    )

    descriptor = parse_apt_source_file(source)[0]

    assert descriptor.signed_by is not None
    assert descriptor.signed_by.embedded_armor == (
        "-----BEGIN PGP PUBLIC KEY BLOCK-----\n\nkey-material\n-----END PGP PUBLIC KEY BLOCK-----\n"
    )


def test_apt_capture_derives_ppa_display_metadata_and_managed_target(
    apt_root: Path, platform: SourcePlatform
) -> None:
    key_path = apt_root / "keyrings" / "ppa.gpg"
    key_path.write_bytes(b"public")
    source = apt_root / "sources.list.d" / "popctl-existing.sources"
    source.write_text(
        "Types: deb\n"
        "URIs: https://ppa.launchpadcontent.net/example/release/ubuntu\n"
        "Suites: noble\n"
        "Components: main\n"
        f"Signed-By: {key_path}\n",
        encoding="utf-8",
    )

    with patch("popctl.sources.capture.capture_apt_keys", side_effect=_captured_key):
        captured = capture_apt_sources(apt_root, platform)

    assert captured.entries[0].ppa_display == "example/release"
    assert captured.entries[0].managed_target == "popctl-existing"


@pytest.mark.parametrize(
    "source_line",
    [
        "deb [signed-by=/key.gpg login=user] https://packages.example.com stable main\n",
        "deb [signed-by=/key.gpg] https://user:secret@packages.example.com stable main\n",
        "deb [signed-by=/key.gpg] https://packages.example.com?token=secret stable main\n",
    ],
)
def test_apt_credential_gate_rejects_before_key_capture(
    apt_root: Path, platform: SourcePlatform, source_line: str
) -> None:
    (apt_root / "sources.list").write_text(source_line, encoding="utf-8")

    with (
        patch("popctl.sources.capture.capture_apt_keys") as captured_keys,
        pytest.raises(CredentialedSourceError),
    ):
        capture_apt_sources(apt_root, platform)

    captured_keys.assert_not_called()


def test_apt_auth_selector_gate_fails_without_serializing_sources(
    apt_root: Path, platform: SourcePlatform
) -> None:
    key_path = apt_root / "keyrings" / "vendor.gpg"
    key_path.write_bytes(b"public")
    (apt_root / "auth.conf").write_text(
        "machine packages.example.com/private login user password secret\n", encoding="utf-8"
    )
    (apt_root / "sources.list").write_text(
        f"deb [signed-by={key_path}] https://packages.example.com/private stable main\n",
        encoding="utf-8",
    )

    with (
        patch("popctl.sources.capture.capture_apt_keys") as captured_keys,
        pytest.raises(CredentialedSourceError, match="authentication selector"),
    ):
        capture_apt_sources(apt_root, platform)

    captured_keys.assert_not_called()


@pytest.mark.parametrize(
    ("selector", "uri"),
    [
        ("private.example:8443/repo", "https://private.example:8443/repository"),
        ("https://private.example/repo", "https://private.example/repository"),
        (
            "https://private.example:8443/repo",
            "https://private.example:8443/repository",
        ),
        ("https://private.example:not-a-port/repo", "https://private.example/repository"),
    ],
    ids=("port", "protocol", "protocol-port-path", "malformed"),
)
def test_apt_auth_selectors_reject_matching_or_malformed_sources_before_key_capture(
    apt_root: Path, platform: SourcePlatform, selector: str, uri: str
) -> None:
    key_path = apt_root / "keyrings" / "private.gpg"
    key_path.write_bytes(b"public")
    (apt_root / "auth.conf").write_text(
        f"machine {selector} login user password secret\n", encoding="utf-8"
    )
    (apt_root / "sources.list").write_text(
        f"deb [signed-by={key_path}] {uri} stable main\n", encoding="utf-8"
    )

    with (
        patch("popctl.sources.capture.capture_apt_keys") as captured_keys,
        pytest.raises(CredentialedSourceError),
    ):
        capture_apt_sources(apt_root, platform)

    captured_keys.assert_not_called()


def test_deb822_comment_only_paragraphs_are_discarded_before_ordinals(apt_root: Path) -> None:
    key_path = apt_root / "keyrings" / "vendor.gpg"
    key_path.write_bytes(b"public")
    source = apt_root / "sources.list.d" / "vendors.sources"
    source.write_text(
        "Types: deb\n"
        "URIs: https://one.example/apt\n"
        "Suites: stable\n"
        "Components: main\n"
        f"Signed-By: {key_path}\n"
        "\n"
        "# human separator\n"
        "\n"
        "Types: deb\n"
        "URIs: https://two.example/apt\n"
        "Suites: stable\n"
        "Components: main\n"
        f"Signed-By: {key_path}\n",
        encoding="utf-8",
    )

    descriptors = parse_apt_source_file(source)
    with patch("popctl.sources.capture.capture_apt_keys", side_effect=_captured_key):
        captured = capture_apt_sources(
            apt_root, SourcePlatform(distro_id="ubuntu", codename="noble")
        )

    assert [descriptor.ordinal for descriptor in descriptors] == [0, 1]
    assert [entry.ordinal for entry in captured.entries] == [0, 1]


def test_missing_gpg_becomes_a_source_capture_failure(
    apt_root: Path, platform: SourcePlatform
) -> None:
    key_path = apt_root / "keyrings" / "vendor.gpg"
    key_path.write_bytes(b"public")
    (apt_root / "sources.list").write_text(
        f"deb [signed-by={key_path}] https://vendor.example stable main\n", encoding="utf-8"
    )
    missing_gpg = CommandResult(stdout="", stderr="Command not found: gpg", returncode=-1)

    with (
        patch("popctl.sources.keytrust.run_command", return_value=missing_gpg),
        pytest.raises(SourceCaptureError, match="signing key material"),
    ):
        capture_apt_sources(apt_root, platform)


def test_unreadable_apt_auth_store_fails_closed_before_platform_capture(apt_root: Path) -> None:
    auth_directory = apt_root / "auth.conf.d"
    auth_directory.mkdir()
    auth_directory.chmod(0)
    (apt_root / "sources.list").write_text("# no source\n", encoding="utf-8")
    os_release = apt_root.parent / "os-release"
    os_release.write_text("ID=ubuntu\nVERSION_CODENAME=noble\n", encoding="utf-8")

    try:
        with (
            patch("popctl.sources.capture.capture_platform") as captured_platform,
            pytest.raises(SourceCaptureError, match="auth store is unreadable"),
        ):
            capture_sources(
                apt_root=apt_root,
                os_release_path=os_release,
                managers=(PackageSource.APT,),
            )
    finally:
        auth_directory.chmod(0o755)

    captured_platform.assert_not_called()


def test_malformed_apt_stanza_fails_closed(apt_root: Path, platform: SourcePlatform) -> None:
    source = apt_root / "sources.list.d" / "bad.sources"
    source.write_text("Types deb\nURIs: https://example.com\n", encoding="utf-8")

    with pytest.raises(AptSourceParseError):
        capture_apt_sources(apt_root, platform)


def test_apt_candidate_provenance_maps_ambiguous_and_unknown() -> None:
    binding = SignedByBinding(key_paths=("/etc/apt/keyrings/vendor.gpg",))
    vendor = AptSource(
        id="vendor",
        capture_path="/etc/apt/sources.list.d/vendor.list",
        format=AptSourceFormat.LEGACY,
        ordinal=0,
        managed_target="popctl-vendor",
        verbatim_stanza=(
            "deb [signed-by=/etc/apt/keyrings/vendor.gpg] https://vendor.example stable main\n"
        ),
        key_ids=("vendor",),
        signed_by=binding,
        replay_mode=ReplayMode.REPLAY,
    )
    duplicate = vendor.model_copy(
        update={
            "id": "duplicate",
            "capture_path": "/etc/apt/sources.list.d/duplicate.list",
            "managed_target": "popctl-duplicate",
        }
    )
    policy = (
        "pkg:\n"
        "  Candidate: 1.0\n"
        "  Version table:\n"
        " *** 1.0 500\n"
        "        500 https://vendor.example stable/main amd64 Packages\n"
    )

    with patch(
        "popctl.sources.capture.run_command",
        return_value=CommandResult(stdout=policy, stderr="", returncode=0),
    ):
        mapped = resolve_apt_candidate_origins(["pkg"], [vendor])
        ambiguous = resolve_apt_candidate_origins(["pkg"], [vendor, duplicate])

    assert mapped == {"pkg": vendor.capture_locator}
    assert ambiguous == {"pkg": "unknown"}

    with patch(
        "popctl.sources.capture.run_command",
        return_value=CommandResult(stdout="pkg:\n  Candidate: (none)\n", stderr="", returncode=0),
    ):
        unknown = resolve_apt_candidate_origins(["pkg"], [vendor])

    assert unknown == {"pkg": "unknown"}


def _flatpak_result(args: list[str]) -> CommandResult:
    if args[:2] == ["flatpak", "remotes"]:
        if "--user" in args:
            return CommandResult(
                stdout="flathub\thttps://dl.flathub.org/repo/flathub.flatpakrepo\tgpg-verify\n",
                stderr="",
                returncode=0,
            )
        return CommandResult(
            stdout="vendor\thttps://vendor.example/repo.flatpakrepo\tgpg-verify\n",
            stderr="",
            returncode=0,
        )
    if args[:2] == ["flatpak", "list"]:
        if "--user" in args:
            return CommandResult(
                stdout="org.example.App\tflathub\tx86_64\tstable\n", stderr="", returncode=0
            )
        return CommandResult(
            stdout="org.example.App\tvendor\taarch64\tbeta\n", stderr="", returncode=0
        )
    if args[0] == "gpg":
        return CommandResult(
            stdout="-----BEGIN PGP PUBLIC KEY BLOCK-----\nkey\n", stderr="", returncode=0
        )
    raise AssertionError(args)


def test_flatpak_capture_uses_scope_local_keyrings_and_app_contexts(tmp_path: Path) -> None:
    paths = FlatpakPaths(user_repo=tmp_path / "user-repo", system_repo=tmp_path / "system-repo")
    verified = VerifiedPublicKey(
        armor="-----BEGIN PGP PUBLIC KEY BLOCK-----\nkey\n",
        fingerprints=("0123456789ABCDEF0123456789ABCDEF01234567",),
    )
    with (
        patch("popctl.sources.capture.command_exists", return_value=True),
        patch("popctl.sources.capture.run_command", side_effect=_flatpak_result) as run,
        patch("popctl.sources.capture.verify_public_material", return_value=verified),
    ):
        captured = capture_flatpak_sources(paths)

    assert [(remote.scope, remote.name) for remote in captured.remotes] == [
        (FlatpakScope.USER, "flathub"),
        (FlatpakScope.SYSTEM, "vendor"),
    ]
    assert [(app.scope, app.origin, app.branch) for app in captured.apps] == [
        (FlatpakScope.USER, "flathub", "stable"),
        (FlatpakScope.SYSTEM, "vendor", "beta"),
    ]
    gpg_commands = [call.args[0] for call in run.call_args_list if call.args[0][0] == "gpg"]
    gpg_keyrings = [command[command.index("--keyring") + 1] for command in gpg_commands]
    assert gpg_keyrings == [
        str(paths.user_repo / "flathub.trustedkeys.gpg"),
        str(paths.system_repo / "vendor.trustedkeys.gpg"),
    ]
    for command in gpg_commands:
        assert "--homedir" in command
        assert "--no-options" in command
        assert "--no-default-keyring" in command


def test_flatpak_descriptor_key_is_validated_fallback(tmp_path: Path) -> None:
    paths = FlatpakPaths(user_repo=tmp_path / "user-repo", system_repo=tmp_path / "system-repo")
    verified = VerifiedPublicKey(
        armor="-----BEGIN PGP PUBLIC KEY BLOCK-----\nkey\n",
        fingerprints=("0123456789ABCDEF0123456789ABCDEF01234567",),
    )

    def result(args: list[str]) -> CommandResult:
        if args[:2] == ["flatpak", "remotes"]:
            return CommandResult(
                stdout="vendor\thttps://vendor.example/repo.flatpakrepo\tgpg-verify\n",
                stderr="",
                returncode=0,
            )
        if args[:2] == ["flatpak", "list"]:
            return CommandResult(stdout="", stderr="", returncode=0)
        return CommandResult(stdout="", stderr="no keyring", returncode=2)

    with (
        patch("popctl.sources.capture.command_exists", return_value=True),
        patch("popctl.sources.capture.run_command", side_effect=result),
        patch(
            "popctl.sources.capture._load_flatpakrepo_key", return_value=b"descriptor-key"
        ) as load_key,
        patch("popctl.sources.capture.verify_public_material", return_value=verified),
    ):
        captured = capture_flatpak_sources(paths)

    assert len(captured.remotes) == 2
    assert load_key.call_count == 2


def test_flatpak_key_capture_and_authenticator_fail_closed(tmp_path: Path) -> None:
    paths = FlatpakPaths(user_repo=tmp_path / "user-repo", system_repo=tmp_path / "system-repo")

    def failed_keyring(args: list[str]) -> CommandResult:
        if args[0] == "gpg":
            return CommandResult(stdout="", stderr="no keyring", returncode=2)
        return CommandResult(
            stdout="vendor\thttps://vendor.example/repo.flatpakrepo\tgpg-verify\n",
            stderr="",
            returncode=0,
        )

    with (
        patch("popctl.sources.capture.command_exists", return_value=True),
        patch(
            "popctl.sources.capture.run_command",
            side_effect=failed_keyring,
        ),
        patch("popctl.sources.capture._load_flatpakrepo_key", side_effect=KeyTrustError("bad key")),
        pytest.raises(SourceCaptureError, match="no verified public key"),
    ):
        capture_flatpak_sources(paths)

    with (
        patch("popctl.sources.capture.command_exists", return_value=True),
        patch(
            "popctl.sources.capture.run_command",
            return_value=CommandResult(
                stdout="vendor\thttps://vendor.example/repo.flatpakrepo\tauthenticator-name=oauth\n",
                stderr="",
                returncode=0,
            ),
        ),
        pytest.raises(CredentialedSourceError, match="Authenticated Flatpak"),
    ):
        capture_flatpak_sources(paths)


def test_flatpak_no_gpg_verify_is_captured_as_blocked(tmp_path: Path) -> None:
    paths = FlatpakPaths(user_repo=tmp_path / "user-repo", system_repo=tmp_path / "system-repo")
    verified = VerifiedPublicKey(
        armor="-----BEGIN PGP PUBLIC KEY BLOCK-----\nkey\n",
        fingerprints=("0123456789ABCDEF0123456789ABCDEF01234567",),
    )

    def no_gpg_verify_remote(args: list[str]) -> CommandResult:
        if args[:2] == ["flatpak", "remotes"]:
            return CommandResult(
                stdout="vendor\thttps://vendor.example/repo.flatpakrepo\tno-gpg-verify\n",
                stderr="",
                returncode=0,
            )
        if args[:2] == ["flatpak", "list"]:
            return CommandResult(stdout="", stderr="", returncode=0)
        if args[0] == "gpg":
            return CommandResult(
                stdout="-----BEGIN PGP PUBLIC KEY BLOCK-----\nkey\n",
                stderr="",
                returncode=0,
            )
        raise AssertionError(args)

    with (
        patch("popctl.sources.capture.command_exists", return_value=True),
        patch("popctl.sources.capture.run_command", side_effect=no_gpg_verify_remote),
        patch("popctl.sources.capture.verify_public_material", return_value=verified),
    ):
        captured = capture_flatpak_sources(paths)

    assert len(captured.remotes) == 2
    assert all(remote.gpg_verify is False for remote in captured.remotes)
    assert all(remote.replay_mode is ReplayMode.BLOCKED for remote in captured.remotes)

    sources = SourcesConfig(
        platform=SourcePlatform(distro_id="ubuntu", codename="noble"),
        flatpak=captured,
    )
    with (
        patch("popctl.sources.phase.capture_sources", return_value=sources),
        patch("popctl.sources.phase.typer.confirm") as confirm,
    ):
        trusted = capture_and_trust_sources(
            SourceChoice.ALL,
            dry_run=False,
            interaction=SourceInteractionPolicy(interactive=True),
        )

    assert trusted.success is False
    assert trusted.sources is None
    assert "blocked" in (trusted.error or "")
    confirm.assert_not_called()


def test_flatpak_capture_rejects_an_app_with_an_empty_branch(tmp_path: Path) -> None:
    paths = FlatpakPaths(user_repo=tmp_path / "user-repo", system_repo=tmp_path / "system-repo")
    verified = VerifiedPublicKey(
        armor="-----BEGIN PGP PUBLIC KEY BLOCK-----\nkey\n",
        fingerprints=("0123456789ABCDEF0123456789ABCDEF01234567",),
    )

    def malformed_list(args: list[str]) -> CommandResult:
        if args[:2] == ["flatpak", "list"] and "--user" in args:
            return CommandResult(
                stdout="org.example.App\tflathub\tx86_64\t\n", stderr="", returncode=0
            )
        return _flatpak_result(args)

    with (
        patch("popctl.sources.capture.command_exists", return_value=True),
        patch("popctl.sources.capture.run_command", side_effect=malformed_list),
        patch("popctl.sources.capture.verify_public_material", return_value=verified),
        pytest.raises(SourceCaptureError, match="Malformed flatpak list output"),
    ):
        capture_flatpak_sources(paths)


def test_snap_capture_uses_tracking_channel_and_skips_runtime_snaps() -> None:
    output = (
        "Name Version Rev Tracking Publisher Notes\n"
        "firefox 128 1 latest/edge mozilla -\n"
        "core22 1 2 latest/stable canonical base\n"
    )
    with (
        patch("popctl.sources.capture.command_exists", return_value=True),
        patch(
            "popctl.sources.capture.run_command",
            return_value=CommandResult(stdout=output, stderr="", returncode=0),
        ),
    ):
        captured = capture_snap_sources()

    assert captured.packages[0].name == "firefox"
    assert captured.packages[0].channel == "latest/edge"
    assert captured.packages[0].replay_mode is ReplayMode.REPLAY


def test_capture_sources_dispatches_only_selected_snap_with_an_authenticated_apt_present(
    apt_root: Path, tmp_path: Path
) -> None:
    (apt_root / "sources.list").write_text(
        "deb https://user:secret@vendor.example/apt stable main\n",
        encoding="utf-8",
    )
    os_release = tmp_path / "os-release"
    os_release.write_text("ID=ubuntu\nVERSION_CODENAME=noble\n", encoding="utf-8")
    snap = SnapSources(
        packages=(
            SnapChannel(name="hello", channel="latest/edge", replay_mode=ReplayMode.REPLAY),
        )
    )

    with (
        patch(
            "popctl.sources.capture.capture_apt_sources",
            side_effect=SourceCaptureError("authenticated APT source"),
        ) as apt,
        patch("popctl.sources.capture.capture_flatpak_sources") as flatpak,
        patch("popctl.sources.capture.capture_snap_sources", return_value=snap) as snap_capture,
    ):
        captured = capture_sources(
            apt_root=apt_root,
            os_release_path=os_release,
            managers=(PackageSource.SNAP,),
        )

    assert captured.apt.entries == ()
    assert captured.flatpak.remotes == ()
    assert captured.snap == snap
    apt.assert_not_called()
    flatpak.assert_not_called()
    snap_capture.assert_called_once()
