from __future__ import annotations

import re
import shlex
from collections.abc import Iterable
from dataclasses import dataclass

from popctl.models.package import PackageSource
from popctl.operators import get_available_operators
from popctl.sources.keytrust import KeyTrustError, selectors_are_satisfied, verify_public_material
from popctl.sources.models import (
    AptSource,
    AptSourceFormat,
    FlatpakApp,
    FlatpakRemote,
    ReplayMode,
    SnapChannel,
    SourcePlatform,
    SourcesConfig,
)

_INSECURE_APT_PATTERN = re.compile(
    r"(?:trusted\s*(?:=|:)\s*(?:yes|true|1)|allow-insecure\s*(?:=|:)\s*(?:yes|true|1))",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class SourcePreflightCheck:
    subject: str
    success: bool
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class SourcePreflightResult:
    selected_managers: tuple[PackageSource, ...]
    checks: tuple[SourcePreflightCheck, ...]

    @property
    def success(self) -> bool:
        return all(check.success for check in self.checks)

    @property
    def error(self) -> str | None:
        failures = [
            f"{check.subject}: {check.detail or 'preflight failed'}"
            for check in self.checks
            if not check.success
        ]
        return "; ".join(failures) if failures else None


def selected_managers(
    sources: SourcesConfig,
    source_filter: PackageSource | None,
) -> tuple[PackageSource, ...]:
    selected: list[PackageSource] = []
    if source_filter in {None, PackageSource.APT} and sources.apt.entries:
        selected.append(PackageSource.APT)
    if source_filter in {None, PackageSource.FLATPAK} and (
        sources.flatpak.remotes or sources.flatpak.apps
    ):
        selected.append(PackageSource.FLATPAK)
    if source_filter in {None, PackageSource.SNAP} and sources.snap.packages:
        selected.append(PackageSource.SNAP)
    return tuple(selected)


def preflight_manager_availability(
    managers: Iterable[PackageSource],
) -> tuple[SourcePreflightCheck, ...]:
    checks: list[SourcePreflightCheck] = []
    for manager in managers:
        available = bool(get_available_operators(manager))
        checks.append(
            SourcePreflightCheck(
                subject=f"manager:{manager.value}",
                success=available,
                detail=None if available else "selected package manager is unavailable",
            )
        )
    return tuple(checks)


def _legacy_apt_identity(stanza: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    line = stanza.split("#", 1)[0].strip()
    try:
        fields = shlex.split(line, posix=True)
    except ValueError:
        return (), ()
    if not fields or fields[0] not in {"deb", "deb-src"}:
        return (), ()
    index = 1
    if index < len(fields) and fields[index].startswith("["):
        index += 1
    if len(fields) <= index + 1:
        return (), ()
    return (fields[index],), (fields[index + 1],)


def _deb822_apt_identity(stanza: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    fields: dict[str, str] = {}
    active_field: str | None = None
    for line in stanza.splitlines():
        if line[:1].isspace() and active_field is not None:
            fields[active_field] = f"{fields[active_field]} {line.strip()}"
            continue
        name, separator, value = line.partition(":")
        if not separator:
            active_field = None
            continue
        active_field = name.lower()
        fields[active_field] = value.strip()
    return tuple(fields.get("uris", "").split()), tuple(fields.get("suites", "").split())


def apt_identity(source: AptSource) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if source.format is AptSourceFormat.LEGACY:
        return _legacy_apt_identity(source.verbatim_stanza)
    return _deb822_apt_identity(source.verbatim_stanza)


def _apt_compatible(
    source: AptSource,
    captured: SourcePlatform,
    target: SourcePlatform,
    live: AptSource | None,
) -> bool:
    expected_uris, suites = apt_identity(source)
    if not expected_uris or not suites:
        return False
    if all(suite.lower() == "stable" for suite in suites):
        if live is None:
            return True
        live_uris, _ = apt_identity(live)
        return live_uris == expected_uris
    return captured.codename.lower() == target.codename.lower() and all(
        suite.lower() == target.codename.lower()
        or suite.lower().startswith(f"{target.codename.lower()}-")
        for suite in suites
    )


def _verify_apt_source(source: AptSource, sources: SourcesConfig) -> str | None:
    if source.replay_mode is ReplayMode.BLOCKED:
        return "blocked APT source cannot be replayed"
    if _INSECURE_APT_PATTERN.search(source.verbatim_stanza):
        return "insecure APT source cannot be replayed"
    keys = {key.id: key for key in sources.apt.keys}
    if not source.key_ids:
        return "APT source has no signing key"
    fingerprints: set[str] = set()
    for key_id in source.key_ids:
        key = keys.get(key_id)
        if key is None:
            return "APT source references an unknown signing key"
        try:
            verified = verify_public_material(key.armor)
        except KeyTrustError:
            return "APT source key has no verified public material"
        if frozenset(verified.fingerprints) != frozenset(key.fingerprints):
            return "APT source key fingerprints do not match the manifest"
        fingerprints.update(verified.fingerprints)
    try:
        selectors_satisfied = selectors_are_satisfied(
            source.signed_by.fingerprint_selectors, tuple(fingerprints)
        )
    except KeyTrustError:
        return "APT Signed-By selectors are invalid"
    if source.signed_by.fingerprint_selectors and not selectors_satisfied:
        return "APT Signed-By fingerprints do not match the manifest"
    return None


def _verify_flatpak_remote(remote: FlatpakRemote) -> str | None:
    if remote.replay_mode is ReplayMode.BLOCKED:
        return "blocked Flatpak remote cannot be replayed"
    if not remote.gpg_verify:
        return "Flatpak remote disables GPG verification"
    try:
        verified = verify_public_material(remote.gpg_key_armor)
    except KeyTrustError:
        return "Flatpak remote has no verified public key material"
    if frozenset(verified.fingerprints) != frozenset(remote.gpg_fingerprints):
        return "Flatpak key fingerprints do not match the manifest"
    return None


def _flatpak_app_error(app: FlatpakApp, remotes: set[tuple[str, str]]) -> str | None:
    if (app.scope.value, app.origin) not in remotes:
        return "Flatpak app has no recorded remote in its scope"
    return None


def _snap_error(channel: SnapChannel) -> str | None:
    if channel.replay_mode is ReplayMode.BLOCKED:
        return "blocked Snap channel cannot be replayed"
    if not channel.channel.strip():
        return "Snap package has no recorded channel"
    return None


def preflight_sources(
    sources: SourcesConfig,
    *,
    source_filter: PackageSource | None,
    target_platform: SourcePlatform,
    live_sources: SourcesConfig | None = None,
) -> SourcePreflightResult:
    managers = selected_managers(sources, source_filter)
    checks = list(preflight_manager_availability(managers))
    if not managers:
        return SourcePreflightResult(selected_managers=managers, checks=tuple(checks))

    platform_matches = sources.platform.distro_id.lower() == target_platform.distro_id.lower()
    checks.append(
        SourcePreflightCheck(
            subject="platform",
            success=platform_matches,
            detail=None if platform_matches else "captured and target distro IDs do not match",
        )
    )

    live_apt = {
        source.managed_target: source
        for source in (live_sources.apt.entries if live_sources is not None else ())
    }
    if PackageSource.APT in managers:
        for source in sources.apt.entries:
            trust_error = _verify_apt_source(source, sources)
            compatible = _apt_compatible(
                source,
                sources.platform,
                target_platform,
                live_apt.get(source.managed_target),
            )
            detail = trust_error
            if detail is None and not compatible:
                detail = "APT source suite is incompatible with the target platform"
            checks.append(
                SourcePreflightCheck(
                    subject=f"apt:{source.managed_target}",
                    success=detail is None,
                    detail=detail,
                )
            )

    if PackageSource.FLATPAK in managers:
        remotes = {(remote.scope.value, remote.name) for remote in sources.flatpak.remotes}
        for remote in sources.flatpak.remotes:
            error = _verify_flatpak_remote(remote)
            checks.append(
                SourcePreflightCheck(
                    subject=f"flatpak:{remote.scope.value}:{remote.name}",
                    success=error is None,
                    detail=error,
                )
            )
        for app in sources.flatpak.apps:
            error = _flatpak_app_error(app, remotes)
            checks.append(
                SourcePreflightCheck(
                    subject=f"flatpak-app:{app.scope.value}:{app.id}@{app.branch}",
                    success=error is None,
                    detail=error,
                )
            )

    if PackageSource.SNAP in managers:
        for channel in sources.snap.packages:
            error = _snap_error(channel)
            checks.append(
                SourcePreflightCheck(
                    subject=f"snap:{channel.name}",
                    success=error is None,
                    detail=error,
                )
            )

    return SourcePreflightResult(selected_managers=managers, checks=tuple(checks))
