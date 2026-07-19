from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest
from popctl.utils.shell import BytesCommandResult, run_command_bytes

_SYSTEM_RUN = subprocess.run
_SYSTEM_POPEN = subprocess.Popen


@dataclass(frozen=True, slots=True)
class RealGitEnvironment:
    home: Path
    config_home: Path
    data_home: Path
    state_home: Path
    cache_home: Path
    runtime_home: Path
    global_config: Path

    def git(self, *args: str) -> BytesCommandResult:
        result = run_command_bytes(["git", *args])
        if not result.success:
            stderr = result.stderr.decode("utf-8", errors="replace")
            pytest.fail(f"test git command failed: {' '.join(args)}\n{stderr}")
        return result


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "real_git: opt in to the scoped real-Git fixture")


@pytest.fixture
def real_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> RealGitEnvironment:
    home = tmp_path / "home"
    config_home = tmp_path / "xdg-config"
    data_home = tmp_path / "xdg-data"
    state_home = tmp_path / "xdg-state"
    cache_home = tmp_path / "xdg-cache"
    runtime_home = tmp_path / "xdg-runtime"
    for path in (home, config_home, data_home, state_home, cache_home, runtime_home):
        path.mkdir()
    global_config = tmp_path / "hostile.gitconfig"
    hostile_hooks = tmp_path / "hostile-hooks"
    hostile_hooks.mkdir()
    excludes = tmp_path / "hostile-excludes"
    excludes.write_text("*\n", encoding="utf-8")
    global_config.write_text(
        "[user]\n"
        "\tname = Dotfiles Test\n"
        "\temail = dotfiles-test@example.invalid\n"
        "[init]\n"
        "\tdefaultBranch = master\n"
        "[core]\n"
        f"\thooksPath = {hostile_hooks}\n"
        f"\texcludesFile = {excludes}\n"
        "\tsshCommand = ssh -o ProxyCommand=none\n"
        "[url \"ssh://127.0.0.1:9/\"]\n"
        "\tinsteadOf = https://github.com/\n"
        "[http]\n"
        "\tproxy = http://127.0.0.1:9\n"
        "[credential]\n"
        "\thelper = cache --timeout=1\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("XDG_DATA_HOME", str(data_home))
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_home))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime_home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(global_config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")

    def run_git_only(args: object, *args_rest: object, **kwargs: object) -> object:
        if not isinstance(args, list) or not args or args[0] != "git":
            pytest.fail("real_git permits only git as argv[0]")
        guarded_popen = subprocess.Popen
        subprocess.Popen = _SYSTEM_POPEN
        try:
            return _SYSTEM_RUN(args, *args_rest, **kwargs)
        finally:
            subprocess.Popen = guarded_popen

    monkeypatch.setattr("popctl.utils.shell.subprocess.run", run_git_only)
    return RealGitEnvironment(
        home=home,
        config_home=config_home,
        data_home=data_home,
        state_home=state_home,
        cache_home=cache_home,
        runtime_home=runtime_home,
        global_config=global_config,
    )
