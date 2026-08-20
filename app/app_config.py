"""Mode-based file config: configs/config.json declares per-mode settings.

Each mode (dev, prod) carries its own env file and storage routing. The
checked-in default is prod (broker reads, Render unchanged); laptops set
APP_MODE=dev to route trade fills through data/local/ instead of Robinhood.
"""

import json
import os
from enum import Enum
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field

_REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = _REPO_ROOT / "configs" / "config.json"

_MODE_REQUIRED_KEYS = ("env_file", "storage_backend")
_STORAGE_BACKENDS = ("broker", "local")


class AppModes(str, Enum):
    DEV = "dev"
    PROD = "prod"


class ModeConfig(BaseModel):
    env_file: str = Field(..., description="dotenv file loaded for this mode")
    storage_backend: str = Field(..., description="'broker' (live RH) or 'local' (data dir)")
    storage_dir: str | None = Field(
        None, description="directory for local fill storage; required for the local backend"
    )


class AppConfig(BaseModel):
    description: str | None = None
    app_mode: str = Field(..., description="active mode: dev or prod")
    mode: ModeConfig


def load_app_config(config_path: Path = CONFIG_PATH) -> AppConfig:
    with open(config_path) as f:
        config_dict = json.load(f)

    # Env wins so deploys/laptops select a mode without editing a tracked file.
    app_mode = os.getenv("APP_MODE") or config_dict.get("app_mode")
    if not app_mode:
        raise ValueError("`app_mode` not found in config or APP_MODE env")
    allowed_modes = [m.value for m in AppModes]
    if app_mode not in allowed_modes:
        raise ValueError(
            f"Invalid app mode: `{app_mode}`. Supported modes are: {allowed_modes}"
        )

    # Expose the resolved mode to downstream clients.
    os.environ["APP_MODE"] = app_mode

    mode_dict = config_dict.get(app_mode, {})
    for key in _MODE_REQUIRED_KEYS:
        if mode_dict.get(key) is None:
            raise ValueError(
                f"`{key}` not found for app mode: `{app_mode}` or its value is None"
            )

    storage_backend = mode_dict["storage_backend"]
    if storage_backend not in _STORAGE_BACKENDS:
        raise ValueError(
            f"Invalid `storage_backend` for `{app_mode}`: "
            f"{storage_backend!r} (must be one of {_STORAGE_BACKENDS})"
        )

    # `storage_dir` (legacy alias: local_storage_dir) — only the local backend
    # touches the filesystem, so broker modes may omit it.
    storage_dir = mode_dict.get("storage_dir") or mode_dict.get("local_storage_dir")
    if storage_backend == "local" and not storage_dir:
        raise ValueError(
            f"`storage_dir` is required for app mode `{app_mode}` "
            "because its storage_backend is 'local'"
        )
    if storage_dir:
        path = Path(storage_dir)
        storage_dir = str(path if path.is_absolute() else _REPO_ROOT / path)

    # Load the mode's env file (relative paths resolve against the repo root).
    env_file = mode_dict["env_file"]
    load_dotenv(_REPO_ROOT / env_file)

    return AppConfig(
        description=config_dict.get("description"),
        app_mode=app_mode,
        mode=ModeConfig(
            env_file=env_file,
            storage_backend=storage_backend,
            storage_dir=storage_dir,
        ),
    )
