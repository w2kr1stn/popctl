"""Unit tests for Action models.

Tests for the Action and ActionResult data structures.
"""

import pytest
from popctl.models.action import (
    Action,
    ActionResult,
    ActionType,
    create_install_action,
    create_remove_action,
)
from popctl.models.package import PackageSource


class TestActionType:
    """Tests for ActionType enum."""

    def test_action_type_values(self) -> None:
        """ActionType has expected values."""
        assert ActionType.INSTALL.value == "install"
        assert ActionType.REMOVE.value == "remove"
        assert ActionType.PURGE.value == "purge"

    def test_action_type_count(self) -> None:
        """ActionType has exactly 3 members."""
        assert len(ActionType) == 3


class TestAction:
    """Tests for Action dataclass."""

    def test_create_install_action(self) -> None:
        """Can create an install action."""
        action = Action(
            action_type=ActionType.INSTALL,
            package="htop",
            source=PackageSource.APT,
        )
        assert action.action_type == ActionType.INSTALL
        assert action.package == "htop"
        assert action.source == PackageSource.APT
        assert action.reason is None

    def test_create_action_with_reason(self) -> None:
        """Can create an action with reason."""
        action = Action(
            action_type=ActionType.REMOVE,
            package="bloatware",
            source=PackageSource.APT,
            reason="User requested removal",
        )
        assert action.reason == "User requested removal"

    def test_action_is_frozen(self) -> None:
        """Action is immutable."""
        action = Action(
            action_type=ActionType.INSTALL,
            package="htop",
            source=PackageSource.APT,
        )
        with pytest.raises(AttributeError):
            action.package = "other"  # type: ignore[misc]

    def test_action_empty_package_raises(self) -> None:
        """Action with empty package name raises ValueError."""
        with pytest.raises(ValueError, match="Package name cannot be empty"):
            Action(
                action_type=ActionType.INSTALL,
                package="",
                source=PackageSource.APT,
            )

    def test_is_install_property(self) -> None:
        """is_install returns True for install actions."""
        action = Action(
            action_type=ActionType.INSTALL,
            package="htop",
            source=PackageSource.APT,
        )
        assert action.is_install is True
        assert action.is_remove is False
        assert action.is_purge is False

    def test_is_remove_property(self) -> None:
        """is_remove returns True for remove actions."""
        action = Action(
            action_type=ActionType.REMOVE,
            package="htop",
            source=PackageSource.APT,
        )
        assert action.is_remove is True
        assert action.is_install is False
        assert action.is_purge is False

    def test_is_purge_property(self) -> None:
        """is_purge returns True for purge actions."""
        action = Action(
            action_type=ActionType.PURGE,
            package="htop",
            source=PackageSource.APT,
        )
        assert action.is_purge is True
        assert action.is_install is False
        assert action.is_remove is False

    def test_is_destructive_for_remove(self) -> None:
        """is_destructive returns True for remove actions."""
        action = Action(
            action_type=ActionType.REMOVE,
            package="htop",
            source=PackageSource.APT,
        )
        assert action.is_destructive is True

    def test_is_destructive_for_purge(self) -> None:
        """is_destructive returns True for purge actions."""
        action = Action(
            action_type=ActionType.PURGE,
            package="htop",
            source=PackageSource.APT,
        )
        assert action.is_destructive is True

    def test_is_destructive_false_for_install(self) -> None:
        """is_destructive returns False for install actions."""
        action = Action(
            action_type=ActionType.INSTALL,
            package="htop",
            source=PackageSource.APT,
        )
        assert action.is_destructive is False


class TestActionResult:
    """Tests for ActionResult dataclass."""

    @pytest.fixture
    def sample_action(self) -> Action:
        """Create a sample action for testing."""
        return Action(
            action_type=ActionType.INSTALL,
            package="htop",
            source=PackageSource.APT,
        )

    def test_create_success_result(self, sample_action: Action) -> None:
        """Can create a successful result."""
        result = ActionResult(
            action=sample_action,
            success=True,
            message="Package installed",
        )
        assert result.success is True
        assert result.message == "Package installed"
        assert result.error is None

    def test_create_failure_result(self, sample_action: Action) -> None:
        """Can create a failure result."""
        result = ActionResult(
            action=sample_action,
            success=False,
            error="Package not found",
        )
        assert result.success is False
        assert result.error == "Package not found"
        assert result.message is None

    def test_result_is_frozen(self, sample_action: Action) -> None:
        """ActionResult is immutable."""
        result = ActionResult(action=sample_action, success=True)
        with pytest.raises(AttributeError):
            result.success = False  # type: ignore[misc]

    def test_failed_property(self, sample_action: Action) -> None:
        """failed property returns opposite of success."""
        success_result = ActionResult(action=sample_action, success=True)
        failure_result = ActionResult(action=sample_action, success=False)

        assert success_result.failed is False
        assert failure_result.failed is True


class TestActionFactories:
    """Tests for action factory functions."""

    def test_create_install_action_factory(self) -> None:
        """create_install_action creates correct action."""
        action = create_install_action(
            package="htop",
            source=PackageSource.APT,
            reason="Missing package",
        )
        assert action.action_type == ActionType.INSTALL
        assert action.package == "htop"
        assert action.source == PackageSource.APT
        assert action.reason == "Missing package"

    def test_create_remove_action_factory(self) -> None:
        """create_remove_action creates remove action by default."""
        action = create_remove_action(
            package="bloatware",
            source=PackageSource.APT,
        )
        assert action.action_type == ActionType.REMOVE
        assert action.package == "bloatware"

    def test_create_remove_action_with_purge(self) -> None:
        """create_remove_action with purge=True creates purge action."""
        action = create_remove_action(
            package="bloatware",
            source=PackageSource.APT,
            purge=True,
        )
        assert action.action_type == ActionType.PURGE

    def test_create_flatpak_action(self) -> None:
        """Can create actions for Flatpak packages."""
        action = create_install_action(
            package="com.spotify.Client",
            source=PackageSource.FLATPAK,
        )
        assert action.source == PackageSource.FLATPAK
        assert action.package == "com.spotify.Client"
