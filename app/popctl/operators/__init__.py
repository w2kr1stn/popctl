from popctl.models.package import PackageSource
from popctl.operators.apt import AptOperator
from popctl.operators.base import Operator
from popctl.operators.flatpak import FlatpakOperator
from popctl.operators.snap import SnapOperator

_OPERATOR_CLASSES: dict[PackageSource, type[Operator]] = {
    PackageSource.APT: AptOperator,
    PackageSource.FLATPAK: FlatpakOperator,
    PackageSource.SNAP: SnapOperator,
}


def get_available_operators(
    source: PackageSource | None = None, dry_run: bool = False
) -> list[Operator]:
    classes = _OPERATOR_CLASSES if source is None else {source: _OPERATOR_CLASSES[source]}
    operators = [cls(dry_run=dry_run) for cls in classes.values()]
    return [op for op in operators if op.is_available()]
