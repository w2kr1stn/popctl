from __future__ import annotations

import os
import shutil
import unicodedata
from collections.abc import Callable, Collection
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final, Protocol

from popctl.dotfiles.secret_filter import MAX_CANDIDATE_BYTES
from popctl.utils.desktop import DesktopFamily, normalize_desktop_family
from popctl.utils.shell import run_command

DESKTOP_SETTINGS_ARTIFACT_PATH: Final = ".config/popctl/desktop-settings.dconf"
DESKTOP_SETTINGS_ARTIFACT_MODE: Final = "100644"
# The bare GNOME Shell root includes extension state, so it is intentionally excluded.
DEFAULT_ROOTS: Final = (
    "/org/gnome/desktop/wm/keybindings/",  # window-manager keybindings
    "/org/gnome/settings-daemon/plugins/media-keys/",  # media and custom keybindings
    "/org/gnome/desktop/interface/",  # interface and theme
    "/org/gnome/desktop/wm/preferences/",  # window-manager preferences
    "/org/gnome/desktop/input-sources/",  # input sources
    "/org/gnome/desktop/background/",  # background appearance
    "/org/gnome/desktop/screensaver/",  # screensaver appearance
)
MAX_DESKTOP_SETTINGS_ARTIFACT_BYTES: Final = MAX_CANDIDATE_BYTES
_ARTIFACT_MAGIC: Final = "# popctl-desktop-settings v1"
_FAMILY_PREFIX: Final = "# family: "
_ROOT_PREFIX: Final = "# root: "
_HEADER_END: Final = "# end-header"
_SECTION_PREFIX: Final = _ROOT_PREFIX
_VALID_FAMILIES: Final = frozenset({"GNOME", "COSMIC", "UNKNOWN"})


class DesktopSettingsArtifactError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class DesktopSettingsSection:
    root: str
    body: bytes


@dataclass(frozen=True, slots=True)
class DesktopSettingsArtifact:
    family: str
    sections: tuple[DesktopSettingsSection, ...]

    @property
    def roots(self) -> tuple[str, ...]:
        return tuple(section.root for section in self.sections)


class DesktopCaptureStatus(str, Enum):
    DISABLED = "disabled"
    UNKNOWN_FAMILY = "unknown-family"
    NO_DCONF = "no-dconf"
    FAMILY_MISMATCH = "family-mismatch"
    INVALID_ARTIFACT = "invalid-artifact"
    DUMP_FAILED = "dump-failed"
    SECRET_REJECTED = "secret-rejected"
    UNCHANGED = "unchanged"
    CHANGED = "changed"


@dataclass(frozen=True, slots=True)
class DesktopCaptureResult:
    status: DesktopCaptureStatus
    family: DesktopFamily | None = None
    artifact: bytes | None = None
    root: str | None = None
    detail: str = ""
    prior_retained: bool = False
    ambiguous_root_allowlist: tuple[str, ...] = ()

    @property
    def changed(self) -> bool:
        return self.status is DesktopCaptureStatus.CHANGED


class DesktopLoadStatus(str, Enum):
    DISABLED = "disabled"
    NO_ARTIFACT = "no-artifact"
    NO_DCONF = "no-dconf"
    NO_SESSION = "no-session"
    FAMILY_MISMATCH = "family-mismatch"
    UNKNOWN_FAMILY = "unknown-family"
    INVALID_ARTIFACT = "invalid-artifact"
    PREVIEW = "preview"
    APPLIED = "applied"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class DesktopLoadResult:
    status: DesktopLoadStatus
    family: DesktopFamily | None = None
    artifact_family: DesktopFamily | None = None
    parsed_roots: tuple[str, ...] = ()
    suppressed_roots: tuple[str, ...] = ()
    applied_roots: tuple[str, ...] = ()
    root: str | None = None
    detail: str = ""

    @property
    def skipped(self) -> bool:
        return self.status in {
            DesktopLoadStatus.DISABLED,
            DesktopLoadStatus.NO_ARTIFACT,
            DesktopLoadStatus.NO_DCONF,
            DesktopLoadStatus.NO_SESSION,
            DesktopLoadStatus.FAMILY_MISMATCH,
            DesktopLoadStatus.UNKNOWN_FAMILY,
            DesktopLoadStatus.INVALID_ARTIFACT,
        }


class _CaptureSettings(Protocol):
    @property
    def enabled(self) -> bool: ...

    @property
    def effective_roots(self) -> tuple[str, ...]: ...


ArtifactReader = Callable[[], bytes | None]
ArtifactAdmitter = Callable[[bytes, Collection[str]], None]


def is_desktop_settings_artifact_path(path: str) -> bool:
    return path == DESKTOP_SETTINGS_ARTIFACT_PATH


def capture_desktop_settings(
    settings: _CaptureSettings,
    *,
    existing_artifact: ArtifactReader,
    admit_artifact: ArtifactAdmitter,
    ambiguous_root_allowlist: Collection[str] = (),
) -> DesktopCaptureResult:
    if not settings.enabled:
        return DesktopCaptureResult(DesktopCaptureStatus.DISABLED)

    family = normalize_desktop_family(
        os.environ.get("XDG_CURRENT_DESKTOP"),
        os.environ.get("XDG_SESSION_DESKTOP"),
    )
    prior = existing_artifact()
    if prior is not None:
        try:
            prior_family = DesktopFamily(parse_desktop_settings_artifact(prior).family)
        except (DesktopSettingsArtifactError, ValueError) as e:
            return DesktopCaptureResult(
                DesktopCaptureStatus.INVALID_ARTIFACT,
                family=family,
                detail=str(e),
                prior_retained=True,
            )
        if prior_family is not family:
            return DesktopCaptureResult(
                DesktopCaptureStatus.FAMILY_MISMATCH,
                family=family,
                detail=prior_family.value,
                prior_retained=True,
            )
    if family is DesktopFamily.UNKNOWN:
        return DesktopCaptureResult(DesktopCaptureStatus.UNKNOWN_FAMILY, family=family)
    if shutil.which("dconf") is None:
        return DesktopCaptureResult(
            DesktopCaptureStatus.NO_DCONF,
            family=family,
            prior_retained=prior is not None,
        )

    sections: list[DesktopSettingsSection] = []
    for root in settings.effective_roots:
        result = run_command(["dconf", "dump", root], env={"LC_ALL": "C"})
        if not result.success:
            return DesktopCaptureResult(
                DesktopCaptureStatus.DUMP_FAILED,
                family=family,
                root=root,
                detail=result.stderr.strip(),
                prior_retained=prior is not None,
            )
        sections.append(DesktopSettingsSection(root, result.stdout.encode("utf-8")))
    try:
        rendered = render_desktop_settings_artifact(family.value, sections)
    except DesktopSettingsArtifactError as e:
        return DesktopCaptureResult(
            DesktopCaptureStatus.SECRET_REJECTED,
            family=family,
            detail=str(e),
            prior_retained=prior is not None,
        )
    try:
        admit_artifact(rendered, ambiguous_root_allowlist)
    except Exception as e:
        return DesktopCaptureResult(
            DesktopCaptureStatus.SECRET_REJECTED,
            family=family,
            detail=str(e),
            prior_retained=prior is not None,
        )
    if rendered == prior:
        return DesktopCaptureResult(
            DesktopCaptureStatus.UNCHANGED,
            family=family,
            ambiguous_root_allowlist=tuple(sorted(set(ambiguous_root_allowlist))),
        )
    return DesktopCaptureResult(
        DesktopCaptureStatus.CHANGED,
        family=family,
        artifact=rendered,
        prior_retained=prior is not None,
        ambiguous_root_allowlist=tuple(sorted(set(ambiguous_root_allowlist))),
    )


def load_desktop_settings(
    settings: _CaptureSettings,
    *,
    existing_artifact: ArtifactReader,
    dry_run: bool = False,
) -> DesktopLoadResult:
    if not settings.enabled:
        return DesktopLoadResult(DesktopLoadStatus.DISABLED)

    content = existing_artifact()
    if content is None:
        return DesktopLoadResult(DesktopLoadStatus.NO_ARTIFACT)
    try:
        artifact = parse_desktop_settings_artifact(content)
        artifact_family = DesktopFamily(artifact.family)
    except (DesktopSettingsArtifactError, ValueError) as e:
        return DesktopLoadResult(DesktopLoadStatus.INVALID_ARTIFACT, detail=str(e))

    parsed_roots = artifact.roots
    family = normalize_desktop_family(
        os.environ.get("XDG_CURRENT_DESKTOP"),
        os.environ.get("XDG_SESSION_DESKTOP"),
    )
    if family is DesktopFamily.UNKNOWN:
        return DesktopLoadResult(
            DesktopLoadStatus.UNKNOWN_FAMILY,
            family=family,
            artifact_family=artifact_family,
            parsed_roots=parsed_roots,
        )
    if artifact_family is not family:
        return DesktopLoadResult(
            DesktopLoadStatus.FAMILY_MISMATCH,
            family=family,
            artifact_family=artifact_family,
            parsed_roots=parsed_roots,
        )

    allowed_roots = set(settings.effective_roots)
    sections = tuple(section for section in artifact.sections if section.root in allowed_roots)
    suppressed_roots = tuple(
        section.root for section in artifact.sections if section.root not in allowed_roots
    )
    if dry_run:
        return DesktopLoadResult(
            DesktopLoadStatus.PREVIEW,
            family=family,
            artifact_family=artifact_family,
            parsed_roots=parsed_roots,
            suppressed_roots=suppressed_roots,
        )
    if shutil.which("dconf") is None:
        return DesktopLoadResult(
            DesktopLoadStatus.NO_DCONF,
            family=family,
            artifact_family=artifact_family,
            parsed_roots=parsed_roots,
        )
    if not _has_session_hint():
        return DesktopLoadResult(
            DesktopLoadStatus.NO_SESSION,
            family=family,
            artifact_family=artifact_family,
            parsed_roots=parsed_roots,
        )

    applied_roots: list[str] = []
    for section in sections:
        result = run_command(
            ["dconf", "load", "-f", section.root],
            input_text=section.body.decode("utf-8"),
            env={"LC_ALL": "C"},
        )
        if not result.success:
            detail = result.stderr.strip()
            if _is_no_session_transport_error(detail):
                return DesktopLoadResult(
                    DesktopLoadStatus.NO_SESSION,
                    family=family,
                    artifact_family=artifact_family,
                    parsed_roots=parsed_roots,
                    suppressed_roots=suppressed_roots,
                    applied_roots=tuple(applied_roots),
                    root=section.root,
                    detail=detail,
                )
            return DesktopLoadResult(
                DesktopLoadStatus.FAILED,
                family=family,
                artifact_family=artifact_family,
                parsed_roots=parsed_roots,
                suppressed_roots=suppressed_roots,
                applied_roots=tuple(applied_roots),
                root=section.root,
                detail=detail,
            )
        applied_roots.append(section.root)
    return DesktopLoadResult(
        DesktopLoadStatus.APPLIED,
        family=family,
        artifact_family=artifact_family,
        parsed_roots=parsed_roots,
        suppressed_roots=suppressed_roots,
        applied_roots=tuple(applied_roots),
    )


def _has_session_hint() -> bool:
    if os.environ.get("DBUS_SESSION_BUS_ADDRESS"):
        return True
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime_dir:
        return False
    try:
        return (Path(runtime_dir) / "bus").exists()
    except OSError:
        return False


def _is_no_session_transport_error(stderr: str) -> bool:
    message = stderr.casefold()
    dbus_signal = "d-bus" in message or "dbus" in message or "session bus" in message
    transport_signal = any(
        marker in message
        for marker in (
            "could not connect",
            "failed to connect",
            "connection refused",
            "connection reset",
            "no such file or directory",
            "cannot autolaunch d-bus",
        )
    )
    return dbus_signal and transport_signal


def canonical_dconf_root(root: str) -> str:
    if not root.startswith("/") or not root.endswith("/") or root == "/":
        raise DesktopSettingsArtifactError(f"Dconf root is not an absolute directory: {root!r}")
    if "\\" in root:
        raise DesktopSettingsArtifactError(f"Dconf root contains a backslash: {root!r}")
    segments = root[1:-1].split("/")
    if any(not segment or segment in {".", ".."} for segment in segments):
        raise DesktopSettingsArtifactError(f"Dconf root has an unsafe segment: {root!r}")
    if any(
        character.isspace() or unicodedata.category(character).startswith("C")
        for segment in segments
        for character in segment
    ):
        raise DesktopSettingsArtifactError(
            f"Dconf root contains whitespace or a control character: {root!r}"
        )
    return root


def render_desktop_settings_artifact(
    family: str,
    sections: Collection[DesktopSettingsSection],
) -> bytes:
    _validate_family(family)
    canonical_sections = _canonical_sections(sections)
    lines = [_ARTIFACT_MAGIC, f"{_FAMILY_PREFIX}{family}"]
    lines.extend(f"{_ROOT_PREFIX}{section.root}" for section in canonical_sections)
    lines.append(_HEADER_END)
    rendered = "\n".join(lines).encode("utf-8") + b"\n"
    for section in canonical_sections:
        rendered += f"{_SECTION_PREFIX}{section.root}\n".encode()
        rendered += section.body
    if len(rendered) > MAX_DESKTOP_SETTINGS_ARTIFACT_BYTES:
        raise DesktopSettingsArtifactError("Desktop settings artifact exceeds the size limit")
    return rendered


def parse_desktop_settings_artifact(content: bytes) -> DesktopSettingsArtifact:
    if len(content) > MAX_DESKTOP_SETTINGS_ARTIFACT_BYTES:
        raise DesktopSettingsArtifactError("Desktop settings artifact exceeds the size limit")
    if b"\x00" in content:
        raise DesktopSettingsArtifactError("Desktop settings artifact contains NUL bytes")
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as e:
        raise DesktopSettingsArtifactError("Desktop settings artifact is not UTF-8") from e
    if "\r" in text:
        raise DesktopSettingsArtifactError("Desktop settings artifact must use LF line endings")
    lines = text.splitlines(keepends=True)
    if not lines or _line_value(lines[0]) != _ARTIFACT_MAGIC:
        raise DesktopSettingsArtifactError(
            "Desktop settings artifact has an unsupported format version"
        )
    if len(lines) < 3 or not _line_value(lines[1]).startswith(_FAMILY_PREFIX):
        raise DesktopSettingsArtifactError("Desktop settings artifact has no family header")
    family = _line_value(lines[1]).removeprefix(_FAMILY_PREFIX)
    _validate_family(family)

    header_roots: list[str] = []
    index = 2
    while index < len(lines) and _line_value(lines[index]).startswith(_ROOT_PREFIX):
        header_roots.append(
            canonical_dconf_root(_line_value(lines[index]).removeprefix(_ROOT_PREFIX))
        )
        index += 1
    _require_unique_roots(header_roots)
    if index >= len(lines) or _line_value(lines[index]) != _HEADER_END:
        raise DesktopSettingsArtifactError("Desktop settings artifact has no complete header")
    index += 1

    sections: list[DesktopSettingsSection] = []
    current_root: str | None = None
    current_body: list[str] = []
    while index < len(lines):
        line = _line_value(lines[index])
        if line.startswith(_SECTION_PREFIX):
            if current_root is not None:
                sections.append(
                    DesktopSettingsSection(current_root, "".join(current_body).encode("utf-8"))
                )
            current_root = canonical_dconf_root(line.removeprefix(_SECTION_PREFIX))
            current_body = []
        elif current_root is None:
            raise DesktopSettingsArtifactError(
                "Desktop settings artifact has content before a section"
            )
        else:
            current_body.append(lines[index])
        index += 1
    if current_root is not None:
        sections.append(DesktopSettingsSection(current_root, "".join(current_body).encode("utf-8")))

    section_roots = [section.root for section in sections]
    _require_unique_roots(section_roots)
    if tuple(header_roots) != tuple(section_roots):
        raise DesktopSettingsArtifactError(
            "Desktop settings artifact sections do not match its header roots"
        )
    return DesktopSettingsArtifact(family, tuple(sections))


def _canonical_sections(
    sections: Collection[DesktopSettingsSection],
) -> tuple[DesktopSettingsSection, ...]:
    canonical_sections: list[DesktopSettingsSection] = []
    for section in sections:
        root = canonical_dconf_root(section.root)
        if b"\x00" in section.body:
            raise DesktopSettingsArtifactError(f"Dconf section {root} contains NUL bytes")
        try:
            section.body.decode("utf-8")
        except UnicodeDecodeError as e:
            raise DesktopSettingsArtifactError(f"Dconf section {root} is not UTF-8") from e
        if b"\r" in section.body:
            raise DesktopSettingsArtifactError(f"Dconf section {root} must use LF line endings")
        if section.body and not section.body.endswith(b"\n"):
            raise DesktopSettingsArtifactError(f"Dconf section {root} must end with a newline")
        canonical_sections.append(DesktopSettingsSection(root, section.body))
    _require_unique_roots([section.root for section in canonical_sections])
    return tuple(sorted(canonical_sections, key=lambda section: section.root))


def _line_value(line: str) -> str:
    if not line.endswith("\n"):
        raise DesktopSettingsArtifactError("Desktop settings artifact has an unterminated line")
    return line[:-1]


def _validate_family(family: str) -> None:
    if family not in _VALID_FAMILIES:
        raise DesktopSettingsArtifactError(
            f"Desktop settings artifact has an invalid family: {family!r}"
        )


def _require_unique_roots(roots: Collection[str]) -> None:
    if len(roots) != len(set(roots)):
        raise DesktopSettingsArtifactError("Desktop settings artifact has duplicate roots")
