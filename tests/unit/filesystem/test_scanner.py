"""Tests for FilesystemScanner orphan detection."""

from pathlib import Path
from unittest.mock import patch

from popctl.filesystem.models import OrphanReason, PathStatus, PathType
from popctl.filesystem.scanner import FilesystemScanner
from popctl.utils.shell import CommandResult


def _dpkg_not_found(*_args: object, **_kwargs: object) -> CommandResult:
    """Simulate dpkg -S returning 'not found' (package does not own path)."""
    return CommandResult(stdout="", stderr="dpkg-query: no path found", returncode=1)


def _dpkg_found(*_args: object, **_kwargs: object) -> CommandResult:
    """Simulate dpkg -S returning success (package owns path)."""
    return CommandResult(stdout="some-package: /path", stderr="", returncode=0)


def _no_apps(*_args: object, **_kwargs: object) -> CommandResult:
    """Simulate flatpak/snap returning no apps."""
    return CommandResult(stdout="", stderr="", returncode=1)


def _flatpak_apps() -> CommandResult:
    """Simulate flatpak list returning app IDs."""
    return CommandResult(
        stdout="org.mozilla.firefox\norg.gnome.Calculator\n",
        stderr="",
        returncode=0,
    )


def _snap_apps() -> CommandResult:
    """Simulate snap list returning installed snaps."""
    return CommandResult(
        stdout=(
            "Name    Version  Rev  Tracking  Publisher  Notes\n"
            "spotify  1.2.3  100  stable    spotify  -\n"
        ),
        stderr="",
        returncode=0,
    )


def _route_command(args: list[str], **_kwargs: object) -> CommandResult:
    """Route mock commands based on the command being called."""
    cmd = args[0] if args else ""
    if cmd == "dpkg":
        return _dpkg_not_found()
    if cmd == "flatpak":
        return _flatpak_apps()
    if cmd == "snap":
        return _snap_apps()
    if cmd == "dpkg-query":
        return CommandResult(
            stdout="vim\ncurl\nwget\n",
            stderr="",
            returncode=0,
        )
    return CommandResult(stdout="", stderr="", returncode=1)


def _route_command_no_apps(args: list[str], **_kwargs: object) -> CommandResult:
    """Route commands but all external tools return nothing."""
    cmd = args[0] if args else ""
    if cmd == "dpkg":
        return _dpkg_not_found()
    if cmd in ("flatpak", "snap", "dpkg-query"):
        return _no_apps()
    return CommandResult(stdout="", stderr="", returncode=1)


class TestScanEmptyDirectory:
    """Tests for scanning empty directories."""

    @patch("popctl.domain.ownership.run_command", side_effect=_route_command_no_apps)
    def test_scan_empty_directory(self, _mock_cmd: object, tmp_path: Path) -> None:
        """Scanning an empty target directory yields no results."""
        target = tmp_path / "config"
        target.mkdir()

        scanner = FilesystemScanner(targets=(target,))
        results = list(scanner.scan())
        assert results == []

    @patch("popctl.domain.ownership.run_command", side_effect=_route_command_no_apps)
    def test_scan_handles_missing_target(self, _mock_cmd: object, tmp_path: Path) -> None:
        """Non-existent target directories are skipped silently."""
        nonexistent = tmp_path / "does-not-exist"
        scanner = FilesystemScanner(targets=(nonexistent,))
        results = list(scanner.scan())
        assert results == []


class TestScanFindsOrphans:
    """Tests for orphan detection."""

    @patch("popctl.domain.ownership.run_command", side_effect=_route_command_no_apps)
    @patch("popctl.filesystem.scanner.is_protected_path", return_value=False)
    def test_scan_finds_orphans(
        self,
        _mock_protected: object,
        _mock_cmd: object,
        tmp_path: Path,
    ) -> None:
        """Directories not owned by dpkg or apps are reported as orphans."""
        config = tmp_path / "config"
        config.mkdir()
        orphan_dir = config / "old-app"
        orphan_dir.mkdir()

        scanner = FilesystemScanner(targets=(config,))
        results = list(scanner.scan())

        assert len(results) == 1
        assert results[0].status == PathStatus.ORPHAN
        assert results[0].path == str(orphan_dir)
        assert results[0].path_type == PathType.DIRECTORY

    @patch("popctl.domain.ownership.run_command", side_effect=_route_command_no_apps)
    @patch("popctl.filesystem.scanner.is_protected_path", return_value=False)
    def test_scan_orphan_has_correct_fields(
        self,
        _mock_protected: object,
        _mock_cmd: object,
        tmp_path: Path,
    ) -> None:
        """Orphaned ScannedPath has all expected fields populated."""
        config = tmp_path / "config"
        config.mkdir()
        orphan = config / "stale-app"
        orphan.mkdir()
        (orphan / "data.txt").write_text("content")

        scanner = FilesystemScanner(targets=(config,))
        results = list(scanner.scan())

        assert len(results) == 1
        result = results[0]
        assert result.orphan_reason is not None
        assert result.confidence > 0.0
        assert result.mtime is not None
        assert result.size_bytes is not None
        assert result.description is None


class TestScanSkipsOwnedPackages:
    """Tests for dpkg ownership detection."""

    @patch("popctl.filesystem.scanner.is_protected_path", return_value=False)
    def test_scan_skips_owned_by_dpkg(
        self,
        _mock_protected: object,
        tmp_path: Path,
    ) -> None:
        """Directories owned by dpkg are skipped (OWNED status)."""
        config = tmp_path / "config"
        config.mkdir()
        owned_dir = config / "vim"
        owned_dir.mkdir()

        def route(args: list[str], **_kw: object) -> CommandResult:
            if args[0] == "dpkg" and args[1] == "-S":
                return _dpkg_found()
            return _no_apps()

        with patch("popctl.domain.ownership.run_command", side_effect=route):
            scanner = FilesystemScanner(targets=(config,))
            results = list(scanner.scan())

        assert len(results) == 0


class TestScanSkipsProtectedPaths:
    """Tests for protected path handling."""

    @patch("popctl.domain.ownership.run_command", side_effect=_route_command_no_apps)
    def test_scan_skips_protected_paths(
        self,
        _mock_cmd: object,
        tmp_path: Path,
    ) -> None:
        """Protected paths are skipped and not reported as orphans."""
        config = tmp_path / "config"
        config.mkdir()
        protected_dir = config / "popctl"
        protected_dir.mkdir()
        orphan_dir = config / "unknown-app"
        orphan_dir.mkdir()

        def mock_protected(path: str) -> bool:
            return "popctl" in path

        with patch("popctl.filesystem.scanner.is_protected_path", side_effect=mock_protected):
            scanner = FilesystemScanner(targets=(config,))
            results = list(scanner.scan())

        paths = [r.path for r in results]
        assert str(protected_dir) not in paths
        assert str(orphan_dir) in paths


class TestScanAppNameMatching:
    """Tests for app name matching (flatpak/snap)."""

    @patch("popctl.filesystem.scanner.is_protected_path", return_value=False)
    def test_scan_skips_owned_by_app(
        self,
        _mock_protected: object,
        tmp_path: Path,
    ) -> None:
        """Directories matching an installed app name are skipped."""
        config = tmp_path / "config"
        config.mkdir()
        # Create a dir matching a flatpak app component
        firefox_dir = config / "firefox"
        firefox_dir.mkdir()

        with patch("popctl.domain.ownership.run_command", side_effect=_route_command):
            scanner = FilesystemScanner(targets=(config,))
            results = list(scanner.scan())

        paths = [r.path for r in results]
        assert str(firefox_dir) not in paths


class TestScanConfidence:
    """Tests for confidence score calculation."""

    def test_scan_confidence_cache_high(self, tmp_path: Path) -> None:
        """Cache directories get 0.95 confidence."""
        scanner = FilesystemScanner()
        cache_dir = tmp_path / ".cache"
        confidence = scanner._calculate_confidence(str(cache_dir))
        assert confidence == 0.95

    def test_scan_confidence_config_default(self, tmp_path: Path) -> None:
        """Config directories get default 0.60 confidence (owned by ConfigScanner)."""
        scanner = FilesystemScanner()
        config_dir = tmp_path / ".config"
        confidence = scanner._calculate_confidence(str(config_dir))
        assert confidence == 0.60

    def test_scan_confidence_data_medium(self, tmp_path: Path) -> None:
        """Data directories get 0.75 confidence."""
        scanner = FilesystemScanner()
        data_dir = tmp_path / ".local" / "share"
        confidence = scanner._calculate_confidence(str(data_dir))
        assert confidence == 0.75

    def test_scan_confidence_etc_low(self) -> None:
        """/etc directories get 0.50 confidence."""
        scanner = FilesystemScanner()
        confidence = scanner._calculate_confidence("/etc")
        assert confidence == 0.50

    def test_scan_confidence_unknown_target(self, tmp_path: Path) -> None:
        """Unknown target directories get 0.60 default confidence."""
        scanner = FilesystemScanner()
        unknown = tmp_path / "something"
        confidence = scanner._calculate_confidence(str(unknown))
        assert confidence == 0.60


class TestScanFileHandling:
    """Tests for file inclusion/exclusion behavior."""

    @patch("popctl.domain.ownership.run_command", side_effect=_route_command_no_apps)
    @patch("popctl.filesystem.scanner.is_protected_path", return_value=False)
    def test_scan_excludes_files_by_default(
        self,
        _mock_protected: object,
        _mock_cmd: object,
        tmp_path: Path,
    ) -> None:
        """Files are excluded from scan results when include_files=False."""
        config = tmp_path / "config"
        config.mkdir()
        (config / "orphan-dir").mkdir()
        (config / "orphan-file.conf").write_text("content")

        scanner = FilesystemScanner(targets=(config,))
        results = list(scanner.scan())

        types = {r.path_type for r in results}
        assert PathType.FILE not in types
        assert PathType.DIRECTORY in types

    @patch("popctl.domain.ownership.run_command", side_effect=_route_command_no_apps)
    @patch("popctl.filesystem.scanner.is_protected_path", return_value=False)
    def test_scan_includes_files_when_flag_set(
        self,
        _mock_protected: object,
        _mock_cmd: object,
        tmp_path: Path,
    ) -> None:
        """Files are included in scan results when include_files=True."""
        config = tmp_path / "config"
        config.mkdir()
        (config / "orphan-file.conf").write_text("content")

        scanner = FilesystemScanner(include_files=True, targets=(config,))
        results = list(scanner.scan())

        assert len(results) == 1
        assert results[0].path_type == PathType.FILE


class TestScanEtcHandling:
    """Tests for /etc inclusion behavior."""

    def test_scan_includes_etc_when_flag_set(self) -> None:
        """include_etc=True adds /etc to scan targets."""
        scanner = FilesystemScanner(include_etc=True)
        target_strs = [str(t) for t in scanner._targets]
        assert "/etc" in target_strs

    def test_scan_excludes_etc_by_default(self) -> None:
        """include_etc=False (default) does not include /etc."""
        scanner = FilesystemScanner()
        target_strs = [str(t) for t in scanner._targets]
        assert "/etc" not in target_strs


class TestScanErrorHandling:
    """Tests for error handling during scanning."""

    @patch("popctl.domain.ownership.run_command", side_effect=_route_command_no_apps)
    @patch("popctl.filesystem.scanner.is_protected_path", return_value=False)
    def test_scan_handles_permission_error(
        self,
        _mock_protected: object,
        _mock_cmd: object,
        tmp_path: Path,
    ) -> None:
        """Permission errors on directory listing are caught and logged."""
        restricted = tmp_path / "restricted"
        restricted.mkdir()
        restricted.chmod(0o000)

        try:
            scanner = FilesystemScanner(targets=(restricted,))
            results = list(scanner.scan())
            # Should not crash, just skip the restricted directory
            assert results == []
        finally:
            restricted.chmod(0o755)

    @patch("popctl.domain.ownership.run_command", side_effect=_route_command_no_apps)
    @patch("popctl.filesystem.scanner.is_protected_path", return_value=False)
    def test_scan_handles_permission_error_on_entry(
        self,
        _mock_protected: object,
        _mock_cmd: object,
        tmp_path: Path,
    ) -> None:
        """Permission errors on individual entries are caught and skipped."""
        config = tmp_path / "config"
        config.mkdir()
        problem_dir = config / "problem"
        problem_dir.mkdir()
        normal_dir = config / "normal"
        normal_dir.mkdir()

        # Make the problem dir's type unreadable by removing access
        problem_dir.chmod(0o000)

        try:
            scanner = FilesystemScanner(targets=(config,))
            results = list(scanner.scan())
            # At least the normal dir should still be scanned
            paths = [r.path for r in results]
            assert str(normal_dir) in paths
        finally:
            problem_dir.chmod(0o755)


class TestDeadSymlinkDetection:
    """Tests for dead symlink detection."""

    @patch("popctl.domain.ownership.run_command", side_effect=_route_command_no_apps)
    @patch("popctl.filesystem.scanner.is_protected_path", return_value=False)
    def test_dead_symlink_detection(
        self,
        _mock_protected: object,
        _mock_cmd: object,
        tmp_path: Path,
    ) -> None:
        """Dead symlinks are detected with DEAD_SYMLINK type."""
        config = tmp_path / "config"
        config.mkdir()
        dead_link = config / "dead-link"
        dead_link.symlink_to(tmp_path / "nonexistent-target")

        scanner = FilesystemScanner(include_files=True, targets=(config,))
        results = list(scanner.scan())

        assert len(results) == 1
        assert results[0].path_type == PathType.DEAD_SYMLINK
        assert results[0].orphan_reason == OrphanReason.DEAD_LINK

    @patch("popctl.domain.ownership.run_command", side_effect=_route_command_no_apps)
    @patch("popctl.filesystem.scanner.is_protected_path", return_value=False)
    def test_live_symlink_detection(
        self,
        _mock_protected: object,
        _mock_cmd: object,
        tmp_path: Path,
    ) -> None:
        """Live symlinks are detected with SYMLINK type."""
        config = tmp_path / "config"
        config.mkdir()
        target = tmp_path / "real-target"
        target.mkdir()
        live_link = config / "live-link"
        live_link.symlink_to(target)

        scanner = FilesystemScanner(include_files=True, targets=(config,))
        results = list(scanner.scan())

        assert len(results) == 1
        assert results[0].path_type == PathType.SYMLINK


class TestPathType:
    """Tests for path type detection."""

    def test_get_path_type_directory(self, tmp_path: Path) -> None:
        """Regular directories are classified as DIRECTORY."""
        d = tmp_path / "mydir"
        d.mkdir()
        scanner = FilesystemScanner()
        assert scanner._get_path_type(d) == PathType.DIRECTORY

    def test_get_path_type_file(self, tmp_path: Path) -> None:
        """Regular files are classified as FILE."""
        f = tmp_path / "myfile.txt"
        f.write_text("content")
        scanner = FilesystemScanner()
        assert scanner._get_path_type(f) == PathType.FILE

    def test_get_path_type_symlink(self, tmp_path: Path) -> None:
        """Live symlinks are classified as SYMLINK."""
        target = tmp_path / "target"
        target.mkdir()
        link = tmp_path / "link"
        link.symlink_to(target)
        scanner = FilesystemScanner()
        assert scanner._get_path_type(link) == PathType.SYMLINK

    def test_get_path_type_dead_symlink(self, tmp_path: Path) -> None:
        """Dead symlinks are classified as DEAD_SYMLINK."""
        link = tmp_path / "dead"
        link.symlink_to(tmp_path / "missing")
        scanner = FilesystemScanner()
        assert scanner._get_path_type(link) == PathType.DEAD_SYMLINK


class TestOrphanReason:
    """Tests for orphan reason determination."""

    def test_dead_symlink_reason(self, tmp_path: Path) -> None:
        """Dead symlinks get DEAD_LINK reason."""
        reason = FilesystemScanner._determine_orphan_reason(
            PathType.DEAD_SYMLINK,
            tmp_path / ".config",
        )
        assert reason == OrphanReason.DEAD_LINK

    def test_cache_directory_reason(self, tmp_path: Path) -> None:
        """Cache directories get STALE_CACHE reason."""
        reason = FilesystemScanner._determine_orphan_reason(
            PathType.DIRECTORY,
            tmp_path / ".cache",
        )
        assert reason == OrphanReason.STALE_CACHE

    def test_config_directory_reason(self, tmp_path: Path) -> None:
        """Config directories get NO_PACKAGE_MATCH reason."""
        reason = FilesystemScanner._determine_orphan_reason(
            PathType.DIRECTORY,
            tmp_path / ".config",
        )
        assert reason == OrphanReason.NO_PACKAGE_MATCH


class TestFormatTarget:
    """Tests for target path formatting."""

    def test_format_target_home_directory(self) -> None:
        """Home subdirectories are formatted with tilde prefix."""
        home = Path.home()
        target = home / ".config"
        result = FilesystemScanner._format_target(target)
        assert result == "~/.config"

    def test_format_target_etc(self) -> None:
        """/etc is formatted as absolute path."""
        target = Path("/etc")
        result = FilesystemScanner._format_target(target)
        assert result == "/etc"


class TestScanIntegration:
    """Integration-level tests for the full scan pipeline."""

    @patch("popctl.domain.ownership.run_command", side_effect=_route_command_no_apps)
    @patch("popctl.filesystem.scanner.is_protected_path", return_value=False)
    def test_scan_multiple_targets(
        self,
        _mock_protected: object,
        _mock_cmd: object,
        tmp_path: Path,
    ) -> None:
        """Scanner processes multiple target directories."""
        config = tmp_path / "config"
        config.mkdir()
        (config / "app1").mkdir()

        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / "app2").mkdir()

        scanner = FilesystemScanner(targets=(config, cache))
        results = list(scanner.scan())

        assert len(results) == 2
        names = {Path(r.path).name for r in results}
        assert names == {"app1", "app2"}

    @patch("popctl.filesystem.scanner.is_protected_path", return_value=False)
    def test_scan_resets_caches_between_scans(
        self,
        _mock_protected: object,
        tmp_path: Path,
    ) -> None:
        """Each scan() call resets internal caches from prior scans."""
        config = tmp_path / "config"
        config.mkdir()
        (config / "app1").mkdir()

        dpkg_call_count = 0

        def counting_route(args: list[str], **_kw: object) -> CommandResult:
            nonlocal dpkg_call_count
            if args[0] == "dpkg":
                dpkg_call_count += 1
                return _dpkg_not_found()
            return _no_apps()

        with patch("popctl.domain.ownership.run_command", side_effect=counting_route):
            scanner = FilesystemScanner(targets=(config,))

            # First scan populates the dpkg cache
            list(scanner.scan())
            first_count = dpkg_call_count

            # Second scan should reset and re-query dpkg
            list(scanner.scan())
            second_count = dpkg_call_count - first_count

        # Both scans should call dpkg the same number of times
        # (caches are reset between scans)
        assert first_count == second_count
        assert first_count > 0

    @patch("popctl.domain.ownership.run_command", side_effect=_route_command_no_apps)
    @patch("popctl.filesystem.scanner.is_protected_path", return_value=False)
    def test_scan_confidence_matches_target(
        self,
        _mock_protected: object,
        _mock_cmd: object,
        tmp_path: Path,
    ) -> None:
        """Orphan confidence scores match the scan target directory."""
        cache = tmp_path / "sub" / ".cache"
        cache.mkdir(parents=True)
        (cache / "old-cache").mkdir()

        config = tmp_path / "sub" / ".config"
        config.mkdir(parents=True)
        (config / "old-config").mkdir()

        scanner = FilesystemScanner(targets=(cache, config))
        results = list(scanner.scan())

        by_name = {Path(r.path).name: r for r in results}
        assert by_name["old-cache"].confidence == 0.95
        assert by_name["old-config"].confidence == 0.60


class TestConstructor:
    """Tests for constructor behavior."""

    def test_custom_targets_override_defaults(self, tmp_path: Path) -> None:
        """Custom targets parameter overrides default targets."""
        custom = (tmp_path / "custom",)
        scanner = FilesystemScanner(targets=custom)
        assert scanner._targets == custom

    def test_default_targets_include_xdg_dirs(self) -> None:
        """Default targets include .local/share and .cache (not .config)."""
        scanner = FilesystemScanner()
        target_strs = [str(t) for t in scanner._targets]
        home = str(Path.home())
        assert f"{home}/.local/share" in target_strs
        assert f"{home}/.cache" in target_strs

    def test_default_targets_exclude_config(self) -> None:
        """Default targets do NOT include .config (owned by ConfigScanner)."""
        scanner = FilesystemScanner()
        target_strs = [str(t) for t in scanner._targets]
        home = str(Path.home())
        assert f"{home}/.config" not in target_strs

    def test_include_etc_adds_etc_target(self) -> None:
        """include_etc=True adds /etc to default targets."""
        scanner = FilesystemScanner(include_etc=True)
        target_strs = [str(t) for t in scanner._targets]
        assert "/etc" in target_strs
        assert len(scanner._targets) == 3  # 2 default + /etc
