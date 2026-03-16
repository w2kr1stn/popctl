from __future__ import annotations

import sys
from typing import Literal

from rich.console import Console

from popctl.core.theme import get_theme

# Type alias for Rich color system options
_ColorSystem = Literal["auto", "standard", "256", "truecolor", "windows"]


# Shared console instances (theme and color system loaded once at import)
_theme = get_theme()
_color_system: _ColorSystem | None = "truecolor" if sys.stdout.isatty() else None
console = Console(theme=_theme, color_system=_color_system)
_err_console = Console(theme=_theme, stderr=True, color_system=_color_system)


def print_info(message: str) -> None:
    console.print(f"[info]{message}[/]")


def print_warning(message: str) -> None:
    _err_console.print(f"[warning]Warning:[/] {message}")


def print_error(message: str) -> None:
    _err_console.print(f"[error]Error:[/] {message}")


def print_success(message: str) -> None:
    console.print(f"[success]{message}[/]")


def format_size(size_bytes: int | None) -> str:
    if size_bytes is None or size_bytes == 0:
        return "0 B"
    size = float(size_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if abs(size) < 1024:
            return f"{size:.1f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.1f} TB"
