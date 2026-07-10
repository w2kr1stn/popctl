import os
from pathlib import Path

# Application identifier for directory naming
APP_NAME = "popctl"


def _get_xdg_dir(env_var: str, default_subdir: str) -> Path:
    base = os.environ.get(env_var)
    if base:
        return Path(base) / APP_NAME
    return Path.home() / default_subdir / APP_NAME


def get_config_dir() -> Path:
    return _get_xdg_dir("XDG_CONFIG_HOME", ".config")


def get_state_dir() -> Path:
    return _get_xdg_dir("XDG_STATE_HOME", ".local/state")


def get_backups_dir() -> Path:
    return get_state_dir() / "backups"


def get_manifest_path() -> Path:
    return get_config_dir() / "manifest.toml"


def ensure_dir(path: Path, name: str) -> Path:
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        msg = f"Cannot create {name} directory {path}: {e}"
        raise RuntimeError(msg) from e
    return path
