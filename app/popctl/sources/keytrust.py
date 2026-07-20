import base64
import hashlib
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from popctl.sources.models import AptKey, SignedByBinding
from popctl.utils.shell import run_command

DEFAULT_APT_KEYRING_ROOTS: tuple[Path, ...] = (
    Path("/etc/apt/keyrings"),
    Path("/etc/apt/trusted.gpg.d"),
    Path("/usr/share/keyrings"),
)

_FINGERPRINT_PATTERN = re.compile(r"^[0-9A-F]{40,64}$")
_ARMOR_PRIVATE_HEADER = b"-----BEGIN PGP PRIVATE KEY BLOCK-----"


class KeyTrustError(RuntimeError): ...


@dataclass(frozen=True, slots=True)
class VerifiedPublicKey:
    armor: str
    fingerprints: tuple[str, ...]


def _packet_has_secret_key(data: bytes) -> bool:
    index = 0
    while index < len(data):
        header = data[index]
        if not header & 0x80:
            index += 1
            continue

        if header & 0x40:
            tag = header & 0x3F
            index += 1
            if index >= len(data):
                break
            length, consumed = _new_packet_length(data, index)
        else:
            tag = (header >> 2) & 0x0F
            length_type = header & 0x03
            index += 1
            length, consumed = _old_packet_length(data, index, length_type)

        if tag in {5, 7}:
            return True
        if length is None:
            break
        index += consumed + length
    return False


def _new_packet_length(data: bytes, index: int) -> tuple[int | None, int]:
    first = data[index]
    if first < 192:
        return first, 1
    if first < 224:
        if index + 1 >= len(data):
            return None, 0
        return ((first - 192) << 8) + data[index + 1] + 192, 2
    if first == 255:
        if index + 4 >= len(data):
            return None, 0
        return int.from_bytes(data[index + 1 : index + 5], "big"), 5
    return None, 0


def _old_packet_length(data: bytes, index: int, length_type: int) -> tuple[int | None, int]:
    sizes = {0: 1, 1: 2, 2: 4}
    size = sizes.get(length_type)
    if size is None or index + size > len(data):
        return None, 0
    return int.from_bytes(data[index : index + size], "big"), size


def _reject_secret_material(data: bytes) -> None:
    if _ARMOR_PRIVATE_HEADER in data or _packet_has_secret_key(data):
        raise KeyTrustError("Secret OpenPGP key material cannot be captured")


def _fingerprints_from_listing(listing: str) -> tuple[str, ...]:
    fingerprints: list[str] = []
    for line in listing.splitlines():
        fields = line.split(":")
        if not fields or fields[0] != "fpr":
            continue
        for field in fields[1:]:
            normalized = field.strip().upper()
            if _FINGERPRINT_PATTERN.fullmatch(normalized):
                fingerprints.append(normalized)
                break
    return tuple(dict.fromkeys(fingerprints))


def _normalize_selectors(selectors: tuple[str, ...]) -> tuple[str, ...]:
    normalized = tuple(selector.rstrip("!").upper() for selector in selectors)
    if any(not _FINGERPRINT_PATTERN.fullmatch(selector) for selector in normalized):
        raise KeyTrustError("Signed-By fingerprint selectors must be full fingerprints")
    if len(set(normalized)) != len(normalized):
        raise KeyTrustError("Signed-By fingerprint selectors must be unique")
    return normalized


def verify_public_material(
    material: str | bytes,
    *,
    selectors: tuple[str, ...] = (),
) -> VerifiedPublicKey:
    data = material.encode() if isinstance(material, str) else material
    _reject_secret_material(data)
    normalized_selectors = _normalize_selectors(selectors)

    with TemporaryDirectory(prefix="popctl-gpg-") as temporary_directory:
        home = Path(temporary_directory) / "home"
        home.mkdir(mode=0o700)
        material_path = Path(temporary_directory) / "material.pgp"
        material_path.write_bytes(data)

        imported = run_command(
            ["gpg", "--batch", "--homedir", str(home), "--import", str(material_path)]
        )
        if not imported.success:
            raise KeyTrustError("Unable to import public OpenPGP key material")

        listed = run_command(
            ["gpg", "--batch", "--homedir", str(home), "--with-colons", "--list-keys"]
        )
        if not listed.success:
            raise KeyTrustError("Unable to inspect imported public OpenPGP key material")
        fingerprints = _fingerprints_from_listing(listed.stdout)
        if not fingerprints:
            raise KeyTrustError("OpenPGP key material has no public fingerprints")

        if normalized_selectors:
            missing = set(normalized_selectors) - set(fingerprints)
            if missing:
                raise KeyTrustError("Signed-By fingerprint selector is absent from key material")
            selected = tuple(
                fingerprint for fingerprint in fingerprints if fingerprint in normalized_selectors
            )
        else:
            selected = fingerprints

        exported = run_command(
            [
                "gpg",
                "--batch",
                "--homedir",
                str(home),
                "--export-options",
                "export-minimal",
                "--armor",
                "--export",
                *selected,
            ]
        )
        if not exported.success or not exported.stdout.strip():
            raise KeyTrustError("Unable to export minimal public OpenPGP key material")

    return VerifiedPublicKey(armor=exported.stdout, fingerprints=selected)


def _path_is_beneath(path: Path, roots: tuple[Path, ...]) -> bool:
    for root in roots:
        try:
            path.relative_to(root.resolve(strict=False))
        except ValueError:
            continue
        return True
    return False


def resolve_key_path(path: str, *, supported_roots: tuple[Path, ...]) -> Path:
    candidate = Path(path)
    try:
        candidate.lstat()
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise KeyTrustError("Signed-By key file cannot be inspected") from error

    if not stat.S_ISREG(resolved.stat().st_mode):
        raise KeyTrustError("Signed-By key target must be a regular file")
    if not _path_is_beneath(resolved, supported_roots):
        raise KeyTrustError("Signed-By key file is outside supported keyring roots")
    return resolved


def _key_identifier(resolved_path: Path, verified: VerifiedPublicKey) -> str:
    stem = re.sub(r"[^a-z0-9]+", "-", resolved_path.stem.lower()).strip("-") or "key"
    digest = hashlib.sha256("\0".join(verified.fingerprints).encode()).hexdigest()[:12]
    return f"{stem}-{digest}"


def capture_apt_keys(
    binding: SignedByBinding,
    *,
    supported_roots: tuple[Path, ...] = DEFAULT_APT_KEYRING_ROOTS,
) -> tuple[SignedByBinding, tuple[AptKey, ...]]:
    if not binding.key_paths and binding.embedded_armor is None:
        raise KeyTrustError("APT source has no Signed-By binding")

    resolved_paths: list[str] = []
    keys: list[AptKey] = []
    selectors = binding.fingerprint_selectors
    material: list[tuple[Path | None, str | bytes, VerifiedPublicKey]] = []
    for source_path in binding.key_paths:
        resolved = resolve_key_path(source_path, supported_roots=supported_roots)
        resolved_paths.append(str(resolved))
        raw_material = resolved.read_bytes()
        material.append((resolved, raw_material, verify_public_material(raw_material)))

    if binding.embedded_armor is not None:
        embedded_armor = binding.embedded_armor
        material.append((None, embedded_armor, verify_public_material(embedded_armor)))

    normalized_selectors = _normalize_selectors(selectors)
    available_fingerprints = {
        fingerprint for _, _, verified in material for fingerprint in verified.fingerprints
    }
    if normalized_selectors and not set(normalized_selectors) <= available_fingerprints:
        raise KeyTrustError("Signed-By fingerprint selector is absent from key material")

    for resolved, raw_material, full_verified in material:
        selected = (
            tuple(
                fingerprint
                for fingerprint in normalized_selectors
                if fingerprint in full_verified.fingerprints
            )
            if normalized_selectors
            else ()
        )
        if normalized_selectors and not selected:
            continue
        verified = (
            verify_public_material(raw_material, selectors=selected)
            if selected
            else full_verified
        )
        if resolved is not None:
            key_id = _key_identifier(resolved, verified)
        else:
            digest = hashlib.sha256("\0".join(verified.fingerprints).encode()).hexdigest()[:12]
            key_id = f"embedded-{digest}"
        keys.append(
            AptKey(
                id=key_id,
                target_path=f"/etc/apt/keyrings/{key_id}.asc",
                armor=verified.armor,
                fingerprints=verified.fingerprints,
            )
        )

    resolved_binding = binding.model_copy(update={"key_paths": tuple(resolved_paths)})
    return resolved_binding, tuple(keys)


def decode_flatpakrepo_key(value: str) -> bytes:
    compact = "".join(value.split())
    try:
        return base64.b64decode(compact, validate=True)
    except ValueError as error:
        raise KeyTrustError("Flatpak repository descriptor has an invalid GPGKey") from error
