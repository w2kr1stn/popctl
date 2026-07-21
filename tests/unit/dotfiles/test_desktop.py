from __future__ import annotations

import pytest
from popctl.dotfiles import desktop
from popctl.dotfiles.desktop import (
    DEFAULT_ROOTS,
    MAX_DESKTOP_SETTINGS_ARTIFACT_BYTES,
    DesktopCaptureStatus,
    DesktopSettingsArtifactError,
    DesktopSettingsSection,
    canonical_dconf_root,
    capture_desktop_settings,
    parse_desktop_settings_artifact,
    render_desktop_settings_artifact,
)
from popctl.utils.shell import CommandResult


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


class _CaptureSettings:
    def __init__(
        self,
        roots: tuple[str, ...],
        *,
        enabled: bool = True,
    ) -> None:
        self.enabled = enabled
        self.effective_roots = roots


def _enable_gnome(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    monkeypatch.delenv("XDG_SESSION_DESKTOP", raising=False)
    monkeypatch.setattr(
        desktop.shutil,
        "which",
        lambda name: "/usr/bin/dconf" if name == "dconf" else None,
    )


def test_capture_dumps_each_effective_root_verbatim_in_sorted_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roots = ("/org/example/a/", "/org/example/z/")
    settings = _CaptureSettings(roots)
    calls: list[tuple[list[str], dict[str, str] | None]] = []
    admitted: list[bytes] = []

    _enable_gnome(monkeypatch)

    def dump(args: list[str], **kwargs: object) -> CommandResult:
        calls.append((args, kwargs.get("env") if isinstance(kwargs.get("env"), dict) else None))
        return CommandResult(f"[{args[2]}]\nvalue='set'\n", "", 0)

    monkeypatch.setattr(desktop, "run_command", dump)
    result = capture_desktop_settings(
        settings,
        existing_artifact=lambda: None,
        admit_artifact=lambda content, _roots: admitted.append(content),
    )

    assert result.status is DesktopCaptureStatus.CHANGED
    assert calls == [
        (["dconf", "dump", "/org/example/a/"], {"LC_ALL": "C"}),
        (["dconf", "dump", "/org/example/z/"], {"LC_ALL": "C"}),
    ]
    assert result.artifact == admitted[0]
    assert parse_desktop_settings_artifact(admitted[0]).roots == roots


def test_capture_allows_empty_and_nonexistent_root_sections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roots = ("/org/example/empty/", "/org/example/nonexistent/")
    settings = _CaptureSettings(roots)
    _enable_gnome(monkeypatch)
    monkeypatch.setattr(
        desktop,
        "run_command",
        lambda *_args, **_kwargs: CommandResult("", "", 0),
    )
    admitted: list[bytes] = []

    result = capture_desktop_settings(
        settings,
        existing_artifact=lambda: None,
        admit_artifact=lambda content, _roots: admitted.append(content),
    )

    assert result.status is DesktopCaptureStatus.CHANGED
    assert parse_desktop_settings_artifact(admitted[0]).sections == (
        DesktopSettingsSection(roots[0], b""),
        DesktopSettingsSection(roots[1], b""),
    )


def test_capture_detects_an_unchanged_rendered_artifact(monkeypatch: pytest.MonkeyPatch) -> None:
    root = "/org/example/current/"
    previous = render_desktop_settings_artifact(
        "GNOME", (DesktopSettingsSection(root, b"[settings]\nvalue='same'\n"),)
    )
    _enable_gnome(monkeypatch)
    monkeypatch.setattr(
        desktop,
        "run_command",
        lambda *_args, **_kwargs: CommandResult("[settings]\nvalue='same'\n", "", 0),
    )

    result = capture_desktop_settings(
        _CaptureSettings((root,)),
        existing_artifact=lambda: previous,
        admit_artifact=lambda *_args: None,
    )

    assert result.status is DesktopCaptureStatus.UNCHANGED
    assert result.artifact is None


def test_capture_is_all_or_nothing_after_one_root_dump_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    roots = ("/org/example/first/", "/org/example/second/")
    settings = _CaptureSettings(roots)
    previous = render_desktop_settings_artifact(
        "GNOME", (DesktopSettingsSection(roots[0], b"[old]\nvalue='old'\n"),)
    )
    calls: list[list[str]] = []
    admitted: list[bytes] = []
    _enable_gnome(monkeypatch)

    def dump(args: list[str], **_kwargs: object) -> CommandResult:
        calls.append(args)
        if args[2] == roots[1]:
            return CommandResult("", "dconf transport failed", 1)
        return CommandResult("[new]\nvalue='new'\n", "", 0)

    monkeypatch.setattr(desktop, "run_command", dump)
    result = capture_desktop_settings(
        settings,
        existing_artifact=lambda: previous,
        admit_artifact=lambda content, _roots: admitted.append(content),
    )

    assert result.status is DesktopCaptureStatus.DUMP_FAILED
    assert result.root == roots[1]
    assert result.prior_retained
    assert calls == [["dconf", "dump", roots[0]], ["dconf", "dump", roots[1]]]
    assert admitted == []


def test_capture_preserves_a_differing_family_artifact_without_dconf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    previous = render_desktop_settings_artifact(
        "COSMIC", (DesktopSettingsSection("/org/example/cosmic/", b""),)
    )
    _enable_gnome(monkeypatch)
    monkeypatch.setattr(
        desktop,
        "run_command",
        lambda *_args, **_kwargs: pytest.fail("capture must not call dconf for a differing family"),
    )

    result = capture_desktop_settings(
        _CaptureSettings(("/org/example/current/",)),
        existing_artifact=lambda: previous,
        admit_artifact=lambda *_args: pytest.fail("capture must not admit a replacement"),
    )

    assert result.status is DesktopCaptureStatus.FAMILY_MISMATCH
    assert result.detail == "COSMIC"
    assert result.prior_retained


@pytest.mark.parametrize(
    ("current_desktop", "binary_available", "status"),
    (
        ("unknown-desktop", True, DesktopCaptureStatus.UNKNOWN_FAMILY),
        ("GNOME", False, DesktopCaptureStatus.NO_DCONF),
    ),
)
def test_capture_skips_unknown_family_or_missing_dconf(
    monkeypatch: pytest.MonkeyPatch,
    current_desktop: str,
    binary_available: bool,
    status: DesktopCaptureStatus,
) -> None:
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", current_desktop)
    monkeypatch.delenv("XDG_SESSION_DESKTOP", raising=False)
    monkeypatch.setattr(
        desktop.shutil,
        "which",
        lambda _name: "/usr/bin/dconf" if binary_available else None,
    )
    monkeypatch.setattr(
        desktop,
        "run_command",
        lambda *_args, **_kwargs: pytest.fail("capture must not call dconf"),
    )

    result = capture_desktop_settings(
        _CaptureSettings(("/org/example/root/",)),
        existing_artifact=lambda: None,
        admit_artifact=lambda *_args: pytest.fail("capture must not admit an artifact"),
    )

    assert result.status is status


def test_disabled_capture_has_no_operational_lookups_or_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(desktop, "normalize_desktop_family", lambda *_args: pytest.fail("family"))
    monkeypatch.setattr(desktop.shutil, "which", lambda _name: pytest.fail("binary lookup"))

    result = capture_desktop_settings(
        _CaptureSettings(("/org/example/root/",), enabled=False),
        existing_artifact=lambda: pytest.fail("artifact lookup"),
        admit_artifact=lambda *_args: pytest.fail("secret admission"),
    )

    assert result.status is DesktopCaptureStatus.DISABLED


def test_capture_secret_gate_rejection_keeps_prior_artifact_and_passes_root_ack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = "/org/example/extra/"
    previous = render_desktop_settings_artifact(
        "GNOME", (DesktopSettingsSection(root, b"[old]\nvalue='old'\n"),)
    )
    _enable_gnome(monkeypatch)
    monkeypatch.setattr(
        desktop,
        "run_command",
        lambda *_args, **_kwargs: CommandResult("[new]\nvalue='new'\n", "", 0),
    )
    acknowledgements: list[tuple[str, ...]] = []

    def reject(_content: bytes, roots: object) -> None:
        acknowledgements.append(tuple(roots))
        raise ValueError("hard secret")

    result = capture_desktop_settings(
        _CaptureSettings((root,)),
        existing_artifact=lambda: previous,
        admit_artifact=reject,
        ambiguous_root_allowlist=(root,),
    )

    assert result.status is DesktopCaptureStatus.SECRET_REJECTED
    assert result.artifact is None
    assert result.prior_retained
    assert acknowledgements == [(root,)]
