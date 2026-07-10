"""Tests for domain ownership checking functions."""

from pathlib import Path
from unittest.mock import patch

from popctl.domain.models import PathType
from popctl.domain.ownership import (
    app_name_matches,
    classify_path_type,
    dpkg_owns_path,
    get_installed_apps,
    get_installed_packages,
    get_path_mtime,
    get_path_size,
)
from popctl.utils.shell import CommandResult

_MOCK = "popctl.domain.ownership.run_command"


def _dpkg_found() -> CommandResult:
    return CommandResult(stdout="some-package: /path", stderr="", returncode=0)


def _dpkg_not_found() -> CommandResult:
    return CommandResult(stdout="", stderr="dpkg-query: no path found", returncode=1)


def _no_result() -> CommandResult:
    return CommandResult(stdout="", stderr="", returncode=1)


class TestDpkgOwnsPath:
    """Tests for dpkg_owns_path function."""

    def test_owned(self, tmp_path: Path) -> None:
        """dpkg -S returning 0 means the path is owned."""
        target = tmp_path / "vim"
        target.mkdir()
        cache: dict[str, bool] = {}

        with patch(_MOCK, return_value=_dpkg_found()):
            assert dpkg_owns_path(target, cache) is True

    def test_not_owned(self, tmp_path: Path) -> None:
        """dpkg -S returning non-zero means the path is not owned."""
        target = tmp_path / "unknown"
        target.mkdir()
        cache: dict[str, bool] = {}

        with patch(_MOCK, return_value=_dpkg_not_found()):
            assert dpkg_owns_path(target, cache) is False

    def test_caching(self, tmp_path: Path) -> None:
        """Results are cached per path."""
        target = tmp_path / "app1"
        target.mkdir()
        cache: dict[str, bool] = {}
        call_count = 0

        def counting_cmd(args: list[str], **_kw: object) -> CommandResult:
            nonlocal call_count
            call_count += 1
            return _dpkg_not_found()

        with patch(_MOCK, side_effect=counting_cmd):
            dpkg_owns_path(target, cache)
            dpkg_owns_path(target, cache)

        assert call_count == 1

    def test_handles_exception(self, tmp_path: Path) -> None:
        """FileNotFoundError when dpkg is missing returns False."""
        target = tmp_path / "app"
        target.mkdir()
        cache: dict[str, bool] = {}

        with patch(_MOCK, side_effect=FileNotFoundError("dpkg not found")):
            assert dpkg_owns_path(target, cache) is False


class TestGetInstalledPackages:
    """Tests for get_installed_packages function."""

    def test_returns_packages(self) -> None:
        """Parses dpkg-query output into a set of package names."""
        result = CommandResult(stdout="vim\ncurl\nwget\n", stderr="", returncode=0)
        with patch(_MOCK, return_value=result):
            packages = get_installed_packages()

        assert packages == {"vim", "curl", "wget"}

    def test_handles_failure(self) -> None:
        """Returns empty set on dpkg-query failure."""
        result = CommandResult(stdout="", stderr="error", returncode=1)
        with patch(_MOCK, return_value=result):
            packages = get_installed_packages()

        assert packages == set()

    def test_handles_exception(self) -> None:
        """Returns empty set on FileNotFoundError."""
        with patch(_MOCK, side_effect=FileNotFoundError("dpkg-query not found")):
            packages = get_installed_packages()

        assert packages == set()


class TestGetInstalledApps:
    """Tests for get_installed_apps function."""

    def test_combines_flatpak_and_snap(self) -> None:
        """Combines results from flatpak and snap."""

        def route(args: list[str], **_kw: object) -> CommandResult:
            if args[0] == "flatpak":
                return CommandResult(
                    stdout="org.mozilla.firefox\norg.gnome.Calculator\n",
                    stderr="",
                    returncode=0,
                )
            if args[0] == "snap":
                return CommandResult(
                    stdout=(
                        "Name    Version  Rev  Tracking  Publisher  Notes\n"
                        "spotify  1.2.3  100  stable    spotify  -\n"
                    ),
                    stderr="",
                    returncode=0,
                )
            return _no_result()

        with patch(_MOCK, side_effect=route):
            apps = get_installed_apps()

        assert "org.mozilla.firefox" in apps
        assert "org.gnome.Calculator" in apps
        assert "spotify" in apps

    def test_handles_missing_flatpak(self) -> None:
        """Missing flatpak does not prevent snap apps from being returned."""

        def route(args: list[str], **_kw: object) -> CommandResult:
            if args[0] == "flatpak":
                raise FileNotFoundError("flatpak not found")
            if args[0] == "snap":
                return CommandResult(
                    stdout="Name  Ver  Rev  Track  Pub  Notes\nspotify  1  1  s  s  -\n",
                    stderr="",
                    returncode=0,
                )
            return _no_result()

        with patch(_MOCK, side_effect=route):
            apps = get_installed_apps()

        assert "spotify" in apps

    def test_handles_missing_snap(self) -> None:
        """Missing snap does not prevent flatpak apps from being returned."""

        def route(args: list[str], **_kw: object) -> CommandResult:
            if args[0] == "flatpak":
                return CommandResult(stdout="org.mozilla.firefox\n", stderr="", returncode=0)
            if args[0] == "snap":
                raise FileNotFoundError("snap not found")
            return _no_result()

        with patch(_MOCK, side_effect=route):
            apps = get_installed_apps()

        assert "org.mozilla.firefox" in apps

    def test_both_unavailable(self) -> None:
        """Both flatpak and snap unavailable returns empty set."""
        with patch(_MOCK, side_effect=FileNotFoundError("not found")):
            apps = get_installed_apps()

        assert apps == set()


class TestAppNameMatches:
    """Tests for app_name_matches function."""

    def test_exact_match(self) -> None:
        """Exact case-insensitive matching works."""
        apps = {"spotify", "discord"}
        assert app_name_matches("spotify", apps) is True
        assert app_name_matches("Spotify", apps) is True
        assert app_name_matches("unknown", apps) is False

    def test_reverse_dns_component(self) -> None:
        """Reverse-DNS component matching works."""
        apps = {"org.mozilla.firefox", "org.gnome.Calculator"}
        assert app_name_matches("firefox", apps) is True
        assert app_name_matches("Calculator", apps) is True
        assert app_name_matches("unknown", apps) is False

    def test_no_partial_match(self) -> None:
        """Partial string matches are not accepted."""
        apps = {"org.mozilla.firefox"}
        assert app_name_matches("fire", apps) is False
        assert app_name_matches("fox", apps) is False


class TestGetPathSize:
    """Tests for get_path_size function."""

    def test_file_size(self, tmp_path: Path) -> None:
        """File size is returned correctly."""
        f = tmp_path / "file.txt"
        f.write_text("hello world")
        size = get_path_size(f)
        assert size is not None
        assert size > 0

    def test_directory_size(self, tmp_path: Path) -> None:
        """Directory size sums all files recursively."""
        d = tmp_path / "mydir"
        d.mkdir()
        (d / "a.txt").write_text("aaa")
        (d / "b.txt").write_text("bbbb")
        size = get_path_size(d)
        assert size is not None
        assert size == 7  # 3 + 4 bytes

    def test_empty_directory(self, tmp_path: Path) -> None:
        """Empty directory returns 0."""
        d = tmp_path / "empty"
        d.mkdir()
        assert get_path_size(d) == 0

    def test_nonexistent_returns_none(self, tmp_path: Path) -> None:
        """Nonexistent path returns None."""
        assert get_path_size(tmp_path / "nonexistent") is None


class TestGetPathMtime:
    """Tests for get_path_mtime function."""

    def test_returns_iso_format(self, tmp_path: Path) -> None:
        """Mtime is returned as ISO 8601 string."""
        f = tmp_path / "file.txt"
        f.write_text("content")
        mtime = get_path_mtime(f)
        assert mtime is not None
        assert "T" in mtime

    def test_nonexistent_returns_none(self, tmp_path: Path) -> None:
        """Nonexistent path returns None for mtime."""
        assert get_path_mtime(tmp_path / "nonexistent") is None


class TestClassifyPathType:
    """Tests for classify_path_type function."""

    def test_regular_file(self, tmp_path: Path) -> None:
        """Regular file is classified as FILE."""
        f = tmp_path / "file.txt"
        f.write_text("content")
        assert classify_path_type(f) == PathType.FILE

    def test_directory(self, tmp_path: Path) -> None:
        """Directory is classified as DIRECTORY."""
        d = tmp_path / "mydir"
        d.mkdir()
        assert classify_path_type(d) == PathType.DIRECTORY

    def test_live_symlink(self, tmp_path: Path) -> None:
        """Live symlink is classified as SYMLINK."""
        target = tmp_path / "target"
        target.mkdir()
        link = tmp_path / "link"
        link.symlink_to(target)
        assert classify_path_type(link) == PathType.SYMLINK

    def test_dead_symlink(self, tmp_path: Path) -> None:
        """Dead symlink is classified as DEAD_SYMLINK."""
        link = tmp_path / "dead"
        link.symlink_to(tmp_path / "nonexistent")
        assert classify_path_type(link) == PathType.DEAD_SYMLINK
