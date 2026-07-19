from __future__ import annotations

import os
import re
import shlex
import tempfile
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Final

from popctl.core.paths import get_state_dir
from popctl.dotfiles.materialize import (
    HomePathError,
    canonical_home_relative_path,
    read_home_regular_file,
)
from popctl.dotfiles.secret_filter import SecretVerdict, scan_dotfile_bytes
from popctl.utils.shell import BytesCommandResult, run_command, run_command_bytes

MAIN_BRANCH: Final = "main"
MAIN_REF: Final = f"refs/heads/{MAIN_BRANCH}"
REMOTE_MAIN_REF: Final = f"refs/remotes/origin/{MAIN_BRANCH}"
MARKER_TAG: Final = "popctl-dotfiles-format-v1"
MARKER_REF: Final = f"refs/tags/{MARKER_TAG}"
MAIN_FETCH_REFSPEC: Final = f"+refs/heads/{MAIN_BRANCH}:{REMOTE_MAIN_REF}"
MARKER_FETCH_REFSPEC: Final = f"+{MARKER_REF}:{MARKER_REF}"
MAIN_PUSH_REFSPEC: Final = f"{MAIN_REF}:{MAIN_REF}"
MARKER_PUSH_REFSPEC: Final = f"{MARKER_REF}:{MARKER_REF}"
_ZERO_OID: Final = "0" * 40
_GITHUB_HTTPS_PATTERN: Final = re.compile(
    r"https://github\.com/(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/"
    r"(?P<repo>[A-Za-z0-9][A-Za-z0-9._-]*?)\.git\Z"
)
_GITHUB_SSH_PATTERN: Final = re.compile(
    r"git@github\.com:(?P<owner>[A-Za-z0-9](?:[A-Za-z0-9-]{0,38}))/"
    r"(?P<repo>[A-Za-z0-9][A-Za-z0-9._-]*?)\.git\Z"
)
_AUTH_MARKERS: Final = (
    "authentication failed",
    "authorization failed",
    "permission denied",
    "could not read username",
    "terminal prompts disabled",
    "publickey",
    "http 401",
    "http 403",
)
_OFFLINE_MARKERS: Final = (
    "could not resolve host",
    "temporary failure in name resolution",
    "network is unreachable",
    "no route to host",
    "failed to connect",
    "couldn't connect",
    "connection refused",
)
_TIMEOUT_MARKERS: Final = (
    "connection timed out",
    "operation timed out",
    "timed out",
)
_OWNED_LOCAL_KEYS: Final = frozenset(
    {
        "core.repositoryformatversion",
        "core.filemode",
        "core.bare",
        "core.logallrefupdates",
        "remote.origin.url",
        "remote.origin.fetch",
    }
)


class DotfilesRepoError(Exception):
    pass


class RemoteUrlError(DotfilesRepoError):
    pass


class GitCommandError(DotfilesRepoError):
    pass


class TreeValidationError(DotfilesRepoError):
    pass


class RefRaceError(DotfilesRepoError):
    pass


class TransportOutcome(str, Enum):
    SUCCESS = "success"
    TIMEOUT = "timeout"
    OFFLINE = "offline"
    AUTH = "auth"
    OTHER = "other"


class RefRelation(str, Enum):
    EQUAL = "equal"
    AHEAD = "ahead"
    BEHIND = "behind"
    DIVERGED = "diverged"
    BOOTSTRAP_UNBORN = "bootstrap-unborn"
    BOOTSTRAP_BEHIND = "bootstrap-behind"


class RepoState(str, Enum):
    READY = "ready"
    BOOTSTRAP_UNBORN = "bootstrap-unborn"


class PathState(str, Enum):
    CLEAN = "clean"
    LOCAL_MOD = "local-mod"
    REMOTE_MOD = "remote-mod"
    BOTH_CHANGED = "both-changed"
    MISSING = "missing"
    CONFLICTED = "conflicted"


@dataclass(frozen=True, slots=True)
class GitIdentity:
    name: str | None
    email: str | None

    @property
    def complete(self) -> bool:
        return bool(self.name and self.email)


@dataclass(frozen=True, slots=True)
class TransportResult:
    outcome: TransportOutcome
    stderr: str = ""
    returncode: int = 0

    @property
    def success(self) -> bool:
        return self.outcome is TransportOutcome.SUCCESS


@dataclass(frozen=True, slots=True)
class RemoteRef:
    oid: str
    ref: str


@dataclass(frozen=True, slots=True)
class LsRemoteResult:
    transport: TransportResult
    refs: tuple[RemoteRef, ...] = ()


@dataclass(frozen=True, slots=True)
class TreeEntry:
    mode: str
    path: str
    oid: str


@dataclass(frozen=True, slots=True)
class TreeRead:
    ref: str
    tree_oid: str
    entries: tuple[TreeEntry, ...]


@dataclass(frozen=True, slots=True)
class PathClassification:
    path: str
    state: PathState

    @property
    def public_state(self) -> PathState:
        if self.state is PathState.BOTH_CHANGED:
            return PathState.CONFLICTED
        return self.state


@dataclass(frozen=True, slots=True)
class CommitResult:
    commit_oid: str
    tree_oid: str
    paths: tuple[str, ...]


def validate_remote_url(url: str) -> str:
    if not url or url != url.strip():
        raise RemoteUrlError("Remote URL must be a canonical GitHub URL")
    if _GITHUB_HTTPS_PATTERN.fullmatch(url) is not None:
        return url
    if _GITHUB_SSH_PATTERN.fullmatch(url) is not None:
        return url
    raise RemoteUrlError(
        "Remote URL must be https://github.com/owner/repo.git or git@github.com:owner/repo.git"
    )


class DotfilesRepo:
    def __init__(
        self,
        bare_repo: Path,
        *,
        home: Path | None = None,
        state_dir: Path | None = None,
    ) -> None:
        self.bare_repo = bare_repo
        self.home = home or Path.home()
        self.state_dir = state_dir or get_state_dir() / "dotfiles"
        self._assets_dir = self.state_dir / "git"
        self._assets_dir.mkdir(parents=True, exist_ok=True)
        self._identity = _capture_identity()
        self._credential_helpers = _capture_credential_helpers()
        self._content_config = self._assets_dir / "content.gitconfig"
        self._network_config = self._assets_dir / "network.gitconfig"
        self._ssh_config = self._assets_dir / "ssh_config"
        self._write_owned_assets()

    @property
    def identity(self) -> GitIdentity:
        return self._identity

    @property
    def repository_state(self) -> RepoState:
        return RepoState.READY if self.ref_oid(MAIN_REF) is not None else RepoState.BOOTSTRAP_UNBORN

    def initialize_bare(self) -> None:
        self.bare_repo.parent.mkdir(parents=True, exist_ok=True)
        result = run_command_bytes(
            self._initialization_args(
                ["init", "--bare", f"--initial-branch={MAIN_BRANCH}", str(self.bare_repo)]
            ),
            env=self._content_environment(),
            timeout=60.0,
        )
        self._require_success(result, "initialize bare dotfiles repository")

    def setup_remote(self, url: str) -> str:
        canonical_url = validate_remote_url(url)
        self._set_remote(canonical_url)
        return canonical_url

    def _install_test_remote(self, url: str) -> None:
        if not url.startswith("file://"):
            raise RemoteUrlError("Test remotes must use file:// URLs")
        self._set_remote(url)

    def _fetch_test_remote(self, url: str, *, marker: bool = False) -> TransportResult:
        if not url.startswith("file://"):
            raise RemoteUrlError("Test remotes must use file:// URLs")
        refspec = MARKER_FETCH_REFSPEC if marker else MAIN_FETCH_REFSPEC
        return self._fetch(url, refspec, status=False)

    def _set_remote(self, canonical_url: str) -> None:
        result = self._content_git(["remote", "add", "origin", canonical_url])
        self._require_success(result, "configure dotfiles remote")
        result = self._content_git(["config", "--local", "remote.origin.fetch", MAIN_FETCH_REFSPEC])
        self._require_success(result, "configure dotfiles fetch refspec")

    def fetch(self, url: str, *, status: bool = False) -> TransportResult:
        canonical_url = validate_remote_url(url)
        return self._fetch(canonical_url, MAIN_FETCH_REFSPEC, status=status)

    def fetch_marker(self, url: str) -> TransportResult:
        canonical_url = validate_remote_url(url)
        return self._fetch(canonical_url, MARKER_FETCH_REFSPEC, status=False)

    def push(self, url: str) -> TransportResult:
        canonical_url = validate_remote_url(url)
        if not self.verify_marker():
            raise DotfilesRepoError("Dotfiles format marker is missing before push")
        args = ["push", canonical_url, MAIN_PUSH_REFSPEC, MARKER_PUSH_REFSPEC]
        return _transport_result(self._network_git(args, canonical_url))

    def ls_remote(self, url: str) -> LsRemoteResult:
        canonical_url = validate_remote_url(url)
        result = self._network_git(
            ["ls-remote", "--refs", canonical_url, MAIN_REF, MARKER_REF], canonical_url
        )
        transport = _transport_result(result)
        if not transport.success:
            return LsRemoteResult(transport)
        refs: list[RemoteRef] = []
        for line in result.stdout.splitlines():
            oid, separator, ref = line.partition(b"\t")
            if not separator:
                raise GitCommandError("Malformed ls-remote output")
            try:
                refs.append(RemoteRef(oid.decode("ascii"), ref.decode("ascii")))
            except UnicodeDecodeError as e:
                raise GitCommandError("Non-ASCII ls-remote output") from e
        return LsRemoteResult(transport, tuple(refs))

    def create_marker(self, commit_oid: str | None = None) -> None:
        target = commit_oid or self._require_ref(MAIN_REF)
        result = self._content_git(["tag", MARKER_TAG, target])
        self._require_success(result, "create dotfiles format marker")

    def verify_marker(self) -> bool:
        result = self._content_git(["rev-parse", "--verify", "--quiet", f"{MARKER_REF}^{{commit}}"])
        return result.success

    def marker_commit(self) -> str:
        result = self._content_git(["rev-parse", "--verify", f"{MARKER_REF}^{{commit}}"])
        self._require_success(result, "verify dotfiles format marker")
        return _single_oid(result.stdout, "format marker")

    def ref_oid(self, ref: str) -> str | None:
        result = self._content_git(["rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"])
        if not result.success:
            return None
        return _single_oid(result.stdout, ref)

    def merge_base_relation(
        self,
        *,
        local_ref: str = MAIN_REF,
        remote_ref: str = REMOTE_MAIN_REF,
    ) -> RefRelation:
        local_oid = self.ref_oid(local_ref)
        remote_oid = self.ref_oid(remote_ref)
        if local_oid is None:
            if remote_oid is None:
                return RefRelation.BOOTSTRAP_UNBORN
            return RefRelation.BOOTSTRAP_BEHIND
        if remote_oid is None:
            raise DotfilesRepoError(f"Remote ref is absent: {remote_ref}")
        if local_oid == remote_oid:
            return RefRelation.EQUAL
        if self._is_ancestor(local_oid, remote_oid):
            return RefRelation.BEHIND
        if self._is_ancestor(remote_oid, local_oid):
            return RefRelation.AHEAD
        return RefRelation.DIVERGED

    def changed_paths(self, older_ref: str, newer_ref: str) -> frozenset[str]:
        result = self._content_git(["diff", "--name-only", "-z", older_ref, newer_ref])
        self._require_success(result, "read changed paths")
        return frozenset(_decode_nul_paths(result.stdout, "changed path"))

    def work_tree_changed_paths(
        self,
        tracked_paths: Collection[str],
        *,
        base_ref: str = MAIN_REF,
    ) -> frozenset[str]:
        base_entries = self._entry_map(self.read_tree(base_ref).entries)
        changed: set[str] = set()
        for path in _canonical_paths(tracked_paths):
            entry = base_entries.get(path)
            if entry is None:
                changed.add(path)
                continue
            try:
                snapshot = read_home_regular_file(self.home, path)
            except FileNotFoundError:
                changed.add(path)
                continue
            except HomePathError:
                changed.add(path)
                continue
            if snapshot.content != self.read_blob(entry.oid) or snapshot.mode != entry.mode:
                changed.add(path)
        return frozenset(changed)

    def classify_paths(
        self,
        tracked_paths: Collection[str],
        *,
        base_ref: str = MAIN_REF,
        remote_ref: str = REMOTE_MAIN_REF,
    ) -> tuple[PathClassification, ...]:
        base_entries = self._entry_map(self.read_tree(base_ref).entries)
        remote_entries = self._entry_map(self.read_tree(remote_ref).entries)
        classifications: list[PathClassification] = []
        for path in _canonical_paths(tracked_paths):
            base_entry = base_entries.get(path)
            if base_entry is None:
                raise DotfilesRepoError(f"Tracked path is absent from base ref: {path}")
            try:
                snapshot = read_home_regular_file(self.home, path)
            except FileNotFoundError:
                classifications.append(PathClassification(path, PathState.MISSING))
                continue
            except HomePathError:
                classifications.append(PathClassification(path, PathState.CONFLICTED))
                continue
            local_changed = (
                snapshot.content != self.read_blob(base_entry.oid)
                or snapshot.mode != base_entry.mode
            )
            remote_entry = remote_entries.get(path)
            remote_changed = remote_entry != base_entry
            if local_changed and remote_changed:
                state = PathState.BOTH_CHANGED
            elif local_changed:
                state = PathState.LOCAL_MOD
            elif remote_changed:
                state = PathState.REMOTE_MOD
            else:
                state = PathState.CLEAN
            classifications.append(PathClassification(path, state))
        return tuple(classifications)

    def read_tree(self, ref: str) -> TreeRead:
        tree_oid_result = self._content_git(["rev-parse", "--verify", f"{ref}^{{tree}}"])
        self._require_success(tree_oid_result, f"resolve tree {ref}")
        tree_oid = _single_oid(tree_oid_result.stdout, ref)
        result = self._content_git(["ls-tree", "-r", "-z", "--full-tree", tree_oid])
        self._require_success(result, f"read tree {ref}")
        entries: list[TreeEntry] = []
        for record in _split_nul(result.stdout, "ls-tree"):
            metadata, separator, raw_path = record.partition(b"\t")
            parts = metadata.split(b" ")
            if not separator or len(parts) != 3:
                raise GitCommandError("Malformed ls-tree output")
            raw_mode, _kind, raw_oid = parts
            try:
                entries.append(
                    TreeEntry(
                        mode=raw_mode.decode("ascii"),
                        path=raw_path.decode("utf-8"),
                        oid=raw_oid.decode("ascii"),
                    )
                )
            except UnicodeDecodeError as e:
                raise TreeValidationError("Tree contains a non-UTF-8 path") from e
        return TreeRead(ref, tree_oid, tuple(entries))

    def read_blob(self, oid: str) -> bytes:
        if re.fullmatch(r"[0-9a-f]{40,64}", oid) is None:
            raise GitCommandError(f"Invalid blob OID: {oid}")
        result = self._content_git(["cat-file", "blob", oid])
        self._require_success(result, f"read blob {oid}")
        return result.stdout

    def validate_tree(
        self,
        ref: str,
        *,
        tracked_paths: Collection[str] = (),
        ambiguous_content_allowlist: Collection[str] = (),
    ) -> TreeRead:
        tree = self.read_tree(ref)
        paths: set[str] = set()
        for entry in tree.entries:
            if entry.mode not in {"100644", "100755"}:
                raise TreeValidationError(f"Unsupported tree mode for {entry.path}: {entry.mode}")
            try:
                path = canonical_home_relative_path(entry.path)
            except HomePathError as e:
                raise TreeValidationError(str(e)) from e
            if path != entry.path or not self._destination_is_under_home(path):
                raise TreeValidationError(f"Unsafe tree destination: {entry.path}")
            if path in paths:
                raise TreeValidationError(f"Duplicate tree path: {path}")
            paths.add(path)
            verdict = scan_dotfile_bytes(
                path,
                self.read_blob(entry.oid),
                ambiguous_content_allowlist=ambiguous_content_allowlist,
            )
            if not verdict.allowed:
                raise TreeValidationError(_secret_failure(path, verdict))
        missing = set(_canonical_paths(tracked_paths)) - paths
        if missing:
            raise TreeValidationError(
                "Source tree drops tracked path(s): " + ", ".join(sorted(missing))
            )
        return tree

    def checked_commit(
        self,
        paths: Collection[str],
        message: str,
        *,
        base_ref: str = MAIN_REF,
        expected_base_oid: str | None = None,
        ambiguous_content_allowlist: Collection[str] = (),
    ) -> CommitResult:
        if not message.strip():
            raise DotfilesRepoError("Dotfiles commit message must not be empty")
        canonical_paths = _canonical_paths(paths)
        if not canonical_paths:
            raise DotfilesRepoError("Checked commits require at least one path")
        base_oid = self.ref_oid(base_ref)
        if expected_base_oid is not None and base_oid != expected_base_oid:
            raise RefRaceError(f"Base ref changed before checked commit: {base_ref}")
        if not self._identity.complete:
            raise DotfilesRepoError("Git identity is required; configure user.name and user.email")
        with tempfile.TemporaryDirectory(
            prefix="popctl-dotfiles-index-", dir=self._assets_dir
        ) as directory:
            index_path = Path(directory) / "index"
            index_env = {"GIT_INDEX_FILE": str(index_path)}
            if base_oid is None:
                result = self._content_git(["read-tree", "--empty"], env_extra=index_env)
            else:
                result = self._content_git(["read-tree", base_oid], env_extra=index_env)
            self._require_success(result, "initialize private index")
            for path in canonical_paths:
                try:
                    snapshot = read_home_regular_file(self.home, path)
                except FileNotFoundError as e:
                    raise DotfilesRepoError(f"Tracked source is missing: {path}") from e
                except HomePathError as e:
                    raise DotfilesRepoError(str(e)) from e
                verdict = scan_dotfile_bytes(
                    path,
                    snapshot.content,
                    ambiguous_content_allowlist=ambiguous_content_allowlist,
                )
                if not verdict.allowed:
                    raise TreeValidationError(_secret_failure(path, verdict))
                blob_oid = self._hash_snapshot(snapshot.content)
                cacheinfo = f"{snapshot.mode},{blob_oid},{path}"
                result = self._content_git(
                    ["update-index", "--add", "--cacheinfo", cacheinfo], env_extra=index_env
                )
                self._require_success(result, f"stage immutable snapshot {path}")
            tree_result = self._content_git(["write-tree"], env_extra=index_env)
            self._require_success(tree_result, "write checked tree")
            tree_oid = _single_oid(tree_result.stdout, "checked tree")
            self.validate_tree(tree_oid, ambiguous_content_allowlist=ambiguous_content_allowlist)
            commit_args = ["commit-tree", tree_oid]
            if base_oid is not None:
                commit_args.extend(["-p", base_oid])
            commit_args.extend(["-m", message])
            commit_result = self._content_git(commit_args)
            self._require_success(commit_result, "create checked commit")
            commit_oid = _single_oid(commit_result.stdout, "checked commit")
        if not self.conditional_advance_ref(base_ref, commit_oid, base_oid):
            raise RefRaceError(f"Base ref changed while committing: {base_ref}")
        return CommitResult(commit_oid, tree_oid, canonical_paths)

    def conditional_advance_ref(self, ref: str, new_oid: str, expected_oid: str | None) -> bool:
        expected = expected_oid or _ZERO_OID
        result = self._content_git(["update-ref", ref, new_oid, expected])
        if result.success:
            return True
        stderr = result.stderr.decode("utf-8", errors="replace").lower()
        if "cannot lock ref" in stderr or "reference already exists" in stderr:
            return False
        self._require_success(result, f"advance ref {ref}")
        return False

    def _fetch(self, canonical_url: str, refspec: str, *, status: bool) -> TransportResult:
        args = ["fetch", "--no-write-fetch-head", canonical_url, refspec]
        result = self._network_git(args, canonical_url)
        return _transport_result(result)

    def _network_git(self, args: list[str], canonical_url: str) -> BytesCommandResult:
        self._validate_owned_local_config(canonical_url)
        return run_command_bytes(
            self._git_args(args),
            env=self._network_environment(),
            timeout=30.0,
        )

    def _content_git(
        self,
        args: list[str],
        *,
        input_data: bytes | None = None,
        env_extra: dict[str, str] | None = None,
    ) -> BytesCommandResult:
        environment = self._content_environment()
        if env_extra is not None:
            environment.update(env_extra)
        return run_command_bytes(
            self._git_args(args),
            input=input_data,
            env=environment,
            timeout=60.0,
        )

    def _git_args(self, args: list[str]) -> list[str]:
        return [
            "git",
            "-c",
            "core.autocrlf=false",
            "-c",
            "core.fileMode=true",
            "-c",
            "core.hooksPath=/dev/null",
            f"--git-dir={self.bare_repo}",
            *self._identity_args(args),
            *args,
        ]

    @staticmethod
    def _initialization_args(args: list[str]) -> list[str]:
        return [
            "git",
            "-c",
            "core.autocrlf=false",
            "-c",
            "core.fileMode=true",
            "-c",
            "core.hooksPath=/dev/null",
            *args,
        ]

    def _identity_args(self, args: list[str]) -> list[str]:
        if not args or args[0] != "commit-tree" or not self._identity.complete:
            return []
        return [
            "-c",
            f"user.name={self._identity.name}",
            "-c",
            f"user.email={self._identity.email}",
        ]

    def _content_environment(self) -> dict[str, str]:
        return {
            "GIT_CONFIG_GLOBAL": str(self._content_config),
            "GIT_CONFIG_NOSYSTEM": "1",
        }

    def _network_environment(self) -> dict[str, str]:
        environment: dict[str, str] = {}
        for key, value in os.environ.items():
            if (
                key in {"PATH", "HOME", "LANG", "LANGUAGE", "SSH_AUTH_SOCK"}
                or key.startswith("LC_")
            ):
                environment[key] = value
        environment["HOME"] = str(self.home)
        environment["GIT_CONFIG_GLOBAL"] = str(self._network_config)
        environment["GIT_CONFIG_NOSYSTEM"] = "1"
        environment["GIT_TERMINAL_PROMPT"] = "0"
        environment["GIT_SSH_COMMAND"] = (
            f"ssh -F {shlex.quote(str(self._ssh_config))} -o BatchMode=yes"
        )
        return environment

    def _write_owned_assets(self) -> None:
        self._content_config.write_text("[core]\n\thooksPath = /dev/null\n", encoding="utf-8")
        network_lines = ["[core]", "\thooksPath = /dev/null"]
        for helper in self._credential_helpers:
            network_lines.extend(["[credential]", f"\thelper = {_quote_config_value(helper)}"])
        self._network_config.write_text("\n".join(network_lines) + "\n", encoding="utf-8")
        self._ssh_config.write_text(
            "Host *\n\tProxyCommand none\n\tProxyJump none\n\tPermitLocalCommand no\n",
            encoding="utf-8",
        )
        for path in (self._content_config, self._network_config, self._ssh_config):
            path.chmod(0o600)

    def _validate_owned_local_config(self, canonical_url: str) -> None:
        result = self._content_git(["config", "--local", "--null", "--list"])
        self._require_success(result, "validate dotfiles local config")
        values: dict[str, list[str]] = {}
        for record in _split_nul(result.stdout, "local git config"):
            raw_key, separator, raw_value = record.partition(b"\n")
            if not separator:
                raise DotfilesRepoError("Malformed local Git configuration")
            try:
                key = raw_key.decode("utf-8")
                value = raw_value.decode("utf-8")
            except UnicodeDecodeError as e:
                raise DotfilesRepoError("Non-UTF-8 local Git configuration") from e
            if key not in _OWNED_LOCAL_KEYS:
                raise DotfilesRepoError(f"Unexpected dotfiles local Git config key: {key}")
            values.setdefault(key, []).append(value)
        required_values = {
            "core.repositoryformatversion": ["0"],
            "core.filemode": ["true"],
            "core.bare": ["true"],
            "remote.origin.url": [canonical_url],
            "remote.origin.fetch": [MAIN_FETCH_REFSPEC],
        }
        if values.get("core.logallrefupdates", ["true"]) != ["true"]:
            raise DotfilesRepoError("Unexpected dotfiles core.logallrefupdates value")
        if any(values.get(key) != expected for key, expected in required_values.items()):
            raise DotfilesRepoError(
                "Dotfiles local Git configuration does not match its owned layout"
            )

    def _hash_snapshot(self, content: bytes) -> str:
        result = self._content_git(
            ["hash-object", "--no-filters", "-w", "--stdin"], input_data=content
        )
        self._require_success(result, "hash immutable snapshot")
        return _single_oid(result.stdout, "immutable snapshot")

    def _is_ancestor(self, ancestor: str, descendant: str) -> bool:
        result = self._content_git(["merge-base", "--is-ancestor", ancestor, descendant])
        if result.returncode in {0, 1}:
            return result.returncode == 0
        self._require_success(result, "compare refs")
        return False

    def _require_ref(self, ref: str) -> str:
        oid = self.ref_oid(ref)
        if oid is None:
            raise DotfilesRepoError(f"Required ref is absent: {ref}")
        return oid

    def _destination_is_under_home(self, path: str) -> bool:
        try:
            (self.home / path).relative_to(self.home)
        except ValueError:
            return False
        return True

    @staticmethod
    def _entry_map(entries: Iterable[TreeEntry]) -> dict[str, TreeEntry]:
        return {entry.path: entry for entry in entries}

    @staticmethod
    def _require_success(result: BytesCommandResult, action: str) -> None:
        if result.success:
            return
        stderr = result.stderr.decode("utf-8", errors="replace").strip()
        raise GitCommandError(f"Failed to {action}: {stderr or f'git exited {result.returncode}'}")


def _capture_identity() -> GitIdentity:
    name = run_command(["git", "config", "--get", "user.name"]).stdout.strip()
    email = run_command(["git", "config", "--get", "user.email"]).stdout.strip()
    return GitIdentity(name or None, email or None)


def _capture_credential_helpers() -> tuple[str, ...]:
    result = run_command_bytes(
        ["git", "config", "--global", "--null", "--get-all", "credential.helper"]
    )
    if result.returncode == 1:
        return ()
    if not result.success:
        raise DotfilesRepoError("Cannot read Git credential.helper configuration")
    values: list[str] = []
    for raw_value in _split_nul(result.stdout, "credential.helper"):
        try:
            values.append(raw_value.decode("utf-8"))
        except UnicodeDecodeError as e:
            raise DotfilesRepoError("Non-UTF-8 Git credential.helper configuration") from e
    return tuple(values)


def _transport_result(result: BytesCommandResult) -> TransportResult:
    stderr = result.stderr.decode("utf-8", errors="replace")
    if result.success:
        return TransportResult(TransportOutcome.SUCCESS, stderr, result.returncode)
    message = f"{stderr}\n{result.stdout.decode('utf-8', errors='replace')}".lower()
    if any(marker in message for marker in _AUTH_MARKERS):
        outcome = TransportOutcome.AUTH
    elif result.returncode == -1 or any(marker in message for marker in _TIMEOUT_MARKERS):
        outcome = TransportOutcome.TIMEOUT
    elif any(marker in message for marker in _OFFLINE_MARKERS):
        outcome = TransportOutcome.OFFLINE
    else:
        outcome = TransportOutcome.OTHER
    return TransportResult(outcome, stderr, result.returncode)


def _canonical_paths(paths: Collection[str]) -> tuple[str, ...]:
    canonical_paths: list[str] = []
    for path in paths:
        try:
            canonical_paths.append(canonical_home_relative_path(path))
        except HomePathError as e:
            raise DotfilesRepoError(str(e)) from e
    if len(canonical_paths) != len(set(canonical_paths)):
        raise DotfilesRepoError("Dotfiles paths must be unique")
    return tuple(sorted(canonical_paths))


def _split_nul(value: bytes, subject: str) -> tuple[bytes, ...]:
    if not value:
        return ()
    if not value.endswith(b"\0"):
        raise GitCommandError(f"Malformed NUL-delimited {subject} output")
    return tuple(part for part in value[:-1].split(b"\0") if part)


def _decode_nul_paths(value: bytes, subject: str) -> tuple[str, ...]:
    paths: list[str] = []
    for raw_path in _split_nul(value, subject):
        try:
            paths.append(raw_path.decode("utf-8"))
        except UnicodeDecodeError as e:
            raise GitCommandError(f"Non-UTF-8 {subject}") from e
    return tuple(paths)


def _single_oid(value: bytes, subject: str) -> str:
    raw_oid = value.strip()
    try:
        oid = raw_oid.decode("ascii")
    except UnicodeDecodeError as e:
        raise GitCommandError(f"Non-ASCII OID for {subject}") from e
    if re.fullmatch(r"[0-9a-f]{40,64}", oid) is None:
        raise GitCommandError(f"Invalid OID for {subject}")
    return oid


def _secret_failure(path: str, verdict: SecretVerdict) -> str:
    category = f" ({verdict.category})" if verdict.category else ""
    return f"Tree content is blocked for {path}{category}"


def _quote_config_value(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'
