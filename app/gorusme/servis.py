"""Takipci + kuyrugu kurup uygulamaya baglayan ince katman.

`server.create_app` acilista bunu cagirir: yarim kalmis isler kuyruga geri
alinir, takip ayari "acilista acik" diyorsa anahtar acilir ve iki arka plan
is parcacigi baslar. Windows disinda (ve ses yakalama paketi yokken) servis
kurulur ama YOKLAMA yapmaz: ekran calisir, kayit yapilmaz.
"""

from __future__ import annotations

import logging
from typing import Any

from . import depo
from .intake import load_config
from .kuyruk import Kuyruk
from .takipci import Takipci
from .source import is_supported

log = logging.getLogger("holocron.gorusme.servis")


class Servis:
    """Takipci ve kuyrugu bir arada tutan kabuk."""

    def __init__(self, takipci: Takipci | None, kuyruk: Kuyruk) -> None:
        self.takipci = takipci
        self.kuyruk = kuyruk

    def start(self) -> None:
        self.kuyruk.start()
        if self.takipci is not None:
            self.takipci.start()

    def stop(self) -> None:
        if self.takipci is not None:
            self.takipci.stop()
        self.kuyruk.stop()

    # --- arayuzun sordugu -------------------------------------------

    def aktif(self) -> dict[str, Any] | None:
        return self.takipci.aktif_ozet() if self.takipci is not None else None

    def gorusme_suruyor(self) -> bool:
        return self.takipci.gorusme_suruyor() if self.takipci is not None else False


def _algilayici(context: Any, ayarlar: Any) -> Any:
    fabrika = getattr(context, "gorusme_algilayici_factory", None)
    if fabrika is None:
        return None
    try:
        return fabrika(ayarlar.mikrofon, ayarlar.hoparlor)
    except Exception:  # noqa: BLE001 - istege bagli paket yoksa takip kurulmaz
        log.info("Görüşme algılayıcısı kurulamadı; takip kapalı kalacak", exc_info=True)
        return None


def _kayitci(context: Any) -> Any:
    fabrika = getattr(context, "gorusme_kayit_factory", None)
    if fabrika is None:
        return None
    try:
        return fabrika()
    except Exception:  # noqa: BLE001
        log.info("Görüşme kayıtçısı kurulamadı; takip kapalı kalacak", exc_info=True)
        return None


def kur(context: Any) -> Servis:
    """Servisi kurar ve baslatir; hazir servis `context.gorusme` olur."""
    ayarlar = load_config(context.settings)
    # Acilista yarim kalmis isler kuyruga geri alinir.
    try:
        depo.yarim_isleri_kuyruga_al(context.connection())
    except Exception:  # noqa: BLE001 - bozuk veritabani acilisi engellemesin
        log.warning("Yarım kalmış görüşme işleri kuyruğa alınamadı", exc_info=True)

    if ayarlar.takip_acilista and is_supported():
        context.settings.set("calls.takip", "1")

    algilayici = _algilayici(context, ayarlar)
    kayitci = _kayitci(context)
    takipci = (
        Takipci(
            context,
            algilayici=algilayici,
            kayitci=kayitci,
            bildirimci=context.gorusme_bildirimci,
        )
        if algilayici is not None and kayitci is not None
        else None
    )
    kuyruk = Kuyruk(
        context,
        dokucu_factory=context.gorusme_dokucu_factory,
        ozetleyici_factory=context.gorusme_ozetleyici_factory,
        bildirimci=context.gorusme_bildirimci,
        gorusme_suruyor=(takipci.gorusme_suruyor if takipci is not None else None),
    )
    if takipci is not None:
        takipci.kuyruga_bagla(kuyruk)
    servis = Servis(takipci, kuyruk)
    context.gorusme = servis
    return servis
