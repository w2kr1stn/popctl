from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from popctl.dotfiles.materialize import HomePathError, read_home_regular_file
from popctl.dotfiles.repo import (
    MAIN_BRANCH,
    MAIN_FETCH_REFSPEC,
    MAIN_REF,
    MARKER_REF,
    REMOTE_MAIN_REF,
    DotfilesRepo,
    DotfilesRepoError,
    PathState,
    RefRaceError,
    RefRelation,
    RemoteUrlError,
    TransportOutcome,
    TreeValidationError,
    validate_remote_url,
)
from popctl.utils.shell import BytesCommandResult, run_command_bytes

from .conftest import RealGitEnvironment


def _repository(
    real_git: RealGitEnvironment, tmp_path: Path, name: str = "dotfiles"
) -> DotfilesRepo:
    repository = DotfilesRepo(
        tmp_path / f"{name}.git",
        home=real_git.home,
        state_dir=real_git.state_home / "popctl" / name,
    )
    repository.initialize_bare()
    return repository


def _write(home: Path, path: str, content: bytes, *, executable: bool = False) -> None:
    target = home / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    target.chmod(0o755 if executable else 0o644)


def _commit(repository: DotfilesRepo, paths: list[str], message: str) -> str:
    return repository.checked_commit(paths, message).commit_oid


def _set_remote_ref(repository: DotfilesRepo, oid: str, expected: str | None) -> None:
    assert repository.conditional_advance_ref(REMOTE_MAIN_REF, oid, expected)


def _raw_git(
    repository: DotfilesRepo,
    *args: str,
    input_data: bytes | None = None,
) -> BytesCommandResult:
    result = run_command_bytes(
        ["git", f"--git-dir={repository.bare_repo}", *args], input=input_data
    )
    assert result.success, result.stderr.decode("utf-8", errors="replace")
    return result


def _literal_tree(repository: DotfilesRepo, mode: str, path: bytes, blob: bytes) -> str:
    blob_oid = _raw_git(repository, "hash-object", "-w", "--stdin", input_data=blob).stdout.strip()
    raw_tree = mode.encode("ascii") + b" " + path + b"\0" + bytes.fromhex(blob_oid.decode("ascii"))
    return _raw_git(
        repository,
        "hash-object",
        "--literally",
        "-t",
        "tree",
        "-w",
        "--stdin",
        input_data=raw_tree,
    ).stdout.strip().decode("ascii")


@pytest.mark.real_git
def test_initializes_a_bare_main_repository(real_git: RealGitEnvironment, tmp_path: Path) -> None:
    repository = _repository(real_git, tmp_path)

    assert repository.bare_repo.is_dir()
    assert repository.identity.name == "Dotfiles Test"
    assert repository.identity.email == "dotfiles-test@example.invalid"
    assert repository.ref_oid(MAIN_REF) is None
    config = (repository.bare_repo / "config").read_text(encoding="utf-8")
    assert "bare = true" in config
    assert MAIN_BRANCH == "main"


@pytest.mark.real_git
def test_owned_git_assets_are_not_rewritten_when_unchanged(
    real_git: RealGitEnvironment, tmp_path: Path
) -> None:
    repository = _repository(real_git, tmp_path)
    assets = {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in repository._assets_dir.iterdir()
    }

    repository._write_owned_assets()

    assert {
        path.name: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in repository._assets_dir.iterdir()
    } == assets


@pytest.mark.real_git
def test_checked_gateway_uses_immutable_snapshots_and_never_deletes_missing_paths(
    real_git: RealGitEnvironment,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(real_git, tmp_path)
    _write(real_git.home, ".config/tool/one", b"one\r\n")
    _write(real_git.home, ".config/tool/two", b"two\n")
    first = _commit(repository, [".config/tool/one", ".config/tool/two"], "first")

    _write(real_git.home, ".config/tool/one", b"snapshot\r\n")
    original_hash = repository._hash_snapshot

    def hash_then_modify(content: bytes) -> str:
        result = original_hash(content)
        _write(real_git.home, ".config/tool/one", b"live-after-snapshot\n")
        return result

    monkeypatch.setattr(repository, "_hash_snapshot", hash_then_modify)
    second = _commit(repository, [".config/tool/one"], "second")
    tree = repository.read_tree(second)
    blobs = {entry.path: repository.read_blob(entry.oid) for entry in tree.entries}

    assert first != second
    assert blobs == {
        ".config/tool/one": b"snapshot\r\n",
        ".config/tool/two": b"two\n",
    }
    assert not hasattr(repository, "ff_merge")
    assert not hasattr(repository, "checkout")
    assert not hasattr(repository, "stage")


@pytest.mark.real_git
def test_fd_safe_source_helper_rejects_symlink_swaps_and_preserves_file_mode(
    real_git: RealGitEnvironment,
) -> None:
    _write(real_git.home, ".config/tool/safe", b"safe\n", executable=True)

    snapshot = read_home_regular_file(real_git.home, ".config/tool/safe")

    assert snapshot.content == b"safe\n"
    assert snapshot.mode == "100755"
    (real_git.home / ".config/tool/safe").unlink()
    (real_git.home / ".config/tool/safe").symlink_to("target")
    with pytest.raises(HomePathError, match="Cannot open source"):
        read_home_regular_file(real_git.home, ".config/tool/safe")

    (real_git.home / ".config").rename(real_git.home / ".config-real")
    (real_git.home / ".config").symlink_to(".config-real")
    with pytest.raises(HomePathError, match="Unsafe parent"):
        read_home_regular_file(real_git.home, ".config/tool/safe")


@pytest.mark.real_git
def test_checked_gateway_rejects_missing_source_and_conditional_ref_races(
    real_git: RealGitEnvironment,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(real_git, tmp_path)
    _write(real_git.home, ".config/tool/config", b"one\n")
    first = _commit(repository, [".config/tool/config"], "first")
    (real_git.home / ".config/tool/config").unlink()

    with pytest.raises(DotfilesRepoError, match="missing"):
        repository.checked_commit([".config/tool/config"], "missing")

    _write(real_git.home, ".config/tool/config", b"two\n")
    monkeypatch.setattr(repository, "conditional_advance_ref", lambda *_args: False)

    with pytest.raises(RefRaceError, match="changed while committing"):
        repository.checked_commit(
            [".config/tool/config"],
            "raced",
            expected_base_oid=first,
        )

    assert repository.ref_oid(MAIN_REF) == first


@pytest.mark.real_git
@pytest.mark.parametrize(
    ("content", "category"),
    [
        (b"-----BEGIN OPENSSH PRIVATE KEY-----\n", "private-key"),
        (b"AGE-SECRET-KEY-1ABCDEFG\n", "age-secret-key"),
        (b"Authorization: Bearer opaque-value\n", "authorization"),
    ],
)
def test_checked_gateway_and_inbound_tree_reject_hard_secret_content(
    real_git: RealGitEnvironment,
    tmp_path: Path,
    content: bytes,
    category: str,
) -> None:
    repository = _repository(real_git, tmp_path)
    _write(real_git.home, ".config/tool/config", content)

    with pytest.raises(TreeValidationError, match=category):
        repository.checked_commit([".config/tool/config"], "blocked")

    assert repository.ref_oid(MAIN_REF) is None
    tree_oid = _literal_tree(repository, "100644", b".config/tool/config", content)
    with pytest.raises(TreeValidationError, match=category):
        repository.validate_tree(tree_oid)


@pytest.mark.real_git
@pytest.mark.parametrize(
    ("content", "category"),
    [
        (b"curl -ualice:password https://example.invalid\n", "curl-user-password"),
        (b"curl -u'alice:password' https://example.invalid\n", "curl-user-password"),
        (b'curl --user alice:"password" https://example.invalid\n', "curl-user-password"),
        (b"curl --proxy-user alice:password https://example.invalid\n", "curl-user-password"),
        (
            b" ".join(
                base64.b64encode(b"Authorization: Bearer opaque-value")[offset : offset + 2]
                for offset in range(
                    0, len(base64.b64encode(b"Authorization: Bearer opaque-value")), 2
                )
            ),
            "authorization",
        ),
        (
            b" ".join(
                base64.b64encode(b"AGE-SECRET-KEY-1ABCDEFG")[offset : offset + 2]
                for offset in range(
                    0, len(base64.b64encode(b"AGE-SECRET-KEY-1ABCDEFG")), 2
                )
            ),
            "age-secret-key",
        ),
        (
            b"encoded:"
            + base64.urlsafe_b64encode(b"\xff\xffAuthorization: Bearer opaque-value"),
            "authorization",
        ),
        (
            b"encoded:" + base64.urlsafe_b64encode(b"\xff\xffAGE-SECRET-KEY-1ABCDEFG"),
            "age-secret-key",
        ),
    ],
)
def test_checked_gateway_rejects_structural_curl_and_base64_secret_variants(
    real_git: RealGitEnvironment,
    tmp_path: Path,
    content: bytes,
    category: str,
) -> None:
    repository = _repository(real_git, tmp_path)
    _write(real_git.home, ".config/tool/config", content)

    with pytest.raises(TreeValidationError, match=category):
        repository.checked_commit([".config/tool/config"], "blocked")

    assert repository.ref_oid(MAIN_REF) is None


@pytest.mark.real_git
def test_merge_base_relations_cover_equal_ahead_behind_and_diverged(
    real_git: RealGitEnvironment,
    tmp_path: Path,
) -> None:
    repository = _repository(real_git, tmp_path)
    _write(real_git.home, ".config/tool/config", b"one\n")
    first = _commit(repository, [".config/tool/config"], "first")
    _set_remote_ref(repository, first, None)
    assert repository.merge_base_relation() is RefRelation.EQUAL

    _write(real_git.home, ".config/tool/config", b"two\n")
    second = _commit(repository, [".config/tool/config"], "second")
    assert repository.merge_base_relation() is RefRelation.AHEAD
    _set_remote_ref(repository, second, first)
    assert repository.merge_base_relation() is RefRelation.EQUAL

    assert repository.conditional_advance_ref(MAIN_REF, first, second)
    assert repository.merge_base_relation() is RefRelation.BEHIND

    _write(real_git.home, ".config/tool/config", b"three\n")
    third = _commit(repository, [".config/tool/config"], "third")
    assert third != second
    assert repository.merge_base_relation() is RefRelation.DIVERGED


@pytest.mark.real_git
def test_bootstrap_state_is_distinct_from_a_missing_work_tree_file(
    real_git: RealGitEnvironment,
    tmp_path: Path,
) -> None:
    repository = _repository(real_git, tmp_path)
    assert repository.merge_base_relation() is RefRelation.BOOTSTRAP_UNBORN

    _write(real_git.home, ".config/tool/config", b"one\n")
    commit = _commit(repository, [".config/tool/config"], "first")
    _set_remote_ref(repository, commit, None)
    (real_git.home / ".config/tool/config").unlink()

    classification = repository.classify_paths([".config/tool/config"])

    assert classification[0].state is PathState.MISSING


@pytest.mark.real_git
def test_path_classification_covers_all_states_and_public_conflict_mapping(
    real_git: RealGitEnvironment,
    tmp_path: Path,
) -> None:
    repository = _repository(real_git, tmp_path)
    paths = [
        ".config/tool/clean",
        ".config/tool/local",
        ".config/tool/remote",
        ".config/tool/same",
        ".config/tool/missing",
        ".config/tool/conflicted",
    ]
    for path in paths:
        _write(real_git.home, path, b"base\n")
    base = _commit(repository, paths, "base")
    _set_remote_ref(repository, base, None)
    _write(real_git.home, ".config/tool/local", b"local\n")
    _write(real_git.home, ".config/tool/same", b"local\n")
    (real_git.home / ".config/tool/missing").unlink()
    (real_git.home / ".config/tool/conflicted").unlink()
    (real_git.home / ".config/tool/conflicted").symlink_to("clean")
    _write(real_git.home, ".config/tool/remote", b"remote\n")
    _write(real_git.home, ".config/tool/same", b"remote\n")
    remote = _commit(repository, [".config/tool/remote", ".config/tool/same"], "remote")
    _set_remote_ref(repository, remote, base)
    assert repository.conditional_advance_ref(MAIN_REF, base, remote)
    _write(real_git.home, ".config/tool/remote", b"base\n")
    _write(real_git.home, ".config/tool/same", b"local\n")

    states = {item.path: item for item in repository.classify_paths(paths)}

    assert states[".config/tool/clean"].state is PathState.CLEAN
    assert states[".config/tool/local"].state is PathState.LOCAL_MOD
    assert states[".config/tool/remote"].state is PathState.REMOTE_MOD
    assert states[".config/tool/same"].state is PathState.BOTH_CHANGED
    assert states[".config/tool/same"].public_state is PathState.CONFLICTED
    assert states[".config/tool/missing"].state is PathState.MISSING
    assert states[".config/tool/conflicted"].state is PathState.CONFLICTED


@pytest.mark.real_git
def test_marker_pushes_with_main_and_fresh_bootstrap_fetches_explicit_refspec(
    real_git: RealGitEnvironment,
    tmp_path: Path,
) -> None:
    remote_store = _repository(real_git, tmp_path, "remote")
    source = _repository(real_git, tmp_path, "source")
    _write(real_git.home, ".config/tool/config", b"one\n", executable=True)
    source_commit = _commit(source, [".config/tool/config"], "first")
    source.create_marker(source_commit)
    remote_url = f"file://{remote_store.bare_repo}"
    source._install_test_remote(remote_url)
    pushed = source._network_git(
        ["push", remote_url, MAIN_REF + ":" + MAIN_REF, MARKER_REF + ":" + MARKER_REF],
        remote_url,
    )
    assert pushed.success

    target = _repository(real_git, tmp_path, "target")
    target._install_test_remote(remote_url)
    home_before = {
        path.relative_to(real_git.home): path.read_bytes()
        for path in real_git.home.rglob("*")
        if path.is_file()
    }
    head_before = (target.bare_repo / "HEAD").read_bytes()
    config_before = (target.bare_repo / "config").read_bytes()
    index_path = target.bare_repo / "index"
    index_before = index_path.read_bytes() if index_path.exists() else None
    history_path = real_git.state_home / "popctl" / "history.jsonl"
    history_before = history_path.read_bytes() if history_path.exists() else None
    apply_plan = target.state_dir / "apply-plan.json"
    assert not apply_plan.exists()
    assert target._fetch_test_remote(remote_url).success
    assert target._fetch_test_remote(remote_url, marker=True).success

    assert target.ref_oid(MAIN_REF) is None
    assert target.ref_oid(REMOTE_MAIN_REF) == source_commit
    assert target.merge_base_relation() is RefRelation.BOOTSTRAP_BEHIND
    assert target.verify_marker()
    assert target.read_tree(REMOTE_MAIN_REF).entries[0].mode == "100755"
    assert not (target.bare_repo / "FETCH_HEAD").exists()
    assert home_before == {
        path.relative_to(real_git.home): path.read_bytes()
        for path in real_git.home.rglob("*")
        if path.is_file()
    }
    assert (target.bare_repo / "HEAD").read_bytes() == head_before
    assert (target.bare_repo / "config").read_bytes() == config_before
    assert (index_path.read_bytes() if index_path.exists() else None) == index_before
    assert (history_path.read_bytes() if history_path.exists() else None) == history_before
    assert not apply_plan.exists()
    config = (target.bare_repo / "config").read_text(encoding="utf-8")
    assert MAIN_FETCH_REFSPEC in config


@pytest.mark.real_git
@pytest.mark.parametrize(
    ("mode", "path", "blob", "match"),
    [
        ("120000", b"link", b"outside", "Unsupported tree mode"),
        ("100644", b".git/config", b"safe", "Unsafe dotfiles path"),
        ("100644", b"/absolute", b"safe", "home-relative"),
        ("100644", b"binary", b"safe\x00content", "Tree content is blocked"),
    ],
)
def test_tree_validation_rejects_unsafe_layout_modes_and_content(
    real_git: RealGitEnvironment,
    tmp_path: Path,
    mode: str,
    path: bytes,
    blob: bytes,
    match: str,
) -> None:
    repository = _repository(real_git, tmp_path)
    tree_oid = _literal_tree(repository, mode, path, blob)

    with pytest.raises(TreeValidationError, match=match):
        repository.validate_tree(tree_oid)


@pytest.mark.real_git
def test_tree_validation_rejects_tracked_path_deletion_and_missing_marker(
    real_git: RealGitEnvironment,
    tmp_path: Path,
) -> None:
    repository = _repository(real_git, tmp_path)
    _write(real_git.home, ".config/tool/present", b"safe\n")
    _commit(repository, [".config/tool/present"], "present")

    assert not repository.verify_marker()
    with pytest.raises(TreeValidationError, match="drops tracked"):
        repository.validate_tree(MAIN_REF, tracked_paths=[".config/tool/missing"])


@pytest.mark.real_git
def test_tree_read_is_nul_delimited_and_preserves_newline_in_a_path(
    real_git: RealGitEnvironment,
    tmp_path: Path,
) -> None:
    repository = _repository(real_git, tmp_path)
    path = ".config/tool/with\nnewline"
    _write(real_git.home, path, b"safe\n")
    _commit(repository, [path], "newline path")

    assert repository.read_tree(MAIN_REF).entries[0].path == path


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/example/dotfiles.git",
        "git@github.com:example/dotfiles.git",
    ],
)
def test_remote_url_accepts_only_canonical_github_forms(url: str) -> None:
    assert validate_remote_url(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/example/dotfiles.git",
        "https://user:password@github.com/example/dotfiles.git",
        "https://user@github.com/example/dotfiles.git",
        "ssh://git@github.com/example/dotfiles.git",
        "git@github.com:example/dotfiles",
        "git@github.com:example/dotfiles.git/",
        "file:///tmp/dotfiles.git",
        "https://github.com/example/dotfiles.git?x=1",
    ],
)
def test_remote_url_rejects_userinfo_and_noncanonical_forms(url: str) -> None:
    with pytest.raises(RemoteUrlError):
        validate_remote_url(url)


@pytest.mark.real_git
def test_network_builder_only_admits_owned_values_and_retains_credential_helpers(
    real_git: RealGitEnvironment,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GIT_ASKPASS", "/tmp/askpass-sentinel")
    monkeypatch.setenv("SSH_ASKPASS", "/tmp/ssh-askpass-sentinel")
    monkeypatch.setenv("SSH_ASKPASS_REQUIRE", "force")
    monkeypatch.setenv("GIT_SSL_NO_VERIFY", "1")
    repository = _repository(real_git, tmp_path)

    environment = repository._network_environment()
    owned_config = repository._network_config.read_text(encoding="utf-8")

    assert "GIT_ASKPASS" not in environment
    assert "SSH_ASKPASS" not in environment
    assert "SSH_ASKPASS_REQUIRE" not in environment
    assert "GIT_SSL_NO_VERIFY" not in environment
    assert environment["GIT_TERMINAL_PROMPT"] == "0"
    assert environment["GIT_CONFIG_NOSYSTEM"] == "1"
    assert " -F " in environment["GIT_SSH_COMMAND"]
    assert "hooksPath = /dev/null" in owned_config
    assert "helper = \"cache --timeout=1\"" in owned_config
    assert "insteadOf" not in owned_config
    assert "pushurl" not in owned_config
    assert "sshCommand" not in owned_config
    assert "proxy" not in owned_config


@pytest.mark.real_git
def test_unknown_local_config_fails_before_a_network_connection(
    real_git: RealGitEnvironment,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(real_git, tmp_path)
    url = "https://github.com/example/dotfiles.git"
    repository.setup_remote(url)
    with (repository.bare_repo / "config").open("a", encoding="utf-8") as file:
        file.write("\n[core]\n\tsshCommand = /tmp/sentinel\n")
    wrapped = MagicMock(wraps=run_command_bytes)
    monkeypatch.setattr("popctl.dotfiles.repo.run_command_bytes", wrapped)

    with pytest.raises(DotfilesRepoError, match="Unexpected dotfiles local Git config key"):
        repository.fetch(url)

    assert all("fetch" not in call.args[0] for call in wrapped.call_args_list)


@pytest.mark.real_git
def test_network_calls_keep_the_literal_url_refspec_and_disable_hostile_hooks(
    real_git: RealGitEnvironment,
    tmp_path: Path,
) -> None:
    remote_store = _repository(real_git, tmp_path, "remote")
    source = _repository(real_git, tmp_path, "source")
    _write(real_git.home, ".config/tool/config", b"one\n")
    commit = _commit(source, [".config/tool/config"], "first")
    source.create_marker(commit)
    hook = tmp_path / "hostile-hooks" / "pre-push"
    hook.write_text("#!/bin/sh\nexit 98\n", encoding="utf-8")
    hook.chmod(0o755)
    remote_url = f"file://{remote_store.bare_repo}"
    source._install_test_remote(remote_url)

    result = source._network_git(
        ["push", remote_url, f"{MAIN_REF}:{MAIN_REF}", f"{MARKER_REF}:{MARKER_REF}"],
        remote_url,
    )

    assert result.success
    assert remote_store.ref_oid(MAIN_REF) == commit


@pytest.mark.real_git
def test_network_transport_never_contacts_hostile_global_or_pushurl_sentinels(
    real_git: RealGitEnvironment,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = tmp_path / "transport-sentinel"
    contacted = tmp_path / "transport-contacted"
    sentinel.write_text(f"#!/bin/sh\ntouch {contacted}\nexit 97\n", encoding="utf-8")
    sentinel.chmod(0o755)
    with real_git.global_config.open("a", encoding="utf-8") as file:
        file.write(
            "[core]\n"
            f"\tsshCommand = {sentinel}\n"
            "[url \"ssh://127.0.0.1:9/\"]\n"
            "\tinsteadOf = file://\n"
            "[http]\n"
            "\tproxy = http://127.0.0.1:9\n"
        )
    monkeypatch.setenv("GIT_ASKPASS", str(sentinel))
    monkeypatch.setenv("SSH_ASKPASS", str(sentinel))
    remote_store = _repository(real_git, tmp_path, "remote")
    source = _repository(real_git, tmp_path, "source")
    _write(real_git.home, ".config/tool/config", b"safe\n")
    commit = _commit(source, [".config/tool/config"], "safe")
    source.create_marker(commit)
    remote_url = f"file://{remote_store.bare_repo}"
    source._install_test_remote(remote_url)

    result = source._network_git(
        ["push", remote_url, f"{MAIN_REF}:{MAIN_REF}", f"{MARKER_REF}:{MARKER_REF}"],
        remote_url,
    )

    assert result.success
    assert remote_store.ref_oid(MAIN_REF) == commit
    assert not contacted.exists()
    with (source.bare_repo / "config").open("a", encoding="utf-8") as file:
        file.write(f"\n[remote \"origin\"]\n\tpushurl = ext::{sentinel}\n")
    with pytest.raises(DotfilesRepoError, match="Unexpected dotfiles local Git config key"):
        source._network_git(["push", remote_url, f"{MAIN_REF}:{MAIN_REF}"], remote_url)
    assert not contacted.exists()


@pytest.mark.real_git
def test_network_public_operations_use_validated_literal_urls_and_explicit_refs(
    real_git: RealGitEnvironment,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = _repository(real_git, tmp_path)
    url = "https://github.com/example/dotfiles.git"
    repository.setup_remote(url)
    _write(real_git.home, ".config/tool/config", b"safe\n")
    commit = _commit(repository, [".config/tool/config"], "first")
    repository.create_marker(commit)
    calls: list[tuple[list[str], str]] = []

    def fake_network(args: list[str], canonical_url: str) -> BytesCommandResult:
        calls.append((args, canonical_url))
        if args[0] == "ls-remote":
            return BytesCommandResult(
                stdout=(f"{'a' * 40}\t{MAIN_REF}\n").encode(), stderr=b"", returncode=0
            )
        return BytesCommandResult(stdout=b"", stderr=b"", returncode=0)

    monkeypatch.setattr(repository, "_network_git", fake_network)

    assert repository.fetch(url, status=True).success
    assert repository.push(url).success
    ls_remote = repository.ls_remote(url)

    assert ls_remote.transport.success
    assert ls_remote.refs[0].ref == MAIN_REF
    assert calls[0] == (["fetch", "--no-write-fetch-head", url, MAIN_FETCH_REFSPEC], url)
    assert calls[1] == (["push", url, f"{MAIN_REF}:{MAIN_REF}", f"{MARKER_REF}:{MARKER_REF}"], url)
    assert calls[2] == (["ls-remote", "--refs", url, MAIN_REF, MARKER_REF], url)


@pytest.mark.real_git
@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        (b"authentication failed", TransportOutcome.AUTH),
        (b"authentication failed; could not resolve host", TransportOutcome.AUTH),
        (b"could not resolve host", TransportOutcome.OFFLINE),
        (b"connection timed out", TransportOutcome.TIMEOUT),
        (b"remote rejected", TransportOutcome.OTHER),
    ],
)
def test_transport_outcomes_are_typed_with_auth_precedence(
    real_git: RealGitEnvironment,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stderr: bytes,
    expected: TransportOutcome,
) -> None:
    repository = _repository(real_git, tmp_path)
    monkeypatch.setattr(
        repository,
        "_network_git",
        lambda *_args: BytesCommandResult(stdout=b"", stderr=stderr, returncode=2),
    )

    result = repository.fetch("https://github.com/example/dotfiles.git")

    assert result.outcome is expected
