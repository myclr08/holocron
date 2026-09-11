"""Calisma dizini ve veri dosyalarinin yerleri.

Tasinabilir kullanimda ikili/klasorun yani veri dizinidir; testler ve
gelistirme icin HOLOCRON_HOME ortam degiskeniyle baska bir yere alinabilir.

Onemli: taban dizin her zaman `__file__`'a gore bulunur, `os.getcwd()`'ye
gore degil. Windows'ta kisayoldan, `start ""` ile ya da gorev zamanlayicidan
acilan surecin calisma dizini baska bir yer olabiliyor; veritabaninin nerede
olusacagi buna bagli kalmamali.
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

_ENV_HOME = "HOLOCRON_HOME"

DB_FILENAME = "holocron.db"
KEY_FILENAME = "holocron.key"
LOG_FILENAME = "holocron.log"
PORT_FILENAME = "holocron.port"

# Yazilabilirlik sinamasi her cagride diske gitmesin.
_RESOLVED: dict[str, Path] = {}


def _frozen_dir() -> Path | None:
    # PyInstaller benzeri paketlemede kaynak agaci gecicidir, ikilinin yani gerekir.
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return None


def base_dir() -> Path:
    """Paketin acildigi klasor: `app/`'in bir ustu (ya da ikilinin yani)."""
    return _frozen_dir() or Path(__file__).resolve().parent.parent


def fallback_dir() -> Path:
    """Paket klasoru yazilamazsa veri buraya duser.

    Zip'i "Program Files" altina ya da zip goruntuleyicinin gecici klasorune
    acan kullanici, yedek yol olmadiginda sessizce cokuyordu.
    """
    for name in ("LOCALAPPDATA", "APPDATA", "XDG_DATA_HOME"):
        value = os.environ.get(name)
        if value:
            return Path(value) / "Holocron"
    try:
        return Path.home() / ".holocron"
    except (RuntimeError, OSError):  # pragma: no cover - ev dizini yoksa
        return Path(tempfile.gettempdir()) / "holocron"


def _is_writable(base: Path) -> bool:
    probe = base / ".holocron-write-probe"
    try:
        base.mkdir(parents=True, exist_ok=True)
        with open(probe, "wb"):
            pass
    except OSError:
        return False
    finally:
        try:
            probe.unlink()
        except OSError:
            pass
    return True


def home_dir() -> Path:
    """Veritabani, anahtar ve log dosyasinin durdugu dizin."""
    env_value = os.environ.get(_ENV_HOME)
    if env_value:
        base = Path(env_value).expanduser()
        base.mkdir(parents=True, exist_ok=True)
        return base

    base = base_dir()
    cached = _RESOLVED.get(str(base))
    if cached is not None:
        return cached

    resolved = base if _is_writable(base) else fallback_dir()
    resolved.mkdir(parents=True, exist_ok=True)
    _RESOLVED[str(base)] = resolved
    return resolved


def forget_home() -> None:
    """Yazilabilirlik onbellegini bosaltir (testler icin)."""
    _RESOLVED.clear()


def db_path() -> Path:
    return home_dir() / DB_FILENAME


def key_path() -> Path:
    return home_dir() / KEY_FILENAME


def log_path() -> Path:
    return home_dir() / LOG_FILENAME


def port_path() -> Path:
    return home_dir() / PORT_FILENAME


def static_dir() -> Path:
    return Path(__file__).resolve().parent / "static"
