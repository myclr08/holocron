"""Isleme kuyrugu: birlestir -> yaziya dok -> ozetle -> temizle.

Tek isci is parcacigi, SIRALI is: aynı anda tek gorusme islenir, digerleri
bekler. Sebep basit -- yaziya dokme CPU'yu doyuruyor, iki is birbirini
yavaslatir ve kullanici o sirada baska bir gorusmede olabilir.

Durum gecisleri her adimda tabloya yazilir: arayuz hattin neresinde
oldugunu anlik gorur, uygulama kapanip acilirsa yarim is kuyruktan surer
(`depo.yarim_isleri_kuyruga_al`).

Hata durumunda ses SILINMEZ; satir "hata" olur ve "Yeniden dene" ucu ayni
klasorden devam eder.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any, Callable, Sequence

from .. import repository
from . import birlestir, depo, ozet as ozet_modulu
from .birlestir import sil_klasor
from .intake import Ayarlar, load_config, sure_metni
from .source import (
    DURUM_BIRLESTIRILIYOR,
    DURUM_HAZIR,
    DURUM_KUYRUKTA,
    DURUM_OZETLENIYOR,
    DURUM_YAZIYA_DOKULUYOR,
    KANAL_AYGIT_ETIKETLERI,
    KANAL_HOP,
    KANAL_MIK,
    GorusmeHatasi,
)
from .yaziyadok import default_dokucu, harmanla

log = logging.getLogger("holocron.gorusme.kuyruk")

# Isci bosta bu araliklarla bakar; uyandirildiginda beklemeden devam eder.
BEKLEME_SN = 2.0

BIRLESIK_MIK = "mik.wav"
BIRLESIK_HOP = "hop.wav"
TRANSKRIPT_DOSYASI = "transkript.txt"


class Kuyruk:
    """Sirali isci: kuyruktaki notlari tek tek isler."""

    def __init__(
        self,
        context: Any,
        dokucu_factory: Callable[[Ayarlar], Any] | None = None,
        ozetleyici_factory: Callable[[Ayarlar], Any] | None = None,
        bildirimci: Any = None,
        gorusme_suruyor: Callable[[], bool] | None = None,
        bekleme: float = BEKLEME_SN,
    ) -> None:
        self._context = context
        self._dokucu_factory = dokucu_factory or _varsayilan_dokucu
        self._ozetleyici_factory = ozetleyici_factory or _varsayilan_ozetleyici
        self._bildirimci = bildirimci
        self._gorusme_suruyor = gorusme_suruyor or (lambda: False)
        self._bekleme = bekleme
        self._uyan = threading.Event()
        self._dur = threading.Event()
        # Ayni anda tek is: arka plan iscisi ile elle tetiklenen isleme
        # birbirinin uzerine binmesin.
        self._is_kilidi = threading.Lock()
        self._thread: threading.Thread | None = None

    # --- yasam dongusu -------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._dongu, name="holocron-gorusme", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._dur.set()
        self._uyan.set()

    def uyandir(self) -> None:
        self._uyan.set()

    def _dongu(self) -> None:
        while not self._dur.is_set():
            try:
                self.sirayi_isle()
            except Exception:  # noqa: BLE001 - isci hicbir zaman olmemeli
                log.warning("Görüşme kuyruğu hata verdi", exc_info=True)
            self._uyan.wait(self._bekleme)
            self._uyan.clear()

    # --- is akisi ------------------------------------------------------

    def sirayi_isle(self, sinir: int = 0) -> int:
        """Kuyruktaki notlari sirayla isler; islenen sayisini dondurur."""
        ayarlar = load_config(self._context.settings)
        islenen = 0
        with self._is_kilidi:
            while not self._dur.is_set():
                if ayarlar.isleme_gorusme_disinda and self._gorusme_suruyor():
                    # Gorusme surerken CPU'yu yormayalim: is sirada bekler.
                    break
                sira = depo.kuyruktan_al(self._baglanti())
                if sira is None:
                    break
                self.isle(int(sira["id"]), ayarlar)
                islenen += 1
                if sinir and islenen >= sinir:
                    break
        return islenen

    def _baglanti(self) -> Any:
        return self._context.connection()

    def isle(self, not_id: int, ayarlar: Ayarlar | None = None) -> dict[str, Any]:
        """Tek bir notu bastan sona isler. Hata durumunda ses silinmez."""
        secenekler = ayarlar or load_config(self._context.settings)
        conn = self._baglanti()
        veri = depo.require_not(conn, not_id)
        klasor = Path(veri["klasor"]) if veri["klasor"] else secenekler.kok() / str(not_id)
        try:
            birlesik = self._birlestir(conn, not_id, klasor)
            metin = self._yaziya_dok(conn, not_id, secenekler, birlesik, klasor)
            self._ozetle(conn, not_id, secenekler, metin, klasor)
        except GorusmeHatasi as hata:
            depo.hataya_dus(conn, not_id, str(hata))
            log.warning("Görüşme notu işlenemedi (%s): %s", not_id, hata)
            return depo.require_not(conn, not_id)
        except Exception as hata:  # noqa: BLE001 - beklenmeyen hata da satira yazilir
            depo.hataya_dus(conn, not_id, str(hata)[:500])
            log.warning("Görüşme notu işlenemedi (%s)", not_id, exc_info=True)
            return depo.require_not(conn, not_id)
        self._temizle(conn, not_id, secenekler, klasor)
        return depo.require_not(conn, not_id)

    # --- asamalar -------------------------------------------------------

    def _birlestir(self, conn: Any, not_id: int, klasor: Path) -> dict[str, Path | None]:
        depo.durum_yaz(conn, not_id, DURUM_BIRLESTIRILIYOR)
        basladi = time.monotonic()
        sonuc: dict[str, Path | None] = {}
        bos_kanallar: list[str] = []
        for kanal, ad in ((KANAL_MIK, BIRLESIK_MIK), (KANAL_HOP, BIRLESIK_HOP)):
            parcalar = sorted(klasor.glob(f"{kanal}-*.wav")) if klasor.exists() else []
            sonuc[kanal] = birlestir.birlestir(parcalar, klasor / ad)
            if sonuc[kanal] is None and parcalar:
                # Parca dosyasi var ama icinde tek cerceve yok: aygit acildi,
                # veri gelmedi. Kullaniciya bunu aynen soyleriz.
                bos_kanallar.append(KANAL_AYGIT_ETIKETLERI.get(kanal, kanal))
        if sonuc[KANAL_MIK] is None and sonuc[KANAL_HOP] is None:
            if bos_kanallar:
                ayrinti = ", ".join(f"{ad}: veri gelmedi" for ad in bos_kanallar)
                raise GorusmeHatasi("ses_alinamadi", f"Ses alınamadı ({ayrinti}).")
            raise GorusmeHatasi("ses_yok", "Ses dosyası bulunamadı, not üretilemedi.")
        depo.not_guncelle(
            conn, not_id, isleme_sn_birlestirme=int(time.monotonic() - basladi)
        )
        return sonuc

    def _yaziya_dok(
        self,
        conn: Any,
        not_id: int,
        ayarlar: Ayarlar,
        birlesik: dict[str, Path | None],
        klasor: Path,
    ) -> str:
        depo.durum_yaz(conn, not_id, DURUM_YAZIYA_DOKULUYOR)
        basladi = time.monotonic()
        dokucu = self._dokucu_factory(ayarlar)
        segmentler = {}
        for kanal in (KANAL_MIK, KANAL_HOP):
            yol = birlesik.get(kanal)
            segmentler[kanal] = dokucu.cevir(yol) if yol is not None else []
        metin = harmanla(segmentler[KANAL_MIK], segmentler[KANAL_HOP])
        if not metin.strip():
            raise GorusmeHatasi("bos_transkript", "Kayıtta konuşma bulunamadı.")
        (klasor / TRANSKRIPT_DOSYASI).write_text(metin, encoding="utf-8")
        depo.not_guncelle(
            conn, not_id, isleme_sn_yaziya_dokme=int(time.monotonic() - basladi)
        )
        return metin

    def _ozetle(
        self, conn: Any, not_id: int, ayarlar: Ayarlar, metin: str, klasor: Path
    ) -> None:
        depo.durum_yaz(conn, not_id, DURUM_OZETLENIYOR)
        basladi = time.monotonic()
        veri = depo.require_not(conn, not_id)
        kisiler = [kisi["ad"] or kisi["kimlik"] for kisi in depo.katilimcilar(conn, not_id)]
        istem = ozet_modulu.istem_kur(
            ayarlar.sablon_metni(),
            klasor / TRANSKRIPT_DOSYASI,
            katilimcilar=kisiler,
            tarih=veri["baslangic"],
            sure=sure_metni(veri["sure_sn"]),
        )
        sonuc = ozet_modulu.calistir(
            self._ozetleyici_factory(ayarlar),
            klasor / TRANSKRIPT_DOSYASI,
            ayarlar.model_sirasi(),
            istem,
        )
        if not sonuc.basarili:
            raise GorusmeHatasi("ozet_yok", f"Özet alınamadı: {sonuc.hata}")

        depo.bolumleri_yaz(conn, not_id, ozet_modulu.bolumler(sonuc.veri))
        baslik = ozet_modulu.baslik(sonuc.veri, yedek=veri["baslik"])
        depo.not_guncelle(
            conn,
            not_id,
            baslik=baslik,
            model=sonuc.model,
            isleme_sn_ozet=int(time.monotonic() - basladi),
            hata="",
        )
        # Calisan model bir sonraki gorusmede basa alinir.
        self._context.settings.set("calls.ozet_model_son", sonuc.model)
        if ayarlar.transkripti_sakla:
            depo.transkript_yaz(conn, not_id, metin)
        self._aranabilir_yap(conn, not_id, baslik)

    def _aranabilir_yap(self, conn: Any, not_id: int, baslik: str) -> None:
        parcalar = [baslik] + [satir["metin"] for satir in depo.bolumler(conn, not_id)]
        depo.fts_yaz(conn, not_id, "\n".join(parca for parca in parcalar if parca))

    def _temizle(self, conn: Any, not_id: int, ayarlar: Ayarlar, klasor: Path) -> None:
        """Not hazir: ses ve ara dosyalar silinir (ayara bagli)."""
        if not ayarlar.sesi_sakla:
            sil_klasor(klasor)
        depo.durum_yaz(conn, not_id, DURUM_HAZIR)
        veri = depo.require_not(conn, not_id)
        if ayarlar.bildirim_hazir and self._bildirimci is not None:
            self._bildirimci.gonder(
                "Görüşme notu hazır", veri["baslik"] or "Görüşme notu hazır."
            )

    # --- yeniden deneme -------------------------------------------------

    def yeniden_dene(self, not_id: int) -> dict[str, Any]:
        """Hataya dusmus notu kuyruga geri koyar."""
        conn = self._baglanti()
        depo.require_not(conn, not_id)
        depo.durum_yaz(conn, not_id, DURUM_KUYRUKTA, "")
        self.uyandir()
        return depo.require_not(conn, not_id)

    def eksik_paket_islerini_kuyruga_al(self) -> int:
        """Yaziya dokme paketi kurulunca bekleyen hatali satirlari surdurur."""
        sayi = depo.eksik_paket_hatalarini_kuyruga_al(self._baglanti())
        if sayi:
            self.uyandir()
        return sayi

    def yeniden_ozetle(self, not_id: int) -> dict[str, Any]:
        """Saklanmis transkriptten ozeti yeniden uretir (ses gerekmez)."""
        conn = self._baglanti()
        veri = depo.require_not(conn, not_id)
        metin = depo.transkript(conn, not_id)
        if not metin.strip():
            raise GorusmeHatasi(
                "transkript_yok", "Transkript saklanmadığı için yeniden özetlenemez."
            )
        ayarlar = load_config(self._context.settings)
        klasor = Path(veri["klasor"]) if veri["klasor"] else ayarlar.kok() / str(not_id)
        klasor.mkdir(parents=True, exist_ok=True)
        (klasor / TRANSKRIPT_DOSYASI).write_text(metin, encoding="utf-8")
        try:
            self._ozetle(conn, not_id, ayarlar, metin, klasor)
        except GorusmeHatasi as hata:
            depo.hataya_dus(conn, not_id, str(hata))
            raise
        depo.durum_yaz(conn, not_id, DURUM_HAZIR)
        sil_klasor(klasor)
        return depo.require_not(conn, not_id)


def _varsayilan_dokucu(ayarlar: Ayarlar) -> Any:
    return default_dokucu(ayarlar.whisper_model, ayarlar.whisper_klasor)


def _varsayilan_ozetleyici(ayarlar: Ayarlar) -> Any:
    return ozet_modulu.default_ozetleyici(
        proxy=ayarlar.copilot_proxy, jira_base_url=ayarlar.jira_base_url
    )


def gorev_uret(
    context: Any, not_id: int, bolum_id: int
) -> dict[str, Any]:
    """Aksiyondan "Gorevlerim" karti uretir ve nota baglar."""
    from .intake import gorev_yuku

    conn = context.connection()
    veri = depo.require_not(conn, not_id)
    satir = next(
        (item for item in depo.bolumler(conn, not_id) if int(item["id"]) == int(bolum_id)), None
    )
    if satir is None:
        raise repository.RepositoryError("not_found", "Aksiyon bulunamadı.", status=404)
    yuk = gorev_yuku(satir, not_id, veri["baslik"])
    gorev = repository.create_task(
        conn,
        title=yuk["title"],
        description=yuk["description"],
        due_date=yuk["due_date"] or None,
        issue_key=depo.jira_key(conn, not_id) or None,
    )
    depo.gorev_bagla(conn, not_id, int(gorev["id"]))
    return gorev
