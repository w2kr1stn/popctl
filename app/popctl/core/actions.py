"""Action conversion from diff results.

Pure business logic for converting diff results into actionable
package management operations. Extracted from cli/commands/apply.py
for reuse by both the apply and sync commands.
"""

from popctl.core.baseline import is_protected
from popctl.core.diff import DiffResult
from popctl.models.action import Action, create_install_action, create_remove_action
from popctl.models.package import PackageSource

SOURCE_MAP: dict[str, PackageSource] = {
    "apt": PackageSource.APT,
    "flatpak": PackageSource.FLATPAK,
    "snap": PackageSource.SNAP,
}


def source_to_package_source(source_str: str) -> PackageSource:
    """Convert source string to PackageSource enum.

    Args:
        source_str: Source string ("apt" or "flatpak").

    Returns:
        Corresponding PackageSource enum value.

    Raises:
        KeyError: If source_str is not a valid source.
    """
    return SOURCE_MAP[source_str]


def diff_to_actions(diff_result: DiffResult, purge: bool = False) -> list[Action]:
    """Convert diff result to list of actions.

    Only MISSING and EXTRA diffs are converted to actions:
    - MISSING: Package in manifest but not installed -> INSTALL
    - EXTRA: Package marked for removal but still installed -> REMOVE/PURGE

    NEW packages (installed but not in manifest) are ignored - the user
    must explicitly add them to the remove list in the manifest.

    Protected packages are excluded from removal actions.

    Args:
        diff_result: Result from DiffEngine.compute_diff().
        purge: If True, use PURGE instead of REMOVE for APT packages.

    Returns:
        List of Action objects to execute.
    """
    actions: list[Action] = []

    # MISSING -> INSTALL
    for entry in diff_result.missing:
        pkg_source = source_to_package_source(entry.source)
        action = create_install_action(
            package=entry.name,
            source=pkg_source,
            reason="Package in manifest but not installed",
        )
        actions.append(action)

    # EXTRA -> REMOVE/PURGE
    for entry in diff_result.extra:
        # Skip protected packages (should not happen as DiffEngine filters them,
        # but defense in depth)
        if is_protected(entry.name):
            continue

        pkg_source = source_to_package_source(entry.source)

        # Purge applies to APT and Snap packages
        use_purge = purge and pkg_source in (PackageSource.APT, PackageSource.SNAP)

        action = create_remove_action(
            package=entry.name,
            source=pkg_source,
            reason="Package marked for removal in manifest",
            purge=use_purge,
        )
        actions.append(action)

    return actions
