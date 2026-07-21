from pathlib import Path
from unittest.mock import MagicMock

import pytest
from popctl.dotfiles.config import (
    DesktopSettingsConfig,
    DotfilesConfig,
    DotfilesConfigError,
    RemotePrivacyRecord,
    load_dotfiles_config,
    save_dotfiles_config,
)
from popctl.dotfiles.desktop import DEFAULT_ROOTS
from pydantic import ValidationError


class TestDotfilesConfig:
    def test_runtime_default_uses_xdg_data_home(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))

        config = DotfilesConfig()

        assert config.bare_repo == tmp_path / "xdg-data" / "popctl" / "dotfiles.git"

    def test_round_trip(self, tmp_path: Path) -> None:
        path = tmp_path / "config" / "dotfiles.toml"
        config = DotfilesConfig(
            bare_repo=tmp_path / "dotfiles.git",
            remote_url="https://github.com/example/dotfiles.git",
            ambiguous_content_allowlist=[".config/tool/config.toml"],
            ignored=[".cache/tool/config"],
            remote_privacy=RemotePrivacyRecord(
                canonical_remote_url="https://github.com/example/dotfiles.git",
                method="acknowledged",
            ),
        )

        saved_path = save_dotfiles_config(config, path)

        assert saved_path == path
        assert load_dotfiles_config(path) == config

    def test_does_not_replace_an_unchanged_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        path = tmp_path / "dotfiles.toml"
        config = DotfilesConfig(remote_url="https://github.com/example/dotfiles.git")
        save_dotfiles_config(config, path)
        replace = MagicMock()
        monkeypatch.setattr("popctl.dotfiles.config.os.replace", replace)

        saved_path = save_dotfiles_config(config, path)

        assert saved_path == path
        replace.assert_not_called()

    def test_rejects_extra_fields(self, tmp_path: Path) -> None:
        with pytest.raises(ValidationError):
            DotfilesConfig.model_validate({"unexpected": "value"})

        path = tmp_path / "dotfiles.toml"
        path.write_text('unexpected = "value"\n', encoding="utf-8")

        with pytest.raises(DotfilesConfigError, match="Invalid dotfiles config"):
            load_dotfiles_config(path)

    def test_missing_config_uses_runtime_defaults(self, tmp_path: Path) -> None:
        assert load_dotfiles_config(tmp_path / "missing.toml") == DotfilesConfig()

    def test_rejects_malformed_toml(self, tmp_path: Path) -> None:
        path = tmp_path / "dotfiles.toml"
        path.write_text("not = [valid", encoding="utf-8")

        with pytest.raises(DotfilesConfigError, match="Invalid TOML syntax"):
            load_dotfiles_config(path)

    def test_url_change_invalidates_privacy_record(self) -> None:
        original_url = "https://github.com/example/dotfiles.git"
        changed_url = "https://github.com/example/other.git"
        config = DotfilesConfig().with_remote_privacy_record(
            original_url,
            method="acknowledged",
        )

        changed = config.with_remote_url(changed_url)

        assert config.has_remote_privacy_record(original_url)
        assert not changed.has_remote_privacy_record(changed_url)
        assert changed.remote_privacy is None

    def test_refreshes_privacy_record_for_canonical_url(self) -> None:
        url = "git@github.com:example/dotfiles.git"

        config = DotfilesConfig().with_remote_privacy_record(url, method="verified")

        assert config.remote_url == url
        assert config.has_remote_privacy_record(url)
        assert config.remote_privacy is not None
        assert config.remote_privacy.method == "verified"

    def test_rejects_extra_desktop_settings_fields(self) -> None:
        with pytest.raises(ValidationError):
            DotfilesConfig.model_validate({"desktop_settings": {"unexpected": True}})

    @pytest.mark.parametrize(
        ("root", "message"),
        (
            ("/", "absolute directory"),
            ("/org/gnome/shell/", "bare GNOME Shell root"),
            ("relative/root/", "absolute directory"),
            ("/org/example", "absolute directory"),
            ("/org//example/", "unsafe segment"),
            ("/org/./example/", "unsafe segment"),
            ("/org/../example/", "unsafe segment"),
            ("/org\\example/", "backslash"),
            ("/org/example root/", "whitespace"),
            ("/org/example\x1froot/", "whitespace"),
        ),
    )
    def test_rejects_noncanonical_desktop_roots(self, root: str, message: str) -> None:
        with pytest.raises(ValidationError, match=message):
            DesktopSettingsConfig(extra_roots=(root,))

    @pytest.mark.parametrize(
        ("field", "roots", "message"),
        (
            (
                "extra_roots",
                ("/org/example/duplicate/", "/org/example/duplicate/"),
                "duplicates",
            ),
            ("extra_roots", (DEFAULT_ROOTS[0],), "duplicates"),
            (
                "disabled_roots",
                ("/org/example/duplicate/", "/org/example/duplicate/"),
                "duplicates",
            ),
            (
                "extra_roots",
                ("/org/example/", "/org/example/child/"),
                "overlap",
            ),
            (
                "disabled_roots",
                ("/org/example/", "/org/example/child/"),
                "overlap",
            ),
        ),
    )
    def test_rejects_duplicate_or_overlapping_root_collections(
        self,
        field: str,
        roots: tuple[str, ...],
        message: str,
    ) -> None:
        with pytest.raises(ValidationError, match=message):
            DesktopSettingsConfig(**{field: roots})

    def test_rejects_an_extra_root_that_overlaps_a_default(self) -> None:
        with pytest.raises(ValidationError, match="overlap"):
            DesktopSettingsConfig(extra_roots=("/org/gnome/desktop/",))

    def test_accepts_exact_disabled_candidate_root(self) -> None:
        config = DesktopSettingsConfig(
            extra_roots=("/org/example/",),
            disabled_roots=(DEFAULT_ROOTS[0], "/org/example/"),
        )

        assert DEFAULT_ROOTS[0] not in config.effective_roots
        assert "/org/example/" not in config.effective_roots

    def test_resolves_sorted_effective_roots_when_disabled(self) -> None:
        config = DesktopSettingsConfig(
            enabled=False,
            extra_roots=("/org/zulu/", "/org/alpha/"),
            disabled_roots=(DEFAULT_ROOTS[0],),
        )

        assert not config.enabled
        assert config.effective_roots == tuple(
            sorted((*DEFAULT_ROOTS[1:], "/org/alpha/", "/org/zulu/"))
        )
