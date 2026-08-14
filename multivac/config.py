"""Carga de config.toml, compartida por los tres servicios."""

from __future__ import annotations

import functools
import os
import tomllib
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def config_path() -> Path:
    if env := os.environ.get("MULTIVAC_CONFIG"):
        return Path(env)
    user = Path(
        os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
    ) / "multivac/config.toml"
    return user if user.exists() else PROJECT_ROOT / "config.toml"


@functools.lru_cache(maxsize=1)
def load() -> dict[str, Any]:
    with config_path().open("rb") as fh:
        return tomllib.load(fh)


def state_dir() -> Path:
    base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    path = base / "multivac"
    path.mkdir(parents=True, exist_ok=True)
    return path
