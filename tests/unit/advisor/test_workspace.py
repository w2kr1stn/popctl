"""Unit tests for advisor workspace management."""

import json
from pathlib import Path

from popctl.advisor.workspace import (
    _APPLIED_MARKER,
    _SESSION_RETENTION,
    cleanup_empty_sessions,
    create_session_workspace,
    find_all_unapplied_decisions,
    list_sessions,
    mark_session_applied,
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


class TestCreateSessionWorkspace:
    """Tests for create_session_workspace function."""

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
        assert "Interaktive Paket-Klassifikation" in content
        assert "KEEP" in content
        assert "REMOVE" in content
        assert "AskUserQuestion" in content
        assert "output/decisions.toml" in content

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

    def test_chains_memory_from_previous_session(self, tmp_path: Path) -> None:
        """Falls back to memory.md from latest previous session."""
        scan = _make_scan_result()

        # Create a previous session with memory.md
        old_session = tmp_path / "20260101T100000"
        old_session.mkdir(parents=True)
        (old_session / "memory.md").write_text("# Previous Memory\n")
        (old_session / "output").mkdir()

        workspace = create_session_workspace(scan, tmp_path)

        assert (workspace / "memory.md").exists()
        assert "Previous Memory" in (workspace / "memory.md").read_text()

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

    def test_empty_directory(self, tmp_path: Path) -> None:
        """Returns empty list for empty directory."""
        assert list_sessions(tmp_path) == []

    def test_nonexistent_directory(self, tmp_path: Path) -> None:
        """Returns empty list for nonexistent directory."""
        assert list_sessions(tmp_path / "nonexistent") == []

    def test_sorted_newest_first(self, tmp_path: Path) -> None:
        """Sessions are sorted newest first."""
        (tmp_path / "20260101T100000").mkdir()
        (tmp_path / "20260301T100000").mkdir()
        (tmp_path / "20260201T100000").mkdir()

        sessions = list_sessions(tmp_path)

        assert len(sessions) == 3
        assert sessions[0].name == "20260301T100000"
        assert sessions[1].name == "20260201T100000"
        assert sessions[2].name == "20260101T100000"

    def test_ignores_hidden_dirs(self, tmp_path: Path) -> None:
        """Ignores directories starting with '.'."""
        (tmp_path / "20260101T100000").mkdir()
        (tmp_path / ".hidden").mkdir()

        sessions = list_sessions(tmp_path)

        assert len(sessions) == 1

    def test_ignores_files(self, tmp_path: Path) -> None:
        """Ignores regular files."""
        (tmp_path / "20260101T100000").mkdir()
        (tmp_path / "some_file.txt").touch()

        sessions = list_sessions(tmp_path)

        assert len(sessions) == 1


class TestSessionRetention:
    """Tests for automatic session pruning."""

    def test_prunes_old_sessions_on_create(self, tmp_path: Path) -> None:
        """Creating a workspace prunes sessions beyond retention limit."""
        scan = _make_scan_result()

        # Create more sessions than retention limit
        for i in range(_SESSION_RETENTION + 3):
            (tmp_path / f"2026010{i}T100000").mkdir()
            (tmp_path / f"2026010{i}T100000" / "output").mkdir()

        create_session_workspace(scan, tmp_path)

        sessions = list_sessions(tmp_path)
        assert len(sessions) == _SESSION_RETENTION

    def test_keeps_newest_sessions(self, tmp_path: Path) -> None:
        """Pruning keeps the most recent sessions."""
        scan = _make_scan_result()

        # Create old sessions
        for i in range(8):
            (tmp_path / f"2026010{i}T100000").mkdir()
            (tmp_path / f"2026010{i}T100000" / "output").mkdir()

        create_session_workspace(scan, tmp_path)

        sessions = list_sessions(tmp_path)
        # The newly created session + the newest old ones are kept
        names = [s.name for s in sessions]
        # Oldest sessions (20260100, 20260101, 20260102, 20260103) should be gone
        assert "20260100T100000" not in names
        assert "20260101T100000" not in names

    def test_no_pruning_below_limit(self, tmp_path: Path) -> None:
        """No pruning when session count is within limit."""
        scan = _make_scan_result()

        # Create fewer sessions than limit
        (tmp_path / "20260101T100000").mkdir()
        (tmp_path / "20260101T100000" / "output").mkdir()

        create_session_workspace(scan, tmp_path)

        sessions = list_sessions(tmp_path)
        assert len(sessions) == 2  # old + new


class TestFindAllUnappliedDecisions:
    """Tests for find_all_unapplied_decisions function."""

    def test_returns_empty_for_nonexistent_dir(self, tmp_path: Path) -> None:
        """Returns empty list when sessions dir doesn't exist."""
        assert find_all_unapplied_decisions(tmp_path / "nonexistent") == []

    def test_returns_empty_for_empty_dir(self, tmp_path: Path) -> None:
        """Returns empty list when no sessions have decisions."""
        (tmp_path / "20260101T100000" / "output").mkdir(parents=True)
        assert find_all_unapplied_decisions(tmp_path) == []

    def test_finds_single_unapplied(self, tmp_path: Path) -> None:
        """Finds a single unapplied decisions.toml."""
        session = tmp_path / "20260101T100000"
        (session / "output").mkdir(parents=True)
        (session / "output" / "decisions.toml").write_text("[packages.apt]\n")

        result = find_all_unapplied_decisions(tmp_path)

        assert len(result) == 1
        assert result[0] == session / "output" / "decisions.toml"

    def test_finds_multiple_unapplied_oldest_first(self, tmp_path: Path) -> None:
        """Returns unapplied decisions in chronological order (oldest first)."""
        for ts in ["20260101T100000", "20260201T100000", "20260301T100000"]:
            session = tmp_path / ts
            (session / "output").mkdir(parents=True)
            (session / "output" / "decisions.toml").write_text(f"# {ts}\n")

        result = find_all_unapplied_decisions(tmp_path)

        assert len(result) == 3
        assert "20260101" in str(result[0])
        assert "20260201" in str(result[1])
        assert "20260301" in str(result[2])

    def test_skips_applied_sessions(self, tmp_path: Path) -> None:
        """Skips sessions that have .applied marker."""
        # Applied session
        applied = tmp_path / "20260101T100000"
        (applied / "output").mkdir(parents=True)
        (applied / "output" / "decisions.toml").write_text("[packages.apt]\n")
        (applied / "output" / _APPLIED_MARKER).touch()

        # Unapplied session
        unapplied = tmp_path / "20260201T100000"
        (unapplied / "output").mkdir(parents=True)
        (unapplied / "output" / "decisions.toml").write_text("[packages.apt]\n")

        result = find_all_unapplied_decisions(tmp_path)

        assert len(result) == 1
        assert "20260201" in str(result[0])

    def test_returns_empty_when_all_applied(self, tmp_path: Path) -> None:
        """Returns empty list when all sessions are applied."""
        session = tmp_path / "20260101T100000"
        (session / "output").mkdir(parents=True)
        (session / "output" / "decisions.toml").write_text("[packages.apt]\n")
        (session / "output" / _APPLIED_MARKER).touch()

        assert find_all_unapplied_decisions(tmp_path) == []


class TestMarkSessionApplied:
    """Tests for mark_session_applied function."""

    def test_creates_applied_marker(self, tmp_path: Path) -> None:
        """Creates .applied marker file next to decisions.toml."""
        output = tmp_path / "output"
        output.mkdir()
        decisions = output / "decisions.toml"
        decisions.write_text("[packages.apt]\n")

        mark_session_applied(decisions)

        assert (output / _APPLIED_MARKER).exists()

    def test_idempotent(self, tmp_path: Path) -> None:
        """Calling mark_session_applied twice doesn't raise."""
        output = tmp_path / "output"
        output.mkdir()
        decisions = output / "decisions.toml"
        decisions.write_text("[packages.apt]\n")

        mark_session_applied(decisions)
        mark_session_applied(decisions)

        assert (output / _APPLIED_MARKER).exists()


class TestCleanupEmptySessions:
    """Tests for cleanup_empty_sessions function."""

    def test_returns_zero_for_nonexistent_dir(self, tmp_path: Path) -> None:
        """Returns 0 for nonexistent directory."""
        assert cleanup_empty_sessions(tmp_path / "nonexistent") == 0

    def test_removes_sessions_without_decisions(self, tmp_path: Path) -> None:
        """Removes sessions that have no decisions.toml."""
        empty = tmp_path / "20260101T100000"
        (empty / "output").mkdir(parents=True)

        removed = cleanup_empty_sessions(tmp_path)

        assert removed == 1
        assert not empty.exists()

    def test_keeps_sessions_with_decisions(self, tmp_path: Path) -> None:
        """Does not remove sessions that have decisions.toml."""
        session = tmp_path / "20260101T100000"
        (session / "output").mkdir(parents=True)
        (session / "output" / "decisions.toml").write_text("[packages.apt]\n")

        removed = cleanup_empty_sessions(tmp_path)

        assert removed == 0
        assert session.exists()

    def test_mixed_sessions(self, tmp_path: Path) -> None:
        """Removes empty sessions while keeping ones with decisions."""
        # Empty session
        empty = tmp_path / "20260101T100000"
        (empty / "output").mkdir(parents=True)

        # Session with decisions
        with_decisions = tmp_path / "20260201T100000"
        (with_decisions / "output").mkdir(parents=True)
        (with_decisions / "output" / "decisions.toml").write_text("[packages.apt]\n")

        # Another empty session
        empty2 = tmp_path / "20260301T100000"
        (empty2 / "output").mkdir(parents=True)

        removed = cleanup_empty_sessions(tmp_path)

        assert removed == 2
        assert not empty.exists()
        assert with_decisions.exists()
        assert not empty2.exists()
