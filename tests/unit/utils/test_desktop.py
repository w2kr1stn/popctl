import pytest
from popctl.utils.desktop import DesktopFamily, normalize_desktop_family


@pytest.mark.parametrize(
    ("current_desktop", "session_desktop", "expected"),
    (
        ("GNOME", None, DesktopFamily.GNOME),
        ("ubuntu:GNOME", None, DesktopFamily.GNOME),
        ("pop:GNOME", None, DesktopFamily.GNOME),
        ("COSMIC", None, DesktopFamily.COSMIC),
        ("cosmic", "COSMIC", DesktopFamily.COSMIC),
        (None, "gnome", DesktopFamily.GNOME),
        (None, None, DesktopFamily.UNKNOWN),
        ("", "", DesktopFamily.UNKNOWN),
        ("KDE", None, DesktopFamily.UNKNOWN),
        ("GNOME:KDE", None, DesktopFamily.UNKNOWN),
        ("GNOME:COSMIC", None, DesktopFamily.UNKNOWN),
        ("GNOME", "COSMIC", DesktopFamily.UNKNOWN),
    ),
)
def test_normalize_desktop_family(
    current_desktop: str | None,
    session_desktop: str | None,
    expected: DesktopFamily,
) -> None:
    assert normalize_desktop_family(current_desktop, session_desktop) is expected
