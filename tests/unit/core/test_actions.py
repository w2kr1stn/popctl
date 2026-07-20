"""Unit tests for diff_to_actions in core/diff.py.

Tests for diff-to-action conversion and source mapping logic.
"""

from popctl.core.diff import DiffEntry, DiffResult, DiffType, diff_to_actions
from popctl.models.action import ActionType
from popctl.models.package import PackageSource
from popctl.sources.models import (
    AptSources,
    FlatpakApp,
    FlatpakRemote,
    FlatpakScope,
    FlatpakSources,
    ReplayMode,
    SnapChannel,
    SnapSources,
    SourcePlatform,
    SourcesConfig,
)


class TestDiffToActionsMissing:
    """Tests for MISSING entries in diff_to_actions()."""

    def test_diff_to_actions_missing_creates_install(self) -> None:
        """MISSING entries produce INSTALL actions."""
        diff_result = DiffResult(
            new=(),
            missing=(
                DiffEntry(name="vim", source=PackageSource.APT, diff_type=DiffType.MISSING),
                DiffEntry(name="git", source=PackageSource.APT, diff_type=DiffType.MISSING),
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
                    source=PackageSource.FLATPAK,
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
                    source=PackageSource.APT,
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
                    source=PackageSource.FLATPAK,
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
            extra=(
                DiffEntry(name="bloatware", source=PackageSource.APT, diff_type=DiffType.EXTRA),
            ),
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
                    source=PackageSource.FLATPAK,
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
            extra=(
                DiffEntry(
                    name="telegram-desktop", source=PackageSource.SNAP, diff_type=DiffType.EXTRA,
                ),
            ),
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
                DiffEntry(name="bloatware", source=PackageSource.APT, diff_type=DiffType.EXTRA),
                DiffEntry(
                    name="com.unwanted.App",
                    source=PackageSource.FLATPAK,
                    diff_type=DiffType.EXTRA,
                ),
                DiffEntry(
                    name="telegram-desktop",
                    source=PackageSource.SNAP,
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


class TestDiffToActionsEdgeCases:
    """Tests for edge cases in diff_to_actions()."""

    def test_diff_to_actions_ignores_new(self) -> None:
        """NEW entries produce no actions."""
        diff_result = DiffResult(
            new=(
                DiffEntry(
                    name="htop",
                    source=PackageSource.APT,
                    diff_type=DiffType.NEW,
                    version="3.2.2",
                ),
                DiffEntry(
                    name="neofetch",
                    source=PackageSource.APT,
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
            new=(DiffEntry(name="htop", source=PackageSource.APT, diff_type=DiffType.NEW),),
            missing=(DiffEntry(name="vim", source=PackageSource.APT, diff_type=DiffType.MISSING),),
            extra=(
                DiffEntry(name="bloatware", source=PackageSource.APT, diff_type=DiffType.EXTRA),
            ),
        )

        actions = diff_to_actions(diff_result)

        assert len(actions) == 2
        install_actions = [a for a in actions if a.action_type == ActionType.INSTALL]
        remove_actions = [a for a in actions if a.action_type == ActionType.REMOVE]
        assert len(install_actions) == 1
        assert len(remove_actions) == 1
        assert install_actions[0].package == "vim"
        assert remove_actions[0].package == "bloatware"


class TestSourceAwareInstallActions:
    def test_flatpak_actions_preserve_scope_arch_and_branch_identity(self) -> None:
        remotes = (
            FlatpakRemote(
                name="flathub",
                scope=FlatpakScope.USER,
                url="https://example.com/flathub.flatpakrepo",
                gpg_verify=True,
                gpg_key_armor="armor",
                gpg_fingerprints=("A" * 40,),
                replay_mode=ReplayMode.REPLAY,
            ),
            FlatpakRemote(
                name="vendor-system",
                scope=FlatpakScope.SYSTEM,
                url="https://example.com/vendor.flatpakrepo",
                gpg_verify=True,
                gpg_key_armor="armor",
                gpg_fingerprints=("B" * 40,),
                replay_mode=ReplayMode.REPLAY,
            ),
        )
        sources = SourcesConfig(
            platform=SourcePlatform(distro_id="ubuntu", codename="noble"),
            apt=AptSources(),
            flatpak=FlatpakSources(
                remotes=remotes,
                apps=(
                    FlatpakApp(
                        id="org.example.App",
                        origin="flathub",
                        scope=FlatpakScope.USER,
                        arch="x86_64",
                        branch="stable",
                    ),
                    FlatpakApp(
                        id="org.example.App",
                        origin="flathub",
                        scope=FlatpakScope.USER,
                        arch="x86_64",
                        branch="beta",
                    ),
                    FlatpakApp(
                        id="org.example.App",
                        origin="vendor-system",
                        scope=FlatpakScope.SYSTEM,
                        arch="aarch64",
                        branch="stable",
                    ),
                ),
            ),
            snap=SnapSources(),
        )
        diff = DiffResult(
            new=(),
            missing=(
                DiffEntry(
                    name="org.example.App",
                    source=PackageSource.FLATPAK,
                    diff_type=DiffType.MISSING,
                ),
            ),
            extra=(),
        )

        actions = diff_to_actions(diff, sources=sources)

        assert len(actions) == 3
        assert {
            (
                action.source_install_context.flatpak_scope,
                action.source_install_context.flatpak_arch,
                action.source_install_context.flatpak_branch,
            )
            for action in actions
            if action.source_install_context is not None
        } == {
            (FlatpakScope.USER, "x86_64", "stable"),
            (FlatpakScope.USER, "x86_64", "beta"),
            (FlatpakScope.SYSTEM, "aarch64", "stable"),
        }

    def test_snap_action_uses_recorded_channel_and_bare_fallback_requires_no_record(self) -> None:
        sources = SourcesConfig(
            platform=SourcePlatform(distro_id="ubuntu", codename="noble"),
            apt=AptSources(),
            flatpak=FlatpakSources(),
            snap=SnapSources(
                packages=(
                    SnapChannel(
                        name="firefox",
                        channel="latest/beta",
                        replay_mode=ReplayMode.REPLAY,
                    ),
                )
            ),
        )
        diff = DiffResult(
            new=(),
            missing=(
                DiffEntry(name="firefox", source=PackageSource.SNAP, diff_type=DiffType.MISSING),
                DiffEntry(name="bare", source=PackageSource.SNAP, diff_type=DiffType.MISSING),
            ),
            extra=(),
        )

        actions = diff_to_actions(diff, sources=sources)

        assert actions[0].source_install_context is not None
        assert actions[0].source_install_context.snap_channel == "latest/beta"
        assert actions[1].source_install_context is None
