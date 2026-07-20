from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import typer

from popctl.models.manifest import Manifest
from popctl.models.package import PackageSource, SourceChoice
from popctl.sources.capture import (
    SourceCaptureError,
    capture_platform,
    capture_sources,
    has_managed_apt_stanza_marker,
)
from popctl.sources.diff import (
    SourceDiffEntry,
    SourceDiffError,
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
    ReplayMode,
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
    retained_artifacts: tuple[str, ...] = ()
    error: str | None = None


@dataclass(frozen=True, slots=True)
class SourceRefreshResult:
    success: bool
    manifest: Manifest
    source_diff: SourceDiffResult = SourceDiffResult()
    changed: bool = False
    error: str | None = None


@dataclass(frozen=True, slots=True)
class SourceCaptureTrustResult:
    success: bool
    sources: SourcesConfig | None = None
    error: str | None = None


def _capture_entries(
    sources: SourcesConfig,
    modes: frozenset[ReplayMode],
) -> tuple[SourceDiffEntry, ...]:
    entries: list[SourceDiffEntry] = []
    entries.extend(
        SourceDiffEntry(
            locator=source.managed_target_locator,
            kind=SourceRecordKind.APT,
            diff_type=SourceDiffType.EXTRA,
            live=source,
        )
        for source in sources.apt.entries
        if source.replay_mode in modes
    )
    entries.extend(
        SourceDiffEntry(
            locator=remote.locator,
            kind=SourceRecordKind.FLATPAK_REMOTE,
            diff_type=SourceDiffType.EXTRA,
            live=remote,
        )
        for remote in sources.flatpak.remotes
        if remote.replay_mode in modes
    )
    entries.extend(
        SourceDiffEntry(
            locator=channel.locator,
            kind=SourceRecordKind.SNAP,
            diff_type=SourceDiffType.EXTRA,
            live=channel,
        )
        for channel in sources.snap.packages
        if channel.replay_mode in modes
    )
    return tuple(
        sorted(
            entries,
            key=lambda entry: (
                entry.locator.manager.value,
                entry.kind.value,
                entry.locator.parts,
            ),
        )
    )


def _entry_is_replayable(entry: SourceDiffEntry) -> bool:
    return not isinstance(entry.expected, (AptSource, FlatpakRemote, SnapChannel)) or (
        entry.expected.replay_mode is ReplayMode.REPLAY
    )


def _entry_requires_reconciliation(entry: SourceDiffEntry) -> bool:
    return _entry_is_replayable(entry) and entry.kind is not SourceRecordKind.FLATPAK_APP


def _live_entry_mode(entry: SourceDiffEntry) -> ReplayMode | None:
    if isinstance(entry.live, (AptSource, FlatpakRemote, SnapChannel)):
        return entry.live.replay_mode
    return None


def _apt_identity(source: AptSource) -> str:
    if source.ppa_display is not None:
        return f"ppa:{source.ppa_display}"
    for line in source.verbatim_stanza.splitlines():
        name, separator, value = line.partition(":")
        if separator and name.strip().lower() == "uris" and value.strip():
            return value.strip().split()[0]
    return next(
        (
            value
            for value in source.verbatim_stanza.split()
            if value.startswith(("http://", "https://", "file:"))
        ),
        source.managed_target,
    )


def _print_capture_trust_preview(
    sources: SourcesConfig,
    entries: tuple[SourceDiffEntry, ...],
) -> None:
    apt_keys = {key.id: key for key in sources.apt.keys}
    for entry in entries:
        record = entry.live
        if isinstance(record, AptSource):
            fingerprints = tuple(
                fingerprint
                for key_id in record.key_ids
                for fingerprint in apt_keys[key_id].fingerprints
            )
            print_info(f"Third-party APT source: {_apt_identity(record)}")
            print_info(f"  fingerprints: {', '.join(fingerprints)}")
        elif isinstance(record, FlatpakRemote):
            print_info(
                f"Third-party Flatpak remote: {record.scope.value}:{record.name} ({record.url})"
            )
            print_info(f"  fingerprints: {', '.join(record.gpg_fingerprints)}")
        elif isinstance(record, SnapChannel):
            print_info(f"Third-party Snap channel: {record.name} ({record.channel})")
        elif isinstance(record, FlatpakApp):
            print_info(
                "Flatpak application source: "
                f"{record.scope.value}:{record.id}@{record.branch} ({record.origin})"
            )


def capture_and_trust_sources(
    source: SourceChoice,
    *,
    dry_run: bool,
    interaction: SourceInteractionPolicy,
) -> SourceCaptureTrustResult:
    source_filter = source.to_package_source()
    managers = (source_filter,) if source_filter is not None else None
    try:
        sources = capture_sources(managers=managers)
    except SourceCaptureError as error:
        print_error(f"Source capture failed: {error}")
        return SourceCaptureTrustResult(success=False, error=str(error))

    blocked = _capture_entries(sources, frozenset({ReplayMode.BLOCKED}))
    if blocked:
        error = "blocked source cannot be recorded: " + ", ".join(
            entry.label for entry in blocked
        )
        print_error(f"Source capture failed: {error}")
        return SourceCaptureTrustResult(success=False, error=error)

    trust_entries = _capture_entries(sources, frozenset({ReplayMode.REPLAY}))
    _print_capture_trust_preview(sources, trust_entries)
    if dry_run:
        return SourceCaptureTrustResult(success=True, sources=sources)

    approved, _, error = _confirm_changes(
        trust_entries,
        interaction,
        action="Trust and record",
    )
    if not approved:
        print_warning(f"Source capture stopped: {error}")
        return SourceCaptureTrustResult(success=False, error=error)
    return SourceCaptureTrustResult(success=True, sources=sources)


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
        and has_managed_apt_stanza_marker(change.live)
    )


def _provision_changes(source_diff: SourceDiffResult) -> tuple[SourceProvisionChange, ...]:
    changes: list[SourceProvisionChange] = []
    for entry in (*source_diff.missing, *source_diff.changed):
        if not _entry_requires_reconciliation(entry):
            continue
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
    apt_changes = tuple(
        entry
        for entry in (*source_diff.missing, *source_diff.changed)
        if _entry_is_replayable(entry) and isinstance(entry.expected, AptSource)
    )
    if apt_changes:
        print_info("  command: sudo install -d -o root -g root -m 0755 /etc/apt/keyrings")
        print_info("  command: sudo install -d -o root -g root -m 0755 /etc/apt/sources.list.d")
    for entry in (*source_diff.missing, *source_diff.changed):
        if not _entry_is_replayable(entry):
            continue
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
                if _is_operation_owned(entry):
                    print_info(f"  command: sudo rm -f {target}")
                else:
                    print_info("  conflict: unmanaged APT target will not be replaced")
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

    source_filter = source.to_package_source()
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
            error=error,
        )

    try:
        target_platform = capture_platform()
        live_sources = capture_sources(managers=managers)
    except SourceCaptureError as error:
        print_error(f"Source preflight failed: {error}")
        return SourcePhaseResult(
            success=False,
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
            error=error,
        )

    try:
        source_diff = compute_source_diff(sources, live_sources, source_filter=source_filter)
    except SourceDiffError as error:
        print_error(f"Source preflight failed: {error}")
        return SourcePhaseResult(
            success=False,
            error=str(error),
        )
    _print_preview(sources, source_diff, managers)
    if dry_run:
        return SourcePhaseResult(
            success=True,
            source_diff=source_diff,
        )

    approved, _, error = _confirm_changes(
        tuple(entry for entry in source_diff.changed if _entry_requires_reconciliation(entry)),
        interaction,
        action="Replace changed",
    )
    if not approved:
        print_warning(f"Source phase stopped: {error}")
        return SourcePhaseResult(
            success=False,
            source_diff=source_diff,
            error=error,
        )

    if interaction.interactive and not interaction.yes:
        approved, _, error = _confirm_changes(
            tuple(entry for entry in source_diff.missing if _entry_requires_reconciliation(entry)),
            interaction,
            action="Provision missing",
        )
        if not approved:
            print_warning(f"Source phase stopped: {error}")
            return SourcePhaseResult(
                success=False,
                source_diff=source_diff,
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
            retained_artifacts=result.retained_artifacts,
            error=error,
        )

    return SourcePhaseResult(
        success=True,
        source_diff=source_diff,
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


def _flatpak_remote_key(remote: FlatpakRemote) -> tuple[str, str]:
    return remote.scope.value, remote.name


def _flatpak_app_remote_key(app: FlatpakApp) -> tuple[str, str]:
    return app.scope.value, app.origin


def _trusted_flatpak_remotes(
    sources: SourcesConfig,
    source_diff: SourceDiffResult,
) -> frozenset[tuple[str, str]]:
    changed = {
        _flatpak_remote_key(entry.live)
        for entry in source_diff.changed
        if isinstance(entry.live, FlatpakRemote)
    }
    return frozenset(
        _flatpak_remote_key(remote)
        for remote in sources.flatpak.remotes
        if remote.replay_mode is ReplayMode.REPLAY and _flatpak_remote_key(remote) not in changed
    )


def _flatpak_app_refresh_entries(
    entries: tuple[SourceDiffEntry, ...],
    approved_remotes: frozenset[tuple[str, str]],
) -> tuple[SourceDiffEntry, ...]:
    return tuple(
        entry
        for entry in entries
        if isinstance(entry.live, FlatpakApp)
        and _flatpak_app_remote_key(entry.live) in approved_remotes
    )


def refresh_manifest_sources(
    manifest: Manifest,
    source: SourceChoice,
    *,
    interaction: SourceInteractionPolicy,
) -> SourceRefreshResult:
    sources = manifest.sources
    if sources is None:
        return SourceRefreshResult(success=True, manifest=manifest)

    source_filter = source.to_package_source()
    managers = (source_filter,) if source_filter is not None else None
    try:
        live_sources = capture_sources(managers=managers)
    except SourceCaptureError as error:
        return SourceRefreshResult(success=False, manifest=manifest, error=str(error))

    try:
        source_diff = compute_source_diff(sources, live_sources, source_filter=source_filter)
    except SourceDiffError as error:
        return SourceRefreshResult(success=False, manifest=manifest, error=str(error))
    blocked = _capture_entries(live_sources, frozenset({ReplayMode.BLOCKED}))
    if blocked:
        error = "blocked source cannot be recorded: " + ", ".join(entry.label for entry in blocked)
        return SourceRefreshResult(
            success=False,
            manifest=manifest,
            source_diff=source_diff,
            error=error,
        )
    changed_or_extra = (*source_diff.extra, *source_diff.changed)
    trust_candidates = tuple(
        entry for entry in changed_or_extra if _live_entry_mode(entry) is ReplayMode.REPLAY
    )
    trusted_remotes = _trusted_flatpak_remotes(sources, source_diff)
    approvable_remotes = frozenset(
        _flatpak_remote_key(entry.live)
        for entry in trust_candidates
        if isinstance(entry.live, FlatpakRemote)
    )
    app_candidates = _flatpak_app_refresh_entries(
        changed_or_extra, trusted_remotes | approvable_remotes
    )
    _print_capture_trust_preview(live_sources, (*trust_candidates, *app_candidates))
    approved, confirmed, error = _confirm_changes(
        trust_candidates,
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
    confirmed_remotes = frozenset(
        _flatpak_remote_key(entry.live)
        for entry in confirmed
        if isinstance(entry.live, FlatpakRemote)
    )
    confirmed_apps = _flatpak_app_refresh_entries(
        app_candidates, trusted_remotes | confirmed_remotes
    )
    merged_entries = (*confirmed, *confirmed_apps)
    if not merged_entries:
        return SourceRefreshResult(success=True, manifest=manifest, source_diff=source_diff)

    try:
        merged_sources = _merge_confirmed_sources(sources, live_sources, merged_entries)
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
