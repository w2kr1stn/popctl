"""Unit tests for AgentRunner and AgentResult."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from popctl.advisor.config import AdvisorConfig
from popctl.advisor.runner import AgentResult, AgentRunner


class TestAgentResult:
    """Tests for AgentResult dataclass."""

    def test_successful_result(self, tmp_path: Path) -> None:
        decisions_path = tmp_path / "decisions.toml"
        result = AgentResult(
            success=True,
            output="done",
            decisions_path=decisions_path,
            workspace_path=tmp_path,
        )
        assert result.success is True
        assert result.error is None
        assert result.decisions_path == decisions_path

    def test_failed_result(self) -> None:
        result = AgentResult(success=False, output="", error="crashed")
        assert result.success is False
        assert result.decisions_path is None

    def test_result_is_frozen(self) -> None:
        result = AgentResult(success=True, output="test")
        with pytest.raises(AttributeError):
            result.success = False  # type: ignore[misc]


# ── Host mode: headless ────────────────────────────────────────


class TestRunHeadlessHost:
    """Tests for AgentRunner.run_headless in host mode (no session)."""

    def _make_workspace(self, tmp_path: Path) -> Path:
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "output").mkdir()
        return ws

    def test_success(self, tmp_path: Path) -> None:
        ws = self._make_workspace(tmp_path)
        (ws / "output" / "decisions.toml").write_text('[packages.apt]\nkeep = ["vim"]')
        mock_result = MagicMock(success=True, stdout="done", stderr="", returncode=0)
        with patch("popctl.advisor.runner.run_command", return_value=mock_result):
            result = AgentRunner(AdvisorConfig()).run_headless(ws)
        assert result.success is True
        assert result.decisions_path == ws / "output" / "decisions.toml"

    def test_no_decisions_file(self, tmp_path: Path) -> None:
        ws = self._make_workspace(tmp_path)
        mock_result = MagicMock(success=True, stdout="", stderr="", returncode=0)
        with patch("popctl.advisor.runner.run_command", return_value=mock_result):
            result = AgentRunner(AdvisorConfig()).run_headless(ws)
        assert result.success is False

    def test_nonzero_exit(self, tmp_path: Path) -> None:
        ws = self._make_workspace(tmp_path)
        mock_result = MagicMock(success=False, stdout="", stderr="API error", returncode=1)
        with patch("popctl.advisor.runner.run_command", return_value=mock_result):
            result = AgentRunner(AdvisorConfig()).run_headless(ws)
        assert result.success is False
        assert result.error == "API error"

    def test_timeout(self, tmp_path: Path) -> None:
        ws = self._make_workspace(tmp_path)
        with patch(
            "popctl.advisor.runner.run_command",
            side_effect=subprocess.TimeoutExpired(cmd=["claude"], timeout=60),
        ):
            result = AgentRunner(AdvisorConfig(timeout_seconds=60)).run_headless(ws)
        assert result.success is False
        assert "timed out" in (result.error or "")

    def test_command_not_found(self, tmp_path: Path) -> None:
        ws = self._make_workspace(tmp_path)
        with patch("popctl.advisor.runner.run_command", side_effect=FileNotFoundError):
            result = AgentRunner(AdvisorConfig()).run_headless(ws)
        assert result.success is False

    def test_uses_configured_timeout(self, tmp_path: Path) -> None:
        ws = self._make_workspace(tmp_path)
        (ws / "output" / "decisions.toml").write_text("[packages.apt]")
        mock_result = MagicMock(success=True, stdout="", stderr="", returncode=0)
        with patch("popctl.advisor.runner.run_command", return_value=mock_result) as mock_run:
            AgentRunner(AdvisorConfig(timeout_seconds=300)).run_headless(ws)
        assert mock_run.call_args.kwargs["timeout"] == 300.0


# ── Host mode: interactive ─────────────────────────────────────


class TestLaunchInteractiveHost:
    """Tests for launch_interactive in host mode (no session)."""

    def test_no_cli_returns_manual(self, tmp_path: Path) -> None:
        with patch("shutil.which", return_value=None):
            result = AgentRunner(AdvisorConfig()).launch_interactive(tmp_path)
        assert result.error == "manual_mode"

    def test_cli_no_tty_runs_headless(self, tmp_path: Path) -> None:
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "output").mkdir()
        (ws / "output" / "decisions.toml").write_text("[packages.apt]")
        mock_result = MagicMock(success=True, stdout="", stderr="", returncode=0)
        with (
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("sys.stdin") as mock_stdin,
            patch("popctl.advisor.runner.run_command", return_value=mock_result),
        ):
            mock_stdin.isatty.return_value = False
            result = AgentRunner(AdvisorConfig()).launch_interactive(ws)
        assert result.success is True
        assert result.decisions_path is not None

    def test_cli_tty_runs_interactive(self, tmp_path: Path) -> None:
        ws = tmp_path / "workspace"
        ws.mkdir()
        output_dir = ws / "output"
        output_dir.mkdir()

        def mock_run(cmd: list[str], **kwargs: object) -> int:
            (output_dir / "decisions.toml").write_text("[packages.apt]")
            return 0

        with (
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("sys.stdin") as mock_stdin,
            patch("popctl.advisor.runner.run_interactive", side_effect=mock_run),
        ):
            mock_stdin.isatty.return_value = True
            result = AgentRunner(AdvisorConfig()).launch_interactive(ws)
        assert result.success is True
        assert result.decisions_path == output_dir / "decisions.toml"

    def test_host_persists_memory(self, tmp_path: Path) -> None:
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "output").mkdir()

        def mock_run(cmd: list[str], **kwargs: object) -> int:
            (ws / "memory.md").write_text("# Memory\n")
            return 0

        runner = AgentRunner(AdvisorConfig())
        with (
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("sys.stdin") as mock_stdin,
            patch("popctl.advisor.runner.run_interactive", side_effect=mock_run),
            patch.object(runner, "_persist_memory") as mock_persist,
        ):
            mock_stdin.isatty.return_value = True
            runner.launch_interactive(ws)
        mock_persist.assert_called_once_with(ws / "memory.md")

    def test_gemini_provider(self, tmp_path: Path) -> None:
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "output").mkdir()
        with (
            patch("shutil.which", return_value="/usr/bin/gemini"),
            patch("sys.stdin") as mock_stdin,
            patch("popctl.advisor.runner.run_interactive", return_value=0) as mock_run,
        ):
            mock_stdin.isatty.return_value = True
            AgentRunner(AdvisorConfig(provider="gemini")).launch_interactive(ws)
        cmd = mock_run.call_args[0][0]
        assert cmd[0] == "gemini"
        assert "--prompt" in cmd

    def test_with_model_flag(self, tmp_path: Path) -> None:
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "output").mkdir()
        with (
            patch("shutil.which", return_value="/usr/bin/claude"),
            patch("sys.stdin") as mock_stdin,
            patch("popctl.advisor.runner.run_interactive", return_value=0) as mock_run,
        ):
            mock_stdin.isatty.return_value = True
            AgentRunner(AdvisorConfig(model="claude-sonnet-4-5-20250514")).launch_interactive(ws)
        cmd = mock_run.call_args[0][0]
        assert "--model" in cmd
        assert "claude-sonnet-4-5-20250514" in cmd

    def test_manual_instructions_include_provider(self, tmp_path: Path) -> None:
        result = AgentRunner(AdvisorConfig(provider="gemini"))._manual_instructions(tmp_path)
        assert "gemini" in result.output
        assert str(tmp_path) in result.output


# ── SessionManager mode ───────────────────────────────────────


@dataclass(frozen=True)
class _FakeSessionResult:
    returncode: int
    stdout: str
    stderr: str
    workspace_dir: Path

    @property
    def success(self) -> bool:
        return self.returncode == 0


class TestRunHeadlessSession:
    """Tests for run_headless via SessionManager."""

    def _make_workspace(self, tmp_path: Path) -> Path:
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "output").mkdir()
        return ws

    def test_success_with_decisions(self, tmp_path: Path) -> None:
        ws = self._make_workspace(tmp_path)
        (ws / "output" / "decisions.toml").write_text("[packages.apt]")
        session = MagicMock()
        session.run_headless.return_value = _FakeSessionResult(
            returncode=0, stdout="done", stderr="", workspace_dir=ws,
        )
        result = AgentRunner(AdvisorConfig(), session=session).run_headless(ws)
        assert result.success is True
        assert result.decisions_path == ws / "output" / "decisions.toml"

    def test_no_decisions_file(self, tmp_path: Path) -> None:
        ws = self._make_workspace(tmp_path)
        session = MagicMock()
        session.run_headless.return_value = _FakeSessionResult(
            returncode=0, stdout="", stderr="", workspace_dir=ws,
        )
        result = AgentRunner(AdvisorConfig(), session=session).run_headless(ws)
        assert result.success is False

    def test_nonzero_exit(self, tmp_path: Path) -> None:
        ws = self._make_workspace(tmp_path)
        session = MagicMock()
        session.run_headless.return_value = _FakeSessionResult(
            returncode=1, stdout="", stderr="API error", workspace_dir=ws,
        )
        result = AgentRunner(AdvisorConfig(), session=session).run_headless(ws)
        assert result.success is False
        assert result.error == "API error"

    def test_value_error_returns_failure(self, tmp_path: Path) -> None:
        ws = self._make_workspace(tmp_path)
        session = MagicMock()
        session.run_headless.side_effect = ValueError("no container")
        result = AgentRunner(AdvisorConfig(), session=session).run_headless(ws)
        assert result.success is False
        assert "no container" in (result.error or "")

    def test_passes_model_when_configured(self, tmp_path: Path) -> None:
        ws = self._make_workspace(tmp_path)
        (ws / "output" / "decisions.toml").write_text("[packages.apt]")
        session = MagicMock()
        session.run_headless.return_value = _FakeSessionResult(
            returncode=0, stdout="", stderr="", workspace_dir=ws,
        )
        AgentRunner(AdvisorConfig(model="opus"), session=session).run_headless(ws)
        call_kwargs = session.run_headless.call_args.kwargs
        assert call_kwargs["model"] == "opus"

    def test_no_model_omits_key(self, tmp_path: Path) -> None:
        ws = self._make_workspace(tmp_path)
        (ws / "output" / "decisions.toml").write_text("[packages.apt]")
        session = MagicMock()
        session.run_headless.return_value = _FakeSessionResult(
            returncode=0, stdout="", stderr="", workspace_dir=ws,
        )
        AgentRunner(AdvisorConfig(), session=session).run_headless(ws)
        call_kwargs = session.run_headless.call_args.kwargs
        assert "model" not in call_kwargs

    def test_persists_memory(self, tmp_path: Path) -> None:
        ws = self._make_workspace(tmp_path)
        (ws / "output" / "decisions.toml").write_text("[packages.apt]")
        (ws / "memory.md").write_text("# Memory\n")
        session = MagicMock()
        session.run_headless.return_value = _FakeSessionResult(
            returncode=0, stdout="", stderr="", workspace_dir=ws,
        )
        runner = AgentRunner(AdvisorConfig(), session=session)
        with patch.object(runner, "_persist_memory") as mock_persist:
            runner.run_headless(ws)
        mock_persist.assert_called_once_with(ws / "memory.md")


class TestLaunchInteractiveSession:
    """Tests for launch_interactive via SessionManager."""

    def test_success_with_decisions(self, tmp_path: Path) -> None:
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "output").mkdir()
        (ws / "output" / "decisions.toml").write_text("[packages.apt]")
        session = MagicMock()
        session.run_interactive.return_value = _FakeSessionResult(
            returncode=0, stdout="", stderr="", workspace_dir=ws,
        )
        result = AgentRunner(AdvisorConfig(), session=session).launch_interactive(ws)
        assert result.success is True
        assert result.decisions_path == ws / "output" / "decisions.toml"

    def test_nonzero_exit(self, tmp_path: Path) -> None:
        ws = tmp_path / "workspace"
        ws.mkdir()
        session = MagicMock()
        session.run_interactive.return_value = _FakeSessionResult(
            returncode=1, stdout="", stderr="session error", workspace_dir=ws,
        )
        result = AgentRunner(AdvisorConfig(), session=session).launch_interactive(ws)
        assert result.success is False
        assert "session error" in (result.error or "")

    def test_value_error_returns_failure(self, tmp_path: Path) -> None:
        ws = tmp_path / "workspace"
        ws.mkdir()
        session = MagicMock()
        session.run_interactive.side_effect = ValueError("no container")
        result = AgentRunner(AdvisorConfig(), session=session).launch_interactive(ws)
        assert result.success is False

    def test_prefers_session_over_host(self, tmp_path: Path) -> None:
        """When session is set, should NOT check shutil.which."""
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "output").mkdir()
        session = MagicMock()
        session.run_interactive.return_value = _FakeSessionResult(
            returncode=0, stdout="", stderr="", workspace_dir=ws,
        )
        with patch("shutil.which") as mock_which:
            AgentRunner(AdvisorConfig(), session=session).launch_interactive(ws)
        mock_which.assert_not_called()


# ── Build commands ─────────────────────────────────────────────


class TestBuildCommands:
    """Tests for command building helpers."""

    def test_build_headless_claude(self) -> None:
        cmd = AgentRunner(AdvisorConfig())._build_headless_command()
        assert cmd[0] == "claude"
        assert "-p" in cmd
        assert "--output-format" in cmd

    def test_build_headless_gemini(self) -> None:
        cmd = AgentRunner(AdvisorConfig(provider="gemini"))._build_headless_command()
        assert cmd[0] == "gemini"
        assert "--prompt" in cmd

    def test_build_interactive_claude(self) -> None:
        cmd = AgentRunner(AdvisorConfig())._build_interactive_command()
        assert cmd[0] == "claude"
        assert "--model" not in cmd

    def test_build_interactive_with_model(self) -> None:
        cmd = AgentRunner(AdvisorConfig(model="opus"))._build_interactive_command()
        assert "--model" in cmd
        assert "opus" in cmd


# ── Persist memory ─────────────────────────────────────────────


class TestPersistMemory:
    """Tests for _persist_memory."""

    def test_copies_file(self, tmp_path: Path) -> None:
        workspace_memory = tmp_path / "workspace" / "memory.md"
        workspace_memory.parent.mkdir()
        workspace_memory.write_text("# Memory\n")
        persistent_dir = tmp_path / "state" / "popctl" / "advisor"
        with patch("popctl.advisor.runner.ensure_dir", return_value=persistent_dir):
            persistent_dir.mkdir(parents=True, exist_ok=True)
            AgentRunner(AdvisorConfig())._persist_memory(workspace_memory)
        assert (persistent_dir / "memory.md").exists()

    def test_handles_failure(self, tmp_path: Path) -> None:
        memory = tmp_path / "memory.md"
        memory.write_text("# Memory\n")
        with (
            patch("popctl.advisor.runner.ensure_dir", side_effect=RuntimeError("fail")),
            patch("popctl.advisor.runner.print_warning") as mock_warn,
        ):
            AgentRunner(AdvisorConfig())._persist_memory(memory)
        mock_warn.assert_called_once()
