from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import typer

from popctl.cli.types import SourceChoice
from popctl.models.manifest import Manifest
from popctl.models.package import PackageSource
from popctl.sources.capture import SourceCaptureError, capture_platform, capture_sources
from popctl.sources.diff import (
    SourceDiffEntry,
    SourceDiffResult,
    SourceDiffType,
    SourceRecordKind,
    compute_source_diff,
)
from popctl.sources.models import (
    AptSource,
    AptSources,
    FlatpakApp,
    FlatpakRemote,
    FlatpakSources,
    SnapChannel,
    SnapSources,
    SourcesConfig,
)
from popctl.sources.preflight import (
    preflight_manager_availability,
    preflight_sources,
    selected_managers,
)
from popctl.sources.provision import (
    SourceProvisionChange,
    SourceProvisionStatus,
    provision_sources,
)
from popctl.utils.formatting import print_error, print_info, print_warning


@dataclass(frozen=True, slots=True)
class SourceInteractionPolicy:
    yes: bool = False
    interactive: bool = True


@dataclass(frozen=True, slots=True)
class SourcePhaseResult:
    success: bool
    source_diff: SourceDiffResult = SourceDiffResult()
    selected_managers: tuple[PackageSource, ...] = ()
    retained_artifacts: tuple[str, ...] = ()
    error: str | None = None


@dataclass(frozen=True, slots=True)
class SourceRefreshResult:
    success: bool
    manifest: Manifest
    source_diff: SourceDiffResult = SourceDiffResult()
    changed: bool = False
    error: str | None = None


def _source_filter(source: SourceChoice) -> PackageSource | None:
    return source.to_package_source()


def _source_diff(
    sources: SourcesConfig,
    live_sources: SourcesConfig,
    source_filter: PackageSource | None,
) -> SourceDiffResult:
    return compute_source_diff(sources, live_sources, source_filter=source_filter)


def _source_changes(source_diff: SourceDiffResult) -> tuple[SourceDiffEntry, ...]:
    return source_diff.changed


def _confirm_changes(
    changes: tuple[SourceDiffEntry, ...],
    interaction: SourceInteractionPolicy,
    *,
    action: str,
    allow_rejection: bool = False,
) -> tuple[bool, tuple[SourceDiffEntry, ...], str | None]:
    confirmed: list[SourceDiffEntry] = []
    for change in changes:
        if interaction.yes:
            return False, (), "--yes cannot approve a new or changed source trust relationship"
        if not interaction.interactive:
            return False, (), "non-interactive execution cannot approve a new or changed source"
        if not typer.confirm(
            f"{action} source {change.label} ({change.kind.value})?",
            default=False,
        ):
            if allow_rejection:
                continue
            return False, (), "source trust confirmation was declined"
        confirmed.append(change)
    return True, tuple(confirmed), None


def _is_operation_owned(change: SourceDiffEntry) -> bool:
    if change.kind is not SourceRecordKind.APT or not isinstance(change.live, AptSource):
        return False
    captured_path = Path(change.live.capture_path)
    expected = change.expected
    if not isinstance(expected, AptSource):
        return False
    return (
        captured_path.parent == Path("/etc/apt/sources.list.d")
        and captured_path.stem == expected.managed_target
    )


def _provision_changes(source_diff: SourceDiffResult) -> tuple[SourceProvisionChange, ...]:
    changes: list[SourceProvisionChange] = []
    for entry in (*source_diff.missing, *source_diff.changed):
        if entry.kind not in {SourceRecordKind.APT, SourceRecordKind.FLATPAK_REMOTE}:
            continue
        changes.append(
            SourceProvisionChange(
                locator=entry.locator,
                status=(
                    SourceProvisionStatus.MISSING
                    if entry.diff_type is SourceDiffType.MISSING
                    else SourceProvisionStatus.CHANGED
                ),
                operation_owned=_is_operation_owned(entry),
            )
        )
    return tuple(changes)


def _print_preview(
    sources: SourcesConfig,
    source_diff: SourceDiffResult,
    managers: tuple[PackageSource, ...],
) -> None:
    print_info("Source phase preview:")
    for entry in (*source_diff.missing, *source_diff.changed, *source_diff.extra):
        print_info(f"  {entry.diff_type.value}: {entry.kind.value} {entry.label}")
    apt_keys = {key.id: key for key in sources.apt.keys}
    for entry in (*source_diff.missing, *source_diff.changed):
        expected = entry.expected
        if isinstance(expected, AptSource):
            for key_id in expected.key_ids:
                key = apt_keys[key_id]
                print_info(f"  key: {key.target_path} fingerprints: {', '.join(key.fingerprints)}")
                print_info(
                    "  command: sudo install -o root -g root -m 0644 "
                    f"<public-key> {key.target_path}"
                )
            suffix = ".list" if expected.format.value == "legacy" else ".sources"
            target = f"/etc/apt/sources.list.d/{expected.managed_target}{suffix}"
            if entry.diff_type is SourceDiffType.CHANGED:
                print_info(f"  command: sudo rm -f {target}")
            print_info(f"  command: sudo install -o root -g root -m 0644 <managed-stanza> {target}")
        elif isinstance(expected, FlatpakRemote):
            prefix = "sudo " if expected.scope.value == "system" else ""
            scope = "--system" if expected.scope.value == "system" else "--user"
            print_info(
                f"  key: flatpak:{expected.scope.value}:{expected.name} fingerprints: "
                f"{', '.join(expected.gpg_fingerprints)}"
            )
            if entry.diff_type is SourceDiffType.CHANGED:
                print_info(
                    f"  command: {prefix}flatpak remote-delete {scope} --force {expected.name}"
                )
            print_info(
                "  command: "
                f"{prefix}flatpak remote-add --if-not-exists {scope} "
                f"--gpg-import=<public-key> {expected.name} {expected.url}"
            )
    if PackageSource.APT in managers:
        print_info("  command: sudo apt-get update --error-on=any")
    if PackageSource.SNAP in managers:
        for channel in sources.snap.packages:
            print_info(
                f"  command: sudo snap install --channel={channel.channel} -- {channel.name}"
            )


def run_source_phase(
    manifest: Manifest,
    source: SourceChoice,
    *,
    dry_run: bool,
    interaction: SourceInteractionPolicy,
) -> SourcePhaseResult:
    sources = manifest.sources
    if sources is None:
        return SourcePhaseResult(success=True)

    source_filter = _source_filter(source)
    managers = selected_managers(sources, source_filter)
    if not managers:
        return SourcePhaseResult(success=True)
    availability = preflight_manager_availability(managers)
    if not all(check.success for check in availability):
        error = "; ".join(
            f"{check.subject}: {check.detail}" for check in availability if not check.success
        )
        print_error(f"Source preflight failed: {error}")
        return SourcePhaseResult(
            success=False,
            selected_managers=managers,
            error=error,
        )

    try:
        target_platform = capture_platform()
        live_sources = capture_sources(managers=managers)
    except SourceCaptureError as error:
        print_error(f"Source preflight failed: {error}")
        return SourcePhaseResult(
            success=False,
            selected_managers=managers,
            error=str(error),
        )

    preflight = preflight_sources(
        sources,
        source_filter=source_filter,
        target_platform=target_platform,
        live_sources=live_sources,
    )
    if not preflight.success:
        error = preflight.error or "source preflight failed"
        print_error(f"Source preflight failed: {error}")
        return SourcePhaseResult(
            success=False,
            selected_managers=managers,
            error=error,
        )

    source_diff = _source_diff(sources, live_sources, source_filter)
    _print_preview(sources, source_diff, managers)
    if dry_run:
        return SourcePhaseResult(
            success=True,
            source_diff=source_diff,
            selected_managers=managers,
        )

    approved, _, error = _confirm_changes(
        _source_changes(source_diff), interaction, action="Replace changed"
    )
    if not approved:
        print_warning(f"Source phase stopped: {error}")
        return SourcePhaseResult(
            success=False,
            source_diff=source_diff,
            selected_managers=managers,
            error=error,
        )

    result = provision_sources(
        sources,
        changes=_provision_changes(source_diff),
        selected_managers=managers,
    )
    if not result.success:
        error = result.error or "source provisioning failed"
        retained = ", ".join(result.retained_artifacts)
        if retained:
            print_warning(f"Source artifacts retained: {retained}")
        print_error(f"Source phase failed: {error}")
        return SourcePhaseResult(
            success=False,
            source_diff=source_diff,
            selected_managers=managers,
            retained_artifacts=result.retained_artifacts,
            error=error,
        )

    return SourcePhaseResult(
        success=True,
        source_diff=source_diff,
        selected_managers=managers,
        retained_artifacts=result.retained_artifacts,
    )


def _replace_apt_record(
    sources: SourcesConfig,
    live_sources: SourcesConfig,
    record: AptSource,
) -> AptSources:
    entries = {entry.managed_target: entry for entry in sources.apt.entries}
    entries[record.managed_target] = record
    keys = {key.id: key for key in sources.apt.keys}
    live_keys = {key.id: key for key in live_sources.apt.keys}
    for key_id in record.key_ids:
        key = live_keys.get(key_id)
        if key is None:
            raise ValueError("live APT source references an uncaptured signing key")
        keys[key_id] = key
    return AptSources(
        entries=tuple(entries.values()),
        keys=tuple(keys.values()),
    )


def _replace_flatpak_remote(sources: SourcesConfig, record: FlatpakRemote) -> FlatpakSources:
    remotes = {(remote.scope, remote.name): remote for remote in sources.flatpak.remotes}
    remotes[(record.scope, record.name)] = record
    return FlatpakSources(remotes=tuple(remotes.values()), apps=sources.flatpak.apps)


def _replace_flatpak_app(sources: SourcesConfig, record: FlatpakApp) -> FlatpakSources:
    apps = {app.locator: app for app in sources.flatpak.apps}
    apps[record.locator] = record
    return FlatpakSources(remotes=sources.flatpak.remotes, apps=tuple(apps.values()))


def _replace_snap_channel(sources: SourcesConfig, record: SnapChannel) -> SnapSources:
    packages = {channel.name: channel for channel in sources.snap.packages}
    packages[record.name] = record
    return SnapSources(packages=tuple(packages.values()))


def _merge_confirmed_sources(
    sources: SourcesConfig,
    live_sources: SourcesConfig,
    confirmed: tuple[SourceDiffEntry, ...],
) -> SourcesConfig:
    merged = sources
    for entry in confirmed:
        live = entry.live
        if isinstance(live, AptSource):
            merged = merged.model_copy(
                update={"apt": _replace_apt_record(merged, live_sources, live)}
            )
        elif isinstance(live, FlatpakRemote):
            merged = merged.model_copy(update={"flatpak": _replace_flatpak_remote(merged, live)})
        elif isinstance(live, FlatpakApp):
            merged = merged.model_copy(update={"flatpak": _replace_flatpak_app(merged, live)})
        elif isinstance(live, SnapChannel):
            merged = merged.model_copy(update={"snap": _replace_snap_channel(merged, live)})
    return merged


def refresh_manifest_sources(
    manifest: Manifest,
    source: SourceChoice,
    *,
    interaction: SourceInteractionPolicy,
) -> SourceRefreshResult:
    sources = manifest.sources
    if sources is None:
        return SourceRefreshResult(success=True, manifest=manifest)

    source_filter = _source_filter(source)
    managers = (source_filter,) if source_filter is not None else None
    try:
        live_sources = capture_sources(managers=managers)
    except SourceCaptureError as error:
        return SourceRefreshResult(success=False, manifest=manifest, error=str(error))

    source_diff = _source_diff(sources, live_sources, source_filter)
    candidates = (*source_diff.extra, *source_diff.changed)
    approved, confirmed, error = _confirm_changes(
        candidates,
        interaction,
        action="Trust and record changed",
        allow_rejection=True,
    )
    if not approved:
        return SourceRefreshResult(
            success=False,
            manifest=manifest,
            source_diff=source_diff,
            error=error,
        )
    if not confirmed:
        return SourceRefreshResult(success=True, manifest=manifest, source_diff=source_diff)

    try:
        merged_sources = _merge_confirmed_sources(sources, live_sources, confirmed)
    except ValueError as error:
        return SourceRefreshResult(
            success=False,
            manifest=manifest,
            source_diff=source_diff,
            error=str(error),
        )
    refreshed = manifest.model_copy(
        update={
            "sources": merged_sources,
            "meta": manifest.meta.model_copy(update={"updated": datetime.now(UTC)}),
        }
    )
    return SourceRefreshResult(
        success=True,
        manifest=refreshed,
        source_diff=source_diff,
        changed=True,
    )
