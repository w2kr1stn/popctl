from __future__ import annotations

import unicodedata
from collections.abc import Collection
from dataclasses import dataclass
from typing import Final

from popctl.dotfiles.secret_filter import MAX_CANDIDATE_BYTES

DESKTOP_SETTINGS_ARTIFACT_PATH: Final = ".config/popctl/desktop-settings.dconf"
DESKTOP_SETTINGS_ARTIFACT_MODE: Final = "100644"
DEFAULT_ROOTS: Final = (
    "/org/gnome/desktop/wm/keybindings/",
    "/org/gnome/settings-daemon/plugins/media-keys/",
    "/org/gnome/desktop/interface/",
    "/org/gnome/desktop/wm/preferences/",
    "/org/gnome/desktop/input-sources/",
    "/org/gnome/desktop/background/",
    "/org/gnome/desktop/screensaver/",
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


def is_desktop_settings_artifact_path(path: str) -> bool:
    return path == DESKTOP_SETTINGS_ARTIFACT_PATH


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
