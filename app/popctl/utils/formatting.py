"""Rich console formatting utilities.

Provides consistent formatting for CLI output using Rich.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from rich.console import Console
from rich.table import Table

from popctl.core.theme import get_theme

if TYPE_CHECKING:
    from popctl.models.package import ScannedPackage


def _detect_color_system() -> str | None:
    """Detect the best color system for the current terminal.

    Returns "truecolor" for interactive terminals to enable full hex color support,
    None otherwise to let Rich auto-detect.
    """
    if sys.stdout.isatty():
        return "truecolor"
    return None


# Shared console instances (theme loaded once at import)
console = Console(theme=get_theme(), color_system=_detect_color_system())
err_console = Console(theme=get_theme(), stderr=True, color_system=_detect_color_system())


def create_package_table(title: str = "Installed Packages") -> Table:
    """Create a pre-configured table for displaying packages.

    The table uses zebra striping for improved readability and a minimal
    status icon column.

    Args:
        title: Table title.

    Returns:
        Rich Table configured for package display.
    """
    table = Table(
        title=title,
        show_header=True,
        header_style="bold_header",
        border_style="border",
        row_styles=["", "on grey7"],  # Zebra striping for readability
    )
    # Status column: minimal width, icon only, no header text
    table.add_column("", width=2, justify="center")
    table.add_column("Package", no_wrap=True)  # Style set per row
    table.add_column("Version", style="muted")
    table.add_column("Size", style="info", justify="right")
    table.add_column("Description", style="text", overflow="ellipsis")
    return table


def format_package_row(pkg: ScannedPackage) -> tuple[str, str, str, str, str]:
    """Format a package as a table row with proper styling.

    Manual packages are highlighted with a filled circle icon and mint color,
    while auto-installed packages use an empty circle and muted styling.

    Args:
        pkg: The scanned package to format.

    Returns:
        Tuple of (icon, name, version, size, description) with Rich markup.
    """
    if pkg.is_manual:
        icon = "[package_manual]\u25cf[/]"  # Filled circle
        name = f"[package_manual]{pkg.name}[/]"  # bold is in the style
    else:
        icon = "[package_auto]\u25cb[/]"  # Empty circle
        name = f"[package_auto]{pkg.name}[/]"

    version = f"[muted]{pkg.version}[/]"
    size = f"[info]{pkg.size_human}[/]"
    desc = f"[text]{pkg.description or '-'}[/]"

    return (icon, name, version, size, desc)


def format_status(is_manual: bool) -> str:
    """Format package status with color markup.

    .. deprecated::
        Use :func:`format_package_row` instead for full row formatting.

    Args:
        is_manual: True if package was manually installed.

    Returns:
        Rich markup string for status display.
    """
    if is_manual:
        return "[package.manual]manual[/]"
    return "[package.auto]auto[/]"


def print_info(message: str) -> None:
    """Print an info message."""
    console.print(f"[info]{message}[/]")


def print_warning(message: str) -> None:
    """Print a warning message."""
    err_console.print(f"[warning]Warning:[/] {message}")


def print_error(message: str) -> None:
    """Print an error message."""
    err_console.print(f"[error]Error:[/] {message}")


def print_success(message: str) -> None:
    """Print a success message."""
    console.print(f"[success]{message}[/]")
