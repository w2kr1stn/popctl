from pathlib import Path
from unittest.mock import patch

from popctl.advisor.config import AdvisorConfig, AdvisorConfigError
from popctl.cli.main import app
from typer.testing import CliRunner

runner = CliRunner()


def _run_doctor(
    tmp_path: Path,
    *,
    missing: set[str] | None = None,
    advisor_config: AdvisorConfig | None = None,
    advisor_error: AdvisorConfigError | None = None,
    alerts_config_content: str = 'ws_url = "wss://alerts.example.test/"\n',
    alerts_path_exists: bool = True,
    backup_path_exists: bool = True,
) -> object:
    missing = missing or set()
    alerts_path = tmp_path / "alerts.toml"
    backup_path = tmp_path / "backup.toml"
    if alerts_path_exists:
        alerts_path.write_text(alerts_config_content, encoding="utf-8")
    if backup_path_exists:
        backup_path.touch()

    def command_is_available(command: str) -> bool:
        return command not in missing

    with (
        patch(
            "popctl.cli.commands.doctor.command_exists",
            side_effect=command_is_available,
        ),
        patch(
            "popctl.cli.commands.doctor.get_alerts_config_path",
            return_value=alerts_path,
        ),
        patch("popctl.cli.commands.doctor.get_config_dir", return_value=tmp_path),
        patch(
            "popctl.cli.commands.doctor.load_advisor_config",
            return_value=advisor_config,
            side_effect=advisor_error,
        ),
    ):
        return runner.invoke(app, ["doctor"])


def test_doctor_all_present_exits_successfully(tmp_path: Path) -> None:
    result = _run_doctor(tmp_path, advisor_config=AdvisorConfig(provider="claude"))

    assert result.exit_code == 0
    assert "Package management (core) — ready" in result.output
    assert "using the provider CLI's own login" in result.output
    assert "Summary:" in result.output


def test_doctor_missing_dpkg_query_exits_nonzero(tmp_path: Path) -> None:
    result = _run_doctor(
        tmp_path,
        missing={"dpkg-query"},
        advisor_config=AdvisorConfig(provider="claude"),
    )

    assert result.exit_code == 1
    assert "Package management (core) — unavailable" in result.output
    assert "sudo apt install dpkg" in result.output


def test_doctor_unconfigured_advisor_warns_without_failing(tmp_path: Path) -> None:
    result = _run_doctor(
        tmp_path,
        advisor_error=AdvisorConfigError("Advisor config not found"),
    )

    assert result.exit_code == 0
    assert "Not configured yet" in result.output
    assert "popctl setup" in result.output
    assert "warning does not fail doctor" in result.output
    assert "codex CLI" in result.output


def test_doctor_reports_stored_api_key_without_exposing_it(tmp_path: Path) -> None:
    api_key = "api-key-canary-must-not-leak"

    result = _run_doctor(
        tmp_path,
        advisor_config=AdvisorConfig(provider="codex", api_key=api_key),
    )

    assert result.exit_code == 0
    assert "API key configured (stored in" in result.output
    assert "advisor.toml)" in result.output
    assert api_key not in result.output


def test_doctor_redacts_invalid_api_key_value(tmp_path: Path) -> None:
    canary = "api-key-canary-must-not-leak"
    (tmp_path / "advisor.toml").touch()

    result = _run_doctor(
        tmp_path,
        advisor_error=AdvisorConfigError(f"advisor.toml is invalid: api_key: {canary!r}"),
    )

    assert result.exit_code == 0
    assert "Cannot read config:" in result.output
    assert "advisor.toml is invalid" in result.output
    assert canary not in result.output


def test_doctor_lists_configured_codex_first(tmp_path: Path) -> None:
    result = _run_doctor(
        tmp_path,
        missing={"codex"},
        advisor_config=AdvisorConfig(provider="codex"),
    )

    assert result.exit_code == 0
    assert "codex CLI (configured)" in result.output
    assert "claude CLI (alternative)" in result.output
    assert "gemini CLI (alternative)" in result.output
    assert result.output.index("codex CLI (configured)") < result.output.index(
        "claude CLI (alternative)"
    )
    assert "npm install -g @openai/codex" in result.output


def test_doctor_missing_notify_send_keeps_optional_alerts_nonfatal(tmp_path: Path) -> None:
    result = _run_doctor(
        tmp_path,
        missing={"notify-send"},
        advisor_config=AdvisorConfig(provider="claude"),
    )

    assert result.exit_code == 0
    assert "Desktop alerts (optional) — unavailable" in result.output


def test_doctor_missing_alerts_config_requires_setup(tmp_path: Path) -> None:
    result = _run_doctor(
        tmp_path,
        advisor_config=AdvisorConfig(provider="claude"),
        alerts_path_exists=False,
    )

    assert result.exit_code == 0
    assert "Desktop alerts (optional) — setup required" in result.output
    assert "Setup required — config absent" in result.output
    assert "popctl alerts init-config" in result.output
    assert "popctl alerts --help" not in result.output


def test_doctor_invalid_alerts_config_requires_setup(tmp_path: Path) -> None:
    result = _run_doctor(
        tmp_path,
        advisor_config=AdvisorConfig(provider="claude"),
        alerts_config_content="ws_url = [\n",
    )

    assert result.exit_code == 0
    assert "Desktop alerts (optional) — setup required" in result.output


def test_doctor_alerts_with_no_configured_sound_player_warns(tmp_path: Path) -> None:
    result = _run_doctor(
        tmp_path,
        missing={"custom-player"},
        advisor_config=AdvisorConfig(provider="claude"),
        alerts_config_content=(
            'ws_url = "wss://alerts.example.test/"\n'
            'sound_players = ["custom-player"]\n'
        ),
    )

    assert result.exit_code == 0
    assert "Desktop alerts (optional) — warning" in result.output
    assert "No configured sound player is available" in result.output


def test_doctor_help_renders_as_a_plain_command() -> None:
    result = runner.invoke(app, ["doctor", "--help"])

    assert result.exit_code == 0
    assert "doctor [OPTIONS]" in result.output
    assert "COMMAND [ARGS]..." not in result.output


def test_doctor_shows_install_hint_for_missing_backup_binary(tmp_path: Path) -> None:
    result = _run_doctor(
        tmp_path,
        missing={"age"},
        advisor_config=AdvisorConfig(provider="claude"),
    )

    assert result.exit_code == 0
    assert "sudo apt install age" in result.output


def test_doctor_missing_backup_config_shows_setup_command(tmp_path: Path) -> None:
    result = _run_doctor(
        tmp_path,
        advisor_config=AdvisorConfig(provider="claude"),
        backup_path_exists=False,
    )

    assert result.exit_code == 0
    assert "Absent:" in result.output
    assert "popctl backup init" in result.output
