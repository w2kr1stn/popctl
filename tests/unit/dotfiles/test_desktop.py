from __future__ import annotations

import pytest
from popctl.dotfiles.desktop import (
    DEFAULT_ROOTS,
    MAX_DESKTOP_SETTINGS_ARTIFACT_BYTES,
    DesktopSettingsArtifactError,
    DesktopSettingsSection,
    canonical_dconf_root,
    parse_desktop_settings_artifact,
    render_desktop_settings_artifact,
)


def test_artifact_round_trip_preserves_sorted_roots_and_verbatim_bodies() -> None:
    artifact = render_desktop_settings_artifact(
        "GNOME",
        (
            DesktopSettingsSection("/org/example/second/", b"[settings]\nvalue='two'\n"),
            DesktopSettingsSection(DEFAULT_ROOTS[0], b"[custom-keybindings]\nvalue=[]\n"),
        ),
    )

    parsed = parse_desktop_settings_artifact(artifact)

    assert artifact.startswith(b"# popctl-desktop-settings v1\n# family: GNOME\n")
    assert parsed.family == "GNOME"
    assert parsed.sections == (
        DesktopSettingsSection("/org/example/second/", b"[settings]\nvalue='two'\n"),
        DesktopSettingsSection(DEFAULT_ROOTS[0], b"[custom-keybindings]\nvalue=[]\n"),
    )


@pytest.mark.parametrize(
    "root",
    (
        "/org/example/empty/",
        "/org/example/nonexistent/",
    ),
)
def test_zero_byte_sections_round_trip(root: str) -> None:
    artifact = render_desktop_settings_artifact(
        "GNOME",
        (DesktopSettingsSection(root, b""),),
    )

    assert artifact.count(f"# root: {root}\n".encode()) == 2
    assert parse_desktop_settings_artifact(artifact).sections == (
        DesktopSettingsSection(root, b""),
    )


@pytest.mark.parametrize(
    ("root", "message"),
    (
        ("relative/root/", "absolute directory"),
        ("/org/example", "absolute directory"),
        ("/org//example/", "unsafe segment"),
        ("/org/./example/", "unsafe segment"),
        ("/org/../example/", "unsafe segment"),
        ("/org\\example/", "backslash"),
        ("/org/example root/", "whitespace"),
        ("/org/example\x1froot/", "whitespace"),
    ),
)
def test_root_grammar_rejects_noncanonical_directories(root: str, message: str) -> None:
    with pytest.raises(DesktopSettingsArtifactError, match=message):
        canonical_dconf_root(root)


@pytest.mark.parametrize(
    "content",
    (
        b"# popctl-desktop-settings v2\n# family: GNOME\n# end-header\n",
        b"# popctl-desktop-settings v1\n# family: KDE\n# end-header\n",
        b"# popctl-desktop-settings v1\n# root: /org/example/\n# end-header\n",
        (
            b"# popctl-desktop-settings v1\n# family: GNOME\n"
            b"# root: /org/example/\n# end-header\n# section: /org/other/\n"
        ),
        (
            b"# popctl-desktop-settings v1\n# family: GNOME\n"
            b"# root: /org/example/\n# root: /org/example/\n# end-header\n"
            b"# section: /org/example/\n"
        ),
    ),
)
def test_artifact_parser_rejects_malformed_headers_and_sections(content: bytes) -> None:
    with pytest.raises(DesktopSettingsArtifactError):
        parse_desktop_settings_artifact(content)


def test_artifact_parser_rejects_oversized_input_before_parsing() -> None:
    with pytest.raises(DesktopSettingsArtifactError, match="size limit"):
        parse_desktop_settings_artifact(b"x" * (MAX_DESKTOP_SETTINGS_ARTIFACT_BYTES + 1))


def test_renderer_rejects_duplicate_roots() -> None:
    root = "/org/example/duplicate/"

    with pytest.raises(DesktopSettingsArtifactError, match="duplicate"):
        render_desktop_settings_artifact(
            "GNOME",
            (DesktopSettingsSection(root, b""), DesktopSettingsSection(root, b"")),
        )
