from __future__ import annotations

import sys
from pathlib import Path


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def app_dir() -> Path:
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bundled_resource_path(name: str) -> Path:
    frozen_base = getattr(sys, "_MEIPASS", "")
    if frozen_base:
        candidate = Path(frozen_base) / name
        if candidate.exists():
            return candidate
    return app_dir() / name


def app_icon_path() -> Path:
    return bundled_resource_path("assets/app_icon.ico")
