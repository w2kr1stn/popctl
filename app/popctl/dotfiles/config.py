import os
import tomllib
from contextlib import suppress
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Literal, Self

import tomli_w
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from popctl.core.paths import get_config_dir, get_data_dir


class DotfilesConfigError(Exception):
    pass


def _default_bare_repo() -> Path:
    return get_data_dir() / "dotfiles.git"


class RemotePrivacyRecord(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    canonical_remote_url: str = Field(min_length=1)
    method: Literal["verified", "acknowledged"]


class DotfilesConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    bare_repo: Path = Field(default_factory=_default_bare_repo)
    remote_url: str = ""
    ambiguous_content_allowlist: list[str] = Field(default_factory=list)
    ignored: list[str] = Field(default_factory=list)
    remote_privacy: RemotePrivacyRecord | None = None

    def has_remote_privacy_record(self, canonical_remote_url: str) -> bool:
        return (
            self.remote_privacy is not None
            and self.remote_privacy.canonical_remote_url == canonical_remote_url
        )

    def with_remote_url(self, canonical_remote_url: str) -> Self:
        remote_privacy = self.remote_privacy
        if (
            remote_privacy is not None
            and remote_privacy.canonical_remote_url != canonical_remote_url
        ):
            remote_privacy = None
        return self.model_copy(
            update={"remote_url": canonical_remote_url, "remote_privacy": remote_privacy}
        )

    def with_remote_privacy_record(
        self,
        canonical_remote_url: str,
        *,
        method: Literal["verified", "acknowledged"],
    ) -> Self:
        return self.model_copy(
            update={
                "remote_url": canonical_remote_url,
                "remote_privacy": RemotePrivacyRecord(
                    canonical_remote_url=canonical_remote_url,
                    method=method,
                ),
            }
        )


def get_dotfiles_config_path() -> Path:
    return get_config_dir() / "dotfiles.toml"


def load_dotfiles_config(path: Path | None = None) -> DotfilesConfig:
    config_path = path or get_dotfiles_config_path()
    if not config_path.exists():
        return DotfilesConfig()
    try:
        with config_path.open("rb") as f:
            data = tomllib.load(f)
    except tomllib.TOMLDecodeError as e:
        raise DotfilesConfigError(f"Invalid TOML syntax in {config_path}: {e}") from e
    except OSError as e:
        raise DotfilesConfigError(f"Failed to read dotfiles config {config_path}: {e}") from e
    try:
        return DotfilesConfig.model_validate(data)
    except ValidationError as e:
        raise DotfilesConfigError(f"Invalid dotfiles config {config_path}: {e}") from e


def save_dotfiles_config(config: DotfilesConfig, path: Path | None = None) -> Path:
    config_path = path or get_dotfiles_config_path()
    content = tomli_w.dumps(config.model_dump(mode="json", exclude_none=True)).encode("utf-8")
    try:
        if config_path.read_bytes() == content:
            return config_path
    except FileNotFoundError:
        pass
    except OSError as e:
        raise DotfilesConfigError(f"Failed to read dotfiles config {config_path}: {e}") from e
    config_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="wb",
            dir=config_path.parent,
            delete=False,
            suffix=".tmp",
        ) as f:
            temporary_path = Path(f.name)
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary_path, config_path)
    except OSError as e:
        if temporary_path is not None:
            with suppress(OSError):
                temporary_path.unlink(missing_ok=True)
        raise DotfilesConfigError(f"Failed to write dotfiles config {config_path}: {e}") from e
    return config_path
