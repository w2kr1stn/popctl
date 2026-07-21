from enum import Enum
from typing import Final


class DesktopFamily(str, Enum):
    GNOME = "GNOME"
    COSMIC = "COSMIC"
    UNKNOWN = "UNKNOWN"


_FAMILY_ALIASES: Final = {
    "ubuntu": DesktopFamily.GNOME,
    "pop": DesktopFamily.GNOME,
    "gnome": DesktopFamily.GNOME,
    "cosmic": DesktopFamily.COSMIC,
}


def normalize_desktop_family(
    current_desktop: str | None,
    session_desktop: str | None,
) -> DesktopFamily:
    families = tuple(
        family
        for signal in (current_desktop, session_desktop)
        if (family := _family_from_signal(signal)) is not None
    )
    if not families or DesktopFamily.UNKNOWN in families or len(set(families)) != 1:
        return DesktopFamily.UNKNOWN
    return families[0]


def _family_from_signal(signal: str | None) -> DesktopFamily | None:
    if signal is None or signal == "":
        return None
    families: set[DesktopFamily] = set()
    for token in signal.split(":"):
        family = _FAMILY_ALIASES.get(token.casefold())
        if family is None:
            return DesktopFamily.UNKNOWN
        families.add(family)
    if len(families) != 1:
        return DesktopFamily.UNKNOWN
    return families.pop()
