from __future__ import annotations

from pathlib import Path

import pytest
from popctl.dotfiles.discovery import (
    BlockedCandidateKind,
    Candidate,
    discover_dotfiles,
)


def _write(home: Path, path: str, content: bytes = b"setting = true\n") -> Path:
    target = home / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return target


def test_discovery_returns_exact_leaves_in_lexical_order(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _write(home, ".zshrc")
    _write(home, ".bash_profile")
    _write(home, ".config/tool/z.conf")
    _write(home, ".config/tool/a.conf")

    result = discover_dotfiles(home)

    assert result.candidate_paths == (
        ".bash_profile",
        ".config/tool/a.conf",
        ".config/tool/z.conf",
        ".zshrc",
    )
    assert [candidate.group for candidate in result.candidates] == [
        ".bash_profile",
        ".config",
        ".config",
        ".zshrc",
    ]
    assert all(candidate.path != ".config" for candidate in result.candidates)


def test_directory_entry_limit_blocks_before_leaf_sorting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _write(home, ".config/z.conf")
    _write(home, ".config/a.conf")
    monkeypatch.setattr("popctl.dotfiles.discovery.MAX_DIRECTORY_ENTRIES", 1)

    result = discover_dotfiles(home)

    assert result.candidates == ()
    assert len(result.blocked) == 1
    assert result.blocked[0].path == ".config"
    assert result.blocked[0].category == "discovery-directory-entry-limit"


def test_depth_bound_emits_a_redacted_block_for_each_truncated_subtree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _write(home, ".bashrc")
    _write(home, ".config/top.conf")
    _write(home, ".config/nested/leaf.conf")
    monkeypatch.setattr("popctl.dotfiles.discovery.MAX_DISCOVERY_DEPTH", 2)

    result = discover_dotfiles(home)

    assert result.candidate_paths == (".bashrc", ".config/top.conf")
    assert result.blocked[0].path == ".config/nested"
    assert result.blocked[0].category == "discovery-depth-limit"
    assert result.blocked[0].actionable


def test_file_bound_emits_a_redacted_block(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _write(home, ".bashrc")
    _write(home, ".zshrc")
    monkeypatch.setattr("popctl.dotfiles.discovery.MAX_DISCOVERY_FILES", 1)

    result = discover_dotfiles(home)

    assert result.candidate_paths == (".bashrc",)
    assert result.blocked[0].path == ".zshrc"
    assert result.blocked[0].category == "discovery-file-limit"


def test_permission_error_fails_the_directory_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    config = home / ".config"
    config.mkdir()

    def fail_config(path: Path) -> list[object]:
        if path == config:
            raise PermissionError
        return []

    monkeypatch.setattr("popctl.dotfiles.discovery._bounded_directory_entries", fail_config)

    result = discover_dotfiles(home)

    assert result.candidates == ()
    assert len(result.blocked) == 1
    assert result.blocked[0].path == ".config"
    assert result.blocked[0].category == "unreadable-directory"
    assert result.blocked[0].kind is BlockedCandidateKind.ACTIONABLE


def test_tracked_and_ignored_paths_are_subtracted_per_leaf(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _write(home, ".bashrc")
    _write(home, ".zshrc")
    _write(home, ".config/tool/config.toml")

    result = discover_dotfiles(
        home,
        tracked_files=(".bashrc",),
        ignored=(".zshrc",),
    )

    assert result.candidate_paths == (".config/tool/config.toml",)


def test_hard_exclusions_and_actionable_blocks_are_redacted(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _write(home, ".config/popctl/dotfiles.toml")
    _write(home, ".config/tool/secret", b"Authorization: Bearer opaque-value\n")

    result = discover_dotfiles(home)

    hard_exclusion = next(
        blocked for blocked in result.blocked if blocked.path.endswith("dotfiles.toml")
    )
    content_block = next(blocked for blocked in result.blocked if blocked.path.endswith("secret"))
    assert hard_exclusion.expected
    assert hard_exclusion.category == ".config/popctl/**"
    assert content_block.actionable
    assert content_block.category == "authorization"
    assert "opaque-value" not in repr(result.blocked)


@pytest.mark.parametrize(
    "content",
    [
        b"curl -ualice:password https://example.invalid\n",
        b"curl -u'alice:password' https://example.invalid\n",
        b'curl --user alice:"password" https://example.invalid\n',
        b"curl --proxy-user alice:password https://example.invalid\n",
    ],
)
def test_discovery_blocks_shell_curl_credential_flags(tmp_path: Path, content: bytes) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _write(home, ".zshrc", content)

    result = discover_dotfiles(home)

    assert result.candidates == ()
    assert [(blocked.path, blocked.category) for blocked in result.blocked] == [
        (".zshrc", "curl-user-password"),
    ]


def test_binary_and_oversize_leaves_are_not_candidates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _write(home, ".bashrc", b"binary\x00content")
    _write(home, ".zshrc", b"too-large")
    monkeypatch.setattr("popctl.dotfiles.discovery.MAX_CANDIDATE_BYTES", 3)

    result = discover_dotfiles(home)

    assert result.candidates == ()
    assert {(blocked.path, blocked.category) for blocked in result.blocked} == {
        (".bashrc", "oversize"),
        (".zshrc", "oversize"),
    }


def test_binary_leaf_is_actionably_blocked(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _write(home, ".bashrc", b"binary\x00content")

    result = discover_dotfiles(home)

    assert result.candidates == ()
    assert result.blocked[0].path == ".bashrc"
    assert result.blocked[0].category == "binary-content"


def test_discovery_never_returns_a_directory_approval_unit(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    _write(home, ".config/tool/config.toml")

    result = discover_dotfiles(home)

    assert result.candidates == (Candidate(path=".config/tool/config.toml", group=".config"),)
