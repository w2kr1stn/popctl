"""Unit tests for advisor workspace management."""

import json
from pathlib import Path

from popctl.advisor.workspace import (
    create_session_workspace,
    find_latest_decisions,
    list_sessions,
)
from popctl.models.package import PackageSource, PackageStatus, ScannedPackage
from popctl.models.scan_result import ScanResult


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
    return ScanResult.create(packages, ["apt"])


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
        assert "Interactive Package Classification" in content
        assert "KEEP" in content
        assert "REMOVE" in content
        assert "Discuss uncertain packages" in content
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


class TestFindLatestDecisions:
    """Tests for find_latest_decisions function."""

    def test_returns_none_for_empty_dir(self, tmp_path: Path) -> None:
        """Returns None when sessions dir is empty."""
        assert find_latest_decisions(tmp_path) is None

    def test_returns_none_for_nonexistent_dir(self, tmp_path: Path) -> None:
        """Returns None when sessions dir doesn't exist."""
        assert find_latest_decisions(tmp_path / "nonexistent") is None

    def test_finds_latest_decisions(self, tmp_path: Path) -> None:
        """Finds decisions.toml from most recent session."""
        # Create older session
        old_session = tmp_path / "20260101T100000"
        (old_session / "output").mkdir(parents=True)
        (old_session / "output" / "decisions.toml").write_text("[packages.apt]\n")

        # Create newer session
        new_session = tmp_path / "20260201T100000"
        (new_session / "output").mkdir(parents=True)
        (new_session / "output" / "decisions.toml").write_text("[packages.apt]\nnew = true\n")

        result = find_latest_decisions(tmp_path)

        assert result is not None
        assert "20260201" in str(result)

    def test_skips_sessions_without_decisions(self, tmp_path: Path) -> None:
        """Skips sessions that don't have decisions.toml."""
        # Session without decisions
        no_decisions = tmp_path / "20260301T100000"
        (no_decisions / "output").mkdir(parents=True)

        # Older session WITH decisions
        with_decisions = tmp_path / "20260201T100000"
        (with_decisions / "output").mkdir(parents=True)
        (with_decisions / "output" / "decisions.toml").write_text("[packages.apt]\n")

        result = find_latest_decisions(tmp_path)

        assert result is not None
        assert "20260201" in str(result)

    def test_returns_none_when_no_decisions_exist(self, tmp_path: Path) -> None:
        """Returns None when no session has decisions.toml."""
        session = tmp_path / "20260101T100000"
        (session / "output").mkdir(parents=True)

        assert find_latest_decisions(tmp_path) is None


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
