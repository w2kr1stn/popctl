import re
from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from tempfile import NamedTemporaryFile

from popctl.models.package import PackageSource
from popctl.sources.keytrust import KeyTrustError, VerifiedPublicKey, verify_public_material
from popctl.sources.models import (
    AptKey,
    AptSource,
    AptSourceFormat,
    FlatpakRemote,
    FlatpakScope,
    ReplayMode,
    SourceLocator,
    SourcesConfig,
)
from popctl.utils.shell import CommandResult, run_command


class SourceProvisionStatus(StrEnum):
    MISSING = "missing"
    CHANGED = "changed"


@dataclass(frozen=True, slots=True)
class SourceProvisionChange:
    locator: SourceLocator
    status: SourceProvisionStatus
    operation_owned: bool = False


@dataclass(frozen=True, slots=True)
class ProvisioningPaths:
    apt_keyrings_dir: Path = Path("/etc/apt/keyrings")
    apt_sources_dir: Path = Path("/etc/apt/sources.list.d")


@dataclass(frozen=True, slots=True)
class SourceProvisionResult:
    success: bool
    retained_artifacts: tuple[str, ...]
    error: str | None = None


class SourceProvisionError(RuntimeError): ...


_INSECURE_APT_PATTERN = re.compile(
    r"(?:trusted\s*(?:=|:)\s*(?:yes|true|1)|allow-insecure\s*(?:=|:)\s*(?:yes|true|1))",
    re.IGNORECASE,
)
_LEGACY_SIGNED_BY_PATTERN = re.compile(
    r"(signed-by\s*=\s*)(?:\"[^\"]*\"|'[^']*'|[^\s\]]+)",
    re.IGNORECASE,
)
_DEB822_SIGNED_BY_PATTERN = re.compile(r"^signed-by:.*(?:\n [^\n]*)*", re.IGNORECASE | re.MULTILINE)


def _command_or_error(args: list[str]) -> CommandResult:
    result = run_command(args, timeout=300.0)
    if not result.success:
        detail = result.stderr.strip() or result.stdout.strip() or "source command failed"
        raise SourceProvisionError(detail)
    return result


def _key_fingerprints(key: AptKey) -> VerifiedPublicKey:
    try:
        verified = verify_public_material(key.armor)
    except KeyTrustError as error:
        raise SourceProvisionError("APT key has no verified public material") from error
    if frozenset(verified.fingerprints) != frozenset(key.fingerprints):
        raise SourceProvisionError("APT key fingerprints do not match the recorded key")
    return verified


def _flatpak_fingerprints(remote: FlatpakRemote) -> VerifiedPublicKey:
    if not remote.gpg_verify:
        raise SourceProvisionError("Flatpak remote disables GPG verification")
    try:
        verified = verify_public_material(remote.gpg_key_armor)
    except KeyTrustError as error:
        raise SourceProvisionError("Flatpak remote has no verified public key material") from error
    if frozenset(verified.fingerprints) != frozenset(remote.gpg_fingerprints):
        raise SourceProvisionError("Flatpak key fingerprints do not match the recorded remote")
    return verified


def _managed_key_path(key: AptKey, paths: ProvisioningPaths) -> Path:
    target = Path(key.target_path)
    if target.parent != paths.apt_keyrings_dir or target.suffix != ".asc":
        raise SourceProvisionError("APT key target is outside the managed keyring directory")
    return target


def _managed_source_paths(source: AptSource, paths: ProvisioningPaths) -> tuple[Path, Path]:
    target_name = source.managed_target
    if (
        Path(target_name).name != target_name
        or not target_name.startswith("popctl-")
        or target_name in {"popctl-", "popctl-."}
    ):
        raise SourceProvisionError("APT source has an invalid managed target")
    return (
        paths.apt_sources_dir / f"{target_name}.list",
        paths.apt_sources_dir / f"{target_name}.sources",
    )


def _source_key_map(source: AptSource, keys: dict[str, AptKey]) -> tuple[AptKey, ...]:
    if not source.key_ids or (
        not source.signed_by.key_paths and source.signed_by.embedded_armor is None
    ):
        raise SourceProvisionError("APT source has no Signed-By binding")
    try:
        return tuple(keys[key_id] for key_id in source.key_ids)
    except KeyError as error:
        raise SourceProvisionError("APT source references an unknown signing key") from error


def _expected_binding_fingerprints(source: AptSource, keys: tuple[AptKey, ...]) -> frozenset[str]:
    selectors = frozenset(
        selector.rstrip("!").upper() for selector in source.signed_by.fingerprint_selectors
    )
    if selectors:
        return selectors
    return frozenset(fingerprint for key in keys for fingerprint in key.fingerprints)


def _verify_binding(
    source: AptSource,
    verified_keys: dict[str, VerifiedPublicKey],
    keys: tuple[AptKey, ...],
) -> None:
    actual = frozenset(
        fingerprint
        for key in keys
        for fingerprint in verified_keys[key.id].fingerprints
    )
    if actual != _expected_binding_fingerprints(source, keys):
        raise SourceProvisionError(
            "APT key fingerprints do not match the recorded Signed-By binding"
        )


def _signed_by_value(source: AptSource, keys: tuple[AptKey, ...]) -> str:
    parts = [key.target_path for key in keys]
    parts.extend(source.signed_by.fingerprint_selectors)
    return ",".join(parts)


def render_managed_apt_stanza(source: AptSource, keys: tuple[AptKey, ...]) -> str:
    if source.replay_mode is not ReplayMode.REPLAY:
        raise SourceProvisionError("Only replayable APT sources can be rendered")
    if _INSECURE_APT_PATTERN.search(source.verbatim_stanza):
        raise SourceProvisionError("Insecure APT sources cannot be replayed")

    signed_by = _signed_by_value(source, keys)
    if source.format is AptSourceFormat.LEGACY:
        rendered, substitutions = _LEGACY_SIGNED_BY_PATTERN.subn(
            rf"\1{signed_by}", source.verbatim_stanza
        )
    else:
        rendered, substitutions = _DEB822_SIGNED_BY_PATTERN.subn(
            f"Signed-By: {signed_by}", source.verbatim_stanza
        )
    if substitutions != 1:
        raise SourceProvisionError("APT source has no unambiguous Signed-By stanza")
    return rendered if rendered.endswith("\n") else f"{rendered}\n"


def _write_temporary_material(material: str, suffix: str) -> Path:
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", suffix=suffix, delete=False
    ) as temporary_file:
        temporary_file.write(material)
        return Path(temporary_file.name)


def _install_apt_key(
    key: AptKey,
    paths: ProvisioningPaths,
    retained_artifacts: list[str],
) -> VerifiedPublicKey:
    _key_fingerprints(key)
    target = _managed_key_path(key, paths)
    temporary_path = _write_temporary_material(key.armor, ".asc")
    retained_artifacts.append(str(target))
    try:
        _command_or_error(
            [
                "sudo",
                "install",
                "-o",
                "root",
                "-g",
                "root",
                "-m",
                "0644",
                str(temporary_path),
                str(target),
            ]
        )
        installed = _command_or_error(["sudo", "cat", str(target)])
    finally:
        temporary_path.unlink(missing_ok=True)
    try:
        verified = verify_public_material(installed.stdout)
    except KeyTrustError as error:
        raise SourceProvisionError("Installed APT key has no verified public material") from error
    if frozenset(verified.fingerprints) != frozenset(key.fingerprints):
        raise SourceProvisionError("Installed APT key fingerprints do not match the recorded key")
    return verified


def _write_managed_apt_stanza(
    source: AptSource,
    keys: tuple[AptKey, ...],
    paths: ProvisioningPaths,
    *,
    replace: bool,
    retained_artifacts: list[str],
) -> None:
    legacy_target, deb822_target = _managed_source_paths(source, paths)
    target = legacy_target if source.format is AptSourceFormat.LEGACY else deb822_target
    if replace:
        for managed_path in (legacy_target, deb822_target):
            retained_artifacts.append(str(managed_path))
            _command_or_error(["sudo", "rm", "-f", str(managed_path)])

    stanza = render_managed_apt_stanza(source, keys)
    temporary_path = _write_temporary_material(stanza, target.suffix)
    retained_artifacts.append(str(target))
    try:
        _command_or_error(
            [
                "sudo",
                "install",
                "-o",
                "root",
                "-g",
                "root",
                "-m",
                "0644",
                str(temporary_path),
                str(target),
            ]
        )
    finally:
        temporary_path.unlink(missing_ok=True)


def _scope_option(scope: FlatpakScope) -> str:
    return "--user" if scope is FlatpakScope.USER else "--system"


def _flatpak_command(scope: FlatpakScope, args: list[str]) -> list[str]:
    return (["sudo"] if scope is FlatpakScope.SYSTEM else []) + ["flatpak", *args]


def _provision_flatpak_remote(
    remote: FlatpakRemote,
    *,
    replace: bool,
    retained_artifacts: list[str],
) -> None:
    _flatpak_fingerprints(remote)
    artifact = f"flatpak:{remote.scope.value}:{remote.name}"
    retained_artifacts.append(artifact)
    scope = _scope_option(remote.scope)
    if replace:
        _command_or_error(
            _flatpak_command(remote.scope, ["remote-delete", scope, "--force", remote.name])
        )

    temporary_path = _write_temporary_material(remote.gpg_key_armor, ".asc")
    try:
        _command_or_error(
            _flatpak_command(
                remote.scope,
                [
                    "remote-add",
                    "--if-not-exists",
                    scope,
                    f"--gpg-import={temporary_path}",
                    remote.name,
                    remote.url,
                ],
            )
        )
    finally:
        temporary_path.unlink(missing_ok=True)


def _change_map(
    changes: Iterable[SourceProvisionChange],
) -> dict[SourceLocator, SourceProvisionChange]:
    mapped: dict[SourceLocator, SourceProvisionChange] = {}
    for change in changes:
        if change.locator in mapped:
            raise SourceProvisionError("Source diff contains duplicate locators")
        mapped[change.locator] = change
    return mapped


def _should_write(change: SourceProvisionChange | None) -> tuple[bool, bool]:
    if change is None:
        return False, False
    if change.status is SourceProvisionStatus.MISSING:
        return True, False
    if change.operation_owned:
        return True, True
    raise SourceProvisionError("Changed source conflicts with an unmanaged target")


def provision_sources(
    sources: SourcesConfig,
    *,
    changes: Iterable[SourceProvisionChange],
    selected_managers: Iterable[PackageSource],
    paths: ProvisioningPaths = ProvisioningPaths(),
) -> SourceProvisionResult:
    retained_artifacts: list[str] = []
    try:
        selected = frozenset(selected_managers)
        change_by_locator = _change_map(changes)
        keys = {key.id: key for key in sources.apt.keys}

        apt_records: list[tuple[AptSource, tuple[AptKey, ...], bool]] = []
        if PackageSource.APT in selected:
            for source in sources.apt.entries:
                if source.replay_mode is not ReplayMode.REPLAY:
                    continue
                write, replace = _should_write(change_by_locator.get(source.managed_target_locator))
                if not write:
                    continue
                source_keys = _source_key_map(source, keys)
                render_managed_apt_stanza(source, source_keys)
                for key in source_keys:
                    _key_fingerprints(key)
                    _managed_key_path(key, paths)
                apt_records.append((source, source_keys, replace))

        flatpak_records: list[tuple[FlatpakRemote, bool]] = []
        if PackageSource.FLATPAK in selected:
            for remote in sources.flatpak.remotes:
                if remote.replay_mode is not ReplayMode.REPLAY:
                    continue
                write, replace = _should_write(change_by_locator.get(remote.locator))
                if not write:
                    continue
                _flatpak_fingerprints(remote)
                flatpak_records.append((remote, replace))

        installed_keys: dict[str, VerifiedPublicKey] = {}
        for source, source_keys, replace in apt_records:
            for key in source_keys:
                if key.id not in installed_keys:
                    installed_keys[key.id] = _install_apt_key(key, paths, retained_artifacts)
            _verify_binding(source, installed_keys, source_keys)
            _write_managed_apt_stanza(
                source,
                source_keys,
                paths,
                replace=replace,
                retained_artifacts=retained_artifacts,
            )

        for remote, replace in flatpak_records:
            _provision_flatpak_remote(
                remote,
                replace=replace,
                retained_artifacts=retained_artifacts,
            )

        if PackageSource.APT in selected:
            _command_or_error(["sudo", "apt-get", "update", "--error-on=any"])
    except SourceProvisionError as error:
        return SourceProvisionResult(
            success=False,
            retained_artifacts=tuple(dict.fromkeys(retained_artifacts)),
            error=str(error),
        )

    return SourceProvisionResult(
        success=True,
        retained_artifacts=tuple(dict.fromkeys(retained_artifacts)),
    )
