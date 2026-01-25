"""Unit tests for Operator base class.

Tests for the abstract Operator interface.
"""

import pytest

from popctl.models.action import Action, ActionResult, ActionType
from popctl.models.package import PackageSource
from popctl.operators.base import Operator


class ConcreteOperator(Operator):
    """Concrete implementation for testing the base class."""

    def __init__(self, dry_run: bool = False, available: bool = True) -> None:
        super().__init__(dry_run)
        self._available = available
        self.install_calls: list[list[str]] = []
        self.remove_calls: list[tuple[list[str], bool]] = []

    @property
    def source(self) -> PackageSource:
        return PackageSource.APT

    def is_available(self) -> bool:
        return self._available

    def install(self, packages: list[str]) -> list[ActionResult]:
        self.install_calls.append(packages)
        return [
            ActionResult(
                action=Action(
                    action_type=ActionType.INSTALL,
                    package=pkg,
                    source=PackageSource.APT,
                ),
                success=True,
            )
            for pkg in packages
        ]

    def remove(self, packages: list[str], purge: bool = False) -> list[ActionResult]:
        self.remove_calls.append((packages, purge))
        action_type = ActionType.PURGE if purge else ActionType.REMOVE
        return [
            ActionResult(
                action=Action(
                    action_type=action_type,
                    package=pkg,
                    source=PackageSource.APT,
                ),
                success=True,
            )
            for pkg in packages
        ]


class TestOperatorBase:
    """Tests for Operator base class."""

    def test_dry_run_default_false(self) -> None:
        """Operator dry_run defaults to False."""
        operator = ConcreteOperator()
        assert operator.dry_run is False

    def test_dry_run_can_be_enabled(self) -> None:
        """Operator dry_run can be set to True."""
        operator = ConcreteOperator(dry_run=True)
        assert operator.dry_run is True

    def test_execute_groups_installs(self) -> None:
        """execute() groups install actions together."""
        operator = ConcreteOperator()
        actions = [
            Action(action_type=ActionType.INSTALL, package="pkg1", source=PackageSource.APT),
            Action(action_type=ActionType.INSTALL, package="pkg2", source=PackageSource.APT),
        ]

        operator.execute(actions)

        assert len(operator.install_calls) == 1
        assert operator.install_calls[0] == ["pkg1", "pkg2"]

    def test_execute_groups_removes(self) -> None:
        """execute() groups remove actions together."""
        operator = ConcreteOperator()
        actions = [
            Action(action_type=ActionType.REMOVE, package="pkg1", source=PackageSource.APT),
            Action(action_type=ActionType.REMOVE, package="pkg2", source=PackageSource.APT),
        ]

        operator.execute(actions)

        assert len(operator.remove_calls) == 1
        assert operator.remove_calls[0] == (["pkg1", "pkg2"], False)

    def test_execute_groups_purges_separately(self) -> None:
        """execute() groups purge actions separately from removes."""
        operator = ConcreteOperator()
        actions = [
            Action(action_type=ActionType.REMOVE, package="pkg1", source=PackageSource.APT),
            Action(action_type=ActionType.PURGE, package="pkg2", source=PackageSource.APT),
        ]

        operator.execute(actions)

        assert len(operator.remove_calls) == 2
        assert operator.remove_calls[0] == (["pkg1"], False)
        assert operator.remove_calls[1] == (["pkg2"], True)

    def test_execute_raises_when_unavailable(self) -> None:
        """execute() raises RuntimeError when operator unavailable."""
        operator = ConcreteOperator(available=False)
        actions = [
            Action(action_type=ActionType.INSTALL, package="pkg1", source=PackageSource.APT),
        ]

        with pytest.raises(RuntimeError, match="not available"):
            operator.execute(actions)

    def test_execute_raises_on_source_mismatch(self) -> None:
        """execute() raises ValueError when action source doesn't match."""
        operator = ConcreteOperator()  # APT operator
        actions = [
            Action(action_type=ActionType.INSTALL, package="com.spotify.Client", source=PackageSource.FLATPAK),
        ]

        with pytest.raises(ValueError, match="doesn't match"):
            operator.execute(actions)

    def test_execute_returns_all_results(self) -> None:
        """execute() returns results for all actions."""
        operator = ConcreteOperator()
        actions = [
            Action(action_type=ActionType.INSTALL, package="pkg1", source=PackageSource.APT),
            Action(action_type=ActionType.REMOVE, package="pkg2", source=PackageSource.APT),
            Action(action_type=ActionType.PURGE, package="pkg3", source=PackageSource.APT),
        ]

        results = operator.execute(actions)

        assert len(results) == 3
        assert all(r.success for r in results)
