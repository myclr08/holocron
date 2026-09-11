"""Calisma dizini ve veri dosyalarinin yerleri.

Tasinabilir kullanimda ikili/klasorun yani veri dizinidir; testler ve
gelistirme icin HOLOCRON_HOME ortam degiskeniyle baska bir yere alinabilir.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ENV_HOME = "HOLOCRON_HOME"

DB_FILENAME = "holocron.db"
KEY_FILENAME = "holocron.key"


def _frozen_dir() -> Path | None:
    # PyInstaller benzeri paketlemede kaynak agaci gecicidir, ikilinin yani gerekir.
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return None


def home_dir() -> Path:
    """Veritabani ve anahtar dosyasinin durdugu dizin."""
    env_value = os.environ.get(_ENV_HOME)
    if env_value:
        base = Path(env_value).expanduser()
    else:
        base = _frozen_dir() or Path(__file__).resolve().parent.parent
    base.mkdir(parents=True, exist_ok=True)
    return base


def db_path() -> Path:
    return home_dir() / DB_FILENAME


def key_path() -> Path:
    return home_dir() / KEY_FILENAME


def static_dir() -> Path:
    return Path(__file__).resolve().parent / "static"
