"""Pytest configuration for all tests."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest


def pytest_configure(config: pytest.Config) -> None:
    """Isolate user-state paths before test collection imports application modules."""
    session_dir = Path(tempfile.mkdtemp(prefix="popctl-pytest-"))
    home_dir = session_dir / "home"
    config_dir = session_dir / "xdg-config"
    state_dir = session_dir / "xdg-state"

    home_dir.mkdir()
    config_dir.mkdir()
    state_dir.mkdir()
    os.environ["HOME"] = str(home_dir)
    os.environ["XDG_CONFIG_HOME"] = str(config_dir)
    os.environ["XDG_STATE_HOME"] = str(state_dir)

    config.add_cleanup(lambda: shutil.rmtree(session_dir, ignore_errors=True))


@pytest.fixture(autouse=True)
def _isolate_user_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect HOME and the XDG base dirs into a per-test tmp path.

    Guarantees no test reads or writes real user state under `HOME`,
    `~/.config/popctl`, or `~/.local/state/popctl` (config, manifest, history,
    backups, advisor memory). Tests that assert `Path.home()`-derived defaults
    are being adjusted in a follow-up wave.
    """
    isolated_home = tmp_path / "isolated-home"
    isolated_home.mkdir()
    monkeypatch.setenv("HOME", str(isolated_home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    # Deterministic Rich console width: CI leaves COLUMNS unset (80-column
    # wrapping breaks path-containment assertions), local terminals vary.
    monkeypatch.setenv("COLUMNS", "200")


@pytest.fixture(autouse=True)
def _no_real_system_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse any real system-command execution from unit tests.

    An unmocked test could otherwise execute a host binary, including an agent
    CLI that hangs the suite and burns quota. The failure is not catchable by
    production recovery handlers, so any reached guard fails the test. Tests
    that exercise these modules override their bindings with their own mocks,
    which take precedence for their scope.
    """

    def _refuse(*_args: object, **_kwargs: object) -> object:
        pytest.fail("unit test attempted to execute a real system command")

    # Deterministic scanner baseline: GitHub runners preinstall snap while the
    # dev container does not — pin it unavailable so availability-dependent
    # tests behave identically everywhere; tests wanting snap override this.
    monkeypatch.setattr("popctl.scanners.snap.command_exists", lambda _cmd: False)

    monkeypatch.setattr("popctl.domain.ownership.run_command", _refuse)
    monkeypatch.setattr("popctl.alerts.notifier.run_command", _refuse)
    monkeypatch.setattr("popctl.scanners.flatpak.run_command", _refuse)
    monkeypatch.setattr("popctl.scanners.apt.run_command", _refuse)
    monkeypatch.setattr("popctl.scanners.snap.run_command", _refuse)
    monkeypatch.setattr("popctl.filesystem.operator.run_command", _refuse)
    monkeypatch.setattr("popctl.operators.apt.run_command", _refuse)
    monkeypatch.setattr("popctl.operators.base.run_command", _refuse)
    monkeypatch.setattr("popctl.backup.backup.run_command", _refuse)
    monkeypatch.setattr("popctl.backup.restore.run_command", _refuse)
    monkeypatch.setattr("popctl.advisor.runner.run_command", _refuse)
    monkeypatch.setattr("popctl.advisor.runner.run_interactive", _refuse)
    monkeypatch.setattr("popctl.utils.shell.subprocess.run", _refuse)
    monkeypatch.setattr("popctl.backup.backup.subprocess.Popen", _refuse)
    monkeypatch.setattr("popctl.backup.restore.subprocess.Popen", _refuse)
