"""Unit tests for Action models.

Tests for the Action and ActionResult data structures.
"""

import pytest
from popctl.models.action import (
    Action,
    ActionResult,
    ActionType,
)
from popctl.models.package import PackageSource


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

    def test_purge_valid_for_snap(self) -> None:
        """PURGE action is valid for SNAP packages."""
        action = Action(
            action_type=ActionType.PURGE,
            package="firefox",
            source=PackageSource.SNAP,
        )
        assert action.action_type == ActionType.PURGE
        assert action.source == PackageSource.SNAP

    def test_purge_invalid_for_flatpak(self) -> None:
        """PURGE action raises ValueError for FLATPAK packages."""
        with pytest.raises(
            ValueError, match="PURGE action is only valid for APT and SNAP packages"
        ):
            Action(
                action_type=ActionType.PURGE,
                package="com.spotify.Client",
                source=PackageSource.FLATPAK,
            )


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
            detail="Package installed",
        )
        assert result.success is True
        assert result.detail == "Package installed"

    def test_create_failure_result(self, sample_action: Action) -> None:
        """Can create a failure result."""
        result = ActionResult(
            action=sample_action,
            success=False,
            detail="Package not found",
        )
        assert result.success is False
        assert result.detail == "Package not found"

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
