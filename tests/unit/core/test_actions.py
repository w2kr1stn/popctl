"""Unit tests for core/actions.py.

Tests for diff-to-action conversion and source mapping logic.
"""

import pytest
from popctl.core.actions import SOURCE_MAP, diff_to_actions, source_to_package_source
from popctl.core.diff import DiffEntry, DiffResult, DiffType
from popctl.models.action import ActionType
from popctl.models.package import PackageSource


class TestSourceToPackageSource:
    """Tests for source_to_package_source()."""

    def test_source_to_package_source_apt(self) -> None:
        """'apt' maps to PackageSource.APT."""
        assert source_to_package_source("apt") == PackageSource.APT

    def test_source_to_package_source_flatpak(self) -> None:
        """'flatpak' maps to PackageSource.FLATPAK."""
        assert source_to_package_source("flatpak") == PackageSource.FLATPAK

    def test_source_to_package_source_snap(self) -> None:
        """'snap' maps to PackageSource.SNAP."""
        assert source_to_package_source("snap") == PackageSource.SNAP

    def test_source_to_package_source_invalid(self) -> None:
        """Invalid source string raises KeyError."""
        with pytest.raises(KeyError):
            source_to_package_source("brew")

    def test_source_map_contains_expected_keys(self) -> None:
        """SOURCE_MAP contains exactly 'apt', 'flatpak', and 'snap'."""
        assert set(SOURCE_MAP.keys()) == {"apt", "flatpak", "snap"}


class TestDiffToActionsMissing:
    """Tests for MISSING entries in diff_to_actions()."""

    def test_diff_to_actions_missing_creates_install(self) -> None:
        """MISSING entries produce INSTALL actions."""
        diff_result = DiffResult(
            new=(),
            missing=(
                DiffEntry(name="vim", source="apt", diff_type=DiffType.MISSING),
                DiffEntry(name="git", source="apt", diff_type=DiffType.MISSING),
            ),
            extra=(),
        )

        actions = diff_to_actions(diff_result)

        assert len(actions) == 2
        assert all(a.action_type == ActionType.INSTALL for a in actions)
        assert actions[0].package == "vim"
        assert actions[0].source == PackageSource.APT
        assert actions[1].package == "git"
        assert actions[1].source == PackageSource.APT

    def test_diff_to_actions_missing_flatpak_creates_install(self) -> None:
        """MISSING flatpak entries produce INSTALL actions with FLATPAK source."""
        diff_result = DiffResult(
            new=(),
            missing=(
                DiffEntry(
                    name="com.spotify.Client",
                    source="flatpak",
                    diff_type=DiffType.MISSING,
                ),
            ),
            extra=(),
        )

        actions = diff_to_actions(diff_result)

        assert len(actions) == 1
        assert actions[0].action_type == ActionType.INSTALL
        assert actions[0].package == "com.spotify.Client"
        assert actions[0].source == PackageSource.FLATPAK


class TestDiffToActionsExtra:
    """Tests for EXTRA entries in diff_to_actions()."""

    def test_diff_to_actions_extra_creates_remove(self) -> None:
        """EXTRA entries produce REMOVE actions."""
        diff_result = DiffResult(
            new=(),
            missing=(),
            extra=(
                DiffEntry(
                    name="bloatware",
                    source="apt",
                    diff_type=DiffType.EXTRA,
                    version="1.0",
                ),
            ),
        )

        actions = diff_to_actions(diff_result)

        assert len(actions) == 1
        assert actions[0].action_type == ActionType.REMOVE
        assert actions[0].package == "bloatware"
        assert actions[0].source == PackageSource.APT

    def test_diff_to_actions_extra_flatpak_creates_remove(self) -> None:
        """EXTRA flatpak entries produce REMOVE actions with FLATPAK source."""
        diff_result = DiffResult(
            new=(),
            missing=(),
            extra=(
                DiffEntry(
                    name="com.unwanted.App",
                    source="flatpak",
                    diff_type=DiffType.EXTRA,
                ),
            ),
        )

        actions = diff_to_actions(diff_result)

        assert len(actions) == 1
        assert actions[0].action_type == ActionType.REMOVE
        assert actions[0].source == PackageSource.FLATPAK


class TestDiffToActionsPurge:
    """Tests for purge behavior in diff_to_actions()."""

    def test_diff_to_actions_purge_for_apt(self) -> None:
        """purge=True produces PURGE actions for APT packages."""
        diff_result = DiffResult(
            new=(),
            missing=(),
            extra=(DiffEntry(name="bloatware", source="apt", diff_type=DiffType.EXTRA),),
        )

        actions = diff_to_actions(diff_result, purge=True)

        assert len(actions) == 1
        assert actions[0].action_type == ActionType.PURGE
        assert actions[0].source == PackageSource.APT

    def test_diff_to_actions_purge_ignored_for_flatpak(self) -> None:
        """purge=True still produces REMOVE for flatpak packages."""
        diff_result = DiffResult(
            new=(),
            missing=(),
            extra=(
                DiffEntry(
                    name="com.unwanted.App",
                    source="flatpak",
                    diff_type=DiffType.EXTRA,
                ),
            ),
        )

        actions = diff_to_actions(diff_result, purge=True)

        assert len(actions) == 1
        assert actions[0].action_type == ActionType.REMOVE
        assert actions[0].source == PackageSource.FLATPAK

    def test_diff_to_actions_purge_for_snap(self) -> None:
        """purge=True produces PURGE actions for Snap packages."""
        diff_result = DiffResult(
            new=(),
            missing=(),
            extra=(DiffEntry(name="telegram-desktop", source="snap", diff_type=DiffType.EXTRA),),
        )

        actions = diff_to_actions(diff_result, purge=True)

        assert len(actions) == 1
        assert actions[0].action_type == ActionType.PURGE
        assert actions[0].source == PackageSource.SNAP

    def test_diff_to_actions_purge_mixed_sources(self) -> None:
        """purge=True: APT gets PURGE, flatpak gets REMOVE, snap gets PURGE."""
        diff_result = DiffResult(
            new=(),
            missing=(),
            extra=(
                DiffEntry(name="bloatware", source="apt", diff_type=DiffType.EXTRA),
                DiffEntry(
                    name="com.unwanted.App",
                    source="flatpak",
                    diff_type=DiffType.EXTRA,
                ),
                DiffEntry(
                    name="telegram-desktop",
                    source="snap",
                    diff_type=DiffType.EXTRA,
                ),
            ),
        )

        actions = diff_to_actions(diff_result, purge=True)

        assert len(actions) == 3
        apt_action = next(a for a in actions if a.source == PackageSource.APT)
        flatpak_action = next(a for a in actions if a.source == PackageSource.FLATPAK)
        snap_action = next(a for a in actions if a.source == PackageSource.SNAP)
        assert apt_action.action_type == ActionType.PURGE
        assert flatpak_action.action_type == ActionType.REMOVE
        assert snap_action.action_type == ActionType.PURGE


class TestDiffToActionsProtected:
    """Tests for protected package handling in diff_to_actions()."""

    def test_diff_to_actions_skips_protected(self) -> None:
        """Protected packages in EXTRA are skipped."""
        diff_result = DiffResult(
            new=(),
            missing=(),
            extra=(
                DiffEntry(name="systemd", source="apt", diff_type=DiffType.EXTRA),
                DiffEntry(name="bash", source="apt", diff_type=DiffType.EXTRA),
                DiffEntry(name="bloatware", source="apt", diff_type=DiffType.EXTRA),
            ),
        )

        actions = diff_to_actions(diff_result)

        assert len(actions) == 1
        assert actions[0].package == "bloatware"

    def test_diff_to_actions_skips_pattern_protected(self) -> None:
        """Packages matching protected patterns are skipped."""
        diff_result = DiffResult(
            new=(),
            missing=(),
            extra=(
                DiffEntry(
                    name="linux-image-6.1",
                    source="apt",
                    diff_type=DiffType.EXTRA,
                ),
                DiffEntry(name="bloatware", source="apt", diff_type=DiffType.EXTRA),
            ),
        )

        actions = diff_to_actions(diff_result)

        assert len(actions) == 1
        assert actions[0].package == "bloatware"


class TestDiffToActionsEdgeCases:
    """Tests for edge cases in diff_to_actions()."""

    def test_diff_to_actions_ignores_new(self) -> None:
        """NEW entries produce no actions."""
        diff_result = DiffResult(
            new=(
                DiffEntry(
                    name="htop",
                    source="apt",
                    diff_type=DiffType.NEW,
                    version="3.2.2",
                ),
                DiffEntry(
                    name="neofetch",
                    source="apt",
                    diff_type=DiffType.NEW,
                    version="7.1.0",
                ),
            ),
            missing=(),
            extra=(),
        )

        actions = diff_to_actions(diff_result)

        assert actions == []

    def test_diff_to_actions_empty_result(self) -> None:
        """Empty DiffResult produces empty action list."""
        diff_result = DiffResult(new=(), missing=(), extra=())

        actions = diff_to_actions(diff_result)

        assert actions == []

    def test_diff_to_actions_combined(self) -> None:
        """Combined NEW + MISSING + EXTRA: only MISSING and EXTRA produce actions."""
        diff_result = DiffResult(
            new=(DiffEntry(name="htop", source="apt", diff_type=DiffType.NEW),),
            missing=(DiffEntry(name="vim", source="apt", diff_type=DiffType.MISSING),),
            extra=(DiffEntry(name="bloatware", source="apt", diff_type=DiffType.EXTRA),),
        )

        actions = diff_to_actions(diff_result)

        assert len(actions) == 2
        install_actions = [a for a in actions if a.action_type == ActionType.INSTALL]
        remove_actions = [a for a in actions if a.action_type == ActionType.REMOVE]
        assert len(install_actions) == 1
        assert len(remove_actions) == 1
        assert install_actions[0].package == "vim"
        assert remove_actions[0].package == "bloatware"

    def test_diff_to_actions_reason_strings(self) -> None:
        """Actions have descriptive reason strings."""
        diff_result = DiffResult(
            new=(),
            missing=(DiffEntry(name="vim", source="apt", diff_type=DiffType.MISSING),),
            extra=(DiffEntry(name="bloatware", source="apt", diff_type=DiffType.EXTRA),),
        )

        actions = diff_to_actions(diff_result)

        install_action = next(a for a in actions if a.is_install)
        remove_action = next(a for a in actions if a.is_remove)
        assert install_action.reason == "Package in manifest but not installed"
        assert remove_action.reason == "Package marked for removal in manifest"
