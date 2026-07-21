from __future__ import annotations

from pathlib import Path

import pytest
from popctl.dotfiles import desktop
from popctl.dotfiles.desktop import (
    DEFAULT_ROOTS,
    MAX_DESKTOP_SETTINGS_ARTIFACT_BYTES,
    DesktopCaptureStatus,
    DesktopLoadStatus,
    DesktopSettingsArtifactError,
    DesktopSettingsSection,
    canonical_dconf_root,
    capture_desktop_settings,
    load_desktop_settings,
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
    "root",
    (
        "relative/root/",
        "/org/example",
        "/org//example/",
        "/org/./example/",
        "/org/../example/",
        "/org\\example/",
        "/org/example root/",
        "/org/example\x1froot/",
    ),
)
def test_load_rejects_raw_artifact_root_grammar_before_dconf(
    root: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw_root = root.encode("utf-8")
    artifact = (
        b"# popctl-desktop-settings v1\n# family: GNOME\n# root: "
        + raw_root
        + b"\n# end-header\n# root: "
        + raw_root
        + b"\n"
    )
    monkeypatch.setattr(desktop, "run_command", lambda *_args, **_kwargs: pytest.fail("dconf"))

    result = load_desktop_settings(
        _CaptureSettings(("/org/example/",)),
        existing_artifact=lambda: artifact,
    )

    assert result.status is DesktopLoadStatus.INVALID_ARTIFACT


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


def test_capture_and_load_preserve_non_ascii_gvariant_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = "/org/example/utf8/"
    body = "[settings]\nlabel='café für Änne'\n"
    calls: list[tuple[list[str], dict[str, object]]] = []
    _enable_gnome(monkeypatch)
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/tmp/test-bus")

    def dconf(args: list[str], **kwargs: object) -> CommandResult:
        calls.append((args, kwargs))
        if args[1] == "dump":
            return CommandResult(body, "", 0)
        assert kwargs["input_text"] == body
        return CommandResult("", "", 0)

    monkeypatch.setattr(desktop, "run_command", dconf)
    captured = capture_desktop_settings(
        _CaptureSettings((root,)),
        existing_artifact=lambda: None,
        admit_artifact=lambda *_args: None,
    )

    assert captured.status is DesktopCaptureStatus.CHANGED
    assert captured.artifact is not None
    parsed = parse_desktop_settings_artifact(captured.artifact)
    assert parsed.sections[0].body == body.encode("utf-8")

    loaded = load_desktop_settings(
        _CaptureSettings((root,)),
        existing_artifact=lambda: captured.artifact,
    )

    assert loaded.status is DesktopLoadStatus.APPLIED
    assert calls == [
        (["dconf", "dump", root], {"env": {"LC_ALL": "C"}}),
        (["dconf", "load", "-f", root], {"input_text": body, "env": {"LC_ALL": "C"}}),
    ]


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


class _LoadSettings:
    def __init__(self, roots: tuple[str, ...], *, enabled: bool = True) -> None:
        self.enabled = enabled
        self.effective_roots = roots


def _load_artifact(*sections: DesktopSettingsSection, family: str = "GNOME") -> bytes:
    return render_desktop_settings_artifact(family, sections)


def _enable_load_gnome(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "GNOME")
    monkeypatch.delenv("XDG_SESSION_DESKTOP", raising=False)
    monkeypatch.setenv("DBUS_SESSION_BUS_ADDRESS", "unix:path=/run/user/1000/bus")
    monkeypatch.setattr(
        desktop.shutil,
        "which",
        lambda name: "/usr/bin/dconf" if name == "dconf" else None,
    )


def test_load_is_disabled_before_every_operational_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(desktop, "normalize_desktop_family", lambda *_args: pytest.fail("family"))
    monkeypatch.setattr(desktop.shutil, "which", lambda _name: pytest.fail("binary"))

    result = load_desktop_settings(
        _LoadSettings(("/org/example/root/",), enabled=False),
        existing_artifact=lambda: pytest.fail("artifact"),
    )

    assert result.status is DesktopLoadStatus.DISABLED


def test_load_reports_no_artifact_before_dconf(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(desktop.shutil, "which", lambda _name: pytest.fail("binary"))

    result = load_desktop_settings(
        _LoadSettings(("/org/example/root/",)), existing_artifact=lambda: None
    )

    assert result.status is DesktopLoadStatus.NO_ARTIFACT


def test_load_reports_invalid_artifact_before_family_or_dconf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(desktop, "normalize_desktop_family", lambda *_args: pytest.fail("family"))
    monkeypatch.setattr(desktop.shutil, "which", lambda _name: pytest.fail("binary"))

    result = load_desktop_settings(
        _LoadSettings(("/org/example/root/",)), existing_artifact=lambda: b"not an artifact\n"
    )

    assert result.status is DesktopLoadStatus.INVALID_ARTIFACT


def test_load_reports_unknown_or_mismatched_family_without_dconf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    artifact = _load_artifact(DesktopSettingsSection("/org/example/root/", b""))
    monkeypatch.setattr(desktop.shutil, "which", lambda _name: pytest.fail("binary"))
    monkeypatch.setattr(desktop, "run_command", lambda *_args, **_kwargs: pytest.fail("dconf"))

    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "unsupported")
    unknown = load_desktop_settings(
        _LoadSettings(("/org/example/root/",)), existing_artifact=lambda: artifact
    )
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "COSMIC")
    mismatch = load_desktop_settings(
        _LoadSettings(("/org/example/root/",)), existing_artifact=lambda: artifact
    )

    assert unknown.status is DesktopLoadStatus.UNKNOWN_FAMILY
    assert mismatch.status is DesktopLoadStatus.FAMILY_MISMATCH


def test_load_reports_suppressed_roots_before_a_family_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    authorized = "/org/example/authorized/"
    disabled = "/org/example/disabled/"
    artifact = _load_artifact(
        DesktopSettingsSection(authorized, b""),
        DesktopSettingsSection(disabled, b""),
    )
    monkeypatch.setenv("XDG_CURRENT_DESKTOP", "COSMIC")
    monkeypatch.delenv("XDG_SESSION_DESKTOP", raising=False)
    monkeypatch.setattr(desktop.shutil, "which", lambda _name: pytest.fail("binary"))
    monkeypatch.setattr(desktop, "run_command", lambda *_args, **_kwargs: pytest.fail("dconf"))

    result = load_desktop_settings(
        _LoadSettings((authorized,)),
        existing_artifact=lambda: artifact,
    )

    assert result.status is DesktopLoadStatus.FAMILY_MISMATCH
    assert result.suppressed_roots == (disabled,)


def test_load_reports_missing_dconf_after_parsing_and_family_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = "/org/example/root/"
    _enable_load_gnome(monkeypatch)
    monkeypatch.setattr(desktop.shutil, "which", lambda _name: None)

    result = load_desktop_settings(
        _LoadSettings((root,)),
        existing_artifact=lambda: _load_artifact(DesktopSettingsSection(root, b"")),
    )

    assert result.status is DesktopLoadStatus.NO_DCONF
    assert result.parsed_roots == (root,)


def test_load_absent_session_hint_makes_zero_dconf_calls(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = "/org/example/root/"
    _enable_load_gnome(monkeypatch)
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    monkeypatch.setattr(desktop, "run_command", lambda *_args, **_kwargs: pytest.fail("dconf"))

    result = load_desktop_settings(
        _LoadSettings((root,)),
        existing_artifact=lambda: _load_artifact(DesktopSettingsSection(root, b"")),
    )

    assert result.status is DesktopLoadStatus.NO_SESSION


@pytest.mark.parametrize(
    "stderr",
    (
        "error: Could not connect: No such file or directory",
        "error: Could not connect: Connection refused",
        "error: The given address is empty",
    ),
)
def test_load_classifies_stale_address_and_dead_socket_as_no_session(
    monkeypatch: pytest.MonkeyPatch,
    stderr: str,
) -> None:
    root = "/org/example/root/"
    _enable_load_gnome(monkeypatch)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fail(args: list[str], **kwargs: object) -> CommandResult:
        calls.append((args, kwargs))
        return CommandResult("", stderr, 1)

    monkeypatch.setattr(desktop, "run_command", fail)
    result = load_desktop_settings(
        _LoadSettings((root,)),
        existing_artifact=lambda: _load_artifact(DesktopSettingsSection(root, b"[x]\ny=1\n")),
    )

    assert result.status is DesktopLoadStatus.NO_SESSION
    assert result.root == root
    assert calls[0][0] == ["dconf", "load", "-f", root]
    assert calls[0][1] == {"input_text": "[x]\ny=1\n", "env": {"LC_ALL": "C"}}


def test_load_classifies_a_dead_runtime_bus_socket_as_no_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = "/org/example/root/"
    _enable_load_gnome(monkeypatch)
    monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS")
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    (runtime / "bus").touch()
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
    monkeypatch.setattr(
        desktop,
        "run_command",
        lambda *_args, **_kwargs: CommandResult(
            "", "error: Could not connect: Connection refused", 1
        ),
    )

    result = load_desktop_settings(
        _LoadSettings((root,)),
        existing_artifact=lambda: _load_artifact(DesktopSettingsSection(root, b"")),
    )

    assert result.status is DesktopLoadStatus.NO_SESSION


def test_load_malformed_gvariant_nonzero_is_a_failed_root(monkeypatch: pytest.MonkeyPatch) -> None:
    root = "/org/example/root/"
    _enable_load_gnome(monkeypatch)
    monkeypatch.setattr(
        desktop,
        "run_command",
        lambda *_args, **_kwargs: CommandResult("", "error: malformed GVariant body", 1),
    )

    result = load_desktop_settings(
        _LoadSettings((root,)),
        existing_artifact=lambda: _load_artifact(DesktopSettingsSection(root, b"[x]\ny=broken\n")),
    )

    assert result.status is DesktopLoadStatus.FAILED
    assert result.root == root
    assert result.detail == "error: malformed GVariant body"


def test_load_malformed_gvariant_with_connect_phrase_is_a_failed_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = "/org/example/root/"
    detail = "error: malformed GVariant body: Could not connect: Connection refused"
    _enable_load_gnome(monkeypatch)
    monkeypatch.setattr(
        desktop,
        "run_command",
        lambda *_args, **_kwargs: CommandResult("", detail, 1),
    )

    result = load_desktop_settings(
        _LoadSettings((root,)),
        existing_artifact=lambda: _load_artifact(DesktopSettingsSection(root, b"[x]\ny=broken\n")),
    )

    assert result.status is DesktopLoadStatus.FAILED
    assert result.root == root
    assert result.detail == detail


def test_load_applies_each_authorized_root_with_verbatim_argv_and_stdin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = "/org/example/first/"
    second = "/org/example/second/"
    _enable_load_gnome(monkeypatch)
    calls: list[tuple[list[str], dict[str, object]]] = []

    def load(args: list[str], **kwargs: object) -> CommandResult:
        calls.append((args, kwargs))
        return CommandResult("", "", 0)

    monkeypatch.setattr(desktop, "run_command", load)
    result = load_desktop_settings(
        _LoadSettings((first, second)),
        existing_artifact=lambda: _load_artifact(
            DesktopSettingsSection(second, b"[second]\nvalue=2\n"),
            DesktopSettingsSection(first, b"[first]\nvalue=1\n"),
        ),
    )

    assert result.status is DesktopLoadStatus.APPLIED
    assert result.applied_roots == (first, second)
    assert calls == [
        (
            ["dconf", "load", "-f", first],
            {"input_text": "[first]\nvalue=1\n", "env": {"LC_ALL": "C"}},
        ),
        (
            ["dconf", "load", "-f", second],
            {"input_text": "[second]\nvalue=2\n", "env": {"LC_ALL": "C"}},
        ),
    ]
    assert all(call[0][-1].endswith("/") and not call[0][-1].endswith("//") for call in calls)


def test_load_suppresses_currently_unapproved_roots_without_dconf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = "/org/example/root/"
    crafted = "/org/crafted/root/"
    _enable_load_gnome(monkeypatch)
    calls: list[list[str]] = []

    def load(args: list[str], **_kwargs: object) -> CommandResult:
        calls.append(args)
        return CommandResult("", "", 0)

    monkeypatch.setattr(desktop, "run_command", load)
    result = load_desktop_settings(
        _LoadSettings((root,)),
        existing_artifact=lambda: _load_artifact(
            DesktopSettingsSection(root, b""), DesktopSettingsSection(crafted, b"")
        ),
    )

    assert result.status is DesktopLoadStatus.APPLIED
    assert result.applied_roots == (root,)
    assert result.suppressed_roots == (crafted,)
    assert calls == [["dconf", "load", "-f", root]]


def test_load_retains_applied_roots_and_stops_on_later_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = "/org/example/first/"
    second = "/org/example/second/"
    third = "/org/example/third/"
    _enable_load_gnome(monkeypatch)
    calls: list[list[str]] = []

    def load(args: list[str], **_kwargs: object) -> CommandResult:
        calls.append(args)
        if args[-1] == second:
            return CommandResult("", "dconf rejected this root", 1)
        return CommandResult("", "", 0)

    monkeypatch.setattr(desktop, "run_command", load)
    result = load_desktop_settings(
        _LoadSettings((first, second, third)),
        existing_artifact=lambda: _load_artifact(
            DesktopSettingsSection(first, b""),
            DesktopSettingsSection(second, b""),
            DesktopSettingsSection(third, b""),
        ),
    )

    assert result.status is DesktopLoadStatus.FAILED
    assert result.root == second
    assert result.detail == "dconf rejected this root"
    assert result.applied_roots == (first,)
    assert calls == [["dconf", "load", "-f", first], ["dconf", "load", "-f", second]]


def test_dry_run_previews_without_dconf_lookup_or_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    root = "/org/example/root/"
    _enable_load_gnome(monkeypatch)
    monkeypatch.setattr(desktop.shutil, "which", lambda _name: pytest.fail("binary"))
    monkeypatch.setattr(desktop, "run_command", lambda *_args, **_kwargs: pytest.fail("dconf"))

    result = load_desktop_settings(
        _LoadSettings((root,)),
        existing_artifact=lambda: _load_artifact(DesktopSettingsSection(root, b"")),
        dry_run=True,
    )

    assert result.status is DesktopLoadStatus.PREVIEW
    assert result.parsed_roots == (root,)
