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
# Belgelerim klasorunun yerine gecer: testler ve demo gercek Belgeler'e yazmasin.
_ENV_DOCUMENTS = "HOLOCRON_DOCUMENTS"

DB_FILENAME = "holocron.db"
KEY_FILENAME = "holocron.key"
LOG_FILENAME = "holocron.log"
PORT_FILENAME = "holocron.port"

# Belgeler altindaki klasorler: Arsiv dosyalari ve guncelleme oncesi yedekler.
DOCS_FOLDER = "holocron"
ARCHIVE_FOLDER = "holocron-belgeler"
BACKUP_FOLDER = "holocron-yedek"

# FOLDERID_Documents: Windows'un "Belgeler" bilinen klasoru. OneDrive ya da
# grup ilkesi Belgeler'i baska yere yonlendirdiyse gercek yeri yalnizca bu
# API soyler; `~/Documents` o makinede bos, unutulmus bir klasor olabilir.
_FOLDERID_DOCUMENTS = "FDD39AD0-238F-46AF-ADB4-6C85480369C7"

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


# --- Belgeler -------------------------------------------------------------


def _windows_documents() -> Path | None:
    """`SHGetKnownFolderPath(FOLDERID_Documents)`; olmazsa `None`."""
    if not sys.platform.startswith("win"):
        return None
    try:
        import ctypes
        import uuid
        from ctypes import wintypes

        class _Guid(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD),
                ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD),
                ("Data4", ctypes.c_ubyte * 8),
            ]

        raw = uuid.UUID(_FOLDERID_DOCUMENTS).bytes_le
        guid = _Guid.from_buffer_copy(raw)
        shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
        ole32 = ctypes.windll.ole32  # type: ignore[attr-defined]
        shell32.SHGetKnownFolderPath.argtypes = [
            ctypes.POINTER(_Guid), wintypes.DWORD, wintypes.HANDLE,
            ctypes.POINTER(ctypes.c_wchar_p),
        ]
        shell32.SHGetKnownFolderPath.restype = ctypes.c_long
        found = ctypes.c_wchar_p()
        result = shell32.SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(found))
        try:
            value = found.value if result == 0 else None
        finally:
            ole32.CoTaskMemFree(found)
    except Exception:  # noqa: BLE001 - API yoksa ev dizinine dusulur
        return None
    return Path(value) if value else None


def _xdg_documents() -> Path | None:
    """Linux masaustlerinde `user-dirs.dirs` icindeki XDG_DOCUMENTS_DIR."""
    config = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    try:
        text = (Path(config) / "user-dirs.dirs").read_text(encoding="utf-8")
    except (OSError, RuntimeError, UnicodeDecodeError):
        return None
    for line in text.splitlines():
        name, _, value = line.strip().partition("=")
        if name != "XDG_DOCUMENTS_DIR":
            continue
        value = value.strip().strip('"').replace("$HOME", str(Path.home()))
        if value and value.rstrip("/") != str(Path.home()):
            return Path(value)
    return None


def documents_dir() -> Path:
    """Kullanicinin Belgeler klasoru (yonlendirilmis/OneDrive dahil)."""
    override = os.environ.get(_ENV_DOCUMENTS)
    if override:
        return Path(override).expanduser()
    found = _windows_documents() or _xdg_documents()
    if found is not None:
        return found
    try:
        return Path.home() / "Documents"
    except (RuntimeError, OSError):  # pragma: no cover - ev dizini yoksa
        return fallback_dir() / "Documents"


def holocron_documents() -> Path:
    """`<Belgeler>/holocron`: Arsiv ve yedeklerin ortak ust klasoru."""
    return documents_dir() / DOCS_FOLDER


def archive_dir() -> Path:
    """Arsiv dosyalarinin kopyalandigi klasor; yoksa olusturulur."""
    folder = holocron_documents() / ARCHIVE_FOLDER
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def backup_dir() -> Path:
    """Guncelleme oncesi veri yedeklerinin klasoru (olusturmaz)."""
    return holocron_documents() / BACKUP_FOLDER
