"""Windows bildirimi: kayit baslayinca ve not hazir olunca.

`winotify` ISTEGE BAGLI bir bagimliliktir; yoksa bildirim sessizce atlanir.
Uygulamanin kendi ekrani zaten her seyi gosteriyor, bildirim yalnizca
kolayliktir -- eksikligi hicbir seyi durdurmaz.
"""

from __future__ import annotations

import logging

from .source import is_supported

log = logging.getLogger("holocron.gorusme.bildirim")

UYGULAMA = "Holocron"


class WindowsBildirimci:
    """`BildirimProtokolu`nun gercek uygulamasi."""

    def gonder(self, baslik: str, metin: str) -> bool:
        if not is_supported():
            return False
        try:
            from winotify import Notification  # yerel ice aktarim
        except ImportError:
            return False
        try:
            Notification(app_id=UYGULAMA, title=baslik, msg=metin).show()
        except Exception:  # noqa: BLE001 - bildirim yuzunden hic bir sey olmesin
            log.debug("Bildirim gönderilemedi", exc_info=True)
            return False
        return True


class SessizBildirimci:
    """Hicbir sey yapmaz; bildirim ayari kapaliyken kullanilir."""

    def gonder(self, baslik: str, metin: str) -> bool:
        return False


def default_bildirimci() -> WindowsBildirimci:
    return WindowsBildirimci()
