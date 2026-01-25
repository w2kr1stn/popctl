"""Unit tests for AptScanner.

Tests for the APT package scanner implementation.
"""

from unittest.mock import patch

import pytest
from popctl.models.package import PackageSource, PackageStatus
from popctl.scanners.apt import AptScanner
from popctl.utils.shell import CommandResult


class TestAptScanner:
    """Tests for AptScanner class."""

    @pytest.fixture
    def scanner(self) -> AptScanner:
        """Create AptScanner instance."""
        return AptScanner()

    def test_source_is_apt(self, scanner: AptScanner) -> None:
        """Scanner returns APT as source."""
        assert scanner.source == PackageSource.APT

    def test_is_available_with_both_commands(self, scanner: AptScanner) -> None:
        """is_available returns True when both commands exist."""
        with patch("popctl.scanners.apt.command_exists") as mock_exists:
            mock_exists.side_effect = lambda cmd: cmd in ["dpkg-query", "apt-mark"]
            assert scanner.is_available() is True

    def test_is_available_missing_dpkg(self, scanner: AptScanner) -> None:
        """is_available returns False when dpkg-query is missing."""
        with patch("popctl.scanners.apt.command_exists") as mock_exists:
            mock_exists.side_effect = lambda cmd: cmd == "apt-mark"
            assert scanner.is_available() is False

    def test_is_available_missing_apt_mark(self, scanner: AptScanner) -> None:
        """is_available returns False when apt-mark is missing."""
        with patch("popctl.scanners.apt.command_exists") as mock_exists:
            mock_exists.side_effect = lambda cmd: cmd == "dpkg-query"
            assert scanner.is_available() is False

    def test_scan_parses_packages_correctly(
        self,
        scanner: AptScanner,
        mock_dpkg_output: str,
        mock_apt_mark_output: str,
    ) -> None:
        """Scan correctly parses dpkg-query output."""
        with (
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.apt.run_command") as mock_run,
        ):
            mock_run.side_effect = [
                CommandResult(stdout=mock_apt_mark_output, stderr="", returncode=0),
                CommandResult(stdout=mock_dpkg_output, stderr="", returncode=0),
            ]

            packages = list(scanner.scan())

        assert len(packages) == 5

        # Check firefox (manual)
        firefox = packages[0]
        assert firefox.name == "firefox"
        assert firefox.version == "128.0"
        assert firefox.status == PackageStatus.MANUAL
        assert firefox.size_bytes == 204800 * 1024
        assert firefox.description == "Mozilla Firefox web browser"

        # Check libgtk-3-0 (auto)
        libgtk = packages[2]
        assert libgtk.name == "libgtk-3-0"
        assert libgtk.status == PackageStatus.AUTO_INSTALLED

    def test_scan_handles_auto_installed(
        self,
        scanner: AptScanner,
        mock_dpkg_output: str,
        mock_apt_mark_output: str,
    ) -> None:
        """Scan correctly identifies auto-installed packages."""
        with (
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.apt.run_command") as mock_run,
        ):
            mock_run.side_effect = [
                CommandResult(stdout=mock_apt_mark_output, stderr="", returncode=0),
                CommandResult(stdout=mock_dpkg_output, stderr="", returncode=0),
            ]

            packages = list(scanner.scan())
            auto_packages = [p for p in packages if p.status == PackageStatus.AUTO_INSTALLED]

        assert len(auto_packages) == 2
        auto_names = {p.name for p in auto_packages}
        assert auto_names == {"libgtk-3-0", "python3"}

    def test_scan_raises_when_unavailable(self, scanner: AptScanner) -> None:
        """Scan raises RuntimeError when APT is unavailable."""
        with (
            patch("popctl.scanners.apt.command_exists", return_value=False),
            pytest.raises(RuntimeError, match="not available"),
        ):
            list(scanner.scan())

    def test_scan_raises_on_dpkg_failure(self, scanner: AptScanner) -> None:
        """Scan raises RuntimeError when dpkg-query fails."""
        with (
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.apt.run_command") as mock_run,
        ):
            mock_run.side_effect = [
                CommandResult(stdout="", stderr="", returncode=0),  # apt-mark
                CommandResult(stdout="", stderr="dpkg error", returncode=1),  # dpkg-query
            ]

            with pytest.raises(RuntimeError, match="dpkg-query failed"):
                list(scanner.scan())

    def test_scan_handles_apt_mark_failure(
        self,
        scanner: AptScanner,
        mock_dpkg_output: str,
    ) -> None:
        """Scan treats all packages as manual if apt-mark fails."""
        with (
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.apt.run_command") as mock_run,
        ):
            mock_run.side_effect = [
                CommandResult(stdout="", stderr="error", returncode=1),  # apt-mark fails
                CommandResult(stdout=mock_dpkg_output, stderr="", returncode=0),
            ]

            packages = list(scanner.scan())

        # All should be treated as manual
        assert all(p.status == PackageStatus.MANUAL for p in packages)

    def test_scan_handles_empty_output(self, scanner: AptScanner) -> None:
        """Scan handles empty dpkg-query output."""
        with (
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.apt.run_command") as mock_run,
        ):
            mock_run.side_effect = [
                CommandResult(stdout="", stderr="", returncode=0),
                CommandResult(stdout="", stderr="", returncode=0),
            ]

            packages = list(scanner.scan())

        assert len(packages) == 0

    def test_scan_skips_malformed_lines(
        self,
        scanner: AptScanner,
        mock_malformed_output: str,
    ) -> None:
        """Scan skips lines that cannot be parsed."""
        with (
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.apt.run_command") as mock_run,
        ):
            mock_run.side_effect = [
                CommandResult(stdout="", stderr="", returncode=0),
                CommandResult(stdout=mock_malformed_output, stderr="", returncode=0),
            ]

            packages = list(scanner.scan())

        assert len(packages) == 0

    def test_scan_handles_missing_description(self, scanner: AptScanner) -> None:
        """Scan handles packages without description."""
        minimal_output = "minimal-pkg\t1.0\t100\t"

        with (
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.apt.run_command") as mock_run,
        ):
            mock_run.side_effect = [
                CommandResult(stdout="", stderr="", returncode=0),
                CommandResult(stdout=minimal_output, stderr="", returncode=0),
            ]

            packages = list(scanner.scan())

        assert len(packages) == 1
        assert packages[0].description is None

    def test_scan_handles_non_numeric_size(self, scanner: AptScanner) -> None:
        """Scan handles packages with non-numeric size."""
        bad_size_output = "pkg\t1.0\tNaN\tSome package"

        with (
            patch("popctl.scanners.apt.command_exists", return_value=True),
            patch("popctl.scanners.apt.run_command") as mock_run,
        ):
            mock_run.side_effect = [
                CommandResult(stdout="", stderr="", returncode=0),
                CommandResult(stdout=bad_size_output, stderr="", returncode=0),
            ]

            packages = list(scanner.scan())

        assert len(packages) == 1
        assert packages[0].size_bytes is None


class TestAptScannerIntegration:
    """Integration tests that use actual system commands."""

    @pytest.mark.skipif(
        True,  # Skip by default in unit tests
        reason="Integration test - requires actual APT system",
    )
    def test_real_scan(self) -> None:
        """Test actual system scan (only on real Debian/Ubuntu)."""
        scanner = AptScanner()
        if scanner.is_available():
            packages = list(scanner.scan())
            assert len(packages) > 0
