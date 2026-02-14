"""Unit tests for SnapScanner.

Tests for the Snap package scanner implementation.
"""

from unittest.mock import patch

import pytest
from popctl.models.package import PackageSource, PackageStatus
from popctl.scanners.snap import SnapScanner
from popctl.utils.shell import CommandResult

MOCK_SNAP_OUTPUT = """\
Name                     Version    Rev    Tracking       Publisher      Notes
firefox                  128.0      4336   latest/stable  mozilla✓       -
vlc                      3.0.20     3777   latest/stable  videolan✓      -
core22                   20240607   1612   latest/stable  canonical✓     base
snapd                    2.63       21465  latest/stable  canonical✓     snapd
bare                     1.0        5      latest/stable  canonical✓     base
signal-desktop           7.0.0      750    latest/stable  snapcrafters   classic
gnome-42-2204-platform   0+git      176    latest/stable  canonical✓     base
"""


class TestSnapScanner:
    """Tests for SnapScanner class."""

    @pytest.fixture
    def scanner(self) -> SnapScanner:
        """Create SnapScanner instance."""
        return SnapScanner()

    def test_source_is_snap(self, scanner: SnapScanner) -> None:
        """Scanner returns SNAP as source."""
        assert scanner.source == PackageSource.SNAP

    def test_is_available_with_snap(self, scanner: SnapScanner) -> None:
        """is_available returns True when snap command exists."""
        with patch("popctl.scanners.snap.command_exists") as mock_exists:
            mock_exists.return_value = True
            assert scanner.is_available() is True
            mock_exists.assert_called_once_with("snap")

    def test_is_available_without_snap(self, scanner: SnapScanner) -> None:
        """is_available returns False when snap is not installed."""
        with patch("popctl.scanners.snap.command_exists") as mock_exists:
            mock_exists.return_value = False
            assert scanner.is_available() is False

    def test_scan_parses_packages_correctly(self, scanner: SnapScanner) -> None:
        """Scan correctly parses snap list output with header skipped."""
        with (
            patch("popctl.scanners.snap.command_exists", return_value=True),
            patch("popctl.scanners.snap.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(stdout=MOCK_SNAP_OUTPUT, stderr="", returncode=0)

            packages = list(scanner.scan())

        # Only firefox, vlc, and signal-desktop should remain
        assert len(packages) == 3

        firefox = packages[0]
        assert firefox.name == "firefox"
        assert firefox.version == "128.0"
        assert firefox.source == PackageSource.SNAP
        assert firefox.size_bytes is None
        assert firefox.description is None

        vlc = packages[1]
        assert vlc.name == "vlc"
        assert vlc.version == "3.0.20"

    def test_scan_filters_base_snaps(self, scanner: SnapScanner) -> None:
        """Snaps with Notes=base are filtered out."""
        output = """\
Name      Version    Rev   Tracking       Publisher    Notes
core22    20240607   1612  latest/stable  canonical✓   base
firefox   128.0      4336  latest/stable  mozilla✓     -
"""
        with (
            patch("popctl.scanners.snap.command_exists", return_value=True),
            patch("popctl.scanners.snap.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(stdout=output, stderr="", returncode=0)

            packages = list(scanner.scan())

        names = [p.name for p in packages]
        assert "core22" not in names
        assert "firefox" in names

    def test_scan_filters_snapd(self, scanner: SnapScanner) -> None:
        """Snaps with Notes=snapd are filtered out."""
        output = """\
Name    Version  Rev    Tracking       Publisher    Notes
snapd   2.63     21465  latest/stable  canonical✓   snapd
firefox 128.0    4336   latest/stable  mozilla✓     -
"""
        with (
            patch("popctl.scanners.snap.command_exists", return_value=True),
            patch("popctl.scanners.snap.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(stdout=output, stderr="", returncode=0)

            packages = list(scanner.scan())

        names = [p.name for p in packages]
        assert "snapd" not in names
        assert "firefox" in names

    def test_scan_filters_core_by_name(self, scanner: SnapScanner) -> None:
        """Snaps whose name starts with 'core' are filtered by name pattern."""
        output = """\
Name      Version    Rev   Tracking       Publisher    Notes
core      16-2.61    1234  latest/stable  canonical✓   -
core18    20240101   5678  latest/stable  canonical✓   -
firefox   128.0      4336  latest/stable  mozilla✓     -
"""
        with (
            patch("popctl.scanners.snap.command_exists", return_value=True),
            patch("popctl.scanners.snap.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(stdout=output, stderr="", returncode=0)

            packages = list(scanner.scan())

        names = [p.name for p in packages]
        assert "core" not in names
        assert "core18" not in names
        assert "firefox" in names

    def test_scan_filters_gnome_platform(self, scanner: SnapScanner) -> None:
        """Snaps matching gnome-*-platform pattern are filtered out."""
        output = """\
Name                     Version  Rev  Tracking       Publisher    Notes
gnome-42-2204-platform   0+git    176  latest/stable  canonical✓   base
firefox                  128.0    4336 latest/stable  mozilla✓     -
"""
        with (
            patch("popctl.scanners.snap.command_exists", return_value=True),
            patch("popctl.scanners.snap.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(stdout=output, stderr="", returncode=0)

            packages = list(scanner.scan())

        names = [p.name for p in packages]
        assert "gnome-42-2204-platform" not in names
        assert "firefox" in names

    def test_scan_filters_snapd_by_name(self, scanner: SnapScanner) -> None:
        """The 'snapd' and 'bare' snaps are filtered by name even without runtime Notes."""
        output = """\
Name      Version  Rev  Tracking       Publisher    Notes
snapd     2.63     21465 latest/stable canonical✓   -
bare      1.0      5    latest/stable  canonical✓   -
firefox   128.0    4336 latest/stable  mozilla✓     -
"""
        with (
            patch("popctl.scanners.snap.command_exists", return_value=True),
            patch("popctl.scanners.snap.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(stdout=output, stderr="", returncode=0)

            packages = list(scanner.scan())

        names = [p.name for p in packages]
        assert "snapd" not in names
        assert "bare" not in names
        assert "firefox" in names

    def test_scan_filters_bare(self, scanner: SnapScanner) -> None:
        """The 'bare' snap is filtered out."""
        output = """\
Name      Version  Rev  Tracking       Publisher    Notes
bare      1.0      5    latest/stable  canonical✓   base
firefox   128.0    4336 latest/stable  mozilla✓     -
"""
        with (
            patch("popctl.scanners.snap.command_exists", return_value=True),
            patch("popctl.scanners.snap.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(stdout=output, stderr="", returncode=0)

            packages = list(scanner.scan())

        names = [p.name for p in packages]
        assert "bare" not in names
        assert "firefox" in names

    def test_scan_keeps_classic_snaps(self, scanner: SnapScanner) -> None:
        """Classic snaps (Notes=classic) are NOT filtered out."""
        output = """\
Name              Version  Rev  Tracking       Publisher      Notes
signal-desktop    7.0.0    750  latest/stable  snapcrafters   classic
"""
        with (
            patch("popctl.scanners.snap.command_exists", return_value=True),
            patch("popctl.scanners.snap.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(stdout=output, stderr="", returncode=0)

            packages = list(scanner.scan())

        assert len(packages) == 1
        assert packages[0].name == "signal-desktop"

    def test_scan_keeps_normal_apps(self, scanner: SnapScanner) -> None:
        """Normal apps (Notes=-) are NOT filtered out."""
        output = """\
Name      Version  Rev   Tracking       Publisher   Notes
firefox   128.0    4336  latest/stable  mozilla✓    -
vlc       3.0.20   3777  latest/stable  videolan✓   -
"""
        with (
            patch("popctl.scanners.snap.command_exists", return_value=True),
            patch("popctl.scanners.snap.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(stdout=output, stderr="", returncode=0)

            packages = list(scanner.scan())

        assert len(packages) == 2
        names = [p.name for p in packages]
        assert "firefox" in names
        assert "vlc" in names

    def test_scan_all_packages_are_manual(self, scanner: SnapScanner) -> None:
        """All yielded snap packages have MANUAL status."""
        with (
            patch("popctl.scanners.snap.command_exists", return_value=True),
            patch("popctl.scanners.snap.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(stdout=MOCK_SNAP_OUTPUT, stderr="", returncode=0)

            packages = list(scanner.scan())

        assert all(p.status == PackageStatus.MANUAL for p in packages)
        assert all(p.is_manual for p in packages)

    def test_scan_raises_when_unavailable(self, scanner: SnapScanner) -> None:
        """Scan raises RuntimeError when snap is unavailable."""
        with (
            patch("popctl.scanners.snap.command_exists", return_value=False),
            pytest.raises(RuntimeError, match="not available"),
        ):
            list(scanner.scan())

    def test_scan_raises_on_snap_failure(self, scanner: SnapScanner) -> None:
        """Scan raises RuntimeError when snap list fails."""
        with (
            patch("popctl.scanners.snap.command_exists", return_value=True),
            patch("popctl.scanners.snap.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(
                stdout="", stderr="error: snap failed", returncode=1
            )

            with pytest.raises(RuntimeError, match="snap list failed"):
                list(scanner.scan())

    def test_scan_handles_empty_output(self, scanner: SnapScanner) -> None:
        """Scan handles snap list output with header only."""
        header_only = "Name  Version  Rev  Tracking  Publisher  Notes"
        with (
            patch("popctl.scanners.snap.command_exists", return_value=True),
            patch("popctl.scanners.snap.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(stdout=header_only, stderr="", returncode=0)

            packages = list(scanner.scan())

        assert len(packages) == 0

    def test_scan_skips_blank_lines(self, scanner: SnapScanner) -> None:
        """Blank lines in snap list output are skipped."""
        output = (
            "Name  Version  Rev  Tracking  Publisher  Notes\n"
            "\n"
            "firefox  128.0  4336  latest/stable  mozilla  -\n"
            "\n"
        )
        with (
            patch("popctl.scanners.snap.command_exists", return_value=True),
            patch("popctl.scanners.snap.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(stdout=output, stderr="", returncode=0)

            packages = list(scanner.scan())

        assert len(packages) == 1
        assert packages[0].name == "firefox"

    def test_scan_skips_malformed_lines(self, scanner: SnapScanner) -> None:
        """Lines with fewer than 6 fields are skipped."""
        output = """\
Name      Version  Rev  Tracking       Publisher   Notes
firefox   128.0    4336 latest/stable  mozilla✓    -
bad-line  1.0
incomplete 2.0    123
vlc       3.0.20   3777  latest/stable  videolan✓   -
"""
        with (
            patch("popctl.scanners.snap.command_exists", return_value=True),
            patch("popctl.scanners.snap.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(stdout=output, stderr="", returncode=0)

            packages = list(scanner.scan())

        assert len(packages) == 2
        names = [p.name for p in packages]
        assert "firefox" in names
        assert "vlc" in names
