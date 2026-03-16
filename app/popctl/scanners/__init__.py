from popctl.models.package import PackageSource
from popctl.scanners.apt import AptScanner
from popctl.scanners.base import Scanner
from popctl.scanners.flatpak import FlatpakScanner
from popctl.scanners.snap import SnapScanner

_SCANNER_CLASSES: dict[PackageSource, type[Scanner]] = {
    PackageSource.APT: AptScanner,
    PackageSource.FLATPAK: FlatpakScanner,
    PackageSource.SNAP: SnapScanner,
}


def get_scanners(source: PackageSource | None = None) -> list[Scanner]:
    classes = _SCANNER_CLASSES if source is None else {source: _SCANNER_CLASSES[source]}
    return [cls() for cls in classes.values()]


def get_available_scanners(source: PackageSource | None = None) -> list[Scanner]:
    return [s for s in get_scanners(source) if s.is_available()]
