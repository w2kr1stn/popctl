#!/usr/bin/env python3
"""Move failed-purge packages from remove to keep in the manifest.

Run on the host after a sync where packages failed to purge due to
reverse dependencies:

    python3 scripts/fix_failed_purges.py
"""

from popctl.core.manifest import load_manifest, save_manifest
from popctl.models.manifest import PackageEntry

FAILED_PACKAGES = [
    "chrony",
    "gettext",
    "gnome-menus",
    "humanity-icon-theme",
    "inputattach",
    "intel-microcode",
    "libdrm-intel1",
    "libibus-1.0-5",
    "libiec61883-0",
    "libraw1394-11",
    "libwacom-common",
    "libwacom9",
    "python3-httplib2",
    "python3-ibus-1.0",
    "python3-launchpadlib",
    "python3-lazr.restfulclient",
    "python3-lazr.uri",
    "python3-oauthlib",
    "python3-wadllib",
    "sgml-base",
    "ubuntu-mono",
    "vdpau-driver-all",
    "wireless-tools",
    "xml-core",
]


def main() -> None:
    manifest = load_manifest()
    moved = 0

    for pkg_name in FAILED_PACKAGES:
        if pkg_name in manifest.packages.remove:
            entry = manifest.packages.remove.pop(pkg_name)
            manifest.packages.keep[pkg_name] = PackageEntry(
                source=entry.source,
                reason="Has reverse dependencies — cannot be safely purged",
            )
            moved += 1
            print(f"  {pkg_name}: remove → keep")
        elif pkg_name not in manifest.packages.keep:
            manifest.packages.keep[pkg_name] = PackageEntry(
                source="apt",
                reason="Has reverse dependencies — cannot be safely purged",
            )
            moved += 1
            print(f"  {pkg_name}: (missing) → keep")

    if moved:
        path = save_manifest(manifest)
        print(f"\nMoved {moved} packages to keep. Manifest saved: {path}")
    else:
        print("No changes needed — all packages already in keep.")


if __name__ == "__main__":
    main()
