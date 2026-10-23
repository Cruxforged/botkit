"""
Чтение настроек из `.env`: мусорное значение не роняет бота, а даёт значение
по умолчанию.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

TRUE = {"1", "true", "yes", "on", "да"}


def load_env(base_dir: str | Path) -> Path:
    """Читает `<base_dir>/.env` и возвращает папку бота."""
    base = Path(base_dir).resolve()
    load_dotenv(base / ".env")
    return base


def env_str(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    return value.strip() if value and value.strip() else default


def env_int(name: str, default: int | None = None) -> int | None:
    try:
        return int(os.getenv(name, "").strip())
    except ValueError:
        return default


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in TRUE


def env_int_set(name: str) -> set[int]:
    """`111,222,333` → {111, 222, 333}; опечатки пропускаются."""
    result: set[int] = set()
    for part in os.getenv(name, "").split(","):
        try:
            result.add(int(part.strip()))
        except ValueError:
            pass
    return result
