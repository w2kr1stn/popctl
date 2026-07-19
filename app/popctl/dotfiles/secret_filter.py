import ast
import base64
import configparser
import fnmatch
import json
import os
import re
import shlex
import stat
import tomllib
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import cast

import yaml
from yaml.nodes import MappingNode, Node, ScalarNode, SequenceNode

MAX_CANDIDATE_BYTES = 1_048_576
MAX_BASE64_DEPTH = 2
MIN_BASE64_RUN_LENGTH = 8

PATH_DENY_PATTERNS: tuple[str, ...] = (
    ".ssh/**",
    ".gnupg/**",
    ".gpg/**",
    ".config/age/**",
    ".local/share/keyrings/**",
    ".netrc",
    ".git-credentials",
    ".config/popctl/**",
    ".local/state/popctl/**",
    "**/id_rsa*",
    "**/id_ed25519*",
    "**/*.pem",
    ".config/google-chrome/**/Login Data",
    ".config/chromium/**/Login Data",
    ".config/BraveSoftware/**/Login Data",
    ".mozilla/firefox/**/logins.json",
    ".mozilla/firefox/**/key4.db",
)

AMBIGUOUS_CREDENTIAL_FIELDS = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "access_key",
        "client_secret",
        "private_key",
        "credential",
        "credentials",
    }
)

_PRIVATE_KEY_PATTERN = re.compile(
    rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY(?: BLOCK)?-----",
    re.IGNORECASE,
)
_AGE_SECRET_KEY_PATTERN = re.compile(rb"AGE-SECRET-KEY-1[0-9A-Z]+", re.IGNORECASE)
_AWS_ACCESS_KEY_PATTERN = re.compile(rb"(?:AKIA|ASIA)[0-9A-Z]{16}")
_GITHUB_TOKEN_PATTERN = re.compile(
    rb"(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{22,})"
)
_AUTHORIZATION_PATTERN = re.compile(
    rb"\bauthorization\s*:\s*(?:bearer|basic)\s+\S+",
    re.IGNORECASE,
)
_GIT_EXTRAHEADER_PATTERN = re.compile(
    rb"^\s*(?:http\..*\.extraheader|extraheader)\s*=\s*"
    rb"authorization\s*:\s*(?:bearer|basic)\s+\S+",
    re.IGNORECASE | re.MULTILINE,
)
_PROXY_AUTH_PATTERN = re.compile(
    rb"\bproxy-(?:authorization|auth)\s*:\s*(?:bearer|basic)\s+\S+",
    re.IGNORECASE,
)
_CREDENTIALED_PROXY_PATTERN = re.compile(
    rb"\b(?:https?_proxy|proxy)\s*=\s*\S*://[^/\s:@]+:[^/\s@]+@",
    re.IGNORECASE,
)
_CURL_USER_PATTERN = re.compile(
    rb"^\s*(?:user|proxy-user)\s*=\s*['\"]?[^:\s'\"]+:[^\s'\"]+",
    re.IGNORECASE | re.MULTILINE,
)
_URL_USERINFO_PATTERN = re.compile(
    rb"\b[a-z][a-z0-9+.-]*://[^/\s:@]+:[^/\s@]+@",
    re.IGNORECASE,
)
_BASE64_RUN_PATTERN = re.compile(
    rb"[A-Za-z0-9+/_=-](?:[A-Za-z0-9+/_=-]|[ \t\r\n\v\f]+(?=[A-Za-z0-9+/_=-]))*"
)
_ASCII_WHITESPACE_PATTERN = re.compile(rb"[ \t\r\n\v\f]+")
_SHELL_LINE_CONTINUATION_PATTERN = re.compile(r"\\\n")
_FIELD_NORMALIZATION_PATTERN = re.compile(r"[^a-z0-9]+")
_CREDENTIAL_SHAPED_FIELD_PATTERN = re.compile(
    r"(?:pass(?:word)?|secret|token|key|credential|auth)",
    re.IGNORECASE,
)
_RAW_ASSIGNMENT_PATTERN = re.compile(
    rb"(?m)^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_.-]*)\s*(?:=|:)\s*(.+?)\s*$"
)
_DOTENV_KEY_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_INI_ASSIGNMENT_PATTERN = re.compile(r"^\s*([^=:#\s][^=:#]*?)\s*(?:=|:)\s*(.*?)\s*$")
_TOML_ASSIGNMENT_PATTERN = re.compile(r"^\s*([A-Za-z0-9_.-]+)\s*=")
_TOML_TABLE_PATTERN = re.compile(r"^\s*\[\[?([^\]]+)\]?\]\s*$")
_HARD_RECOGNIZERS: tuple[tuple[str, re.Pattern[bytes]], ...] = (
    ("private-key", _PRIVATE_KEY_PATTERN),
    ("age-secret-key", _AGE_SECRET_KEY_PATTERN),
    ("aws-access-key-id", _AWS_ACCESS_KEY_PATTERN),
    ("github-token", _GITHUB_TOKEN_PATTERN),
    ("git-extraheader", _GIT_EXTRAHEADER_PATTERN),
    ("proxy-auth", _PROXY_AUTH_PATTERN),
    ("credentialed-proxy", _CREDENTIALED_PROXY_PATTERN),
    ("curl-user-password", _CURL_USER_PATTERN),
    ("authorization", _AUTHORIZATION_PATTERN),
)


class SecretVerdictKind(str, Enum):
    ALLOWED = "allowed"
    DENIED_PATH = "denied_path"
    DENIED_UNAMBIGUOUS_CONTENT = "denied_unambiguous_content"
    DENIED_AMBIGUOUS_CONTENT = "denied_ambiguous_content"
    DENIED_UNREADABLE = "denied_unreadable"
    DENIED_BINARY = "denied_binary"
    DENIED_OVERSIZE = "denied_oversize"


@dataclass(frozen=True, slots=True)
class SecretVerdict:
    kind: SecretVerdictKind
    category: str | None = None

    @property
    def allowed(self) -> bool:
        return self.kind is SecretVerdictKind.ALLOWED

    @property
    def allowlistable(self) -> bool:
        return self.kind is SecretVerdictKind.DENIED_AMBIGUOUS_CONTENT


@dataclass(frozen=True, slots=True)
class _ParserInspection:
    scalar_forms: tuple[bytes, ...] = ()
    pair_forms: tuple[bytes, ...] = ()
    ambiguous_fields: tuple[str, ...] = ()
    duplicate_credential_field: bool = False
    error_category: str | None = None


@dataclass(frozen=True, slots=True)
class _JsonObject:
    pairs: tuple[tuple[str, object], ...]


def scan_dotfile(
    path: Path,
    *,
    home: Path | None = None,
    ambiguous_content_allowlist: Collection[str] = (),
) -> SecretVerdict:
    home_path = (home or Path.home()).resolve(strict=False)
    candidate_path = path if path.is_absolute() else home_path / path
    try:
        path_stat = candidate_path.lstat()
        resolved_path = candidate_path.resolve(strict=True)
        relative_path = resolved_path.relative_to(home_path).as_posix()
    except (OSError, ValueError):
        return SecretVerdict(SecretVerdictKind.DENIED_UNREADABLE, "outside-home-or-unreadable")
    if stat.S_ISLNK(path_stat.st_mode) or not stat.S_ISREG(path_stat.st_mode):
        return SecretVerdict(SecretVerdictKind.DENIED_UNREADABLE, "non-regular-file")
    if path_stat.st_size > MAX_CANDIDATE_BYTES:
        return SecretVerdict(SecretVerdictKind.DENIED_OVERSIZE)

    try:
        flags = os.O_RDONLY | os.O_NOFOLLOW
        descriptor = os.open(candidate_path, flags)
    except OSError:
        return SecretVerdict(SecretVerdictKind.DENIED_UNREADABLE, "unreadable")
    try:
        with os.fdopen(descriptor, "rb", closefd=True) as file:
            opened_stat = os.fstat(file.fileno())
            if (
                not stat.S_ISREG(opened_stat.st_mode)
                or (opened_stat.st_dev, opened_stat.st_ino) != (path_stat.st_dev, path_stat.st_ino)
            ):
                return SecretVerdict(SecretVerdictKind.DENIED_UNREADABLE, "changed-file")
            content = file.read(MAX_CANDIDATE_BYTES + 1)
    except OSError:
        return SecretVerdict(SecretVerdictKind.DENIED_UNREADABLE, "unreadable")

    return scan_dotfile_bytes(
        relative_path,
        content,
        ambiguous_content_allowlist=ambiguous_content_allowlist,
    )


def scan_dotfile_bytes(
    home_relative_path: str,
    content: bytes,
    *,
    ambiguous_content_allowlist: Collection[str] = (),
) -> SecretVerdict:
    canonical_path = _canonical_relative_path(home_relative_path)
    if canonical_path is None:
        return SecretVerdict(SecretVerdictKind.DENIED_UNREADABLE, "non-canonical-path")
    for pattern in PATH_DENY_PATTERNS:
        if _matches_path_glob(canonical_path, pattern):
            return SecretVerdict(SecretVerdictKind.DENIED_PATH, pattern)
    if len(content) > MAX_CANDIDATE_BYTES:
        return SecretVerdict(SecretVerdictKind.DENIED_OVERSIZE)
    if b"\x00" in content:
        return SecretVerdict(SecretVerdictKind.DENIED_BINARY)
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return SecretVerdict(SecretVerdictKind.DENIED_BINARY)

    raw_form = _normalize_line_endings(content)
    raw_forms, raw_too_deep = _expand_base64_forms((raw_form,))
    hard_category = _first_hard_category(raw_forms)
    if hard_category is not None:
        return SecretVerdict(SecretVerdictKind.DENIED_UNAMBIGUOUS_CONTENT, hard_category)
    if raw_too_deep:
        return SecretVerdict(SecretVerdictKind.DENIED_UNAMBIGUOUS_CONTENT, "base64-nesting")

    raw_pairs = _raw_assignment_pairs(raw_form)
    hard_category = _hard_category_from_pairs(raw_pairs)
    if hard_category is not None:
        return SecretVerdict(SecretVerdictKind.DENIED_UNAMBIGUOUS_CONTENT, hard_category)

    inspection = _inspect_named_parser(canonical_path, text)
    if inspection.duplicate_credential_field:
        return SecretVerdict(
            SecretVerdictKind.DENIED_UNAMBIGUOUS_CONTENT,
            "duplicate-credential-field",
        )
    if inspection.error_category is not None:
        return SecretVerdict(
            SecretVerdictKind.DENIED_UNAMBIGUOUS_CONTENT,
            inspection.error_category,
        )

    scalar_forms, scalar_too_deep = _expand_base64_forms(inspection.scalar_forms)
    hard_category = _first_hard_category(scalar_forms)
    if hard_category is not None:
        return SecretVerdict(SecretVerdictKind.DENIED_UNAMBIGUOUS_CONTENT, hard_category)
    if scalar_too_deep:
        return SecretVerdict(SecretVerdictKind.DENIED_UNAMBIGUOUS_CONTENT, "base64-nesting")
    hard_category = _hard_category_from_pairs(inspection.pair_forms)
    if hard_category is not None:
        return SecretVerdict(SecretVerdictKind.DENIED_UNAMBIGUOUS_CONTENT, hard_category)

    ambiguous_category = _first_ambiguous_category(
        (*raw_forms, *scalar_forms),
        (*raw_pairs, *inspection.pair_forms),
        inspection.ambiguous_fields,
    )
    if ambiguous_category is not None:
        return _ambiguous_verdict(
            canonical_path,
            ambiguous_category,
            ambiguous_content_allowlist,
        )
    return SecretVerdict(SecretVerdictKind.ALLOWED)


def _canonical_relative_path(path: str) -> str | None:
    if not path or "\\" in path:
        return None
    pure_path = PurePosixPath(path)
    if pure_path.is_absolute() or pure_path == PurePosixPath("."):
        return None
    if any(part in {"", ".", ".."} for part in pure_path.parts):
        return None
    canonical_path = pure_path.as_posix()
    return canonical_path if canonical_path == path else None


def _matches_path_glob(path: str, pattern: str) -> bool:
    if fnmatch.fnmatchcase(path, pattern):
        return True
    if pattern.startswith("**/"):
        return fnmatch.fnmatchcase(path, pattern.removeprefix("**/"))
    return False


def _normalize_line_endings(value: bytes) -> bytes:
    return value.replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def _expand_base64_forms(initial_forms: Iterable[bytes]) -> tuple[tuple[bytes, ...], bool]:
    forms: list[bytes] = []
    seen: set[bytes] = set()
    frontier: list[bytes] = []
    for value in initial_forms:
        normalized = _normalize_line_endings(value)
        if len(normalized) <= MAX_CANDIDATE_BYTES and normalized not in seen:
            seen.add(normalized)
            forms.append(normalized)
            frontier.append(normalized)

    for _ in range(MAX_BASE64_DEPTH):
        next_frontier: list[bytes] = []
        for value in frontier:
            for candidate in _canonical_base64_candidates(value):
                decoded = _decode_canonical_base64(candidate)
                if decoded is None or len(decoded) > MAX_CANDIDATE_BYTES:
                    continue
                normalized = _normalize_line_endings(decoded)
                if normalized not in seen:
                    seen.add(normalized)
                    forms.append(normalized)
                    next_frontier.append(normalized)
        frontier = next_frontier

    for value in frontier:
        if any(True for _ in _canonical_base64_candidates(value)):
            return tuple(forms), True
    return tuple(forms), False


def _canonical_base64_candidates(value: bytes) -> Iterable[bytes]:
    candidates: set[bytes] = set()
    for match in _BASE64_RUN_PATTERN.finditer(value):
        candidate = _ASCII_WHITESPACE_PATTERN.sub(b"", match.group(0))
        if len(candidate) >= MIN_BASE64_RUN_LENGTH:
            candidates.add(candidate)
    for candidate in sorted(candidates):
        if (
            len(candidate) <= MAX_CANDIDATE_BYTES
            and _decode_canonical_base64(candidate) is not None
        ):
            yield candidate


def _decode_canonical_base64(candidate: bytes) -> bytes | None:
    if not candidate or b"=" in candidate.rstrip(b"="):
        return None
    unpadded = candidate.rstrip(b"=")
    if (b"-" in unpadded or b"_" in unpadded) and (b"+" in unpadded or b"/" in unpadded):
        return None
    remainder = len(unpadded) % 4
    if remainder == 1:
        return None
    padded = unpadded + b"=" * (-len(unpadded) % 4)
    urlsafe = b"-" in unpadded or b"_" in unpadded
    try:
        decoded = base64.b64decode(padded, altchars=b"-_" if urlsafe else None, validate=True)
    except ValueError:
        return None
    encoded = base64.urlsafe_b64encode(decoded) if urlsafe else base64.b64encode(decoded)
    if encoded.rstrip(b"=") != unpadded:
        return None
    if b"=" in candidate and encoded != candidate:
        return None
    return decoded


def _first_hard_category(forms: Iterable[bytes]) -> str | None:
    for value in forms:
        if _has_curl_credentials(value):
            return "curl-user-password"
        for category, pattern in _HARD_RECOGNIZERS:
            if pattern.search(value) is not None:
                return category
    return None


def _raw_assignment_pairs(value: bytes) -> tuple[bytes, ...]:
    return tuple(
        match.group(1) + b": " + match.group(2)
        for match in _RAW_ASSIGNMENT_PATTERN.finditer(value)
    )


def _hard_category_from_pairs(pairs: Iterable[bytes]) -> str | None:
    for pair in pairs:
        key, _, value = pair.partition(b":")
        field_name = _normalize_field_name(key.decode("utf-8", errors="ignore"))
        if field_name == "authorization":
            return "authorization"
        if field_name in {"proxy_authorization", "proxy_auth"} and _has_auth_scheme(value):
            return "proxy-auth"
        hard_category = _first_hard_category((pair,))
        if hard_category is not None:
            return hard_category
        for category, pattern in _HARD_RECOGNIZERS:
            if pattern.search(pair) is not None:
                return category
        alternate_pair = key + b" =" + value
        for category, pattern in _HARD_RECOGNIZERS:
            if pattern.search(alternate_pair) is not None:
                return category
    return None


def _has_curl_credentials(value: bytes) -> bool:
    logical_value = _SHELL_LINE_CONTINUATION_PATTERN.sub(
        "", value.decode("utf-8", errors="replace")
    )
    for line in logical_value.splitlines():
        arguments = _shell_arguments(line)
        for index, argument in enumerate(arguments):
            if argument.rsplit("/", 1)[-1] != "curl":
                continue
            if _curl_arguments_have_credentials(arguments[index + 1 :]):
                return True
    return False


def _shell_arguments(line: str) -> tuple[str, ...]:
    # Python shlex does not implement ANSI-C $'...' quoting; those exotic shell
    # embeddings remain a documented best-effort defense-in-depth residual.
    try:
        return tuple(shlex.split(line, posix=True))
    except ValueError:
        try:
            return tuple(shlex.split(line, posix=False))
        except ValueError:
            return tuple(line.split())


def _curl_arguments_have_credentials(arguments: tuple[str, ...]) -> bool:
    for index, argument in enumerate(arguments):
        credential = _curl_credential_option_value(arguments, index, argument)
        if credential is not None and _is_user_password(credential):
            return True
    return False


def _curl_credential_option_value(
    arguments: tuple[str, ...], index: int, argument: str
) -> str | None:
    if argument in {"-u", "-U", "--user", "--proxy-user"}:
        return _curl_next_option_value(arguments, index)
    if argument.startswith("--user="):
        return argument.removeprefix("--user=")
    if argument.startswith("--proxy-user="):
        return argument.removeprefix("--proxy-user=")
    if argument.startswith("-") and not argument.startswith("--"):
        short_options = argument[1:]
        for option_index, option in enumerate(short_options):
            if option in {"u", "U"}:
                attached = short_options[option_index + 1 :].removeprefix("=")
                return attached or _curl_next_option_value(arguments, index)
    return None


def _curl_next_option_value(arguments: tuple[str, ...], index: int) -> str | None:
    if index + 1 >= len(arguments):
        return None
    value = arguments[index + 1]
    if index + 2 < len(arguments) and arguments[index + 2].startswith(":"):
        value += arguments[index + 2]
    return value


def _is_user_password(value: str) -> bool:
    normalized = value.replace("'", "").replace('"', "")
    _, separator, password = normalized.partition(":")
    return bool(separator and password)


def _has_auth_scheme(value: bytes) -> bool:
    normalized = value.strip(b" \t")
    if (
        len(normalized) >= 2
        and normalized[:1] == normalized[-1:]
        and normalized[:1] in {b"'", b'"'}
    ):
        normalized = normalized[1:-1]
    normalized = normalized.strip(b" \t").lower()
    return normalized.startswith((b"bearer ", b"basic "))


def _inspect_named_parser(path: str, text: str) -> _ParserInspection:
    parser_kind = _parser_kind(path, text)
    if parser_kind is None:
        return _ParserInspection()
    try:
        if parser_kind == "json":
            return _inspect_json(text)
        if parser_kind == "yaml":
            return _inspect_yaml(text)
        if parser_kind == "toml":
            return _inspect_toml(text)
        if parser_kind == "dotenv":
            return _inspect_dotenv(text)
        return _inspect_ini(text)
    except (
        ValueError,
        SyntaxError,
        json.JSONDecodeError,
        tomllib.TOMLDecodeError,
        configparser.Error,
        yaml.YAMLError,
    ):
        return _ParserInspection(error_category=f"malformed-{parser_kind}")


def _parser_kind(path: str, text: str) -> str | None:
    name = PurePosixPath(path).name.lower()
    suffix = PurePosixPath(path).suffix.lower()
    if suffix == ".json" or text.lstrip().startswith("{"):
        return "json"
    if suffix in {".yaml", ".yml"}:
        return "yaml"
    if suffix == ".toml":
        return "toml"
    if name == ".env" or name.startswith(".env.") or suffix == ".env":
        return "dotenv"
    if suffix in {".ini", ".cfg"} or name in {".gitconfig", ".npmrc"}:
        return "ini"
    return None


def _inspect_json(text: str) -> _ParserInspection:
    def pairs_hook(pairs: list[tuple[str, object]]) -> _JsonObject:
        return _JsonObject(tuple(pairs))

    value = json.loads(text, object_pairs_hook=pairs_hook)
    scalars: list[bytes] = []
    pairs: list[bytes] = []
    ambiguous_fields: list[str] = []
    duplicate = _walk_json(value, scalars, pairs, ambiguous_fields)
    return _ParserInspection(
        tuple(scalars),
        tuple(pairs),
        tuple(ambiguous_fields),
        duplicate,
    )


def _walk_json(
    value: object,
    scalars: list[bytes],
    pairs: list[bytes],
    ambiguous_fields: list[str],
) -> bool:
    if isinstance(value, _JsonObject):
        seen_fields: set[str] = set()
        duplicate = False
        for key, nested_value in value.pairs:
            _append_scalar(scalars, key)
            normalized_key = _normalize_field_name(key)
            if _is_credential_shaped_field(normalized_key):
                if normalized_key in seen_fields:
                    duplicate = True
                seen_fields.add(normalized_key)
                ambiguous_fields.append(normalized_key)
            if _is_scalar(nested_value):
                pairs.append(_pair_form(key, nested_value))
            duplicate = _walk_json(nested_value, scalars, pairs, ambiguous_fields) or duplicate
        return duplicate
    if isinstance(value, list):
        items = cast("list[object]", value)
        return any(_walk_json(item, scalars, pairs, ambiguous_fields) for item in items)
    _append_scalar(scalars, value)
    return False


def _inspect_yaml(text: str) -> _ParserInspection:
    root = _compose_yaml(text)
    scalars: list[bytes] = []
    pairs: list[bytes] = []
    ambiguous_fields: list[str] = []
    duplicate = False
    if root is not None:
        duplicate = _walk_yaml_node(root, scalars, pairs, ambiguous_fields, set())
    if duplicate:
        return _ParserInspection(
            tuple(scalars),
            tuple(pairs),
            tuple(ambiguous_fields),
            duplicate_credential_field=True,
        )
    semantic_value = _safe_load_yaml(text)
    _collect_semantic_scalars(semantic_value, scalars)
    return _ParserInspection(
        tuple(scalars),
        tuple(pairs),
        tuple(ambiguous_fields),
        duplicate,
    )


def _compose_yaml(text: str) -> Node | None:
    result: object = yaml.compose(  # type: ignore[reportUnknownMemberType]
        text,
        Loader=yaml.SafeLoader,
    )
    return cast("Node | None", result)


def _safe_load_yaml(text: str) -> object:
    result: object = yaml.safe_load(text)  # type: ignore[reportUnknownMemberType]
    return result


def _walk_yaml_node(
    node: Node,
    scalars: list[bytes],
    pairs: list[bytes],
    ambiguous_fields: list[str],
    visited: set[int],
) -> bool:
    node_id = id(node)
    if node_id in visited:
        return False
    visited.add(node_id)
    if isinstance(node, ScalarNode):
        _append_scalar(scalars, node.value)
        return False
    if isinstance(node, SequenceNode):
        return any(
            _walk_yaml_node(item, scalars, pairs, ambiguous_fields, visited)
            for item in node.value
        )
    if isinstance(node, MappingNode):
        seen_fields: set[str] = set()
        duplicate = False
        for key_node, value_node in node.value:
            normalized_key: str | None = None
            if isinstance(key_node, ScalarNode):
                normalized_key = _normalize_field_name(key_node.value)
                if _is_credential_shaped_field(normalized_key):
                    if normalized_key in seen_fields:
                        duplicate = True
                    seen_fields.add(normalized_key)
                    ambiguous_fields.append(normalized_key)
                if isinstance(value_node, ScalarNode):
                    pairs.append(_pair_form(key_node.value, value_node.value))
            duplicate = (
                _walk_yaml_node(key_node, scalars, pairs, ambiguous_fields, visited) or duplicate
            )
            duplicate = (
                _walk_yaml_node(value_node, scalars, pairs, ambiguous_fields, visited) or duplicate
            )
        return duplicate
    return False


def _inspect_toml(text: str) -> _ParserInspection:
    duplicate = _has_duplicate_credential_assignment(_toml_assignment_keys(text))
    if duplicate:
        return _ParserInspection(duplicate_credential_field=True)
    value = tomllib.loads(text)
    scalars: list[bytes] = []
    pairs: list[bytes] = []
    ambiguous_fields: list[str] = []
    _walk_mapping(value, scalars, pairs, ambiguous_fields)
    return _ParserInspection(tuple(scalars), tuple(pairs), tuple(ambiguous_fields), duplicate)


def _inspect_dotenv(text: str) -> _ParserInspection:
    assignments = _parse_dotenv(text)
    duplicate = _has_duplicate_credential_assignment(key for key, _ in assignments)
    scalars: list[bytes] = []
    pairs: list[bytes] = []
    ambiguous_fields: list[str] = []
    for key, value in assignments:
        _append_scalar(scalars, key)
        _append_scalar(scalars, value)
        pairs.append(_pair_form(key, value))
        normalized_key = _normalize_field_name(key)
        if _is_credential_shaped_field(normalized_key):
            ambiguous_fields.append(normalized_key)
    return _ParserInspection(tuple(scalars), tuple(pairs), tuple(ambiguous_fields), duplicate)


def _parse_dotenv(text: str) -> tuple[tuple[str, str], ...]:
    assignments: list[tuple[str, str]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        assignment = stripped.removeprefix("export ").strip()
        key, separator, raw_value = assignment.partition("=")
        if not separator or _DOTENV_KEY_PATTERN.fullmatch(key.strip()) is None:
            raise ValueError("Malformed dotenv assignment")
        value = raw_value.strip()
        if value.startswith(("'", '"')):
            quote = value[0]
            if len(value) < 2 or not value.endswith(quote):
                raise ValueError("Unclosed dotenv quote")
            if quote == '"':
                decoded_value = ast.literal_eval(value)
                if not isinstance(decoded_value, str):
                    raise ValueError("Invalid dotenv value")
                value = decoded_value
            else:
                value = value[1:-1]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        assignments.append((key.strip(), value))
    return tuple(assignments)


def _inspect_ini(text: str) -> _ParserInspection:
    duplicate = _has_duplicate_credential_assignment(_ini_assignment_keys(text))
    if duplicate:
        return _ParserInspection(duplicate_credential_field=True)
    parser = configparser.ConfigParser(interpolation=None, strict=True, empty_lines_in_values=False)
    parser.optionxform = _preserve_option_case
    parser.read_string(text)
    scalars: list[bytes] = []
    pairs: list[bytes] = []
    ambiguous_fields: list[str] = []
    for section in parser.sections():
        for key, value in parser.items(section, raw=True):
            _append_scalar(scalars, key)
            _append_scalar(scalars, value)
            pairs.append(_pair_form(key, value))
            normalized_key = _normalize_field_name(key)
            if _is_credential_shaped_field(normalized_key):
                ambiguous_fields.append(normalized_key)
    return _ParserInspection(tuple(scalars), tuple(pairs), tuple(ambiguous_fields), duplicate)


def _preserve_option_case(optionstr: str) -> str:
    return optionstr


def _walk_mapping(
    value: object,
    scalars: list[bytes],
    pairs: list[bytes],
    ambiguous_fields: list[str],
) -> None:
    if isinstance(value, dict):
        mapping = cast("dict[object, object]", value)
        for key, nested_value in mapping.items():
            key_text = str(key)
            _append_scalar(scalars, key_text)
            normalized_key = _normalize_field_name(key_text)
            if _is_credential_shaped_field(normalized_key):
                ambiguous_fields.append(normalized_key)
            if _is_scalar(nested_value):
                pairs.append(_pair_form(key_text, nested_value))
            _walk_mapping(nested_value, scalars, pairs, ambiguous_fields)
    elif isinstance(value, list):
        items = cast("list[object]", value)
        for item in items:
            _walk_mapping(item, scalars, pairs, ambiguous_fields)
    else:
        _append_scalar(scalars, value)


def _collect_semantic_scalars(value: object, scalars: list[bytes]) -> None:
    if isinstance(value, dict):
        mapping = cast("dict[object, object]", value)
        for key, nested_value in mapping.items():
            _append_scalar(scalars, key)
            _collect_semantic_scalars(nested_value, scalars)
    elif isinstance(value, list):
        items = cast("list[object]", value)
        for item in items:
            _collect_semantic_scalars(item, scalars)
    else:
        _append_scalar(scalars, value)


def _pair_form(key: object, value: object) -> bytes:
    return _scalar_bytes(key) + b": " + _scalar_bytes(value)


def _append_scalar(destination: list[bytes], value: object) -> None:
    destination.append(_scalar_bytes(value))


def _scalar_bytes(value: object) -> bytes:
    if isinstance(value, bytes):
        return _normalize_line_endings(value)
    if value is None:
        return b"null"
    if isinstance(value, bool):
        return b"true" if value else b"false"
    return _normalize_line_endings(str(value).encode("utf-8"))


def _is_scalar(value: object) -> bool:
    return not isinstance(value, (dict, list, _JsonObject))


def _toml_assignment_keys(text: str) -> Iterable[str]:
    table = ""
    for line in text.splitlines():
        stripped = line.strip()
        table_match = _TOML_TABLE_PATTERN.fullmatch(stripped)
        if table_match is not None:
            table = table_match.group(1).strip()
            continue
        assignment_match = _TOML_ASSIGNMENT_PATTERN.match(line)
        if assignment_match is not None:
            yield f"{table}.{assignment_match.group(1)}" if table else assignment_match.group(1)


def _ini_assignment_keys(text: str) -> Iterable[str]:
    section = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1]
            continue
        match = _INI_ASSIGNMENT_PATTERN.match(line)
        if match is not None:
            yield f"{section}.{match.group(1)}" if section else match.group(1)


def _has_duplicate_credential_assignment(keys: Iterable[str]) -> bool:
    seen: set[str] = set()
    for key in keys:
        normalized_key = _normalize_field_name(key.rsplit(".", 1)[-1])
        if not _is_credential_shaped_field(normalized_key):
            continue
        if normalized_key in seen:
            return True
        seen.add(normalized_key)
    return False


def _first_ambiguous_category(
    forms: Iterable[bytes],
    pairs: Iterable[bytes],
    ambiguous_fields: Iterable[str],
) -> str | None:
    if any(_URL_USERINFO_PATTERN.search(value) is not None for value in forms):
        return "url-userinfo"
    for pair in pairs:
        key, _, _ = pair.partition(b":")
        normalized_key = _normalize_field_name(key.decode("utf-8", errors="ignore"))
        if normalized_key in AMBIGUOUS_CREDENTIAL_FIELDS:
            return normalized_key
        if _is_unknown_credential_shaped_field(normalized_key):
            return "credential-shaped-field"
    for field in ambiguous_fields:
        if field in AMBIGUOUS_CREDENTIAL_FIELDS:
            return field
        if _is_unknown_credential_shaped_field(field):
            return "credential-shaped-field"
    return None


def _ambiguous_verdict(
    canonical_path: str,
    category: str,
    allowlist: Collection[str],
) -> SecretVerdict:
    if _is_allowlisted(canonical_path, allowlist):
        return SecretVerdict(SecretVerdictKind.ALLOWED)
    return SecretVerdict(SecretVerdictKind.DENIED_AMBIGUOUS_CONTENT, category)


def _is_allowlisted(canonical_path: str, allowlist: Collection[str]) -> bool:
    return any(_canonical_relative_path(path) == canonical_path for path in allowlist)


def _normalize_field_name(field: str) -> str:
    return _FIELD_NORMALIZATION_PATTERN.sub("_", field.casefold()).strip("_")


def _is_credential_shaped_field(field: str) -> bool:
    return (
        field == "authorization"
        or field in AMBIGUOUS_CREDENTIAL_FIELDS
        or _is_unknown_credential_shaped_field(field)
    )


def _is_unknown_credential_shaped_field(field: str) -> bool:
    return (
        field not in AMBIGUOUS_CREDENTIAL_FIELDS
        and _CREDENTIAL_SHAPED_FIELD_PATTERN.search(field) is not None
    )
