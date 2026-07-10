"""Unit tests for advisor workspace management."""

import json
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import patch

import pytest
from popctl.advisor.workspace import (
    cleanup_empty_sessions,
    create_session_workspace,
    delete_session,
    ensure_advisor_sessions_dir,
    find_all_unapplied_decisions,
    get_advisor_sessions_dir,
    list_sessions,
)
from popctl.models.package import PackageSource, PackageStatus, ScannedPackage, ScanResult


def _make_scan_result() -> ScanResult:
    """Create a minimal ScanResult for testing."""
    packages = [
        ScannedPackage(
            name="firefox",
            source=PackageSource.APT,
            version="120.0",
            status=PackageStatus.MANUAL,
            description="Web browser",
        ),
        ScannedPackage(
            name="vim",
            source=PackageSource.APT,
            version="9.0",
            status=PackageStatus.MANUAL,
        ),
    ]
    return tuple(packages)


@pytest.fixture
def sessions_dir(tmp_path: Path) -> Path:
    """Create an isolated directory for session fixtures."""
    directory = tmp_path / "sessions"
    directory.mkdir()
    return directory


class TestEnsureAdvisorSessionsDir:
    """Tests for the advisor session workspace base directory."""

    def test_defaults_to_xdg_state_dir(self, tmp_path: Path) -> None:
        """Default session workspaces use the isolated XDG state directory."""
        result = ensure_advisor_sessions_dir()

        assert result == tmp_path / "xdg-state" / "popctl" / "sessions"
        assert result.is_dir()

    def test_uses_djinn_mount_path_when_requested(self) -> None:
        """Djinn session workspaces preserve the bind-mounted path."""
        result = ensure_advisor_sessions_dir(use_djinn=True)

        assert result == Path.home() / ".djinn" / "sessions" / "popctl"
        assert result.is_dir()

    def test_resolves_sessions_dir_without_creating_it(self, tmp_path: Path) -> None:
        """The non-creating resolver supports alternate-root lookup."""
        result = get_advisor_sessions_dir(use_djinn=True)

        assert result == tmp_path / "isolated-home" / ".djinn" / "sessions" / "popctl"
        assert not result.exists()


class TestCreateSessionWorkspace:
    """Tests for create_session_workspace function."""

    @pytest.fixture(autouse=True)
    def _mock_reverse_deps(self) -> Iterator[None]:
        """Keep workspace tests independent of the host APT database."""
        with patch("popctl.advisor.workspace.get_reverse_deps", return_value={}):
            yield

    def test_creates_session_directory(self, tmp_path: Path) -> None:
        """Workspace directory is created under sessions_dir."""
        scan = _make_scan_result()

        workspace = create_session_workspace(scan, tmp_path)

        assert workspace.exists()
        assert workspace.is_dir()
        assert workspace.parent == tmp_path

    def test_creates_output_directory(self, tmp_path: Path) -> None:
        """Output subdirectory is created."""
        scan = _make_scan_result()

        workspace = create_session_workspace(scan, tmp_path)

        assert (workspace / "output").exists()
        assert (workspace / "output").is_dir()

    def test_writes_claude_md(self, tmp_path: Path) -> None:
        """CLAUDE.md is created with classification instructions."""
        scan = _make_scan_result()

        workspace = create_session_workspace(scan, tmp_path)

        claude_md = workspace / "CLAUDE.md"
        assert claude_md.exists()
        content = claude_md.read_text()
        assert "# Interactive Package Classification" in content
        assert "Debian/Ubuntu-based system (APT)" in content
        assert "Greet the user briefly in English" in content
        assert "prefer ASK over REMOVE" in content
        assert "KEEP" in content
        assert "REMOVE" in content
        assert "ASK" in content
        assert "observable usage evidence on the system" in content
        assert "the user's answers" in content
        assert "AskUserQuestion" in content
        assert "output/decisions.toml" in content
        assert "gcc, make" not in content

    def test_claude_md_filesystem_domain(self, tmp_path: Path) -> None:
        """CLAUDE.md uses filesystem template when domain='filesystem'."""
        scan = _make_scan_result()

        workspace = create_session_workspace(scan, tmp_path, domain="filesystem")

        content = (workspace / "CLAUDE.md").read_text()
        assert "# Interactive Filesystem Classification" in content
        assert "Greet the user briefly in English" in content
        assert "prefer ASK over REMOVE" in content
        assert "filesystem_orphans" in content
        assert "[filesystem]" in content
        assert "Interactive Package Classification" not in content

    def test_claude_md_configs_domain(self, tmp_path: Path) -> None:
        """CLAUDE.md uses configs template when domain='configs'."""
        scan = _make_scan_result()

        workspace = create_session_workspace(scan, tmp_path, domain="configs")

        content = (workspace / "CLAUDE.md").read_text()
        assert "# Interactive Configuration Classification" in content
        assert "Greet the user briefly in English" in content
        assert "prefer ASK over REMOVE" in content
        assert "config_orphans" in content
        assert "[configs]" in content
        assert "Interactive Package Classification" not in content

    def test_claude_md_includes_system_info(self, tmp_path: Path) -> None:
        """CLAUDE.md includes system context."""
        scan = _make_scan_result()

        workspace = create_session_workspace(
            scan, tmp_path, system_info={"hostname": "test-host", "os": "Pop!_OS 24.04"}
        )

        content = (workspace / "CLAUDE.md").read_text()
        assert "test-host" in content
        assert "Pop!_OS 24.04" in content

    def test_writes_scan_json(self, tmp_path: Path) -> None:
        """scan.json is created with package data."""
        scan = _make_scan_result()

        workspace = create_session_workspace(scan, tmp_path)

        scan_json = workspace / "scan.json"
        assert scan_json.exists()
        data = json.loads(scan_json.read_text())
        assert "packages" in data or "metadata" in data

    def test_copies_manifest(self, tmp_path: Path) -> None:
        """manifest.toml is copied when provided."""
        scan = _make_scan_result()
        manifest = tmp_path / "source" / "manifest.toml"
        manifest.parent.mkdir()
        manifest.write_text("[meta]\nversion = '1.0'\n")

        sessions_dir = tmp_path / "sessions"
        workspace = create_session_workspace(scan, sessions_dir, manifest_path=manifest)

        copied = workspace / "manifest.toml"
        assert copied.exists()
        assert "version" in copied.read_text()

    def test_skips_missing_manifest(self, tmp_path: Path) -> None:
        """Gracefully handles missing manifest path."""
        scan = _make_scan_result()
        missing = tmp_path / "nonexistent" / "manifest.toml"

        workspace = create_session_workspace(scan, tmp_path, manifest_path=missing)

        assert not (workspace / "manifest.toml").exists()
        assert workspace.exists()

    def test_timestamp_based_directory_name(self, tmp_path: Path) -> None:
        """Session directory name is a timestamp."""
        scan = _make_scan_result()

        workspace = create_session_workspace(scan, tmp_path)

        name = workspace.name
        # Format: YYYYMMDDTHHMMSS
        assert len(name) == 15
        assert "T" in name

    def test_copies_memory_md(self, tmp_path: Path) -> None:
        """memory.md is copied when provided."""
        scan = _make_scan_result()
        memory = tmp_path / "memory" / "memory.md"
        memory.parent.mkdir()
        memory.write_text("# Advisor Memory\n## Known Decisions\n")

        sessions_dir = tmp_path / "sessions"
        workspace = create_session_workspace(scan, sessions_dir, memory_path=memory)

        copied = workspace / "memory.md"
        assert copied.exists()
        assert "Advisor Memory" in copied.read_text()

    def test_skips_missing_memory(self, tmp_path: Path) -> None:
        """Gracefully handles missing memory path."""
        scan = _make_scan_result()
        missing = tmp_path / "nonexistent" / "memory.md"

        workspace = create_session_workspace(scan, tmp_path, memory_path=missing)

        assert not (workspace / "memory.md").exists()
        assert workspace.exists()

    def test_creates_claude_settings_json(self, tmp_path: Path) -> None:
        """Workspace includes .claude/settings.json with auto-allow permissions."""
        scan = _make_scan_result()

        workspace = create_session_workspace(scan, tmp_path)

        settings_file = workspace / ".claude" / "settings.json"
        assert settings_file.exists()
        data = json.loads(settings_file.read_text())
        assert "Bash" in data["permissions"]["allow"]
        assert any("rm" in rule for rule in data["permissions"]["deny"])

    def test_raises_on_permission_error(self, tmp_path: Path) -> None:
        """Raises RuntimeError when directory cannot be created."""
        scan = _make_scan_result()
        readonly_dir = tmp_path / "readonly"
        readonly_dir.mkdir()
        readonly_dir.chmod(0o444)

        try:
            create_session_workspace(scan, readonly_dir / "nested")
            assert False, "Should have raised"  # noqa: B011
        except RuntimeError:
            pass
        finally:
            readonly_dir.chmod(0o755)


class TestListSessions:
    """Tests for list_sessions function."""

    def test_empty_directory(self, sessions_dir: Path) -> None:
        """Returns empty list for empty directory."""
        assert list_sessions(sessions_dir) == []

    def test_nonexistent_directory(self, tmp_path: Path) -> None:
        """Returns empty list for nonexistent directory."""
        assert list_sessions(tmp_path / "nonexistent") == []

    def test_sorted_newest_first(self, sessions_dir: Path) -> None:
        """Sessions are sorted newest first."""
        (sessions_dir / "20260101T100000").mkdir()
        (sessions_dir / "20260301T100000").mkdir()
        (sessions_dir / "20260201T100000").mkdir()

        sessions = list_sessions(sessions_dir)

        assert len(sessions) == 3
        assert sessions[0].name == "20260301T100000"
        assert sessions[1].name == "20260201T100000"
        assert sessions[2].name == "20260101T100000"

    def test_ignores_hidden_dirs(self, sessions_dir: Path) -> None:
        """Ignores directories starting with '.'."""
        (sessions_dir / "20260101T100000").mkdir()
        (sessions_dir / ".hidden").mkdir()

        sessions = list_sessions(sessions_dir)

        assert len(sessions) == 1

    def test_ignores_files(self, sessions_dir: Path) -> None:
        """Ignores regular files."""
        (sessions_dir / "20260101T100000").mkdir()
        (sessions_dir / "some_file.txt").touch()

        sessions = list_sessions(sessions_dir)

        assert len(sessions) == 1


class TestFindAllUnappliedDecisions:
    """Tests for find_all_unapplied_decisions function."""

    def test_returns_empty_for_nonexistent_dir(self, tmp_path: Path) -> None:
        """Returns empty list when sessions dir doesn't exist."""
        assert find_all_unapplied_decisions(tmp_path / "nonexistent") == []

    def test_returns_empty_for_empty_dir(self, sessions_dir: Path) -> None:
        """Returns empty list when no sessions have decisions."""
        (sessions_dir / "20260101T100000" / "output").mkdir(parents=True)
        assert find_all_unapplied_decisions(sessions_dir) == []

    def test_finds_single_unapplied(self, sessions_dir: Path) -> None:
        """Finds a single unapplied decisions.toml."""
        session = sessions_dir / "20260101T100000"
        (session / "output").mkdir(parents=True)
        (session / "output" / "decisions.toml").write_text("[packages.apt]\n")

        result = find_all_unapplied_decisions(sessions_dir)

        assert len(result) == 1
        assert result[0] == session / "output" / "decisions.toml"

    def test_finds_multiple_unapplied_oldest_first(self, sessions_dir: Path) -> None:
        """Returns unapplied decisions in chronological order (oldest first)."""
        for ts in ["20260101T100000", "20260201T100000", "20260301T100000"]:
            session = sessions_dir / ts
            (session / "output").mkdir(parents=True)
            (session / "output" / "decisions.toml").write_text(f"# {ts}\n")

        result = find_all_unapplied_decisions(sessions_dir)

        assert len(result) == 3
        assert "20260101" in str(result[0])
        assert "20260201" in str(result[1])
        assert "20260301" in str(result[2])

    def test_returns_empty_when_no_sessions_remain(self, sessions_dir: Path) -> None:
        """Returns empty list when sessions dir has no session directories."""
        assert find_all_unapplied_decisions(sessions_dir) == []


class TestDeleteSession:
    """Tests for delete_session function."""

    def test_deletes_session_directory(self, tmp_path: Path) -> None:
        """Deletes the entire session directory after apply."""
        session = tmp_path / "20260101T100000"
        (session / "output").mkdir(parents=True)
        decisions = session / "output" / "decisions.toml"
        decisions.write_text("[packages.apt]\n")

        delete_session(decisions)

        assert not session.exists()

    def test_idempotent(self, tmp_path: Path) -> None:
        """Calling delete_session on already-deleted session doesn't raise."""
        session = tmp_path / "20260101T100000"
        (session / "output").mkdir(parents=True)
        decisions = session / "output" / "decisions.toml"
        decisions.write_text("[packages.apt]\n")

        delete_session(decisions)
        delete_session(decisions)  # Should not raise

        assert not session.exists()


class TestCleanupEmptySessions:
    """Tests for cleanup_empty_sessions function."""

    def test_returns_zero_for_nonexistent_dir(self, tmp_path: Path) -> None:
        """Returns 0 for nonexistent directory."""
        assert cleanup_empty_sessions(tmp_path / "nonexistent") == 0

    def test_removes_sessions_without_decisions(self, sessions_dir: Path) -> None:
        """Removes sessions that have no decisions.toml."""
        empty = sessions_dir / "20260101T100000"
        (empty / "output").mkdir(parents=True)

        removed = cleanup_empty_sessions(sessions_dir)

        assert removed == 1
        assert not empty.exists()

    def test_keeps_sessions_with_decisions(self, sessions_dir: Path) -> None:
        """Does not remove sessions that have decisions.toml."""
        session = sessions_dir / "20260101T100000"
        (session / "output").mkdir(parents=True)
        (session / "output" / "decisions.toml").write_text("[packages.apt]\n")

        removed = cleanup_empty_sessions(sessions_dir)

        assert removed == 0
        assert session.exists()

    def test_mixed_sessions(self, sessions_dir: Path) -> None:
        """Removes empty sessions while keeping ones with decisions."""
        # Empty session
        empty = sessions_dir / "20260101T100000"
        (empty / "output").mkdir(parents=True)

        # Session with decisions
        with_decisions = sessions_dir / "20260201T100000"
        (with_decisions / "output").mkdir(parents=True)
        (with_decisions / "output" / "decisions.toml").write_text("[packages.apt]\n")

        # Another empty session
        empty2 = sessions_dir / "20260301T100000"
        (empty2 / "output").mkdir(parents=True)

        removed = cleanup_empty_sessions(sessions_dir)

        assert removed == 2
        assert not empty.exists()
        assert with_decisions.exists()
        assert not empty2.exists()
