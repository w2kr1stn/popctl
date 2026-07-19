from unittest.mock import patch

import pytest
from popctl.advisor.config import load_advisor_config
from popctl.cli.main import app
from popctl.core.paths import get_config_dir
from typer.testing import CliRunner

runner = CliRunner()
_UBUNTU_OS_RELEASE = {"ID": "ubuntu", "ID_LIKE": "debian"}


def test_setup_persists_selected_provider() -> None:
    with (
        patch("popctl.cli.commands.setup._is_interactive", return_value=True),
        patch(
            "popctl.cli.commands.setup.platform.freedesktop_os_release",
            return_value=_UBUNTU_OS_RELEASE,
        ),
        patch("popctl.cli.commands.setup.command_exists", return_value=True),
        patch("popctl.cli.commands.setup.manifest_exists", return_value=True),
    ):
        result = runner.invoke(app, ["setup"], input="gemini\n1\ny\nn\nn\nn\n")

    assert result.exit_code == 0
    assert load_advisor_config().provider == "gemini"
    assert "CLI (command-line app)" in result.output


def test_setup_saves_hidden_api_key() -> None:
    api_key = "secret-api-key"
    with (
        patch("popctl.cli.commands.setup._is_interactive", return_value=True),
        patch(
            "popctl.cli.commands.setup.platform.freedesktop_os_release",
            return_value=_UBUNTU_OS_RELEASE,
        ),
        patch("popctl.cli.commands.setup.command_exists", return_value=True),
        patch("popctl.cli.commands.setup.manifest_exists", return_value=True),
    ):
        result = runner.invoke(app, ["setup"], input=f"codex\n2\n{api_key}\ny\nn\nn\nn\n")

    assert result.exit_code == 0
    config_path = get_config_dir() / "advisor.toml"
    assert api_key in config_path.read_text(encoding="utf-8")
    assert load_advisor_config().api_key == api_key
    assert api_key not in result.stdout
    assert "stored in ~/.config/popctl/advisor.toml, readable only by you" in result.output


def test_setup_saves_advisor_config_once_after_confirmation() -> None:
    with (
        patch("popctl.cli.commands.setup._is_interactive", return_value=True),
        patch(
            "popctl.cli.commands.setup.platform.freedesktop_os_release",
            return_value=_UBUNTU_OS_RELEASE,
        ),
        patch("popctl.cli.commands.setup.command_exists", return_value=True),
        patch("popctl.cli.commands.setup.manifest_exists", return_value=True),
        patch("popctl.cli.commands.setup.save_advisor_config") as save_advisor_config,
    ):
        result = runner.invoke(app, ["setup"], input="\n\ny\nn\nn\nn\n")

    assert result.exit_code == 0
    save_advisor_config.assert_called_once()


def test_setup_fails_when_core_binary_is_missing() -> None:
    def command_is_available(command: str) -> bool:
        return command != "dpkg-query"

    with (
        patch("popctl.cli.commands.setup._is_interactive", return_value=True),
        patch(
            "popctl.cli.commands.setup.platform.freedesktop_os_release",
            return_value=_UBUNTU_OS_RELEASE,
        ),
        patch("popctl.cli.commands.setup.command_exists", side_effect=command_is_available),
    ):
        result = runner.invoke(app, ["setup"])

    assert result.exit_code == 1
    assert "dpkg-query" in result.output
    assert "popctl doctor" in result.output


@pytest.mark.parametrize(
    "os_release",
    [
        {"ID": "ubuntu", "ID_LIKE": "debian"},
        {"ID": "pop", "ID_LIKE": "ubuntu debian"},
    ],
)
def test_setup_accepts_debian_or_ubuntu_based_distributions(
    os_release: dict[str, str],
) -> None:
    from popctl.cli.commands.setup import _check_core_binaries

    with (
        patch(
            "popctl.cli.commands.setup.platform.freedesktop_os_release",
            return_value=os_release,
        ),
        patch("popctl.cli.commands.setup.command_exists", return_value=True) as command_exists,
    ):
        assert _check_core_binaries()

    assert command_exists.call_count == 4


def test_setup_rejects_unsupported_distribution_before_binary_probe() -> None:
    with (
        patch("popctl.cli.commands.setup._is_interactive", return_value=True),
        patch(
            "popctl.cli.commands.setup.platform.freedesktop_os_release",
            return_value={"ID": "fedora", "ID_LIKE": "fedora"},
        ),
        patch("popctl.cli.commands.setup.command_exists") as command_exists,
    ):
        result = runner.invoke(app, ["setup"])

    assert result.exit_code == 1
    assert "targets Debian/Ubuntu-based systems" in result.output
    assert "Detected distribution: fedora" in result.output
    assert "Missing core tools" not in result.output
    command_exists.assert_not_called()


def test_setup_handles_unavailable_os_release_metadata() -> None:
    with (
        patch("popctl.cli.commands.setup._is_interactive", return_value=True),
        patch(
            "popctl.cli.commands.setup.platform.freedesktop_os_release",
            side_effect=FileNotFoundError,
        ),
        patch("popctl.cli.commands.setup.command_exists") as command_exists,
    ):
        result = runner.invoke(app, ["setup"])

    assert result.exit_code == 1
    assert "Detected distribution: unknown" in result.output
    assert "Missing core tools" not in result.output
    command_exists.assert_not_called()


def test_setup_prints_static_guide_without_a_tty() -> None:
    with patch("popctl.cli.commands.setup.command_exists") as command_exists:
        result = runner.invoke(app, ["setup"])

    assert result.exit_code == 0
    assert "interactive terminal" in result.stdout
    assert "1. Check your system: popctl doctor" in result.stdout
    assert "3. Create a manifest: popctl init" in result.stdout
    assert "7. Set up private dotfiles: popctl dotfiles init" in result.stdout
    command_exists.assert_not_called()


def test_setup_offers_manifest_creation_when_missing() -> None:
    with (
        patch("popctl.cli.commands.setup._is_interactive", return_value=True),
        patch(
            "popctl.cli.commands.setup.platform.freedesktop_os_release",
            return_value=_UBUNTU_OS_RELEASE,
        ),
        patch("popctl.cli.commands.setup.command_exists", return_value=True),
        patch("popctl.cli.commands.setup.manifest_exists", return_value=False),
        patch("popctl.cli.commands.setup.init_manifest") as init_manifest,
    ):
        result = runner.invoke(app, ["setup"], input="\n\ny\ny\nn\nn\nn\n")

    assert result.exit_code == 0
    init_manifest.assert_called_once_with()


def test_setup_skips_manifest_creation_when_present() -> None:
    with (
        patch("popctl.cli.commands.setup._is_interactive", return_value=True),
        patch(
            "popctl.cli.commands.setup.platform.freedesktop_os_release",
            return_value=_UBUNTU_OS_RELEASE,
        ),
        patch("popctl.cli.commands.setup.command_exists", return_value=True),
        patch("popctl.cli.commands.setup.manifest_exists", return_value=True),
        patch("popctl.cli.commands.setup.init_manifest") as init_manifest,
    ):
        result = runner.invoke(app, ["setup"], input="\n\ny\nn\nn\nn\n")

    assert result.exit_code == 0
    init_manifest.assert_not_called()


def test_setup_skips_advisor_without_creating_config() -> None:
    config_path = get_config_dir() / "advisor.toml"
    with (
        patch("popctl.cli.commands.setup._is_interactive", return_value=True),
        patch(
            "popctl.cli.commands.setup.platform.freedesktop_os_release",
            return_value=_UBUNTU_OS_RELEASE,
        ),
        patch("popctl.cli.commands.setup.command_exists", return_value=True),
        patch("popctl.cli.commands.setup.manifest_exists", return_value=True),
    ):
        result = runner.invoke(app, ["setup"], input="skip\nn\nn\nn\n")

    assert result.exit_code == 0
    assert "AI advisor: skipped" in result.output
    assert not config_path.exists()


def test_setup_abort_before_advisor_selection_keeps_config_absent() -> None:
    config_path = get_config_dir() / "advisor.toml"
    with (
        patch("popctl.cli.commands.setup._is_interactive", return_value=True),
        patch(
            "popctl.cli.commands.setup.platform.freedesktop_os_release",
            return_value=_UBUNTU_OS_RELEASE,
        ),
        patch("popctl.cli.commands.setup.command_exists", return_value=True),
    ):
        result = runner.invoke(app, ["setup"], input="")

    assert result.exit_code != 0
    assert not config_path.exists()


def test_setup_recommends_manifest_before_sync_when_manifest_is_skipped() -> None:
    with (
        patch("popctl.cli.commands.setup._is_interactive", return_value=True),
        patch(
            "popctl.cli.commands.setup.platform.freedesktop_os_release",
            return_value=_UBUNTU_OS_RELEASE,
        ),
        patch("popctl.cli.commands.setup.command_exists", return_value=True),
        patch("popctl.cli.commands.setup.manifest_exists", return_value=False),
    ):
        result = runner.invoke(app, ["setup"], input="\n\ny\nn\nn\nn\nn\n")

    assert result.exit_code == 0
    assert "run popctl init first" in result.output
    assert "popctl sync can also create a manifest" in result.output
    assert "on its first" in result.output


def test_setup_offers_dotfiles_initialization_when_accepted() -> None:
    with (
        patch("popctl.cli.commands.setup._is_interactive", return_value=True),
        patch(
            "popctl.cli.commands.setup.platform.freedesktop_os_release",
            return_value=_UBUNTU_OS_RELEASE,
        ),
        patch("popctl.cli.commands.setup.command_exists", return_value=True),
        patch("popctl.cli.commands.setup.manifest_exists", return_value=True),
        patch("popctl.cli.commands.setup.init_dotfiles") as init_dotfiles,
    ):
        result = runner.invoke(app, ["setup"], input="\n\ny\nn\nn\ny\n")

    assert result.exit_code == 0
    assert "Private dotfiles: initialized" in result.output
    init_dotfiles.assert_called_once_with()


def test_setup_skips_dotfiles_initialization_when_declined() -> None:
    with (
        patch("popctl.cli.commands.setup._is_interactive", return_value=True),
        patch(
            "popctl.cli.commands.setup.platform.freedesktop_os_release",
            return_value=_UBUNTU_OS_RELEASE,
        ),
        patch("popctl.cli.commands.setup.command_exists", return_value=True),
        patch("popctl.cli.commands.setup.manifest_exists", return_value=True),
        patch("popctl.cli.commands.setup.init_dotfiles") as init_dotfiles,
    ):
        result = runner.invoke(app, ["setup"], input="\n\ny\nn\nn\nn\n")

    assert result.exit_code == 0
    assert "Private dotfiles: skipped" in result.output
    init_dotfiles.assert_not_called()
