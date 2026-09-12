"""Outlook e-postalarindan gorev uretme.

Katmanlar:

* `source.py` -- kaynak-bagimsiz mesaj/klasor modeli ve `MailSource` sozlesmesi
* `outlook.py` -- COM sarmalayicisi, postalari OKUR (yalnizca Windows)
* `send.py`    -- COM sarmalayicisi, posta GONDERIR (yalnizca Windows)
* `fake.py`    -- bellek ici kaynak ve sahte gonderici (testler icin)
* `intake.py`  -- eslestirme, tekillestirme, gorev uretimi (saf is mantigi)
"""

from __future__ import annotations

from typing import Any

from .source import (
    CONTACT_KINDS,
    CONTACT_LIST,
    CONTACT_PERSON,
    GalEntry,
    MailError,
    MailFolder,
    MailMessage,
    MailSource,
    is_supported,
    unavailable,
)

__all__ = [
    "CONTACT_KINDS",
    "CONTACT_LIST",
    "CONTACT_PERSON",
    "GalEntry",
    "MailError",
    "MailFolder",
    "MailMessage",
    "MailSource",
    "default_sender",
    "default_source",
    "is_supported",
    "unavailable",
]


def default_source(body_limit: int | None = None) -> Any:
    """Uretimdeki kaynak: Windows'ta Outlook, baska yerde anlasilir hata.

    `win32com` ice aktarimi bu cagrinin icinde kalir; Linux paketinde modul
    hic yuklenmez.
    """
    if not is_supported():
        raise unavailable()
    from .outlook import OutlookSource  # yerel ice aktarim: Windows disinda dokunulmaz

    from .source import BODY_LIMIT

    return OutlookSource(body_limit=body_limit or BODY_LIMIT)


def default_sender() -> Any:
    """Uretimdeki gonderici: Windows'ta Outlook, baska yerde anlasilir hata.

    `default_source` ile ayni desen; `win32com` ice aktarimi cagrinin icinde
    kalir, Linux paketinde modul hic yuklenmez.
    """
    if not is_supported():
        raise unavailable()
    from .send import OutlookSender  # yerel ice aktarim: Windows disinda dokunulmaz

    return OutlookSender()
