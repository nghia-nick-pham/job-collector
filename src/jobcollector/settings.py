"""Load secrets from .env and settings from config.yaml."""

import os
from pathlib import Path
from typing import Any

import yaml


def load_env(path: Path) -> dict[str, str]:
    """Return .env values overlaid with the process environment."""
    values: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            name, separator, value = line.strip().partition("=")
            if separator and not name.startswith("#"):
                values[name.strip()] = _unquote(value.strip())
    return {**values, **os.environ}


def load_config(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value
