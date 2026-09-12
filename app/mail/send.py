"""Outlook ile e-posta gonderimi (yalniz Windows).

`outlook.py` postalari OKUR, burasi YAZAR. Ayri dosyada durmasinin sebebi
sozlesmenin ayri olmasi: gonderici yalnizca `send()` bilir, klasor/mesaj modeli
tasimaz; testler sahte bir gondericiyi bir satirda takar.

Bilinmesi gerekenler:

* **Imza.** Outlook kullanicinin imzasini oge *goruntulenirken* ekler. Bu yuzden
  once `Display(False)` cagrilir (kip ne olursa olsun), sonra govde imzanin
  ONUNE yazilir: `mail.HTMLBody = html + mail.HTMLBody`. Once govdeyi yazip
  sonra Display cagirmak imzayi govdenin altina degil, govdenin yerine koyuyor.
* **Ek dosyasi silinmez.** `Attachments.Add` kopyalamayi kendi zamanlamasiyla
  yapabiliyor; gonderimden hemen sonra silinen dosya bazen bos ek olarak
  gidiyordu. Dosyalar `%TEMP%\\holocron-mail` altinda kalir, en fazla yirmi tane
  tutulur ve eskiler yeni yazimda dusurulur.
* **Is parcacigi.** COM icin `pythoncom.CoInitialize()` / `CoUninitialize()`
  cifti sarttir; `outlook.py` ile ayni desen.
"""

from __future__ import annotations

import re
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Protocol, Sequence, runtime_checkable

from .source import MailError, is_supported, unavailable  # noqa: F401 (disari acilir)

# Gonderim kipi: pencereyi ac (kullanici Gonder'e basar) ya da dogrudan gonder.
MODE_DISPLAY = "display"
MODE_SEND = "send"
MODES: tuple[str, ...] = (MODE_DISPLAY, MODE_SEND)

# `Application.CreateItem(0)` = olMailItem.
OL_MAIL_ITEM = 0

# Eklerin durdugu gecici klasor ve tutulan en fazla dosya sayisi.
TEMP_FOLDER = "holocron-mail"
KEEP_FILES = 20

_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")
_DASHES = re.compile(r"-{2,}")

# Turkce harfler ASCII karsiliklarina duser: ek adi her istemcide ayni okunur.
_ASCII_MAP = str.maketrans(
    {
        "ç": "c", "Ç": "C", "ğ": "g", "Ğ": "G", "ı": "i", "İ": "I",
        "ö": "o", "Ö": "O", "ş": "s", "Ş": "S", "ü": "u", "Ü": "U",
    }
)


def clean_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in MODES else MODE_DISPLAY


# --- gecici ek dosyalari ------------------------------------------------


def temp_dir() -> Path:
    """`%TEMP%\\holocron-mail`; yoksa olusturulur."""
    target = Path(tempfile.gettempdir()) / TEMP_FOLDER
    target.mkdir(parents=True, exist_ok=True)
    return target


def safe_name(name: Any) -> str:
    """ASCII guvenli dosya adi; yol ayraci ve Turkce harf gecmez."""
    text = _UNSAFE_NAME.sub("-", str(name or "").strip().translate(_ASCII_MAP))
    text = _DASHES.sub("-", text).strip("-.")
    return text or "holocron.xlsx"


def prune_temp(keep: int = KEEP_FILES, folder: Path | None = None) -> int:
    """En yeni `keep` dosya kalir, kalani silinir; silinen sayisi doner."""
    base = folder or temp_dir()
    try:
        files = [item for item in base.iterdir() if item.is_file()]
    except OSError:
        return 0
    files.sort(key=lambda item: (item.stat().st_mtime, item.name), reverse=True)
    removed = 0
    for path in files[max(0, int(keep)):]:
        try:
            path.unlink()
            removed += 1
        except OSError:
            # Dosya Outlook'ta acik olabilir; bir sonraki gonderimde denenir.
            pass
    return removed


def write_attachment(
    name: Any, payload: bytes, folder: Path | None = None, keep: int = KEEP_FILES
) -> Path:
    """Excel'i gecici klasore yazar ve klasoru `keep` dosyaya indirir."""
    base = folder or temp_dir()
    base.mkdir(parents=True, exist_ok=True)
    path = base / safe_name(name)
    path.write_bytes(payload)
    prune_temp(keep, base)
    return path


# --- sozlesme -----------------------------------------------------------


@runtime_checkable
class MailSender(Protocol):
    """Gonderici sozlesmesi: uretimde Outlook, testlerde bellek ici sahte."""

    def send(
        self,
        to: Sequence[str],
        cc: Sequence[str],
        subject: str,
        html: str,
        attachments: Sequence[Path] = (),
        mode: str = MODE_DISPLAY,
    ) -> dict[str, Any]:
        ...


class OutlookSender:
    """Calisan Outlook oturumundan posta acar ya da gonderir."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise unavailable()

    @contextmanager
    def _application(self) -> Iterator[Any]:
        try:
            import pythoncom  # type: ignore
            import win32com.client  # type: ignore
        except ImportError as exc:  # pragma: no cover - Windows disi
            raise MailError(
                "pywin32_missing",
                "pywin32 bulunamadı; Outlook bağlantısı için gerekli.",
            ) from exc

        pythoncom.CoInitialize()
        try:
            try:
                application = win32com.client.Dispatch("Outlook.Application")
            except Exception as exc:
                raise MailError(
                    "outlook_unavailable",
                    "Outlook'a bağlanılamadı. Outlook açık mı?",
                ) from exc
            yield application
        finally:
            pythoncom.CoUninitialize()

    def send(
        self,
        to: Sequence[str],
        cc: Sequence[str],
        subject: str,
        html: str,
        attachments: Sequence[Path] = (),
        mode: str = MODE_DISPLAY,
    ) -> dict[str, Any]:
        chosen = clean_mode(mode)
        with self._application() as application:
            try:
                item = application.CreateItem(OL_MAIL_ITEM)
                item.To = "; ".join(str(part) for part in to or [])
                item.CC = "; ".join(str(part) for part in cc or [])
                item.Subject = str(subject or "")
                # Imza Display sirasinda eklenir; govde onun ONUNE yazilir.
                item.Display(False)
                item.HTMLBody = str(html or "") + str(getattr(item, "HTMLBody", "") or "")
                for path in attachments or []:
                    item.Attachments.Add(str(path))
                if chosen == MODE_SEND:
                    item.Send()
            except MailError:
                raise
            except Exception as exc:
                raise MailError(
                    "outlook_send_failed",
                    "Outlook e-postayı hazırlayamadı: " + str(exc),
                ) from exc
        return {"ok": True, "mode": chosen, "displayed": chosen != MODE_SEND}
