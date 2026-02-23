"""Unit tests for FlatpakScanner.

Tests for the Flatpak package scanner implementation.
"""

from unittest.mock import patch

import pytest
from popctl.models.package import PackageSource, PackageStatus
from popctl.scanners.flatpak import FlatpakScanner
from popctl.utils.shell import CommandResult


class TestFlatpakScanner:
    """Tests for FlatpakScanner class."""

    @pytest.fixture
    def scanner(self) -> FlatpakScanner:
        """Create FlatpakScanner instance."""
        return FlatpakScanner()

    def test_source_is_flatpak(self, scanner: FlatpakScanner) -> None:
        """Scanner returns FLATPAK as source."""
        assert scanner.source == PackageSource.FLATPAK

    def test_is_available_with_flatpak(self, scanner: FlatpakScanner) -> None:
        """is_available returns True when flatpak command exists."""
        with patch("popctl.scanners.flatpak.command_exists") as mock_exists:
            mock_exists.return_value = True
            assert scanner.is_available() is True
            mock_exists.assert_called_once_with("flatpak")

    def test_is_available_without_flatpak(self, scanner: FlatpakScanner) -> None:
        """is_available returns False when flatpak is not installed."""
        with patch("popctl.scanners.flatpak.command_exists") as mock_exists:
            mock_exists.return_value = False
            assert scanner.is_available() is False

    def test_scan_parses_packages_correctly(
        self,
        scanner: FlatpakScanner,
        mock_flatpak_output: str,
    ) -> None:
        """Scan correctly parses flatpak list output."""
        with (
            patch("popctl.scanners.flatpak.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(
                stdout=mock_flatpak_output, stderr="", returncode=0
            )

            packages = list(scanner.scan())

        assert len(packages) == 4

        # Check Spotify
        spotify = packages[0]
        assert spotify.name == "com.spotify.Client"
        assert spotify.version == "1.2.31.1205"
        assert spotify.status == PackageStatus.MANUAL
        assert spotify.size_bytes == int(1.2 * 1024 * 1024 * 1024)  # 1.2 GB
        assert spotify.description == "Music streaming service"

        # Check Firefox
        firefox = packages[1]
        assert firefox.name == "org.mozilla.firefox"
        assert firefox.version == "128.0"
        assert firefox.size_bytes == 500 * 1024 * 1024  # 500 MB

    def test_scan_all_packages_are_manual(
        self,
        scanner: FlatpakScanner,
        mock_flatpak_output: str,
    ) -> None:
        """All Flatpak packages should be marked as manual."""
        with (
            patch("popctl.scanners.flatpak.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(
                stdout=mock_flatpak_output, stderr="", returncode=0
            )

            packages = list(scanner.scan())

        # All should be manual
        assert all(p.status == PackageStatus.MANUAL for p in packages)
        assert all(p.is_manual for p in packages)

    def test_scan_raises_when_unavailable(self, scanner: FlatpakScanner) -> None:
        """Scan raises RuntimeError when Flatpak is unavailable."""
        with (
            patch("popctl.scanners.flatpak.command_exists", return_value=False),
            pytest.raises(RuntimeError, match="not available"),
        ):
            list(scanner.scan())

    def test_scan_raises_on_flatpak_failure(self, scanner: FlatpakScanner) -> None:
        """Scan raises RuntimeError when flatpak list fails."""
        with (
            patch("popctl.scanners.flatpak.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(
                stdout="", stderr="error: flatpak failed", returncode=1
            )

            with pytest.raises(RuntimeError, match="flatpak list failed"):
                list(scanner.scan())

    def test_scan_handles_empty_output(self, scanner: FlatpakScanner) -> None:
        """Scan handles empty flatpak list output."""
        with (
            patch("popctl.scanners.flatpak.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(stdout="", stderr="", returncode=0)

            packages = list(scanner.scan())

        assert len(packages) == 0

    def test_scan_skips_malformed_lines(self, scanner: FlatpakScanner) -> None:
        """Scan skips lines that cannot be parsed."""
        malformed = """incomplete_app
\t\t\t
single_field"""

        with (
            patch("popctl.scanners.flatpak.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(
                stdout=malformed, stderr="", returncode=0
            )

            packages = list(scanner.scan())

        assert len(packages) == 0

    def test_scan_handles_missing_description(self, scanner: FlatpakScanner) -> None:
        """Scan handles packages without description."""
        minimal = "com.example.App\t1.0\t100 MB\t"

        with (
            patch("popctl.scanners.flatpak.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(
                stdout=minimal, stderr="", returncode=0
            )

            packages = list(scanner.scan())

        assert len(packages) == 1
        assert packages[0].description is None

    def test_scan_handles_missing_size(self, scanner: FlatpakScanner) -> None:
        """Scan handles packages without size."""
        no_size = "com.example.App\t1.0\t\tSome app"

        with (
            patch("popctl.scanners.flatpak.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(
                stdout=no_size, stderr="", returncode=0
            )

            packages = list(scanner.scan())

        assert len(packages) == 1
        assert packages[0].size_bytes is None


class TestFlatpakScannerSizeParsing:
    """Tests for size string parsing."""

    @pytest.fixture
    def scanner(self) -> FlatpakScanner:
        """Create FlatpakScanner instance."""
        return FlatpakScanner()

    @pytest.mark.parametrize(
        ("size_str", "expected_bytes"),
        [
            ("100 B", 100),
            ("100 KB", 100 * 1024),
            ("100 MB", 100 * 1024 * 1024),
            ("1.5 GB", int(1.5 * 1024 * 1024 * 1024)),
            ("0.5 TB", int(0.5 * 1024 * 1024 * 1024 * 1024)),
            ("1 kb", 1024),  # Case insensitive
            ("1.0 mb", 1024 * 1024),  # Case insensitive
            ("  1 GB  ", 1024 * 1024 * 1024),  # Leading/trailing spaces are handled
            ("1GB", 1024 * 1024 * 1024),  # No space between number and unit works
        ],
    )
    def test_parse_size(
        self,
        scanner: FlatpakScanner,
        size_str: str,
        expected_bytes: int | None,
    ) -> None:
        """Test size parsing with various formats."""
        result = scanner._parse_size(size_str)
        assert result == expected_bytes

    def test_parse_size_empty(self, scanner: FlatpakScanner) -> None:
        """Test parsing empty size string."""
        assert scanner._parse_size("") is None

    def test_parse_size_invalid(self, scanner: FlatpakScanner) -> None:
        """Test parsing invalid size strings."""
        assert scanner._parse_size("unknown") is None
        assert scanner._parse_size("abc MB") is None
        assert scanner._parse_size("100 XX") is None


class TestFlatpakScannerManualOnly:
    """Tests for scan_manual_only method."""

    @pytest.fixture
    def scanner(self) -> FlatpakScanner:
        """Create FlatpakScanner instance."""
        return FlatpakScanner()

    def test_scan_manual_only_returns_all(
        self,
        scanner: FlatpakScanner,
        mock_flatpak_output: str,
    ) -> None:
        """scan_manual_only returns all packages since all are manual."""
        with (
            patch("popctl.scanners.flatpak.command_exists", return_value=True),
            patch("popctl.scanners.flatpak.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(
                stdout=mock_flatpak_output, stderr="", returncode=0
            )

            all_packages = list(scanner.scan())
            manual_packages = list(scanner.scan_manual_only())

        # For Flatpak, scan_manual_only should return same as scan
        # since all packages are manual
        assert len(manual_packages) == len(all_packages)
