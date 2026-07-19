import base64
import json
from pathlib import Path

import pytest
from popctl.dotfiles.secret_filter import (
    AMBIGUOUS_CREDENTIAL_FIELDS,
    MAX_CANDIDATE_BYTES,
    PATH_DENY_PATTERNS,
    SecretVerdict,
    SecretVerdictKind,
    scan_dotfile,
    scan_dotfile_bytes,
)


def _scan(path: str, content: bytes, allowlist: tuple[str, ...] = ()) -> SecretVerdict:
    return scan_dotfile_bytes(path, content, ambiguous_content_allowlist=allowlist)


def _assert_hard(path: str, content: bytes, category: str) -> None:
    verdict = _scan(path, content)

    assert verdict.kind is SecretVerdictKind.DENIED_UNAMBIGUOUS_CONTENT
    assert verdict.category == category


@pytest.mark.parametrize(
    ("pattern", "path"),
    [
        (".ssh/**", ".ssh/config"),
        (".gnupg/**", ".gnupg/gpg.conf"),
        (".gpg/**", ".gpg/options"),
        (".config/age/**", ".config/age/config"),
        (".local/share/keyrings/**", ".local/share/keyrings/login.keyring"),
        (".netrc", ".netrc"),
        (".git-credentials", ".git-credentials"),
        (".config/popctl/**", ".config/popctl/dotfiles.toml"),
        (".local/state/popctl/**", ".local/state/popctl/state.json"),
        ("**/id_rsa*", ".config/tool/id_rsa_backup"),
        ("**/id_ed25519*", ".config/tool/id_ed25519.pub"),
        ("**/*.pem", "certificate.pem"),
        (".config/google-chrome/**/Login Data", ".config/google-chrome/Default/Login Data"),
        (".config/chromium/**/Login Data", ".config/chromium/Profile 1/Login Data"),
        (
            ".config/BraveSoftware/**/Login Data",
            ".config/BraveSoftware/Brave-Browser/Default/Login Data",
        ),
        (".mozilla/firefox/**/logins.json", ".mozilla/firefox/abc.default/logins.json"),
        (".mozilla/firefox/**/key4.db", ".mozilla/firefox/abc.default/key4.db"),
    ],
)
def test_hard_path_globs_are_not_allowlistable(pattern: str, path: str) -> None:
    assert pattern in PATH_DENY_PATTERNS

    verdict = _scan(path, b"theme = dark", (path,))

    assert verdict.kind is SecretVerdictKind.DENIED_PATH
    assert verdict.category == pattern


@pytest.mark.parametrize(
    ("pattern", "path"),
    [
        (".ssh/**", ".sshh/config"),
        (".gnupg/**", ".gnupgg/gpg.conf"),
        (".gpg/**", ".gpgg/options"),
        (".config/age/**", ".config/ageing/config"),
        (".local/share/keyrings/**", ".local/share/keyrings-old/login.keyring"),
        (".netrc", ".netrc.example"),
        (".git-credentials", ".git-credential"),
        (".config/popctl/**", ".config/popctll/config.toml"),
        (".local/state/popctl/**", ".local/state/popctll/state.json"),
        ("**/id_rsa*", ".config/tool/not_id_rsa"),
        ("**/id_ed25519*", ".config/tool/not_id_ed25519"),
        ("**/*.pem", "certificate.pem.txt"),
        (".config/google-chrome/**/Login Data", ".config/google-chrome/Default/Login Data.old"),
        (".config/chromium/**/Login Data", ".config/chromium/Profile 1/Login Data.old"),
        (
            ".config/BraveSoftware/**/Login Data",
            ".config/BraveSoftware/Brave-Browser/Default/Login Data.old",
        ),
        (".mozilla/firefox/**/logins.json", ".mozilla/firefox/abc.default/logins.json.bak"),
        (".mozilla/firefox/**/key4.db", ".mozilla/firefox/abc.default/key4.db.bak"),
    ],
)
def test_path_glob_near_misses_are_not_denied_by_path_policy(pattern: str, path: str) -> None:
    assert pattern in PATH_DENY_PATTERNS
    content = (
        b'theme = "dark"'
        if path.endswith(".toml")
        else b"{}"
        if path.endswith(".json")
        else b"theme = dark"
    )

    verdict = _scan(path, content)

    assert verdict.kind is SecretVerdictKind.ALLOWED


@pytest.mark.parametrize(
    "content",
    [
        b"-----BEGIN RSA PRIVATE KEY-----\r\n",
        b"-----BEGIN EC PRIVATE KEY-----\r\n",
        b"-----BEGIN PGP PRIVATE KEY BLOCK-----\r\n",
        b"-----BEGIN OPENSSH PRIVATE KEY-----\r\n",
    ],
)
def test_private_key_variants_and_crlf_are_hard_denied(content: bytes) -> None:
    _assert_hard(".config/tool/config", content, "private-key")


@pytest.mark.parametrize(
    ("content", "category"),
    [
        (b"identity = AGE-SECRET-KEY-1ABCDEFG\r\n", "age-secret-key"),
        (b"aws = AKIA" + b"A" * 16 + b"\r\n", "aws-access-key-id"),
        (b"aws = ASIA" + b"A" * 16 + b"\r\n", "aws-access-key-id"),
        (b"token = ghp_" + b"a" * 36 + b"\r\n", "github-token"),
        (b"token = github_pat_" + b"a" * 22 + b"\r\n", "github-token"),
        (b"Authorization: Bearer opaque\r\n", "authorization"),
        (b"Authorization: Basic opaque\r\n", "authorization"),
        (b"  Authorization: Bearer opaque\r\n", "authorization"),
        (
            b"http.https://example.com/.extraHeader = Authorization: Bearer opaque\r\n",
            "git-extraheader",
        ),
        (b"extraHeader = Authorization: Basic opaque\r\n", "git-extraheader"),
        (b"Proxy-Authorization: Bearer opaque\r\n", "proxy-auth"),
        (b"https_proxy=http://user:password@example.com\r\n", "credentialed-proxy"),
        (b"user = user:password\r\n", "curl-user-password"),
        (b"proxy-user = 'user:password'\r\n", "curl-user-password"),
        (b'  user = "user:password"\r\n', "curl-user-password"),
        (b"curl --user alice:password https://example.invalid\r\n", "curl-user-password"),
        (b"curl -u=alice:password https://example.invalid\r\n", "curl-user-password"),
        (b"curl --proxy-user 'alice:password' https://example.invalid\r\n", "curl-user-password"),
    ],
)
def test_hard_content_grammars_are_crlf_tolerant(content: bytes, category: str) -> None:
    _assert_hard(".config/tool/config", content, category)


@pytest.mark.parametrize(
    "content",
    [
        b"-----BEGIN PUBLIC KEY-----\n",
        b"AGE-PUBLIC-KEY-1ABCDEFG\n",
        b"AKIA" + b"A" * 15 + b"\n",
        b"ghp_" + b"a" * 35 + b"\n",
        b"github_pat_" + b"a" * 21 + b"\n",
        b"extraHeader = X-Trace: opaque\n",
        b"https_proxy=http://example.com\n",
        b"user = username\n",
    ],
)
def test_hard_recognizer_near_misses_are_allowed(content: bytes) -> None:
    assert _scan(".config/tool/config", content).kind is SecretVerdictKind.ALLOWED


@pytest.mark.parametrize(
    ("path", "content", "category"),
    [
        (
            ".config/tool/config.json",
            b'{"authorization": "Bearer opaque"}',
            "authorization",
        ),
        (
            ".config/tool/config.yaml",
            b'authorization: "Bearer opaque"',
            "authorization",
        ),
        (
            ".config/tool/config.toml",
            b'authorization = "Bearer opaque"',
            "authorization",
        ),
        (
            ".config/tool/config.yaml",
            b"identity: >\n  AGE-SECRET-KEY-1ABCDEFG\n",
            "age-secret-key",
        ),
        (
            ".config/tool/config.json",
            b'{"identity": "AGE\\u002dSECRET\\u002dKEY\\u002d1ABCDEFG"}',
            "age-secret-key",
        ),
        (
            ".config/tool/config.yaml",
            b'identity: "AGE\\x2dSECRET\\x2dKEY\\x2d1ABCDEFG"',
            "age-secret-key",
        ),
    ],
)
def test_parser_decoded_hard_forms_are_not_allowlistable(
    path: str, content: bytes, category: str
) -> None:
    verdict = _scan(path, content, (path,))

    assert verdict.kind is SecretVerdictKind.DENIED_UNAMBIGUOUS_CONTENT
    assert verdict.category == category


def test_duplicate_json_pair_is_scanned_before_last_wins() -> None:
    content = b'{"identity": "AGE\\u002dSECRET\\u002dKEY\\u002d1ABCDEFG", "identity": "ok"}'

    _assert_hard(".config/tool/config.json", content, "age-secret-key")


@pytest.mark.parametrize(
    ("path", "content"),
    [
        (".config/tool/config.json", b'{"token": "old", "token": "new"}'),
        (".config/tool/config.yaml", b"token: old\ntoken: new\n"),
        (".env", b"TOKEN=old\nTOKEN=new\n"),
        (".config/tool/config.ini", b"[service]\ntoken = old\ntoken = new\n"),
        (".config/tool/config.toml", b"token = 'old'\ntoken = 'new'\n"),
    ],
)
def test_duplicate_credential_assignments_are_hard_denied_despite_allowlisting(
    path: str, content: bytes
) -> None:
    verdict = _scan(path, content, (path,))

    assert verdict.kind is SecretVerdictKind.DENIED_UNAMBIGUOUS_CONTENT
    assert verdict.category == "duplicate-credential-field"


@pytest.mark.parametrize(
    "field",
    sorted(AMBIGUOUS_CREDENTIAL_FIELDS),
)
def test_named_ambiguous_credential_fields_are_allowlistable(field: str) -> None:
    path = ".config/tool/config.json"
    content = json.dumps({field: "short"}).encode()

    blocked = _scan(path, content)
    allowed = _scan(path, content, (path,))

    assert blocked.kind is SecretVerdictKind.DENIED_AMBIGUOUS_CONTENT
    assert blocked.category == field
    assert allowed.kind is SecretVerdictKind.ALLOWED


@pytest.mark.parametrize(
    "field",
    [
        "theme",
        "font",
        "colour",
        "layout",
        "hostname",
        "timeout",
        "retries",
        "endpoint",
        "profile",
        "language",
        "timezone",
    ],
)
def test_noncredential_fields_are_not_caught_by_credential_field_policy(field: str) -> None:
    verdict = _scan(".config/tool/config.json", json.dumps({field: "short"}).encode())

    assert verdict.kind is SecretVerdictKind.ALLOWED


@pytest.mark.parametrize(
    ("path", "content"),
    [
        (".config/tool/config.json", b'{"customCredential": "short"}'),
        (".config/tool/config.yaml", b"custom_credential: short\n"),
        (".config/tool/config.toml", b"custom_credential = 'short'\n"),
        (".env", b"CUSTOM_CREDENTIAL=short\n"),
        (".config/tool/config.ini", b"[service]\ncustom_credential = short\n"),
    ],
)
def test_unknown_credential_shaped_fields_are_ambiguous(path: str, content: bytes) -> None:
    verdict = _scan(path, content)

    assert verdict.kind is SecretVerdictKind.DENIED_AMBIGUOUS_CONTENT
    assert verdict.category == "credential-shaped-field"


def test_json_and_yaml_decoded_keys_reenter_credential_field_scan() -> None:
    json_verdict = _scan(".config/tool/config.json", b'{"to\\u006ben": "short"}')
    yaml_verdict = _scan(".config/tool/config.yaml", b'"to\\x6ben": short')

    assert json_verdict.category == "token"
    assert yaml_verdict.category == "token"


def test_url_userinfo_is_ambiguous_and_uses_an_exact_canonical_allowlist_path() -> None:
    path = ".config/tool/config"
    content = b"endpoint = https://user:password@example.com/api"

    blocked = _scan(path, content)
    wrong_path = _scan(path, content, (".config/tool/other",))
    noncanonical_path = _scan(path, content, ("./.config/tool/config",))
    allowed = _scan(path, content, (path,))

    assert blocked.kind is SecretVerdictKind.DENIED_AMBIGUOUS_CONTENT
    assert blocked.category == "url-userinfo"
    assert wrong_path.kind is SecretVerdictKind.DENIED_AMBIGUOUS_CONTENT
    assert noncanonical_path.kind is SecretVerdictKind.DENIED_AMBIGUOUS_CONTENT
    assert allowed.kind is SecretVerdictKind.ALLOWED


@pytest.mark.parametrize(
    ("value", "category"),
    [
        (b"AGE-SECRET-KEY-1ABCDEFG", "age-secret-key"),
        (b"ASIA" + b"A" * 16, "aws-access-key-id"),
        (b"Authorization: Bearer opaque", "authorization"),
    ],
)
def test_single_base64_encoded_hard_content_is_denied(value: bytes, category: str) -> None:
    _assert_hard(".config/tool/config", base64.b64encode(value), category)


def test_double_base64_encoded_age_key_is_denied_and_deeper_nesting_fails_closed() -> None:
    encoded = base64.b64encode(b"AGE-SECRET-KEY-1ABCDEFG")
    double_encoded = base64.b64encode(encoded)
    triple_encoded = base64.b64encode(double_encoded)

    _assert_hard(".config/tool/config", double_encoded, "age-secret-key")
    verdict = _scan(".config/tool/config", triple_encoded, (".config/tool/config",))

    assert verdict.kind is SecretVerdictKind.DENIED_UNAMBIGUOUS_CONTENT
    assert verdict.category == "base64-nesting"


def test_base64_without_secret_content_is_allowed_at_each_supported_depth() -> None:
    encoded = base64.b64encode(b"okay")
    double_encoded = base64.b64encode(encoded)

    assert _scan(".config/tool/config", encoded).kind is SecretVerdictKind.ALLOWED
    assert _scan(".config/tool/config", double_encoded).kind is SecretVerdictKind.ALLOWED


def test_mime_wrapped_and_unpadded_base64_hard_content_is_denied() -> None:
    authorization = base64.b64encode(b"Authorization: Bearer opaque-value")
    mime_wrapped = b"\n".join(
        authorization[offset : offset + 8] for offset in range(0, len(authorization), 8)
    )
    unpadded_age = base64.b64encode(b"AGE-SECRET-KEY-1ABCDEFG").rstrip(b"=")

    _assert_hard(".config/tool/config", mime_wrapped, "authorization")
    _assert_hard(".config/tool/config", unpadded_age, "age-secret-key")


@pytest.mark.parametrize(
    ("path", "content", "category"),
    [
        (".config/tool/config.json", b'{"token": }', "malformed-json"),
        (".config/tool/config.yaml", b"token: [", "malformed-yaml"),
        (".config/tool/config.toml", b"token = [", "malformed-toml"),
        (".env", b"TOKEN\n", "malformed-dotenv"),
        (".config/tool/config.ini", b"token = x\n", "malformed-ini"),
    ],
)
def test_malformed_named_parser_input_is_terminal_and_not_allowlistable(
    path: str, content: bytes, category: str
) -> None:
    blocked = _scan(path, content)
    allowed = _scan(path, content, (path,))

    assert blocked.kind is SecretVerdictKind.DENIED_UNAMBIGUOUS_CONTENT
    assert blocked.category == category
    assert allowed == blocked


@pytest.mark.parametrize(
    ("path", "content", "category"),
    [
        (
            ".config/tool/config.json",
            b'{"identity":"AGE\\u002dSECRET\\u002dKEY\\u002d1ABCDEFG","broken":}',
            "malformed-json",
        ),
        (
            ".config/tool/config.yaml",
            b'identity: "AGE\\x2dSECRET\\x2dKEY\\x2d1ABCDEFG"\nbroken: [',
            "malformed-yaml",
        ),
        (
            ".config/tool/config.toml",
            b'identity = "AGE\\u002dSECRET\\u002dKEY\\u002d1ABCDEFG"\nbroken = [',
            "malformed-toml",
        ),
    ],
)
def test_malformed_escaped_hard_content_is_terminal_despite_exact_allowlist(
    path: str, content: bytes, category: str
) -> None:
    verdict = _scan(path, content, (path,))

    assert verdict.kind is SecretVerdictKind.DENIED_UNAMBIGUOUS_CONTENT
    assert verdict.category == category


def test_invalid_or_safe_content_has_no_implicit_allowlist_effect() -> None:
    safe = _scan(".config/tool/config.toml", b'theme = "dark"', (".config/tool/config.toml",))
    invalid_path = _scan(".config/../tool/config.toml", b'theme = "dark"')

    assert safe.kind is SecretVerdictKind.ALLOWED
    assert invalid_path.kind is SecretVerdictKind.DENIED_UNREADABLE


def test_file_inputs_fail_closed_for_unreadable_binary_oversize_symlink_and_nonregular(
    tmp_path: Path,
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    missing = home / ".config" / "missing"
    binary = home / "binary"
    binary.write_bytes(b"text\x00binary")
    symlink_target = home / "target"
    symlink_target.write_text("safe", encoding="utf-8")
    symlink = home / "symlink"
    symlink.symlink_to(symlink_target)
    directory = home / "directory"
    directory.mkdir()

    assert scan_dotfile(missing, home=home).kind is SecretVerdictKind.DENIED_UNREADABLE
    assert scan_dotfile(binary, home=home).kind is SecretVerdictKind.DENIED_BINARY
    assert scan_dotfile(symlink, home=home).kind is SecretVerdictKind.DENIED_UNREADABLE
    assert scan_dotfile(directory, home=home).kind is SecretVerdictKind.DENIED_UNREADABLE
    assert (
        _scan(".config/tool/config", b"x" * (MAX_CANDIDATE_BYTES + 1)).kind
        is SecretVerdictKind.DENIED_OVERSIZE
    )
