"""Unit tests for cli/display.py.

Tests for shared Rich display functions used by apply and sync commands.
"""

import io

import pytest
from popctl.cli.display import (
    create_actions_table,
    create_results_table,
    print_actions_summary,
    print_results_summary,
)
from popctl.core.theme import get_theme
from popctl.models.action import Action, ActionResult, ActionType
from popctl.models.package import PackageSource
from rich.console import Console

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def install_action() -> Action:
    """An install action for an APT package."""
    return Action(
        action_type=ActionType.INSTALL,
        package="vim",
        source=PackageSource.APT,
        reason="Package in manifest but not installed",
    )


@pytest.fixture
def remove_action() -> Action:
    """A remove action for an APT package."""
    return Action(
        action_type=ActionType.REMOVE,
        package="bloatware",
        source=PackageSource.APT,
        reason="Package marked for removal in manifest",
    )


@pytest.fixture
def purge_action() -> Action:
    """A purge action for an APT package."""
    return Action(
        action_type=ActionType.PURGE,
        package="old-tool",
        source=PackageSource.APT,
        reason="Package marked for removal in manifest",
    )


@pytest.fixture
def flatpak_remove_action() -> Action:
    """A remove action for a Flatpak package."""
    return Action(
        action_type=ActionType.REMOVE,
        package="com.example.App",
        source=PackageSource.FLATPAK,
        reason="Package marked for removal in manifest",
    )


@pytest.fixture
def success_result(install_action: Action) -> ActionResult:
    """A successful action result."""
    return ActionResult(
        action=install_action,
        success=True,
        message="Installed successfully",
    )


@pytest.fixture
def failure_result(remove_action: Action) -> ActionResult:
    """A failed action result."""
    return ActionResult(
        action=remove_action,
        success=False,
        error="Permission denied",
    )


def _capture_console_output(func: object, *args: object, **kwargs: object) -> str:
    """Capture Rich console output by temporarily replacing the console.

    Patches the module-level console used by display functions and captures
    output to a StringIO buffer.
    """
    import popctl.cli.display as display_mod
    import popctl.utils.formatting as fmt_mod

    buf = io.StringIO()
    test_console = Console(theme=get_theme(), file=buf, color_system=None)

    original_display_console = display_mod.console
    original_fmt_console = fmt_mod.console
    display_mod.console = test_console
    fmt_mod.console = test_console
    try:
        func(*args, **kwargs)  # type: ignore[operator]
    finally:
        display_mod.console = original_display_console
        fmt_mod.console = original_fmt_console

    return buf.getvalue()


# ===========================================================================
# create_actions_table
# ===========================================================================


class TestCreateActionsTable:
    """Tests for create_actions_table."""

    def test_create_actions_table_has_columns(self, install_action: Action) -> None:
        """Table has Action, Source, Package, and Reason columns."""
        table = create_actions_table([install_action])
        column_names = [col.header for col in table.columns]
        assert column_names == ["Action", "Source", "Package", "Reason"]

    def test_create_actions_table_install_row(self, install_action: Action) -> None:
        """Install action shows '+install' with 'added' style."""
        table = create_actions_table([install_action])
        assert table.row_count == 1

        # Render the table to check content
        buf = io.StringIO()
        test_console = Console(theme=get_theme(), file=buf, color_system=None)
        test_console.print(table)
        output = buf.getvalue()

        assert "+install" in output
        assert "vim" in output

    def test_create_actions_table_remove_row(self, remove_action: Action) -> None:
        """Remove action shows '-remove' with 'warning' style."""
        table = create_actions_table([remove_action])
        assert table.row_count == 1

        buf = io.StringIO()
        test_console = Console(theme=get_theme(), file=buf, color_system=None)
        test_console.print(table)
        output = buf.getvalue()

        assert "-remove" in output
        assert "bloatware" in output

    def test_create_actions_table_purge_row(self, purge_action: Action) -> None:
        """Purge action shows '-purge' with 'removed' style."""
        table = create_actions_table([purge_action])
        assert table.row_count == 1

        buf = io.StringIO()
        test_console = Console(theme=get_theme(), file=buf, color_system=None)
        test_console.print(table)
        output = buf.getvalue()

        assert "-purge" in output
        assert "old-tool" in output

    def test_create_actions_table_dry_run_title(self, install_action: Action) -> None:
        """dry_run=True produces 'Planned Actions (Dry Run)' title."""
        table = create_actions_table([install_action], dry_run=True)
        assert table.title == "Planned Actions (Dry Run)"

    def test_create_actions_table_normal_title(self, install_action: Action) -> None:
        """dry_run=False produces 'Planned Actions' title."""
        table = create_actions_table([install_action], dry_run=False)
        assert table.title == "Planned Actions"

    def test_create_actions_table_multiple_rows(
        self,
        install_action: Action,
        remove_action: Action,
        purge_action: Action,
    ) -> None:
        """Table contains one row per action."""
        table = create_actions_table([install_action, remove_action, purge_action])
        assert table.row_count == 3

    def test_create_actions_table_empty_list(self) -> None:
        """Empty action list produces a table with zero rows."""
        table = create_actions_table([])
        assert table.row_count == 0
        assert table.title == "Planned Actions"

    def test_create_actions_table_no_reason(self) -> None:
        """Action without reason shows empty string in Reason column."""
        action = Action(
            action_type=ActionType.INSTALL,
            package="curl",
            source=PackageSource.APT,
            reason=None,
        )
        table = create_actions_table([action])

        buf = io.StringIO()
        test_console = Console(theme=get_theme(), file=buf, color_system=None)
        test_console.print(table)
        output = buf.getvalue()

        assert "curl" in output
        assert "+install" in output


# ===========================================================================
# create_results_table
# ===========================================================================


class TestCreateResultsTable:
    """Tests for create_results_table."""

    def test_create_results_table_success(self, success_result: ActionResult) -> None:
        """Successful result shows 'OK' status."""
        table = create_results_table([success_result])
        assert table.row_count == 1
        assert table.title == "Results"

        buf = io.StringIO()
        test_console = Console(theme=get_theme(), file=buf, color_system=None)
        test_console.print(table)
        output = buf.getvalue()

        assert "OK" in output
        assert "vim" in output
        assert "Installed successfully" in output

    def test_create_results_table_failure(self, failure_result: ActionResult) -> None:
        """Failed result shows 'FAIL' status with error message."""
        table = create_results_table([failure_result])
        assert table.row_count == 1

        buf = io.StringIO()
        test_console = Console(theme=get_theme(), file=buf, color_system=None)
        test_console.print(table)
        output = buf.getvalue()

        assert "FAIL" in output
        assert "bloatware" in output
        assert "Permission denied" in output

    def test_create_results_table_has_columns(self, success_result: ActionResult) -> None:
        """Table has Status, Action, Package, and Message columns."""
        table = create_results_table([success_result])
        column_names = [col.header for col in table.columns]
        assert column_names == ["Status", "Action", "Package", "Message"]

    def test_create_results_table_failure_no_error(self, install_action: Action) -> None:
        """Failed result without error shows 'Unknown error'."""
        result = ActionResult(action=install_action, success=False, error=None)
        table = create_results_table([result])

        buf = io.StringIO()
        test_console = Console(theme=get_theme(), file=buf, color_system=None)
        test_console.print(table)
        output = buf.getvalue()

        assert "FAIL" in output
        assert "Unknown error" in output

    def test_create_results_table_mixed(
        self, success_result: ActionResult, failure_result: ActionResult
    ) -> None:
        """Table displays both successful and failed results."""
        table = create_results_table([success_result, failure_result])
        assert table.row_count == 2


# ===========================================================================
# print_actions_summary
# ===========================================================================


class TestPrintActionsSummary:
    """Tests for print_actions_summary."""

    def test_print_actions_summary_counts(
        self,
        install_action: Action,
        remove_action: Action,
        purge_action: Action,
    ) -> None:
        """Prints correct install/remove/purge counts."""
        actions = [install_action, remove_action, purge_action]
        output = _capture_console_output(print_actions_summary, actions)

        assert "1 to install" in output
        assert "1 to remove" in output
        assert "1 to purge" in output

    def test_print_actions_summary_empty(self) -> None:
        """No actions produces no output."""
        output = _capture_console_output(print_actions_summary, [])
        assert output.strip() == ""

    def test_print_actions_summary_install_only(self, install_action: Action) -> None:
        """Only install count shown when there are no removals."""
        output = _capture_console_output(print_actions_summary, [install_action])

        assert "1 to install" in output
        assert "to remove" not in output
        assert "to purge" not in output

    def test_print_actions_summary_multiple_install(self) -> None:
        """Multiple install actions are counted correctly."""
        actions = [
            Action(
                action_type=ActionType.INSTALL,
                package=f"pkg-{i}",
                source=PackageSource.APT,
            )
            for i in range(3)
        ]
        output = _capture_console_output(print_actions_summary, actions)
        assert "3 to install" in output


# ===========================================================================
# print_results_summary
# ===========================================================================


class TestPrintResultsSummary:
    """Tests for print_results_summary."""

    def test_print_results_summary_all_success(self, success_result: ActionResult) -> None:
        """All success shows success message."""
        output = _capture_console_output(print_results_summary, [success_result])
        assert "All 1 action(s) completed successfully" in output

    def test_print_results_summary_mixed(
        self, success_result: ActionResult, failure_result: ActionResult
    ) -> None:
        """Mixed results shows succeeded/failed counts."""
        output = _capture_console_output(print_results_summary, [success_result, failure_result])
        assert "1 succeeded" in output
        assert "1 failed" in output

    def test_print_results_summary_all_failed(self, failure_result: ActionResult) -> None:
        """All failures shows 0 succeeded and failure count."""
        output = _capture_console_output(print_results_summary, [failure_result])
        assert "0 succeeded" in output
        assert "1 failed" in output

    def test_print_results_summary_multiple_success(self) -> None:
        """Multiple successful results counted correctly."""
        actions = [
            Action(
                action_type=ActionType.INSTALL,
                package=f"pkg-{i}",
                source=PackageSource.APT,
            )
            for i in range(5)
        ]
        results = [ActionResult(action=a, success=True, message="OK") for a in actions]
        output = _capture_console_output(print_results_summary, results)
        assert "All 5 action(s) completed successfully" in output
