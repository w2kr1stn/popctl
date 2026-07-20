from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum

from popctl.models.package import PackageSource
from popctl.sources.capture import (
    resolve_apt_candidate_origins,
    rewrite_apt_signed_by,
    strip_managed_apt_stanza_marker,
)
from popctl.sources.models import (
    AptKey,
    AptSource,
    AptSourceFormat,
    FlatpakApp,
    FlatpakRemote,
    ReplayMode,
    SnapChannel,
    SourceLocator,
    SourcesConfig,
)


class SourceDiffType(StrEnum):
    MISSING = "missing"
    EXTRA = "extra"
    CHANGED = "changed"


class SourceRecordKind(StrEnum):
    APT = "apt"
    FLATPAK_REMOTE = "flatpak-remote"
    FLATPAK_APP = "flatpak-app"
    SNAP = "snap"


class SourceDiffError(RuntimeError): ...


type SourceRecord = AptSource | FlatpakRemote | FlatpakApp | SnapChannel


@dataclass(frozen=True, slots=True)
class SourceDiffEntry:
    locator: SourceLocator
    kind: SourceRecordKind
    diff_type: SourceDiffType
    expected: SourceRecord | None = None
    live: SourceRecord | None = None

    @property
    def label(self) -> str:
        record = self.expected or self.live
        if isinstance(record, AptSource):
            return record.managed_target
        if isinstance(record, FlatpakRemote):
            return record.name
        if isinstance(record, FlatpakApp):
            return f"{record.id}@{record.branch}"
        if isinstance(record, SnapChannel):
            return record.name
        return "/".join(self.locator.parts)

    def to_dict(self) -> dict[str, object]:
        return {
            "type": self.diff_type.value,
            "kind": self.kind.value,
            "label": self.label,
            "locator": {
                "manager": self.locator.manager.value,
                "parts": list(self.locator.parts),
            },
        }


@dataclass(frozen=True, slots=True)
class AptPackageDiagnostic:
    package: str
    locator: SourceLocator | None

    @property
    def provenance(self) -> str:
        return "unknown" if self.locator is None else "unrecorded"

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "package": self.package,
            "provenance": self.provenance,
        }
        if self.locator is not None:
            result["locator"] = {
                "manager": self.locator.manager.value,
                "parts": list(self.locator.parts),
            }
        return result


@dataclass(frozen=True, slots=True)
class SourceDiffResult:
    missing: tuple[SourceDiffEntry, ...] = ()
    extra: tuple[SourceDiffEntry, ...] = ()
    changed: tuple[SourceDiffEntry, ...] = ()
    unrecorded_apt_packages: tuple[AptPackageDiagnostic, ...] = ()

    @property
    def is_in_sync(self) -> bool:
        return not (self.missing or self.extra or self.changed)

    @property
    def total_changes(self) -> int:
        return len(self.missing) + len(self.extra) + len(self.changed)

    def to_dict(self) -> dict[str, object]:
        return {
            "in_sync": self.is_in_sync,
            "summary": {
                "missing": len(self.missing),
                "extra": len(self.extra),
                "changed": len(self.changed),
                "total": self.total_changes,
            },
            "missing": [entry.to_dict() for entry in self.missing],
            "extra": [entry.to_dict() for entry in self.extra],
            "changed": [entry.to_dict() for entry in self.changed],
            "unrecorded_apt_packages": [
                diagnostic.to_dict() for diagnostic in self.unrecorded_apt_packages
            ],
        }


@dataclass(frozen=True, slots=True)
class _AptAttributes:
    format: AptSourceFormat
    stanza: str
    fingerprint_selectors: frozenset[str]
    key_fingerprints: frozenset[str]
    replay_mode: ReplayMode


@dataclass(frozen=True, slots=True)
class _FlatpakRemoteAttributes:
    url: str
    gpg_verify: bool
    gpg_fingerprints: frozenset[str]
    replay_mode: ReplayMode


@dataclass(frozen=True, slots=True)
class _FlatpakAppAttributes:
    origin: str


@dataclass(frozen=True, slots=True)
class _SnapAttributes:
    channel: str
    replay_mode: ReplayMode


type SourceAttributes = (
    _AptAttributes | _FlatpakRemoteAttributes | _FlatpakAppAttributes | _SnapAttributes
)


@dataclass(frozen=True, slots=True)
class _LocatedSource:
    kind: SourceRecordKind
    locator: SourceLocator
    record: SourceRecord
    attributes: SourceAttributes


def _normalize_apt_stanza(source: AptSource) -> str:
    normalized, _ = rewrite_apt_signed_by(source, "<popctl-signed-by>")
    return strip_managed_apt_stanza_marker(source, normalized).rstrip("\n")


def _apt_key_fingerprints(source: AptSource, keys: dict[str, AptKey]) -> frozenset[str]:
    fingerprints: set[str] = set()
    for key_id in source.key_ids:
        key = keys.get(key_id)
        if key is None:
            fingerprints.add(f"missing:{key_id}")
        else:
            fingerprints.update(fingerprint.upper() for fingerprint in key.fingerprints)
    return frozenset(fingerprints)


def _apt_attributes(source: AptSource, keys: dict[str, AptKey]) -> _AptAttributes:
    return _AptAttributes(
        format=source.format,
        stanza=_normalize_apt_stanza(source),
        fingerprint_selectors=frozenset(
            selector.upper() for selector in source.signed_by.fingerprint_selectors
        ),
        key_fingerprints=_apt_key_fingerprints(source, keys),
        replay_mode=source.replay_mode,
    )


def _located_sources(
    sources: SourcesConfig,
    source_filter: PackageSource | None,
) -> tuple[_LocatedSource, ...]:
    records: list[_LocatedSource] = []
    if source_filter in {None, PackageSource.APT}:
        keys = {key.id: key for key in sources.apt.keys}
        records.extend(
            _LocatedSource(
                kind=SourceRecordKind.APT,
                locator=source.managed_target_locator,
                record=source,
                attributes=_apt_attributes(source, keys),
            )
            for source in sources.apt.entries
        )
    if source_filter in {None, PackageSource.FLATPAK}:
        records.extend(
            _LocatedSource(
                kind=SourceRecordKind.FLATPAK_REMOTE,
                locator=remote.locator,
                record=remote,
                attributes=_FlatpakRemoteAttributes(
                    url=remote.url,
                    gpg_verify=remote.gpg_verify,
                    gpg_fingerprints=frozenset(
                        fingerprint.upper() for fingerprint in remote.gpg_fingerprints
                    ),
                    replay_mode=remote.replay_mode,
                ),
            )
            for remote in sources.flatpak.remotes
        )
        records.extend(
            _LocatedSource(
                kind=SourceRecordKind.FLATPAK_APP,
                locator=app.locator,
                record=app,
                attributes=_FlatpakAppAttributes(origin=app.origin),
            )
            for app in sources.flatpak.apps
        )
    if source_filter in {None, PackageSource.SNAP}:
        records.extend(
            _LocatedSource(
                kind=SourceRecordKind.SNAP,
                locator=channel.locator,
                record=channel,
                attributes=_SnapAttributes(
                    channel=channel.channel,
                    replay_mode=channel.replay_mode,
                ),
            )
            for channel in sources.snap.packages
        )
    return tuple(records)


def _by_locator(records: Iterable[_LocatedSource]) -> dict[SourceLocator, _LocatedSource]:
    indexed: dict[SourceLocator, _LocatedSource] = {}
    for record in records:
        if record.locator in indexed:
            raise SourceDiffError(f"Duplicate source locator: {record.locator}")
        indexed[record.locator] = record
    return indexed


def _sort_entries(entries: Iterable[SourceDiffEntry]) -> tuple[SourceDiffEntry, ...]:
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


def _apt_diagnostics(
    live_records: Iterable[_LocatedSource],
    extra: Iterable[SourceDiffEntry],
    package_names: Iterable[str],
) -> tuple[AptPackageDiagnostic, ...]:
    packages = tuple(dict.fromkeys(package_names))
    extra_capture_locators = {
        entry.live.capture_locator
        for entry in extra
        if isinstance(entry.live, AptSource)
    }
    if not packages or not extra_capture_locators:
        return ()

    live_entries = tuple(
        record.record
        for record in live_records
        if record.kind is SourceRecordKind.APT and isinstance(record.record, AptSource)
    )
    resolved = resolve_apt_candidate_origins(packages, live_entries)
    diagnostics: list[AptPackageDiagnostic] = []
    for package in packages:
        provenance = resolved.get(package, "unknown")
        if isinstance(provenance, SourceLocator):
            if provenance in extra_capture_locators:
                diagnostics.append(AptPackageDiagnostic(package=package, locator=provenance))
        else:
            diagnostics.append(AptPackageDiagnostic(package=package, locator=None))
    return tuple(diagnostics)


def compute_source_diff(
    manifest_sources: SourcesConfig,
    live_sources: SourcesConfig,
    *,
    source_filter: PackageSource | None = None,
    apt_package_names: Iterable[str] = (),
) -> SourceDiffResult:
    expected_records = _located_sources(manifest_sources, source_filter)
    live_records = _located_sources(live_sources, source_filter)
    expected_by_locator = _by_locator(expected_records)
    live_by_locator = _by_locator(live_records)

    missing: list[SourceDiffEntry] = []
    extra: list[SourceDiffEntry] = []
    changed: list[SourceDiffEntry] = []
    for locator, expected in expected_by_locator.items():
        live = live_by_locator.get(locator)
        if live is None:
            missing.append(
                SourceDiffEntry(
                    locator=locator,
                    kind=expected.kind,
                    diff_type=SourceDiffType.MISSING,
                    expected=expected.record,
                )
            )
        elif expected.kind is not live.kind or expected.attributes != live.attributes:
            changed.append(
                SourceDiffEntry(
                    locator=locator,
                    kind=expected.kind,
                    diff_type=SourceDiffType.CHANGED,
                    expected=expected.record,
                    live=live.record,
                )
            )

    for locator, live in live_by_locator.items():
        if locator not in expected_by_locator:
            extra.append(
                SourceDiffEntry(
                    locator=locator,
                    kind=live.kind,
                    diff_type=SourceDiffType.EXTRA,
                    live=live.record,
                )
            )

    sorted_extra = _sort_entries(extra)
    return SourceDiffResult(
        missing=_sort_entries(missing),
        extra=sorted_extra,
        changed=_sort_entries(changed),
        unrecorded_apt_packages=_apt_diagnostics(
            live_records,
            sorted_extra,
            apt_package_names if source_filter in {None, PackageSource.APT} else (),
        ),
    )
