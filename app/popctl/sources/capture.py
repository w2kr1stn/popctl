import configparser
import hashlib
import shlex
from collections.abc import Iterable
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.request import urlopen

from popctl.core.paths import get_data_dir
from popctl.models.package import PackageSource
from popctl.sources.keytrust import (
    DEFAULT_APT_KEYRING_ROOTS,
    KeyTrustError,
    VerifiedPublicKey,
    capture_apt_keys,
    decode_flatpakrepo_key,
    verify_public_material,
)
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
    SnapChannel,
    SnapSources,
    SourceLocator,
    SourcePlatform,
    SourcesConfig,
)
from popctl.utils.shell import CommandResult, command_exists, run_command

APT_ROOT = Path("/etc/apt")
OS_RELEASE_PATH = Path("/etc/os-release")
SYSTEM_FLATPAK_REPO = Path("/var/lib/flatpak/repo")

_AUTH_OPTION_NAMES = frozenset(
    {
        "auth",
        "auth-conf",
        "client-cert",
        "client-key",
        "login",
        "password",
        "token",
        "username",
    }
)
_INSECURE_APT_OPTION_NAMES = frozenset(
    {"allow-downgrade-to-insecure", "allow-insecure", "trusted"}
)
_FLATPAK_AUTH_OPTION_PREFIXES = ("authenticator", "credential", "token", "password")
_ORIGIN_FIELD = "origin"


@dataclass(frozen=True, slots=True)
class CanonicalArchive:
    uris: frozenset[str]
    origins: frozenset[str]


CANONICAL_BASE_ARCHIVES: dict[str, CanonicalArchive] = {
    "debian": CanonicalArchive(
        uris=frozenset(
            {
                "http://deb.debian.org/debian",
                "https://deb.debian.org/debian",
                "http://security.debian.org/debian-security",
                "https://security.debian.org/debian-security",
                "http://deb.debian.org/debian-security",
                "https://deb.debian.org/debian-security",
                "http://deb.debian.org/debian-ports",
                "https://deb.debian.org/debian-ports",
                "http://ftp.debian-ports.org/debian-ports",
                "https://ftp.debian-ports.org/debian-ports",
            }
        ),
        origins=frozenset({"debian"}),
    ),
    "ubuntu": CanonicalArchive(
        uris=frozenset(
            {
                "http://archive.ubuntu.com/ubuntu",
                "https://archive.ubuntu.com/ubuntu",
                "http://security.ubuntu.com/ubuntu",
                "https://security.ubuntu.com/ubuntu",
                "http://ports.ubuntu.com/ubuntu-ports",
                "https://ports.ubuntu.com/ubuntu-ports",
            }
        ),
        origins=frozenset({"ubuntu"}),
    ),
    "pop": CanonicalArchive(
        uris=frozenset(
            {
                "http://apt.pop-os.org/proprietary",
                "https://apt.pop-os.org/proprietary",
                "http://apt.pop-os.org/ubuntu",
                "https://apt.pop-os.org/ubuntu",
                "http://apt.pop-os.org/release",
                "https://apt.pop-os.org/release",
                "http://archive.ubuntu.com/ubuntu",
                "https://archive.ubuntu.com/ubuntu",
                "http://security.ubuntu.com/ubuntu",
                "https://security.ubuntu.com/ubuntu",
                "http://ports.ubuntu.com/ubuntu-ports",
                "https://ports.ubuntu.com/ubuntu-ports",
            }
        ),
        origins=frozenset({"pop", "pop!_os", "pop-os", "ubuntu"}),
    ),
}


class SourceCaptureError(RuntimeError): ...


class CredentialedSourceError(SourceCaptureError): ...


class AptSourceParseError(SourceCaptureError): ...


@dataclass(frozen=True, slots=True)
class AptDescriptor:
    path: Path
    capture_path: str
    ordinal: int
    format: AptSourceFormat
    verbatim: str
    uris: tuple[str, ...]
    suites: tuple[str, ...]
    origins: tuple[str, ...]
    options: dict[str, str]
    signed_by: SignedByBinding | None
    enabled: bool
    insecure: bool


@dataclass(frozen=True, slots=True)
class AptAuthSelector:
    scheme: str | None
    host: str
    port: int | None
    path: str


@dataclass(frozen=True, slots=True)
class FlatpakPaths:
    user_repo: Path
    system_repo: Path = SYSTEM_FLATPAK_REPO


def default_flatpak_paths() -> FlatpakPaths:
    return FlatpakPaths(user_repo=get_data_dir().parent / "flatpak" / "repo")


def _normalize_uri(uri: str) -> str:
    parsed = urlsplit(uri)
    host = parsed.hostname.lower() if parsed.hostname else ""
    if not parsed.scheme or not host:
        return uri.rstrip("/").lower()
    port = f":{parsed.port}" if parsed.port is not None else ""
    path = parsed.path.rstrip("/") or "/"
    return f"{parsed.scheme.lower()}://{host}{port}{path}"


def _normalized_origin(origin: str) -> str:
    return " ".join(origin.lower().split())


def _suite_is_current(suite: str, codename: str) -> bool:
    normalized = suite.lower()
    return normalized == codename or normalized.startswith(f"{codename}-")


def _is_canonical_uri_and_suite(
    platform: SourcePlatform,
    *,
    uris: tuple[str, ...],
    suites: tuple[str, ...],
) -> bool:
    canonical = CANONICAL_BASE_ARCHIVES.get(platform.distro_id.lower())
    if canonical is None or not uris or not suites:
        return False
    if not {_normalize_uri(uri) for uri in uris} <= canonical.uris:
        return False
    return all(_suite_is_current(suite, platform.codename.lower()) for suite in suites)


def classify_apt_archive(
    platform: SourcePlatform,
    *,
    uris: tuple[str, ...],
    suites: tuple[str, ...],
    origins: tuple[str, ...] = (),
) -> ReplayMode:
    canonical = CANONICAL_BASE_ARCHIVES.get(platform.distro_id.lower())
    if canonical is None or not _is_canonical_uri_and_suite(platform, uris=uris, suites=suites):
        return ReplayMode.REPLAY

    normalized_origins = {_normalized_origin(origin) for origin in origins if origin.strip()}
    if len(normalized_origins) != 1 or not normalized_origins <= canonical.origins:
        return ReplayMode.REPLAY
    return ReplayMode.REPORT_ONLY


def _split_comment(line: str) -> tuple[str, str]:
    in_single_quote = False
    in_double_quote = False
    bracket_depth = 0
    for index, character in enumerate(line):
        if character == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
        elif character == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
        elif character == "[" and not in_single_quote and not in_double_quote:
            bracket_depth += 1
        elif character == "]" and bracket_depth:
            bracket_depth -= 1
        elif character == "#" and not in_single_quote and not in_double_quote and not bracket_depth:
            return line[:index], line[index:]
    return line, ""


def _parse_options(option_text: str) -> dict[str, str]:
    try:
        tokens = shlex.split(option_text, posix=True)
    except ValueError as error:
        raise AptSourceParseError("Malformed APT source options") from error

    options: dict[str, str] = {}
    for token in tokens:
        name, separator, value = token.partition("=")
        if not separator or not name:
            raise AptSourceParseError("Malformed APT source option")
        normalized = name.lower()
        if normalized in options:
            raise AptSourceParseError("Duplicate APT source option")
        options[normalized] = value
    return options


def _parse_signed_by(value: str) -> SignedByBinding:
    if "-----BEGIN PGP" in value:
        return SignedByBinding(embedded_armor=value.strip() + "\n")

    try:
        values = tuple(
            part
            for token in shlex.split(value, posix=True)
            for part in token.split(",")
            if part
        )
    except ValueError as error:
        raise AptSourceParseError("Malformed Signed-By value") from error
    if not values:
        raise AptSourceParseError("Empty Signed-By value")

    paths = tuple(item for item in values if item.startswith("/"))
    selectors = tuple(item for item in values if not item.startswith("/"))
    if not paths or len(paths) + len(selectors) != len(values):
        raise AptSourceParseError("Signed-By must contain keyring paths and full fingerprints")
    return SignedByBinding(key_paths=paths, fingerprint_selectors=selectors)


def _has_insecure_options(options: dict[str, str]) -> bool:
    for name in _INSECURE_APT_OPTION_NAMES:
        if options.get(name, "").lower() in {"yes", "true", "1"}:
            return True
    return False


def _parse_legacy_source(path: Path, ordinal: int, line: str) -> AptDescriptor | None:
    statement, _ = _split_comment(line)
    if not statement.strip():
        return None
    if statement.lstrip().startswith("#"):
        return AptDescriptor(
            path=path,
            capture_path=str(path),
            ordinal=ordinal,
            format=AptSourceFormat.LEGACY,
            verbatim=line,
            uris=(),
            suites=(),
            origins=(),
            options={},
            signed_by=None,
            enabled=False,
            insecure=False,
        )

    try:
        source_type, remainder = statement.strip().split(maxsplit=1)
    except ValueError as error:
        raise AptSourceParseError("Malformed legacy APT source line") from error
    if source_type not in {"deb", "deb-src"}:
        raise AptSourceParseError("Unsupported legacy APT source line")

    options: dict[str, str] = {}
    remainder = remainder.lstrip()
    if remainder.startswith("["):
        closing_index = remainder.find("]")
        if closing_index < 0:
            raise AptSourceParseError("Malformed legacy APT option block")
        options = _parse_options(remainder[1:closing_index])
        remainder = remainder[closing_index + 1 :].lstrip()

    try:
        tokens = shlex.split(remainder, posix=True)
    except ValueError as error:
        raise AptSourceParseError("Malformed legacy APT source line") from error
    if len(tokens) < 3:
        raise AptSourceParseError("Legacy APT source requires URI, suite, and component")

    uri = tokens[0]
    suite = tokens[1]
    signed_by = _parse_signed_by(options["signed-by"]) if "signed-by" in options else None
    return AptDescriptor(
        path=path,
        capture_path=str(path),
        ordinal=ordinal,
        format=AptSourceFormat.LEGACY,
        verbatim=line,
        uris=(uri,),
        suites=(suite,),
        origins=(),
        options=options,
        signed_by=signed_by,
        enabled=True,
        insecure=_has_insecure_options(options),
    )


def _split_deb822_paragraphs(content: str) -> tuple[str, ...]:
    paragraphs: list[str] = []
    current: list[str] = []
    for line in content.splitlines(keepends=True):
        if not line.strip():
            if current:
                paragraph = "".join(current)
                if any(
                    raw_line.strip() and not raw_line.lstrip().startswith("#")
                    for raw_line in current
                ):
                    paragraphs.append(paragraph)
                current = []
            continue
        current.append(line)
    if current:
        paragraph = "".join(current)
        if any(raw_line.strip() and not raw_line.lstrip().startswith("#") for raw_line in current):
            paragraphs.append(paragraph)
    return tuple(paragraphs)


def _parse_deb822_fields(paragraph: str) -> dict[str, str]:
    fields: dict[str, list[str]] = {}
    active_field: str | None = None
    for raw_line in paragraph.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        if raw_line[0].isspace():
            if active_field is None:
                raise AptSourceParseError("Malformed deb822 continuation")
            fields[active_field].append("" if raw_line[1:] == "." else raw_line[1:])
            continue
        name, separator, value = raw_line.partition(":")
        if not separator or not name or name.strip() != name:
            raise AptSourceParseError("Malformed deb822 field")
        normalized = name.lower()
        if normalized in fields:
            raise AptSourceParseError("Duplicate deb822 field")
        fields[normalized] = [value.lstrip()]
        active_field = normalized
    return {name: "\n".join(lines).strip() for name, lines in fields.items()}


def _parse_deb822_source(path: Path, ordinal: int, paragraph: str) -> AptDescriptor:
    fields = _parse_deb822_fields(paragraph)
    types = tuple(fields.get("types", "").split())
    if not types or not set(types) <= {"deb", "deb-src"}:
        raise AptSourceParseError("Unsupported deb822 source type")
    uris = tuple(fields.get("uris", "").split())
    suites = tuple(fields.get("suites", "").split())
    if not uris or not suites:
        raise AptSourceParseError("deb822 source requires URIs and Suites")
    enabled = fields.get("enabled", "yes").lower() not in {"no", "false", "0"}
    signed_by = _parse_signed_by(fields["signed-by"]) if "signed-by" in fields else None
    structural_fields = {
        "types",
        "uris",
        "suites",
        "components",
        "enabled",
        "signed-by",
        _ORIGIN_FIELD,
    }
    options = {
        name: value
        for name, value in fields.items()
        if name not in structural_fields
    }
    return AptDescriptor(
        path=path,
        capture_path=str(path),
        ordinal=ordinal,
        format=AptSourceFormat.DEB822,
        verbatim=paragraph,
        uris=uris,
        suites=suites,
        origins=tuple(fields.get(_ORIGIN_FIELD, "").splitlines()),
        options=options,
        signed_by=signed_by,
        enabled=enabled,
        insecure=_has_insecure_options(options),
    )


def _read_source_file(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        raise SourceCaptureError("Unable to read APT source file") from error


def parse_apt_source_file(path: Path) -> tuple[AptDescriptor, ...]:
    content = _read_source_file(path)
    if path.suffix == ".list" or path.name == "sources.list":
        descriptors: list[AptDescriptor] = []
        ordinal = 0
        for line in content.splitlines(keepends=True):
            descriptor = _parse_legacy_source(path, ordinal, line)
            if descriptor is not None:
                descriptors.append(descriptor)
                ordinal += 1
        return tuple(descriptors)
    if path.suffix == ".sources":
        return tuple(
            _parse_deb822_source(path, ordinal, paragraph)
            for ordinal, paragraph in enumerate(_split_deb822_paragraphs(content))
        )
    raise AptSourceParseError("Unsupported APT source file extension")


def _auth_store_files(apt_root: Path) -> tuple[Path, ...]:
    files: list[Path] = []
    auth_file = apt_root / "auth.conf"
    if auth_file.exists():
        files.append(auth_file)
    auth_directory = apt_root / "auth.conf.d"
    if auth_directory.exists():
        try:
            mode = auth_directory.stat().st_mode
            if not mode & 0o444:
                raise SourceCaptureError("APT auth store is unreadable")
            files.extend(sorted(path for path in auth_directory.iterdir() if path.is_file()))
        except OSError as error:
            raise SourceCaptureError("APT auth store is unreadable") from error
    return tuple(files)


def _parse_auth_selector(value: str) -> AptAuthSelector:
    if not value:
        raise CredentialedSourceError("Malformed APT authentication selector")
    has_scheme = "://" in value
    try:
        parsed = urlsplit(value if has_scheme else f"//{value}")
        port = parsed.port
    except ValueError as error:
        raise CredentialedSourceError("Malformed APT authentication selector") from error
    if (
        (has_scheme and not parsed.scheme)
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or (parsed.path and not parsed.path.startswith("/"))
    ):
        raise CredentialedSourceError("Malformed APT authentication selector")
    return AptAuthSelector(
        scheme=parsed.scheme.lower() if has_scheme else None,
        host=parsed.hostname.lower(),
        port=port,
        path=parsed.path.rstrip("/"),
    )


def _read_auth_selectors(apt_root: Path) -> tuple[AptAuthSelector, ...]:
    selectors: list[AptAuthSelector] = []
    for path in _auth_store_files(apt_root):
        try:
            mode = path.stat().st_mode
            if not mode & 0o444:
                raise SourceCaptureError("APT auth store is unreadable")
            content = path.read_text(encoding="utf-8")
        except OSError as error:
            raise SourceCaptureError("APT auth store is unreadable") from error
        for line in content.splitlines():
            statement, _ = _split_comment(line)
            try:
                tokens = shlex.split(statement, posix=True)
            except ValueError as error:
                raise CredentialedSourceError("Malformed APT authentication selector") from error
            for index, token in enumerate(tokens):
                if token.lower() != "machine":
                    continue
                if index + 1 >= len(tokens):
                    raise CredentialedSourceError("Malformed APT authentication selector")
                selectors.append(_parse_auth_selector(tokens[index + 1]))
    return tuple(selectors)


def _uri_matches_selector(uri: str, selector: AptAuthSelector) -> bool:
    try:
        parsed = urlsplit(uri)
        port = parsed.port
    except ValueError:
        return False
    hostname = parsed.hostname.lower() if parsed.hostname else ""
    if hostname != selector.host:
        return False
    if selector.scheme is None:
        if parsed.scheme.lower() not in {"https", "tor+https"}:
            return False
    elif parsed.scheme.lower() != selector.scheme:
        return False
    if selector.port is not None and port != selector.port:
        return False
    if not selector.path:
        return True
    normalized_path = (parsed.path or "/").rstrip("/") or "/"
    return normalized_path.startswith(selector.path)


def _assert_public_uri(uri: str, *, auth_selectors: tuple[AptAuthSelector, ...]) -> None:
    try:
        parsed = urlsplit(uri)
    except ValueError as error:
        raise CredentialedSourceError("Malformed source URI cannot be captured") from error
    if parsed.username is not None or parsed.password is not None or "?" in uri:
        raise CredentialedSourceError("Credential-bearing source URI cannot be captured")
    if any(_uri_matches_selector(uri, selector) for selector in auth_selectors):
        raise CredentialedSourceError("Source URI matches an APT authentication selector")


def _assert_public_apt_descriptor(
    descriptor: AptDescriptor,
    *,
    auth_selectors: tuple[AptAuthSelector, ...],
) -> None:
    for uri in descriptor.uris:
        _assert_public_uri(uri, auth_selectors=auth_selectors)
    if _AUTH_OPTION_NAMES & descriptor.options.keys():
        raise CredentialedSourceError("Credential-bearing APT source options cannot be captured")


def _source_id(capture_path: str, ordinal: int) -> str:
    digest = hashlib.sha256(f"{capture_path}\0{ordinal}".encode()).hexdigest()[:16]
    return f"apt-{digest}"


def _managed_target(path: Path, source_id: str) -> str:
    stem = path.stem
    if stem.startswith("popctl-"):
        return stem
    return f"popctl-{source_id}"


def _ppa_display(uris: tuple[str, ...]) -> str | None:
    for uri in uris:
        parsed = urlsplit(uri)
        host = parsed.hostname.lower() if parsed.hostname else ""
        if host not in {"ppa.launchpad.net", "ppa.launchpadcontent.net"}:
            continue
        parts = [part for part in parsed.path.split("/") if part]
        if len(parts) >= 2:
            return f"{parts[0]}/{parts[1]}"
    return None


def _apt_source_files(apt_root: Path) -> tuple[Path, ...]:
    files: list[Path] = []
    primary = apt_root / "sources.list"
    if primary.exists():
        files.append(primary)
    sources_directory = apt_root / "sources.list.d"
    if sources_directory.exists():
        try:
            files.extend(
                sorted(
                    path
                    for path in sources_directory.iterdir()
                    if path.suffix in {".list", ".sources"}
                )
            )
        except OSError as error:
            raise SourceCaptureError("Unable to enumerate APT source directory") from error
    return tuple(files)


def _validated_apt_descriptors(apt_root: Path) -> tuple[AptDescriptor, ...]:
    auth_selectors = _read_auth_selectors(apt_root)
    descriptors = tuple(
        descriptor
        for path in _apt_source_files(apt_root)
        for descriptor in parse_apt_source_file(path)
    )
    for descriptor in descriptors:
        if descriptor.enabled:
            _assert_public_apt_descriptor(descriptor, auth_selectors=auth_selectors)
    return descriptors


def _descriptor_identities(descriptor: AptDescriptor) -> tuple[tuple[str, str], ...]:
    return tuple(
        (_normalize_uri(uri), suite.lower())
        for uri in descriptor.uris
        for suite in descriptor.suites
    )


def _policy_archive_origins(output: str) -> dict[tuple[str, str], tuple[str, ...]]:
    origins: dict[tuple[str, str], set[str]] = {}
    current: tuple[str, str] | None = None
    for line in output.splitlines():
        fields = line.split()
        if fields and fields[0].isdigit():
            current = None
            for index, value in enumerate(fields):
                if not value.startswith(("http://", "https://")) or index + 1 >= len(fields):
                    continue
                current = (_normalize_uri(value), fields[index + 1].split("/", 1)[0].lower())
                break
            continue
        stripped = line.strip()
        if current is None or not stripped.startswith("release "):
            continue
        for field in stripped.removeprefix("release ").split(","):
            name, separator, value = field.partition("=")
            if name != "o" or not separator or not value.strip():
                continue
            origins.setdefault(current, set()).add(_normalized_origin(value))
            break
    return {identity: tuple(sorted(values)) for identity, values in origins.items()}


def _resolve_apt_archive_origins(
    descriptors: tuple[AptDescriptor, ...], platform: SourcePlatform
) -> tuple[AptDescriptor, ...]:
    candidates = tuple(
        descriptor
        for descriptor in descriptors
        if descriptor.enabled
        and _is_canonical_uri_and_suite(
            platform, uris=descriptor.uris, suites=descriptor.suites
        )
    )
    if not candidates:
        return descriptors

    result = run_command(["apt-cache", "policy"], timeout=10.0)
    if not result.success:
        return tuple(replace(descriptor, origins=()) for descriptor in descriptors)
    policy_origins = _policy_archive_origins(result.stdout)

    resolved: list[AptDescriptor] = []
    for descriptor in descriptors:
        if descriptor not in candidates:
            resolved.append(descriptor)
            continue
        origin_sets = [
            policy_origins.get(identity) for identity in _descriptor_identities(descriptor)
        ]
        if any(origins is None for origins in origin_sets):
            resolved.append(replace(descriptor, origins=()))
            continue
        origins = tuple(sorted({origin for values in origin_sets if values for origin in values}))
        resolved.append(replace(descriptor, origins=origins))
    return tuple(resolved)


def capture_apt_sources(
    apt_root: Path,
    platform: SourcePlatform,
    *,
    descriptors: tuple[AptDescriptor, ...] | None = None,
) -> AptSources:
    source_descriptors = (
        descriptors if descriptors is not None else _validated_apt_descriptors(apt_root)
    )
    source_descriptors = _resolve_apt_archive_origins(source_descriptors, platform)

    roots = tuple(
        dict.fromkeys(
            (
                apt_root / "keyrings",
                apt_root / "trusted.gpg.d",
                *DEFAULT_APT_KEYRING_ROOTS,
            )
        )
    )
    entries: list[AptSource] = []
    keys: dict[str, AptKey] = {}
    for descriptor in source_descriptors:
        if not descriptor.enabled:
            continue
        if descriptor.signed_by is None:
            raise SourceCaptureError("Legacy APT source without Signed-By cannot be captured")
        try:
            signed_by, captured_keys = capture_apt_keys(descriptor.signed_by, supported_roots=roots)
        except KeyTrustError as error:
            raise SourceCaptureError("Unable to capture APT signing key material") from error
        source_id = _source_id(descriptor.capture_path, descriptor.ordinal)
        replay_mode = ReplayMode.BLOCKED if descriptor.insecure else classify_apt_archive(
            platform,
            uris=descriptor.uris,
            suites=descriptor.suites,
            origins=descriptor.origins,
        )
        for key in captured_keys:
            existing = keys.get(key.id)
            if existing is not None and existing != key:
                raise SourceCaptureError("Conflicting APT key identities cannot be captured")
            keys[key.id] = key
        entries.append(
            AptSource(
                id=source_id,
                capture_path=descriptor.capture_path,
                format=descriptor.format,
                ordinal=descriptor.ordinal,
                managed_target=_managed_target(descriptor.path, source_id),
                verbatim_stanza=descriptor.verbatim,
                key_ids=tuple(key.id for key in captured_keys),
                signed_by=signed_by,
                replay_mode=replay_mode,
                ppa_display=_ppa_display(descriptor.uris),
            )
        )
    return AptSources(entries=tuple(entries), keys=tuple(keys.values()))


def _flatpak_options_are_authenticated(options: str) -> bool:
    normalized_options = tuple(option.lower() for option in options.replace(",", " ").split())
    return any(option.startswith(_FLATPAK_AUTH_OPTION_PREFIXES) for option in normalized_options)


def _flatpak_gpg_verify(options: str) -> bool:
    normalized_options = {option.lower() for option in options.replace(",", " ").split()}
    return "no-gpg-verify" not in normalized_options


def _run_flatpak(args: list[str]) -> CommandResult:
    result = run_command(args)
    if not result.success:
        raise SourceCaptureError("Flatpak source capture command failed")
    return result


def _parse_tab_rows(output: str, expected_fields: int, label: str) -> tuple[tuple[str, ...], ...]:
    rows: list[tuple[str, ...]] = []
    for line in output.splitlines():
        if not line.strip():
            continue
        parts = tuple(part.strip() for part in line.split("\t"))
        if len(parts) != expected_fields or any(not part for part in parts[: expected_fields - 1]):
            raise SourceCaptureError(f"Malformed {label} output")
        rows.append(parts)
    return tuple(rows)


def _export_flatpak_keyring(keyring: Path) -> VerifiedPublicKey | None:
    result = run_command(
        [
            "gpg",
            "--batch",
            "--no-default-keyring",
            "--keyring",
            str(keyring),
            "--export-options",
            "export-minimal",
            "--armor",
            "--export",
        ]
    )
    if not result.success or not result.stdout.strip():
        return None
    try:
        return verify_public_material(result.stdout)
    except KeyTrustError:
        return None


def _load_flatpakrepo_key(url: str) -> bytes:
    parsed = urlsplit(url)
    try:
        if parsed.scheme == "file":
            content = Path(unquote(parsed.path)).read_bytes()
        else:
            with urlopen(url, timeout=10) as response:  # noqa: S310
                content = response.read()
    except OSError as error:
        raise SourceCaptureError("Unable to read Flatpak repository descriptor") from error

    parser = configparser.ConfigParser(interpolation=None)
    try:
        parser.read_string(content.decode("utf-8"))
        key = parser.get("Flatpak Repo", "GPGKey")
    except (UnicodeDecodeError, configparser.Error) as error:
        raise SourceCaptureError("Flatpak repository descriptor has no usable GPGKey") from error
    return decode_flatpakrepo_key(key)


def _capture_flatpak_remote_key(name: str, url: str, repository: Path) -> VerifiedPublicKey:
    primary = _export_flatpak_keyring(repository / f"{name}.trustedkeys.gpg")
    if primary is not None:
        return primary
    try:
        return verify_public_material(_load_flatpakrepo_key(url))
    except KeyTrustError as error:
        raise SourceCaptureError("Flatpak remote has no verified public key material") from error


def _capture_flatpak_scope(
    scope: FlatpakScope, repository: Path
) -> tuple[tuple[FlatpakRemote, ...], tuple[FlatpakApp, ...]]:
    scope_option = "--user" if scope is FlatpakScope.USER else "--system"
    remotes_result = _run_flatpak(
        ["flatpak", "remotes", scope_option, "--columns=name,url,options"]
    )
    remote_rows = _parse_tab_rows(remotes_result.stdout, 3, "flatpak remotes")
    remotes: list[FlatpakRemote] = []
    for name, url, options in remote_rows:
        _assert_public_uri(url, auth_selectors=())
        if _flatpak_options_are_authenticated(options):
            raise CredentialedSourceError("Authenticated Flatpak remote cannot be captured")
        verified = _capture_flatpak_remote_key(name, url, repository)
        remotes.append(
            FlatpakRemote(
                name=name,
                scope=scope,
                url=url,
                gpg_verify=_flatpak_gpg_verify(options),
                gpg_key_armor=verified.armor,
                gpg_fingerprints=verified.fingerprints,
                replay_mode=(
                    ReplayMode.REPLAY if _flatpak_gpg_verify(options) else ReplayMode.BLOCKED
                ),
            )
        )

    apps_result = _run_flatpak(
        ["flatpak", "list", scope_option, "--app", "--columns=application,origin,arch,branch"]
    )
    app_rows = _parse_tab_rows(apps_result.stdout, 4, "flatpak list")
    apps = tuple(
        FlatpakApp(id=app_id, origin=origin, scope=scope, arch=arch, branch=branch)
        for app_id, origin, arch, branch in app_rows
    )
    return tuple(remotes), apps


def capture_flatpak_sources(paths: FlatpakPaths | None = None) -> FlatpakSources:
    if not command_exists("flatpak"):
        return FlatpakSources()
    flatpak_paths = paths or default_flatpak_paths()
    user_remotes, user_apps = _capture_flatpak_scope(FlatpakScope.USER, flatpak_paths.user_repo)
    system_remotes, system_apps = _capture_flatpak_scope(
        FlatpakScope.SYSTEM, flatpak_paths.system_repo
    )
    return FlatpakSources(remotes=user_remotes + system_remotes, apps=user_apps + system_apps)


def _is_runtime_snap(name: str, notes: str) -> bool:
    if notes in {"base", "snapd"} or name in {"snapd", "bare"} or name.startswith("core"):
        return True
    return name.startswith("gnome-") and name.endswith("-platform")


def capture_snap_sources() -> SnapSources:
    if not command_exists("snap"):
        return SnapSources()
    result = run_command(["snap", "list"])
    if not result.success:
        raise SourceCaptureError("Snap source capture command failed")
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return SnapSources()
    header = lines[0].split()
    try:
        tracking_index = header.index("Tracking")
        notes_index = header.index("Notes")
    except ValueError as error:
        raise SourceCaptureError("Snap list output has no Tracking column") from error
    packages: list[SnapChannel] = []
    for line in lines[1:]:
        fields = line.split()
        if len(fields) <= max(tracking_index, notes_index):
            raise SourceCaptureError("Malformed snap list output")
        name = fields[0]
        tracking = fields[tracking_index]
        if not tracking or tracking == "-":
            raise SourceCaptureError("Snap has no tracking channel")
        if _is_runtime_snap(name, fields[notes_index]):
            continue
        packages.append(SnapChannel(name=name, channel=tracking, replay_mode=ReplayMode.REPLAY))
    return SnapSources(packages=tuple(packages))


def capture_platform(os_release_path: Path = OS_RELEASE_PATH) -> SourcePlatform:
    try:
        values = {
            key: value.strip().strip('"')
            for line in os_release_path.read_text(encoding="utf-8").splitlines()
            if "=" in line
            for key, value in (line.split("=", 1),)
        }
    except OSError as error:
        raise SourceCaptureError("Unable to read platform identity") from error
    distro_id = values.get("ID", "").lower()
    codename = values.get("VERSION_CODENAME", values.get("UBUNTU_CODENAME", "")).lower()
    if not distro_id or not codename:
        raise SourceCaptureError("Platform identity requires distro ID and codename")
    return SourcePlatform(distro_id=distro_id, codename=codename)


def capture_sources(
    *,
    apt_root: Path = APT_ROOT,
    os_release_path: Path = OS_RELEASE_PATH,
    flatpak_paths: FlatpakPaths | None = None,
    managers: Iterable[PackageSource] | None = None,
) -> SourcesConfig:
    selected = frozenset(managers) if managers is not None else frozenset(PackageSource)
    apt_descriptors = (
        _validated_apt_descriptors(apt_root) if PackageSource.APT in selected else None
    )
    platform = capture_platform(os_release_path)
    apt = (
        capture_apt_sources(apt_root, platform, descriptors=apt_descriptors)
        if apt_descriptors is not None
        else AptSources()
    )
    flatpak = (
        capture_flatpak_sources(flatpak_paths)
        if PackageSource.FLATPAK in selected
        else FlatpakSources()
    )
    snap = capture_snap_sources() if PackageSource.SNAP in selected else SnapSources()
    return SourcesConfig(platform=platform, apt=apt, flatpak=flatpak, snap=snap)


def resolve_apt_candidate_origins(
    packages: Iterable[str],
    entries: Iterable[AptSource],
) -> dict[str, SourceLocator | str]:
    entry_identities = {
        entry.capture_locator: _apt_entry_identity(entry)
        for entry in entries
    }
    resolved: dict[str, SourceLocator | str] = {}
    for package in packages:
        result = run_command(["apt-cache", "policy", package], timeout=10.0)
        if not result.success:
            resolved[package] = "unknown"
            continue
        candidates = _candidate_origins_from_policy(result.stdout)
        matching = {
            locator
            for uri, suite in candidates
            for locator, identities in entry_identities.items()
            if (_normalize_uri(uri), suite.lower()) in identities
        }
        resolved[package] = next(iter(matching)) if len(matching) == 1 else "unknown"
    return resolved


def _apt_entry_identity(entry: AptSource) -> frozenset[tuple[str, str]]:
    try:
        if entry.format is AptSourceFormat.LEGACY:
            matched = _parse_legacy_source(
                Path(entry.capture_path), entry.ordinal, entry.verbatim_stanza
            )
            if matched is None:
                return frozenset()
        else:
            matched = _parse_deb822_source(
                Path(entry.capture_path), entry.ordinal, entry.verbatim_stanza
            )
    except SourceCaptureError:
        return frozenset()
    return frozenset(
        (_normalize_uri(uri), suite.lower()) for uri in matched.uris for suite in matched.suites
    )


def _candidate_origins_from_policy(output: str) -> tuple[tuple[str, str], ...]:
    candidate_version: str | None = None
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("Candidate:"):
            candidate_version = stripped.partition(":")[2].strip()
            break
    if not candidate_version or candidate_version == "(none)":
        return ()

    origins: list[tuple[str, str]] = []
    in_candidate = False
    for line in output.splitlines():
        parts = line.split()
        if parts and parts[0] in {"***", candidate_version}:
            version = parts[1] if parts[0] == "***" and len(parts) > 1 else parts[0]
            if version != candidate_version and in_candidate:
                break
            in_candidate = version == candidate_version
            continue
        if in_candidate and parts and not line.startswith((" ", "\t")):
            break
        if not in_candidate:
            continue
        for index, value in enumerate(parts):
            if not value.startswith(("http://", "https://")):
                continue
            if index + 1 < len(parts):
                origins.append((value, parts[index + 1].split("/")[0]))
            break
    return tuple(origins)
