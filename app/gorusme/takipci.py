"""Takip: iki saniyede bir yoklar, gorusme basladiginda kaydeder.

`lifecycle.Watchdog` ile ayni desen: tek bir arka plan is parcacigi, disaridan
verilebilen saat, testlerde beklemeden ilerletilebilen adimlar. Butun karar
`yokla()` icinde; testler o fonksiyonu elle cagirip is parcacigina hic
dokunmadan butun akisi sinayabilir.

Kurallar:

* Takip duraklatilmissa hicbir sey kaydedilmez; kayit sirasinda duraklatilirsa
  acik kayit kapatilir ve (yeterince uzunsa) kuyruga girer.
* Gorusme icinde aygit degisirse kayit yeni aygitta yeni PARCA olarak surer.
* Asgari surenin altinda kalan gorusme "atlandi" olur ve klasoru silinir.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from . import depo
from .birlestir import sil_klasor
from .intake import Ayarlar, iso, klasor_adi, load_config, utc_now
from .source import (
    DURUM_ATLANDI,
    DURUM_KAYDEDILIYOR,
    DURUM_KUYRUKTA,
    Aygitlar,
)

log = logging.getLogger("holocron.gorusme.takipci")

# Yoklama araligi: iki saniye, tasarimdaki deger.
YOKLAMA_SN = 2.0


class Takipci:
    """Algilayiciyi yoklar, kaydi baslatir/bitirir, kuyruga verir."""

    def __init__(
        self,
        context: Any,
        algilayici: Any,
        kayitci: Any,
        kuyruk: Any = None,
        bildirimci: Any = None,
        saat: Callable[[], datetime] = utc_now,
        tick: float = YOKLAMA_SN,
    ) -> None:
        self._context = context
        self._algilayici = algilayici
        self._kayitci = kayitci
        self._kuyruk = kuyruk
        self._bildirimci = bildirimci
        self._saat = saat
        self._tick = tick
        self._dur = threading.Event()
        self._thread: threading.Thread | None = None
        self._kilit = threading.RLock()
        # Acik kayit: not kimligi, baslangic, klasor, parca sirasi, aygit imzasi.
        self._aktif: dict[str, Any] | None = None

    # --- yasam dongusu -------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._dongu, name="holocron-gorusme-takip", daemon=True
        )
        self._thread.start()

    def kuyruga_bagla(self, kuyruk: Any) -> None:
        """Kuyruk takipciden sonra kuruluyor: bagi burada tamamlariz."""
        self._kuyruk = kuyruk

    def stop(self) -> None:
        self._dur.set()
        with self._kilit:
            if self._aktif is not None:
                self._bitir(load_config(self._context.settings))

    def _dongu(self) -> None:
        while not self._dur.wait(self._tick):
            try:
                self.yokla()
            except Exception:  # noqa: BLE001 - takip hicbir zaman olmemeli
                log.warning("Görüşme takibi hata verdi", exc_info=True)

    # --- karar ----------------------------------------------------------

    def yokla(self, ayarlar: Ayarlar | None = None) -> dict[str, Any] | None:
        """Tek bir yoklama adimi; acik kaydin ozetini dondurur."""
        secenekler = ayarlar or load_config(self._context.settings)
        with self._kilit:
            if not secenekler.takip:
                # Duraklatilmis: acik kayit varsa kapatilir, yenisi acilmaz.
                if self._aktif is not None:
                    self._bitir(secenekler)
                return None
            durum = self._algilayici.durum()
            if durum.aktif and self._aktif is None:
                self._basla(secenekler, durum.aygitlar)
            elif durum.aktif and self._aktif is not None:
                self._aygit_degisimi(durum.aygitlar)
            elif not durum.aktif and self._aktif is not None:
                self._bitir(secenekler)
            return self.aktif_ozet()

    def gorusme_suruyor(self) -> bool:
        """Kuyruk bunu sorar: gorusme surerken isleme bekleyebilir."""
        with self._kilit:
            return self._aktif is not None

    def aktif_ozet(self) -> dict[str, Any] | None:
        """Canli kayit rozeti: not kimligi, baslangic, gecen sure."""
        with self._kilit:
            if self._aktif is None:
                return None
            gecen = int((self._saat() - self._aktif["baslangic"]).total_seconds())
            return {
                "id": self._aktif["not_id"],
                "baslangic": iso(self._aktif["baslangic"]),
                "sure_sn": max(0, gecen),
                "aygitlar": self._aktif["aygitlar"].sozluk(),
            }

    # --- adimlar --------------------------------------------------------

    def _basla(self, ayarlar: Ayarlar, aygitlar: Aygitlar) -> None:
        baslangic = self._saat()
        klasor = ayarlar.kok() / klasor_adi(baslangic)
        klasor.mkdir(parents=True, exist_ok=True)
        conn = self._context.connection()
        not_id = depo.not_olustur(
            conn, iso(baslangic), klasor=str(klasor), durum=DURUM_KAYDEDILIYOR
        )
        self._kayitci.basla(klasor, 1, aygitlar)
        self._aktif = {
            "not_id": not_id,
            "baslangic": baslangic,
            "klasor": klasor,
            "sira": 1,
            "aygitlar": aygitlar,
        }
        log.info("Görüşme kaydı başladı: not %s", not_id)
        if ayarlar.bildirim_baslangic and self._bildirimci is not None:
            self._bildirimci.gonder(
                "Görüşme kaydediliyor",
                "Karşı tarafa kaydettiğini söylemeyi unutma.",
            )

    def _aygit_degisimi(self, aygitlar: Aygitlar) -> None:
        """Aygit degistiyse yeni parca acilir; parcalar sonra birlestirilir."""
        aktif = self._aktif
        if aktif is None or aygitlar.anahtar() == aktif["aygitlar"].anahtar():
            return
        aktif["sira"] += 1
        self._kayitci.basla(aktif["klasor"], aktif["sira"], aygitlar)
        aktif["aygitlar"] = aygitlar
        log.info("Kayıt aygıtı değişti, yeni parça: %s", aktif["sira"])

    def _bitir(self, ayarlar: Ayarlar) -> None:
        aktif = self._aktif
        self._aktif = None
        if aktif is None:
            return
        self._kayitci.bitir()
        sure = max(0, int((self._saat() - aktif["baslangic"]).total_seconds()))
        conn = self._context.connection()
        not_id = int(aktif["not_id"])
        depo.not_guncelle(conn, not_id, bitis=iso(self._saat()), sure_sn=sure)
        if sure < ayarlar.min_saniye:
            # Cok kisa gorusme hic islenmez: listede "atlandi" satiri kalir.
            sil_klasor(Path(aktif["klasor"]))
            depo.not_guncelle(conn, not_id, durum=DURUM_ATLANDI, klasor="")
            log.info("Görüşme asgari sürenin altında, atlandı: not %s", not_id)
            return
        depo.durum_yaz(conn, not_id, DURUM_KUYRUKTA)
        if self._kuyruk is not None:
            self._kuyruk.uyandir()
        log.info("Görüşme kuyruğa alındı: not %s (%s sn)", not_id, sure)
