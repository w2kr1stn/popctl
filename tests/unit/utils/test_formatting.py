"""Unit tests for formatting utilities."""

from popctl.utils.formatting import format_size


class TestFormatSize:
    """Tests for the format_size helper function."""

    def test_format_size_zero(self) -> None:
        """Format 0 bytes."""
        assert format_size(0) == "0 B"

    def test_format_size_none(self) -> None:
        """Format None bytes."""
        assert format_size(None) == "0 B"

    def test_format_size_bytes(self) -> None:
        """Format small byte values."""
        assert format_size(512) == "512 B"

    def test_format_size_kilobytes(self) -> None:
        """Format kilobyte values."""
        result = format_size(2048)
        assert "KB" in result

    def test_format_size_megabytes(self) -> None:
        """Format megabyte values."""
        result = format_size(5 * 1024 * 1024)
        assert "MB" in result
