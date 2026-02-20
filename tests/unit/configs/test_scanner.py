"""Tests for ConfigScanner orphan detection."""

from pathlib import Path
from unittest.mock import patch

from popctl.configs.models import ConfigOrphanReason, ConfigStatus, ConfigType
from popctl.configs.scanner import ConfigScanner
from popctl.utils.shell import CommandResult


def _dpkg_not_found(*_args: object, **_kwargs: object) -> CommandResult:
    """Simulate dpkg -S returning 'not found' (package does not own path)."""
    return CommandResult(stdout="", stderr="dpkg-query: no path found", returncode=1)


def _dpkg_found(*_args: object, **_kwargs: object) -> CommandResult:
    """Simulate dpkg -S returning success (package owns path)."""
    return CommandResult(stdout="some-package: /path", stderr="", returncode=0)


def _no_apps(*_args: object, **_kwargs: object) -> CommandResult:
    """Simulate flatpak/snap/dpkg-query returning no apps."""
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


class TestScanEmptyConfigDir:
    """Tests for scanning empty or missing config directories."""

    @patch("popctl.domain.ownership.run_command", side_effect=_route_command_no_apps)
    def test_scan_empty_config_dir(
        self,
        _mock_cmd: object,
        tmp_path: Path,
    ) -> None:
        """Scanning an empty ~/.config/ directory yields no results."""
        config_dir = tmp_path / ".config"
        config_dir.mkdir()

        with patch("popctl.configs.scanner.Path.home", return_value=tmp_path):
            scanner = ConfigScanner()
            results = list(scanner.scan())

        assert results == []

    @patch("popctl.domain.ownership.run_command", side_effect=_route_command_no_apps)
    def test_scan_handles_missing_config_dir(
        self,
        _mock_cmd: object,
        tmp_path: Path,
    ) -> None:
        """Missing ~/.config/ directory is handled gracefully (yields nothing)."""
        # tmp_path has no .config subdirectory
        with patch("popctl.configs.scanner.Path.home", return_value=tmp_path):
            scanner = ConfigScanner()
            results = list(scanner.scan())

        assert results == []


class TestScanFindsOrphans:
    """Tests for orphan detection in ~/.config/."""

    @patch("popctl.domain.ownership.run_command", side_effect=_route_command_no_apps)
    @patch("popctl.configs.scanner.is_protected_config", return_value=False)
    def test_scan_finds_orphans(
        self,
        _mock_protected: object,
        _mock_cmd: object,
        tmp_path: Path,
    ) -> None:
        """Entries with no package match are reported as orphans."""
        config_dir = tmp_path / ".config"
        config_dir.mkdir()
        orphan_dir = config_dir / "old-app"
        orphan_dir.mkdir()

        with patch("popctl.configs.scanner.Path.home", return_value=tmp_path):
            scanner = ConfigScanner()
            results = list(scanner.scan())

        assert len(results) == 1
        assert results[0].status == ConfigStatus.ORPHAN
        assert results[0].path == str(orphan_dir)
        assert results[0].config_type == ConfigType.DIRECTORY

    @patch("popctl.domain.ownership.run_command", side_effect=_route_command_no_apps)
    @patch("popctl.configs.scanner.is_protected_config", return_value=False)
    def test_scan_orphan_has_correct_fields(
        self,
        _mock_protected: object,
        _mock_cmd: object,
        tmp_path: Path,
    ) -> None:
        """Orphaned ScannedConfig has all expected fields populated."""
        config_dir = tmp_path / ".config"
        config_dir.mkdir()
        orphan = config_dir / "stale-app"
        orphan.mkdir()
        (orphan / "data.txt").write_text("content")

        with patch("popctl.configs.scanner.Path.home", return_value=tmp_path):
            scanner = ConfigScanner()
            results = list(scanner.scan())

        assert len(results) == 1
        result = results[0]
        assert result.orphan_reason == ConfigOrphanReason.NO_PACKAGE_MATCH
        assert result.confidence > 0.0
        assert result.mtime is not None
        assert result.size_bytes is not None
        assert result.description is None


class TestScanSkipsOwnedPackages:
    """Tests for dpkg ownership detection."""

    @patch("popctl.configs.scanner.is_protected_config", return_value=False)
    def test_scan_skips_owned_packages(
        self,
        _mock_protected: object,
        tmp_path: Path,
    ) -> None:
        """Entries owned by dpkg are not yielded."""
        config_dir = tmp_path / ".config"
        config_dir.mkdir()
        owned_dir = config_dir / "vim"
        owned_dir.mkdir()

        def route(args: list[str], **_kw: object) -> CommandResult:
            if args[0] == "dpkg" and args[1] == "-S":
                return _dpkg_found()
            return _no_apps()

        with (
            patch("popctl.domain.ownership.run_command", side_effect=route),
            patch("popctl.configs.scanner.Path.home", return_value=tmp_path),
        ):
            scanner = ConfigScanner()
            results = list(scanner.scan())

        assert len(results) == 0


class TestScanSkipsProtectedConfigs:
    """Tests for protected config handling."""

    @patch("popctl.domain.ownership.run_command", side_effect=_route_command_no_apps)
    def test_scan_skips_protected_configs(
        self,
        _mock_cmd: object,
        tmp_path: Path,
    ) -> None:
        """Protected configs are skipped and not reported as orphans."""
        config_dir = tmp_path / ".config"
        config_dir.mkdir()
        protected_dir = config_dir / "popctl"
        protected_dir.mkdir()
        orphan_dir = config_dir / "unknown-app"
        orphan_dir.mkdir()

        def mock_protected(path: str) -> bool:
            return "popctl" in path

        with (
            patch("popctl.configs.scanner.is_protected_config", side_effect=mock_protected),
            patch("popctl.configs.scanner.Path.home", return_value=tmp_path),
        ):
            scanner = ConfigScanner()
            results = list(scanner.scan())

        paths = [r.path for r in results]
        assert str(protected_dir) not in paths
        assert str(orphan_dir) in paths


class TestScanConfidence:
    """Tests for confidence score assignment."""

    @patch("popctl.domain.ownership.run_command", side_effect=_route_command_no_apps)
    @patch("popctl.configs.scanner.is_protected_config", return_value=False)
    def test_scan_confidence_directory(
        self,
        _mock_protected: object,
        _mock_cmd: object,
        tmp_path: Path,
    ) -> None:
        """Directories in ~/.config/ get 0.70 confidence."""
        config_dir = tmp_path / ".config"
        config_dir.mkdir()
        orphan = config_dir / "orphan-app"
        orphan.mkdir()

        with patch("popctl.configs.scanner.Path.home", return_value=tmp_path):
            scanner = ConfigScanner()
            results = list(scanner.scan())

        assert len(results) == 1
        assert results[0].confidence == 0.70
        assert results[0].config_type == ConfigType.DIRECTORY

    @patch("popctl.domain.ownership.run_command", side_effect=_route_command_no_apps)
    @patch("popctl.configs.scanner.is_protected_config", return_value=False)
    def test_scan_confidence_dotfile(
        self,
        _mock_protected: object,
        _mock_cmd: object,
        tmp_path: Path,
    ) -> None:
        """Dotfiles get 0.60 confidence."""
        # Create a dotfile that is in the _SHELL_DOTFILES list
        dotfile = tmp_path / ".wgetrc"
        dotfile.write_text("# wget config")

        with patch("popctl.configs.scanner.Path.home", return_value=tmp_path):
            scanner = ConfigScanner()
            results = list(scanner.scan())

        # Filter to the dotfile result
        dotfile_results = [r for r in results if r.path == str(dotfile)]
        assert len(dotfile_results) == 1
        assert dotfile_results[0].confidence == 0.60
        assert dotfile_results[0].config_type == ConfigType.FILE


class TestScanDotfiles:
    """Tests for shell dotfile scanning."""

    @patch("popctl.domain.ownership.run_command", side_effect=_route_command_no_apps)
    @patch("popctl.configs.scanner.is_protected_config", return_value=False)
    def test_scan_dotfiles(
        self,
        _mock_protected: object,
        _mock_cmd: object,
        tmp_path: Path,
    ) -> None:
        """Scans known shell dotfiles (.wgetrc, .curlrc, .tmux.conf)."""
        # Create some dotfiles that are not protected
        (tmp_path / ".wgetrc").write_text("# wget config")
        (tmp_path / ".curlrc").write_text("# curl config")
        (tmp_path / ".tmux.conf").write_text("# tmux config")

        with patch("popctl.configs.scanner.Path.home", return_value=tmp_path):
            scanner = ConfigScanner()
            results = list(scanner.scan())

        paths = [r.path for r in results]
        assert str(tmp_path / ".wgetrc") in paths
        assert str(tmp_path / ".curlrc") in paths
        assert str(tmp_path / ".tmux.conf") in paths

    @patch("popctl.domain.ownership.run_command", side_effect=_route_command_no_apps)
    @patch("popctl.configs.scanner.is_protected_config", return_value=False)
    def test_scan_dotfiles_nonexistent_skipped(
        self,
        _mock_protected: object,
        _mock_cmd: object,
        tmp_path: Path,
    ) -> None:
        """Nonexistent dotfiles are silently skipped."""
        # No dotfiles created in tmp_path
        with patch("popctl.configs.scanner.Path.home", return_value=tmp_path):
            scanner = ConfigScanner()
            results = list(scanner.scan())

        assert results == []


class TestScanHandlesPermissionError:
    """Tests for permission error handling."""

    @patch("popctl.domain.ownership.run_command", side_effect=_route_command_no_apps)
    @patch("popctl.configs.scanner.is_protected_config", return_value=False)
    def test_scan_handles_permission_error(
        self,
        _mock_protected: object,
        _mock_cmd: object,
        tmp_path: Path,
    ) -> None:
        """PermissionError on ~/.config/ listing is caught, scan continues."""
        config_dir = tmp_path / ".config"
        config_dir.mkdir()
        config_dir.chmod(0o000)

        try:
            with patch("popctl.configs.scanner.Path.home", return_value=tmp_path):
                scanner = ConfigScanner()
                results = list(scanner.scan())

            # Should not crash, just skip the restricted directory
            assert results == []
        finally:
            config_dir.chmod(0o755)

    @patch("popctl.domain.ownership.run_command", side_effect=_route_command_no_apps)
    @patch("popctl.configs.scanner.is_protected_config", return_value=False)
    def test_scan_handles_permission_error_on_entry(
        self,
        _mock_protected: object,
        _mock_cmd: object,
        tmp_path: Path,
    ) -> None:
        """PermissionError on individual entries is caught, scan continues."""
        config_dir = tmp_path / ".config"
        config_dir.mkdir()
        normal_dir = config_dir / "normal-app"
        normal_dir.mkdir()

        # Create a dotfile that will raise PermissionError
        problem_file = tmp_path / ".wgetrc"
        problem_file.write_text("content")
        problem_file.chmod(0o000)

        try:
            with patch("popctl.configs.scanner.Path.home", return_value=tmp_path):
                scanner = ConfigScanner()
                results = list(scanner.scan())

            # normal_dir should still be scanned
            paths = [r.path for r in results]
            assert str(normal_dir) in paths
        finally:
            problem_file.chmod(0o644)


class TestNormalizedNameMatch:
    """Tests for config-specific normalized name matching."""

    def test_normalized_match_dpkg_package(self) -> None:
        """Normalized dpkg package name matching strips dots and dashes."""
        scanner = ConfigScanner()
        scanner._packages_cache = {"libreoffice-common"}
        scanner._apps_cache = set()

        assert scanner._normalized_name_match("libreofficecommon") is True
        assert scanner._normalized_name_match("LibreOffice-Common") is True

    def test_normalized_match_app(self) -> None:
        """Normalized app name matching works."""
        scanner = ConfigScanner()
        scanner._packages_cache = set()
        scanner._apps_cache = {"spotify"}

        assert scanner._normalized_name_match("spotify") is True
        assert scanner._normalized_name_match("Spotify") is True

    def test_normalized_no_match(self) -> None:
        """No match when names differ after normalization."""
        scanner = ConfigScanner()
        scanner._packages_cache = {"vim"}
        scanner._apps_cache = {"org.mozilla.firefox"}

        assert scanner._normalized_name_match("unknown") is False

    def test_normalized_match_case_insensitive(self) -> None:
        """Normalized matching is case-insensitive."""
        scanner = ConfigScanner()
        scanner._packages_cache = {"VLC"}
        scanner._apps_cache = set()

        assert scanner._normalized_name_match("vlc") is True
        assert scanner._normalized_name_match("VLC") is True


class TestDeadSymlinkDetection:
    """Tests for dead symlink detection."""

    @patch("popctl.domain.ownership.run_command", side_effect=_route_command_no_apps)
    @patch("popctl.configs.scanner.is_protected_config", return_value=False)
    def test_dead_symlink_detection(
        self,
        _mock_protected: object,
        _mock_cmd: object,
        tmp_path: Path,
    ) -> None:
        """Dead symlinks are detected with DEAD_LINK orphan reason."""
        config_dir = tmp_path / ".config"
        config_dir.mkdir()
        dead_link = config_dir / "dead-link"
        dead_link.symlink_to(tmp_path / "nonexistent-target")

        with patch("popctl.configs.scanner.Path.home", return_value=tmp_path):
            scanner = ConfigScanner()
            results = list(scanner.scan())

        assert len(results) == 1
        assert results[0].config_type == ConfigType.FILE
        assert results[0].orphan_reason == ConfigOrphanReason.DEAD_LINK
        assert results[0].status == ConfigStatus.ORPHAN

    @patch("popctl.domain.ownership.run_command", side_effect=_route_command_no_apps)
    @patch("popctl.configs.scanner.is_protected_config", return_value=False)
    def test_dead_symlink_dotfile(
        self,
        _mock_protected: object,
        _mock_cmd: object,
        tmp_path: Path,
    ) -> None:
        """Dead symlink dotfiles are detected."""
        dead_link = tmp_path / ".wgetrc"
        dead_link.symlink_to(tmp_path / "nonexistent")

        with patch("popctl.configs.scanner.Path.home", return_value=tmp_path):
            scanner = ConfigScanner()
            results = list(scanner.scan())

        dotfile_results = [r for r in results if r.path == str(dead_link)]
        assert len(dotfile_results) == 1
        assert dotfile_results[0].orphan_reason == ConfigOrphanReason.DEAD_LINK


class TestCachesResetPerScan:
    """Tests for cache reset behavior between scan() calls."""

    @patch("popctl.configs.scanner.is_protected_config", return_value=False)
    def test_caches_reset_per_scan(
        self,
        _mock_protected: object,
        tmp_path: Path,
    ) -> None:
        """Caches are cleared between scan() calls."""
        config_dir = tmp_path / ".config"
        config_dir.mkdir()
        (config_dir / "app1").mkdir()

        dpkg_call_count = 0

        def counting_route(args: list[str], **_kw: object) -> CommandResult:
            nonlocal dpkg_call_count
            if args[0] == "dpkg":
                dpkg_call_count += 1
                return _dpkg_not_found()
            return _no_apps()

        with (
            patch("popctl.domain.ownership.run_command", side_effect=counting_route),
            patch("popctl.configs.scanner.Path.home", return_value=tmp_path),
        ):
            scanner = ConfigScanner()

            # First scan populates caches
            list(scanner.scan())
            first_count = dpkg_call_count

            # Second scan should reset caches and re-query
            list(scanner.scan())
            second_count = dpkg_call_count - first_count

        # Both scans should call dpkg the same number of times
        assert first_count == second_count
        assert first_count > 0


class TestGetConfigType:
    """Tests for config type detection."""

    def test_get_config_type_directory(self, tmp_path: Path) -> None:
        """Regular directories are classified as DIRECTORY."""
        d = tmp_path / "mydir"
        d.mkdir()
        scanner = ConfigScanner()
        assert scanner._get_config_type(d) == ConfigType.DIRECTORY

    def test_get_config_type_file(self, tmp_path: Path) -> None:
        """Regular files are classified as FILE."""
        f = tmp_path / "myfile.conf"
        f.write_text("content")
        scanner = ConfigScanner()
        assert scanner._get_config_type(f) == ConfigType.FILE

    def test_get_config_type_symlink_is_file(self, tmp_path: Path) -> None:
        """Symlinks (even to directories) are classified as FILE."""
        target = tmp_path / "target"
        target.mkdir()
        link = tmp_path / "link"
        link.symlink_to(target)
        scanner = ConfigScanner()
        assert scanner._get_config_type(link) == ConfigType.FILE


class TestScanIntegration:
    """Integration-level tests for the full config scan pipeline."""

    @patch("popctl.domain.ownership.run_command", side_effect=_route_command)
    @patch("popctl.configs.scanner.is_protected_config", return_value=False)
    def test_scan_skips_owned_by_app(
        self,
        _mock_protected: object,
        _mock_cmd: object,
        tmp_path: Path,
    ) -> None:
        """Entries matching an installed app name are skipped."""
        config_dir = tmp_path / ".config"
        config_dir.mkdir()
        # "firefox" matches org.mozilla.firefox from flatpak
        firefox_dir = config_dir / "firefox"
        firefox_dir.mkdir()
        # "unknown-app" matches nothing
        unknown_dir = config_dir / "unknown-app"
        unknown_dir.mkdir()

        with patch("popctl.configs.scanner.Path.home", return_value=tmp_path):
            scanner = ConfigScanner()
            results = list(scanner.scan())

        paths = [r.path for r in results]
        assert str(firefox_dir) not in paths
        assert str(unknown_dir) in paths

    @patch("popctl.domain.ownership.run_command", side_effect=_route_command_no_apps)
    @patch("popctl.configs.scanner.is_protected_config", return_value=False)
    def test_scan_multiple_orphans(
        self,
        _mock_protected: object,
        _mock_cmd: object,
        tmp_path: Path,
    ) -> None:
        """Scanner yields multiple orphans from ~/.config/ and dotfiles."""
        config_dir = tmp_path / ".config"
        config_dir.mkdir()
        (config_dir / "app1").mkdir()
        (config_dir / "app2").mkdir()
        (tmp_path / ".wgetrc").write_text("# wget")

        with patch("popctl.configs.scanner.Path.home", return_value=tmp_path):
            scanner = ConfigScanner()
            results = list(scanner.scan())

        assert len(results) == 3
        paths = [r.path for r in results]
        assert str(config_dir / "app1") in paths
        assert str(config_dir / "app2") in paths
        assert str(tmp_path / ".wgetrc") in paths
