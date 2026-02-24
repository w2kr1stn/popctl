"""Unit tests for AgentRunner and AgentResult."""

import subprocess
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
    """Tests for AgentRunner.run_headless in host mode."""

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
    """Tests for launch_interactive in host mode (no container)."""

    def test_no_cli_returns_manual(self, tmp_path: Path) -> None:
        with patch("shutil.which", return_value=None):
            result = AgentRunner(AdvisorConfig()).launch_interactive(tmp_path)
        assert result.error == "manual_mode"

    def test_cli_no_tty_runs_headless(self, tmp_path: Path) -> None:
        """Non-TTY with CLI available should run headless, not manual."""
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


# ── Container mode ─────────────────────────────────────────────


class TestLaunchInteractiveContainer:
    """Tests for launch_interactive in container mode."""

    def _container_config(self, tmp_path: Path) -> AdvisorConfig:
        return AdvisorConfig(dev_container_path=tmp_path / "compose")

    def test_container_not_started(self, tmp_path: Path) -> None:
        config = self._container_config(tmp_path)
        with patch("popctl.advisor.runner.ensure_container", return_value=None):
            result = AgentRunner(config).launch_interactive(tmp_path)
        assert result.error == "manual_mode"

    def test_container_cli_missing(self, tmp_path: Path) -> None:
        config = self._container_config(tmp_path)
        with (
            patch("popctl.advisor.runner.ensure_container", return_value="abc123"),
            patch("popctl.advisor.runner.container_has_command", return_value=False),
        ):
            result = AgentRunner(config).launch_interactive(tmp_path)
        assert result.error == "manual_mode"

    def test_container_interactive_success(self, tmp_path: Path) -> None:
        ws = tmp_path / "workspace"
        ws.mkdir()
        output_dir = ws / "output"
        output_dir.mkdir()
        config = self._container_config(tmp_path)

        def mock_run_interactive(cmd: list[str], **kwargs: object) -> int:
            (output_dir / "decisions.toml").write_text("[packages.apt]")
            return 0

        with (
            patch("popctl.advisor.runner.ensure_container", return_value="abc123"),
            patch("popctl.advisor.runner.container_has_command", return_value=True),
            patch("sys.stdin") as mock_stdin,
            patch("popctl.advisor.runner.docker_cp", return_value=True),
            patch("popctl.advisor.runner.container_cleanup"),
            patch("popctl.advisor.runner.run_interactive", side_effect=mock_run_interactive),
        ):
            mock_stdin.isatty.return_value = True
            result = AgentRunner(config).launch_interactive(ws)
        assert result.success is True

    def test_container_headless_no_tty(self, tmp_path: Path) -> None:
        ws = tmp_path / "workspace"
        ws.mkdir()
        output_dir = ws / "output"
        output_dir.mkdir()
        (output_dir / "decisions.toml").write_text("[packages.apt]")
        config = self._container_config(tmp_path)
        mock_result = MagicMock(success=True, stdout="ok", stderr="", returncode=0)

        with (
            patch("popctl.advisor.runner.ensure_container", return_value="abc123"),
            patch("popctl.advisor.runner.container_has_command", return_value=True),
            patch("sys.stdin") as mock_stdin,
            patch("popctl.advisor.runner.docker_cp", return_value=True),
            patch("popctl.advisor.runner.container_cleanup"),
            patch("popctl.advisor.runner.run_command", return_value=mock_result),
        ):
            mock_stdin.isatty.return_value = False
            result = AgentRunner(config).launch_interactive(ws)
        assert result.success is True

    def test_container_cp_in_fails(self, tmp_path: Path) -> None:
        config = self._container_config(tmp_path)
        with (
            patch("popctl.advisor.runner.ensure_container", return_value="abc123"),
            patch("popctl.advisor.runner.container_has_command", return_value=True),
            patch("sys.stdin") as mock_stdin,
            patch("popctl.advisor.runner.docker_cp", return_value=False),
            patch("popctl.advisor.runner.container_cleanup"),
        ):
            mock_stdin.isatty.return_value = True
            result = AgentRunner(config).launch_interactive(tmp_path)
        assert result.success is False
        assert "copy workspace" in (result.error or "").lower()

    def test_container_exec_failure_cleans_up(self, tmp_path: Path) -> None:
        config = self._container_config(tmp_path)
        with (
            patch("popctl.advisor.runner.ensure_container", return_value="abc123"),
            patch("popctl.advisor.runner.container_has_command", return_value=True),
            patch("sys.stdin") as mock_stdin,
            patch("popctl.advisor.runner.docker_cp", return_value=True),
            patch("popctl.advisor.runner.container_cleanup") as mock_cleanup,
            patch("popctl.advisor.runner.run_interactive", side_effect=OSError("fail")),
        ):
            mock_stdin.isatty.return_value = True
            result = AgentRunner(config).launch_interactive(tmp_path)
        assert result.success is False
        mock_cleanup.assert_called()

    def test_container_persists_memory(self, tmp_path: Path) -> None:
        ws = tmp_path / "workspace"
        ws.mkdir()
        (ws / "output").mkdir()
        config = self._container_config(tmp_path)
        runner = AgentRunner(config)

        def mock_docker_cp(src: str, dest: str) -> bool:
            if "memory.md" in src:
                (ws / "memory.md").write_text("# Memory\n")
            return True

        with (
            patch("popctl.advisor.runner.ensure_container", return_value="abc123"),
            patch("popctl.advisor.runner.container_has_command", return_value=True),
            patch("sys.stdin") as mock_stdin,
            patch("popctl.advisor.runner.docker_cp", side_effect=mock_docker_cp),
            patch("popctl.advisor.runner.container_cleanup"),
            patch("popctl.advisor.runner.run_interactive", return_value=0),
            patch.object(runner, "_persist_memory") as mock_persist,
        ):
            mock_stdin.isatty.return_value = True
            runner.launch_interactive(ws)
        mock_persist.assert_called_once()


class TestRunHeadlessContainer:
    """Tests for run_headless in container mode."""

    def test_container_not_available(self, tmp_path: Path) -> None:
        config = AdvisorConfig(dev_container_path=tmp_path / "compose")
        with patch("popctl.advisor.runner.ensure_container", return_value=None):
            result = AgentRunner(config).run_headless(tmp_path)
        assert result.success is False
        assert "container" in (result.error or "").lower()

    def test_headless_success(self, tmp_path: Path) -> None:
        ws = tmp_path / "workspace"
        ws.mkdir()
        output_dir = ws / "output"
        output_dir.mkdir()
        (output_dir / "decisions.toml").write_text("[packages.apt]")
        config = AdvisorConfig(dev_container_path=tmp_path / "compose")
        mock_result = MagicMock(success=True, stdout="ok", stderr="", returncode=0)

        with (
            patch("popctl.advisor.runner.ensure_container", return_value="abc123"),
            patch("popctl.advisor.runner.docker_cp", return_value=True),
            patch("popctl.advisor.runner.container_cleanup"),
            patch("popctl.advisor.runner.run_command", return_value=mock_result),
        ):
            result = AgentRunner(config).run_headless(ws)
        assert result.success is True


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

    def test_build_shell_command(self) -> None:
        shell_cmd = AgentRunner(AdvisorConfig())._build_shell_command(interactive=True)
        assert "claude" in shell_cmd


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
