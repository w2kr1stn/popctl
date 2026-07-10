"""Unit tests for alerts onboarding CLI commands."""

import stat
from pathlib import Path
from unittest.mock import patch

from popctl.cli.main import app
from popctl.utils.shell import CommandResult
from typer.testing import CliRunner

runner = CliRunner()


class TestAlertsInitConfigCommand:
    """Tests for popctl alerts init-config."""

    def test_creates_packaged_template(self, tmp_path: Path) -> None:
        config_path = tmp_path / "popctl" / "alerts.toml"

        with patch(
            "popctl.cli.commands.alerts.get_alerts_config_path", return_value=config_path
        ):
            result = runner.invoke(app, ["alerts", "init-config"])

        assert result.exit_code == 0
        assert config_path.exists()
        assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
        assert 'ws_url = "ws://alert-host.example:8765/"' in config_path.read_text()
        assert str(config_path) in result.output
        assert "ws_url (the alert server address)" in result.output

    def test_refuses_existing_config_without_force(self, tmp_path: Path) -> None:
        config_path = tmp_path / "popctl" / "alerts.toml"
        config_path.parent.mkdir()
        config_path.write_text("ws_url = \"ws://existing\"\n")

        with patch(
            "popctl.cli.commands.alerts.get_alerts_config_path", return_value=config_path
        ):
            result = runner.invoke(app, ["alerts", "init-config"])

        assert result.exit_code == 1
        assert config_path.read_text() == 'ws_url = "ws://existing"\n'
        assert "already exists" in result.output

    def test_force_overwrites_existing_config(self, tmp_path: Path) -> None:
        config_path = tmp_path / "popctl" / "alerts.toml"
        config_path.parent.mkdir()
        config_path.write_text("ws_url = \"ws://existing\"\n")
        config_path.chmod(0o644)

        with patch(
            "popctl.cli.commands.alerts.get_alerts_config_path", return_value=config_path
        ):
            result = runner.invoke(app, ["alerts", "init-config", "--force"])

        assert result.exit_code == 0
        assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
        assert 'ws_url = "ws://alert-host.example:8765/"' in config_path.read_text()
        assert "Overwriting existing alerts config" in result.output


class TestAlertsInstallServiceCommand:
    """Tests for popctl alerts install-service."""

    def test_refuses_existing_service_without_force(self, tmp_path: Path) -> None:
        service_path = tmp_path / "systemd" / "user" / "popctl-alerts.service"
        service_path.parent.mkdir(parents=True)
        service_path.write_text("existing service", encoding="utf-8")

        with (
            patch(
                "popctl.cli.commands.alerts._get_service_path", return_value=service_path
            ),
            patch("popctl.cli.commands.alerts.run_command") as mock_run,
        ):
            result = runner.invoke(app, ["alerts", "install-service"])

        assert result.exit_code == 1
        assert service_path.read_text(encoding="utf-8") == "existing service"
        assert "already exists" in result.output
        assert "--force" in result.output
        mock_run.assert_not_called()

    def test_force_overwrites_existing_service(self, tmp_path: Path) -> None:
        service_path = tmp_path / "systemd" / "user" / "popctl-alerts.service"
        service_path.parent.mkdir(parents=True)
        service_path.write_text("existing service", encoding="utf-8")
        successful_reload = CommandResult(stdout="", stderr="", returncode=0)

        with (
            patch(
                "popctl.cli.commands.alerts._get_service_path", return_value=service_path
            ),
            patch("popctl.cli.commands.alerts.which", return_value="/opt/bin/popctl"),
            patch(
                "popctl.cli.commands.alerts.run_command", return_value=successful_reload
            ),
        ):
            result = runner.invoke(app, ["alerts", "install-service", "--force"])

        assert result.exit_code == 0
        assert "ExecStart=/opt/bin/popctl alerts watch" in service_path.read_text()
        assert "Overwriting existing alerts service" in result.output

    def test_writes_resolved_executable_and_degrades_on_reload_failure(
        self, tmp_path: Path
    ) -> None:
        service_path = tmp_path / "systemd" / "user" / "popctl-alerts.service"
        failed_reload = CommandResult(
            stdout="", stderr="Failed to connect to bus", returncode=1
        )

        with (
            patch(
                "popctl.cli.commands.alerts._get_service_path", return_value=service_path
            ),
            patch("popctl.cli.commands.alerts.which", return_value="/opt/bin/popctl"),
            patch(
                "popctl.cli.commands.alerts.run_command", return_value=failed_reload
            ) as mock_run,
        ):
            result = runner.invoke(app, ["alerts", "install-service"])

        assert result.exit_code == 0
        assert "ExecStart=/opt/bin/popctl alerts watch" in service_path.read_text()
        mock_run.assert_called_once_with(["systemctl", "--user", "daemon-reload"])
        assert "Could not reload user systemd units" in result.output
        assert "systemctl --user daemon-reload" in result.output
        assert "systemctl --user enable --now popctl-alerts.service" in result.output

    def test_degrades_when_systemctl_is_unavailable(self, tmp_path: Path) -> None:
        service_path = tmp_path / "systemd" / "user" / "popctl-alerts.service"

        with (
            patch(
                "popctl.cli.commands.alerts._get_service_path", return_value=service_path
            ),
            patch("popctl.cli.commands.alerts.which", return_value="/opt/bin/popctl"),
            patch(
                "popctl.cli.commands.alerts.run_command", side_effect=FileNotFoundError()
            ),
        ):
            result = runner.invoke(app, ["alerts", "install-service"])

        assert result.exit_code == 0
        assert service_path.exists()
        assert "systemctl --user daemon-reload" in result.output
