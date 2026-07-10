"""Unit tests for AptOperator.

Tests for the APT package operator implementation.
"""

from unittest.mock import patch

import pytest
from popctl.models.action import ActionType
from popctl.models.package import PackageSource
from popctl.operators.apt import AptOperator
from popctl.utils.shell import CommandResult


def _simulation_result(*packages: str) -> CommandResult:
    return CommandResult(
        stdout="\n".join(f"Remv {package} [1.0]" for package in packages),
        stderr="",
        returncode=0,
    )


def _purge_simulation_result(*packages: str) -> CommandResult:
    """Return the shape emitted by `apt-get -s purge` in this environment."""
    return CommandResult(
        stdout="\n".join(
            [
                "NOTE: This is only a simulation!",
                "      apt-get needs root privileges for real execution.",
                "      Keep also in mind that locking is deactivated,",
                "      so don't depend on the relevance to the real current situation!",
                "Reading package lists...",
                "Building dependency tree...",
                "Reading state information...",
                "The following packages will be REMOVED:",
                *(f"  {package.split(':', 1)[0]}*" for package in packages),
                (
                    "0 upgraded, 0 newly installed, "
                    f"{len(packages)} to remove and 0 not upgraded."
                ),
                *(f"Purg {package} [1.0]" for package in packages),
            ]
        ),
        stderr="",
        returncode=0,
    )


class TestAptOperator:
    """Tests for AptOperator class."""

    @pytest.fixture
    def operator(self) -> AptOperator:
        """Create AptOperator instance."""
        return AptOperator()

    @pytest.fixture
    def dry_run_operator(self) -> AptOperator:
        """Create AptOperator in dry-run mode."""
        return AptOperator(dry_run=True)

    def test_source_is_apt(self, operator: AptOperator) -> None:
        """Operator returns APT as source."""
        assert operator.source == PackageSource.APT

    def test_is_available_when_apt_exists(self, operator: AptOperator) -> None:
        """is_available returns True when apt-get exists."""
        with patch("popctl.operators.apt.command_exists", return_value=True):
            assert operator.is_available() is True

    def test_is_available_when_apt_missing(self, operator: AptOperator) -> None:
        """is_available returns False when apt-get is missing."""
        with patch("popctl.operators.apt.command_exists", return_value=False):
            assert operator.is_available() is False

    def test_install_success(self, operator: AptOperator) -> None:
        """install() returns success results on apt-get success."""
        with (
            patch("popctl.operators.apt.command_exists", return_value=True),
            patch("popctl.operators.apt.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(stdout="", stderr="", returncode=0)

            results = operator.install(["htop", "neovim"])

        assert len(results) == 2
        assert all(r.success for r in results)
        assert results[0].action.package == "htop"
        assert results[1].action.package == "neovim"
        assert all(r.action.action_type == ActionType.INSTALL for r in results)

        # Verify correct command was called
        mock_run.assert_called_once()
        args = mock_run.call_args[0][0]
        assert "sudo" in args
        assert "apt-get" in args
        assert "install" in args
        assert "-y" in args
        assert "htop" in args
        assert "neovim" in args

    def test_install_failure(self, operator: AptOperator) -> None:
        """install() returns failure results on apt-get failure."""
        with (
            patch("popctl.operators.apt.command_exists", return_value=True),
            patch("popctl.operators.apt.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(
                stdout="", stderr="E: Package htop not found", returncode=100
            )

            results = operator.install(["htop"])

        assert len(results) == 1
        assert results[0].success is False
        assert "not found" in results[0].detail.lower()

    def test_install_dry_run(self, dry_run_operator: AptOperator) -> None:
        """install() in dry-run mode uses --dry-run flag."""
        with (
            patch("popctl.operators.apt.command_exists", return_value=True),
            patch("popctl.operators.apt.run_command") as mock_run,
        ):
            mock_run.return_value = CommandResult(stdout="", stderr="", returncode=0)

            results = dry_run_operator.install(["htop"])

        assert len(results) == 1
        assert results[0].success is True
        assert "Dry-run" in results[0].detail

        # Verify --dry-run was in command
        args = mock_run.call_args[0][0]
        assert "--dry-run" in args

    def test_install_empty_list(self, operator: AptOperator) -> None:
        """install() with empty list returns empty results."""
        with patch("popctl.operators.apt.command_exists", return_value=True):
            results = operator.install([])

        assert results == []

    def test_remove_success(self, operator: AptOperator) -> None:
        """remove() executes after a safe simulated transaction."""
        with (
            patch("popctl.operators.apt.command_exists", return_value=True),
            patch("popctl.operators.apt.run_command") as mock_run,
        ):
            mock_run.side_effect = [
                _purge_simulation_result("bloatware"),
                CommandResult(stdout="", stderr="", returncode=0),
            ]

            results = operator.remove(["bloatware"])

        assert len(results) == 1
        assert results[0].success is True
        assert results[0].action.action_type == ActionType.REMOVE

        assert mock_run.call_args_list[0].args[0] == [
            "apt-get",
            "-s",
            "remove",
            "--",
            "bloatware",
        ]
        assert mock_run.call_args_list[1].args[0] == [
            "sudo",
            "apt-get",
            "remove",
            "-y",
            "--",
            "bloatware",
        ]

    def test_remove_with_purge(self, operator: AptOperator) -> None:
        """remove() with purge=True simulates and then uses purge."""
        with (
            patch("popctl.operators.apt.command_exists", return_value=True),
            patch("popctl.operators.apt.run_command") as mock_run,
        ):
            mock_run.side_effect = [
                _simulation_result("bloatware"),
                CommandResult(stdout="", stderr="", returncode=0),
            ]

            results = operator.remove(["bloatware"], purge=True)

        assert len(results) == 1
        assert results[0].action.action_type == ActionType.PURGE

        assert mock_run.call_args_list[0].args[0] == [
            "apt-get",
            "-s",
            "purge",
            "--",
            "bloatware",
        ]
        assert mock_run.call_args_list[1].args[0][2] == "purge"

    def test_remove_failure(self, operator: AptOperator) -> None:
        """remove() returns failure results on apt-get failure."""
        with (
            patch("popctl.operators.apt.command_exists", return_value=True),
            patch("popctl.operators.apt.run_command") as mock_run,
        ):
            mock_run.side_effect = [
                _simulation_result("bloatware"),
                CommandResult(
                    stdout="", stderr="E: Package bloatware is not installed", returncode=100
                ),
            ]

            results = operator.remove(["bloatware"])

        assert len(results) == 1
        assert results[0].success is False

    def test_remove_dry_run(self, dry_run_operator: AptOperator) -> None:
        """remove() in dry-run mode uses --dry-run flag."""
        with (
            patch("popctl.operators.apt.command_exists", return_value=True),
            patch("popctl.operators.apt.run_command") as mock_run,
        ):
            mock_run.side_effect = [
                _simulation_result("bloatware"),
                CommandResult(stdout="", stderr="", returncode=0),
            ]

            dry_run_operator.remove(["bloatware"])

        args = mock_run.call_args_list[1].args[0]
        assert "--dry-run" in args

    def test_remove_empty_list(self, operator: AptOperator) -> None:
        """remove() with empty list returns empty results."""
        with patch("popctl.operators.apt.command_exists", return_value=True):
            results = operator.remove([])

        assert results == []

    def test_batch_failure_falls_back_to_single(self, operator: AptOperator) -> None:
        """When batch remove fails, each package is retried individually."""
        batch_fail = CommandResult(
            stdout="", stderr="E: pkgProblemResolver::Resolve", returncode=100
        )
        single_ok = CommandResult(stdout="", stderr="", returncode=0)

        with patch("popctl.operators.apt.run_command") as mock_run:
            mock_run.side_effect = [
                _simulation_result("pkg-a", "pkg-b", "pkg-c"),
                batch_fail,
                _simulation_result("pkg-a"),
                single_ok,
                _simulation_result("pkg-b"),
                single_ok,
                _simulation_result("pkg-c"),
                single_ok,
            ]

            results = operator.remove(["pkg-a", "pkg-b", "pkg-c"])

        assert len(results) == 3
        assert all(r.success for r in results)
        assert mock_run.call_count == 8  # batch + each single op are simulated first
        assert mock_run.call_args_list[2].args[0] == ["apt-get", "-s", "remove", "--", "pkg-a"]

    def test_batch_failure_partial_single_success(self, operator: AptOperator) -> None:
        """Fallback reports per-package success/failure accurately."""
        batch_fail = CommandResult(stdout="", stderr="E: resolver error", returncode=100)
        single_ok = CommandResult(stdout="", stderr="", returncode=0)
        single_fail = CommandResult(stdout="", stderr="E: broken dependency", returncode=100)

        with patch("popctl.operators.apt.run_command") as mock_run:
            mock_run.side_effect = [
                _simulation_result("good-pkg", "bad-pkg"),
                batch_fail,
                _simulation_result("good-pkg"),
                single_ok,
                _simulation_result("bad-pkg"),
                single_fail,
            ]

            results = operator.remove(["good-pkg", "bad-pkg"])

        assert len(results) == 2
        assert results[0].success is True
        assert results[1].success is False
        assert "broken dependency" in results[1].detail

    def test_single_package_failure_no_fallback(self, operator: AptOperator) -> None:
        """Single-package failure does not trigger fallback."""
        with patch("popctl.operators.apt.run_command") as mock_run:
            mock_run.side_effect = [
                _simulation_result("only-pkg"),
                CommandResult(stdout="", stderr="E: not installed", returncode=100),
            ]

            results = operator.remove(["only-pkg"])

        assert len(results) == 1
        assert results[0].success is False
        assert mock_run.call_count == 2  # simulation plus no fallback attempt

    def test_remove_refuses_protected_dependent_from_simulation(
        self, operator: AptOperator
    ) -> None:
        """A protected dependent in the simulated transaction blocks every requested remove."""
        with patch("popctl.operators.apt.run_command") as mock_run:
            mock_run.return_value = _simulation_result("dconf-gsettings-backend", "gnome-shell")

            results = operator.remove(["dconf-gsettings-backend", "other-package"])

        assert len(results) == 2
        assert all(result.success is False for result in results)
        assert all("gnome-shell" in (result.detail or "") for result in results)
        mock_run.assert_called_once_with(
            ["apt-get", "-s", "remove", "--", "dconf-gsettings-backend", "other-package"],
            timeout=operator._TIMEOUT,
        )

    def test_remove_refuses_when_simulation_fails(self, operator: AptOperator) -> None:
        """A failed simulation never falls through to the real remove command."""
        with patch("popctl.operators.apt.run_command") as mock_run:
            mock_run.return_value = CommandResult(
                stdout="", stderr="E: Unable to read package cache", returncode=100
            )

            results = operator.remove(["bloatware"])

        assert len(results) == 1
        assert results[0].success is False
        assert "simulation failed" in (results[0].detail or "")
        assert "Unable to read package cache" in (results[0].detail or "")
        mock_run.assert_called_once()

    def test_purge_refuses_protected_dependent_from_simulation(self, operator: AptOperator) -> None:
        """Purge uses the same transaction guard as remove."""
        with patch("popctl.operators.apt.run_command") as mock_run:
            mock_run.return_value = _purge_simulation_result(
                "dconf-gsettings-backend", "gnome-shell:amd64"
            )

            results = operator.remove(["dconf-gsettings-backend"], purge=True)

        assert len(results) == 1
        assert results[0].success is False
        assert "gnome-shell" in (results[0].detail or "")
        mock_run.assert_called_once_with(
            ["apt-get", "-s", "purge", "--", "dconf-gsettings-backend"],
            timeout=operator._TIMEOUT,
        )

    def test_purge_allows_mixed_remove_and_purge_actions(self, operator: AptOperator) -> None:
        """A purge simulation may contain both Remv and Purg action lines."""
        simulation = CommandResult(
            stdout="\n".join(
                [
                    "Reading package lists...",
                    "Building dependency tree...",
                    "Reading state information...",
                    "The following packages will be REMOVED:",
                    "  bloatware* old-config*",
                    "0 upgraded, 0 newly installed, 2 to remove and 0 not upgraded.",
                    "Remv bloatware [1.0]",
                    "Purg old-config [1.0]",
                ]
            ),
            stderr="",
            returncode=0,
        )
        with patch("popctl.operators.apt.run_command") as mock_run:
            mock_run.side_effect = [
                simulation,
                CommandResult(stdout="", stderr="", returncode=0),
            ]

            results = operator.remove(["bloatware"], purge=True)

        assert len(results) == 1
        assert results[0].success is True
        assert mock_run.call_count == 2

    def test_remove_refuses_positive_summary_with_unparseable_actions(
        self, operator: AptOperator
    ) -> None:
        """A positive removal summary without matching action lines fails closed."""
        with patch("popctl.operators.apt.run_command") as mock_run:
            mock_run.return_value = CommandResult(
                stdout="\n".join(
                    [
                        "The following packages will be REMOVED:",
                        "  bloatware*",
                        "0 upgraded, 0 newly installed, 1 to remove and 0 not upgraded.",
                        "Removing bloatware",
                    ]
                ),
                stderr="",
                returncode=0,
            )

            results = operator.remove(["bloatware"])

        assert len(results) == 1
        assert results[0].success is False
        assert "could not be parsed" in (results[0].detail or "")
        mock_run.assert_called_once()

    def test_remove_allows_zero_removal_summary(self, operator: AptOperator) -> None:
        """An empty, successful transaction is valid when the summary reports zero removals."""
        with patch("popctl.operators.apt.run_command") as mock_run:
            mock_run.side_effect = [
                CommandResult(
                    stdout="0 upgraded, 0 newly installed, 0 to remove and 0 not upgraded.",
                    stderr="",
                    returncode=0,
                ),
                CommandResult(stdout="", stderr="", returncode=0),
            ]

            results = operator.remove(["bloatware"])

        assert len(results) == 1
        assert results[0].success is True
        assert mock_run.call_count == 2

    def test_remove_guard_strips_architecture_suffixes(self, operator: AptOperator) -> None:
        """Architecture-qualified packages are checked against protection rules by base name."""
        with patch("popctl.operators.apt.run_command") as mock_run:
            mock_run.return_value = _simulation_result(
                "dconf-gsettings-backend", "gnome-shell:amd64"
            )

            results = operator.remove(["dconf-gsettings-backend"])

        assert len(results) == 1
        assert results[0].success is False
        assert "gnome-shell" in (results[0].detail or "")
        mock_run.assert_called_once()

    def test_remove_refuses_unparseable_simulation_output(self, operator: AptOperator) -> None:
        """Unexpected successful simulation output still fails closed."""
        with patch("popctl.operators.apt.run_command") as mock_run:
            mock_run.return_value = CommandResult(
                stdout="unexpected output", stderr="", returncode=0
            )

            results = operator.remove(["bloatware"])

        assert len(results) == 1
        assert results[0].success is False
        assert "could not be parsed" in (results[0].detail or "")
        mock_run.assert_called_once()
