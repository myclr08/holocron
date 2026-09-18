"""Bellek ici sahteler: algilayici, kayitci, yaziya dokucu, ozetleyici.

`tests/` altinda degil `app/gorusme/` icinde duruyor: API testleri de ayni
sahteleri baglayabilsin, Windows olmayan makinede gorusme basla -> kuyruk ->
not hazir akisi uctan uca sinanabilsin diye. Uretimde kimse bunu kullanmaz.

Ornek adlar bilerek uydurmadir (`Örnek Kişi`), hicbir sirket icerigi yoktur.
"""

from __future__ import annotations

import array
import json
import math
import threading
import time
from pathlib import Path
from typing import Any, Iterable

from . import birlestir
from .kayit import BLOK, parca_adi, sonuc_ozeti
from .source import (
    KANAL_HOP,
    KANAL_MIK,
    ORNEK_HIZI,
    AlgilamaDurumu,
    Aygitlar,
    OzetCikti,
    Parca,
    Segment,
)

# Sahte aygitlar: gercek bir aygit kimligiyle karismasin diye konusan adlar.
AYGITLAR = Aygitlar(
    mikrofon="sahte-mik",
    hoparlor="sahte-hop",
    mikrofon_ad="Örnek Mikrofon",
    hoparlor_ad="Örnek Hoparlör (döngü)",
)

IKINCI_AYGITLAR = Aygitlar(
    mikrofon="sahte-mik-2",
    hoparlor="sahte-hop-2",
    mikrofon_ad="İkinci Mikrofon",
    hoparlor_ad="İkinci Hoparlör (döngü)",
)


class SahteAlgilayici:
    """Gorusme basladi/bitti kararini test veriyor."""

    def __init__(self, aktif: bool = False, aygitlar: Aygitlar = AYGITLAR) -> None:
        self.aktif = aktif
        self.aygitlar = aygitlar
        self.okuma = 0

    def durum(self) -> AlgilamaDurumu:
        self.okuma += 1
        return AlgilamaDurumu(
            aktif=self.aktif,
            aygitlar=self.aygitlar,
            kaynak="kayit_defteri" if self.aktif else "",
        )

    # --- kolaylik -----------------------------------------------------

    def basla(self, aygitlar: Aygitlar | None = None) -> None:
        self.aktif = True
        if aygitlar is not None:
            self.aygitlar = aygitlar

    def bitir(self) -> None:
        self.aktif = False


def sentetik_cerceve(saniye: float, ton: int = 220, sessiz: bool = False) -> bytes:
    """Kisa bir sinus (ya da sessizlik): gercek ses kartina gerek yok."""
    import array
    import math

    ornek_sayisi = int(ORNEK_HIZI * max(0.0, saniye))
    veri = array.array("h")
    for sira in range(ornek_sayisi):
        if sessiz:
            veri.append(0)
            continue
        veri.append(int(12000 * math.sin(2 * math.pi * ton * sira / ORNEK_HIZI)))
    return veri.tobytes()


class SahteKayitci:
    """Diske gercek ama sentetik WAV yazar; ses karti kullanilmaz."""

    def __init__(self, parca_sn: float = 0.2, sessiz: bool = False) -> None:
        self.parca_sn = parca_sn
        self.sessiz = sessiz
        self.acik: list[Parca] = []
        self.yazilan: list[Parca] = []
        self.basla_cagrisi = 0
        self.bitir_cagrisi = 0

    def basla(self, klasor: Path, sira: int, aygitlar: Aygitlar) -> list[Parca]:
        self.basla_cagrisi += 1
        self.bitir()
        klasor.mkdir(parents=True, exist_ok=True)
        self.acik = []
        for kanal, ton in ((KANAL_MIK, 220), (KANAL_HOP, 330)):
            yol = klasor / parca_adi(kanal, sira)
            birlestir.yaz(yol, sentetik_cerceve(self.parca_sn, ton, self.sessiz))
            self.acik.append(Parca(kanal=kanal, sira=sira, yol=yol))
        return list(self.acik)

    def bitir(self) -> list[Parca]:
        self.bitir_cagrisi += 1
        uretilen = [parca for parca in self.acik if parca.yol.exists()]
        self.yazilan.extend(uretilen)
        self.acik = []
        return uretilen

    def deneme(self, saniye: float, aygitlar: Aygitlar, klasor: Path) -> dict[str, Any]:
        parcalar = self.basla(klasor, 0, aygitlar)
        self.bitir()
        return sonuc_ozeti(parcalar, klasor)


class SahteYaziyaDokucu:
    """Kanal dosyasina gore sabit segmentler dondurur."""

    def __init__(self, segmentler: dict[str, list[Segment]] | None = None) -> None:
        self.segmentler = segmentler or {
            KANAL_MIK: [
                Segment(0.0, 3.0, "Raporlama servisini ekim ortasında taşıyoruz."),
                Segment(8.0, 11.0, "Geri dönüş planını ben yazarım."),
            ],
            KANAL_HOP: [
                Segment(3.5, 7.0, "Kabul testi senaryolarını hazırlayayım mı?"),
                Segment(12.0, 15.0, "Eski uç noktalar ne zaman kapanıyor?"),
            ],
        }
        self.cagri = 0

    def cevir(self, yol: Path, dil: str = "tr") -> list[Segment]:
        self.cagri += 1
        kanal = KANAL_HOP if yol.stem.startswith(KANAL_HOP) else KANAL_MIK
        return list(self.segmentler.get(kanal, []))


ORNEK_OZET: dict[str, Any] = {
    "baslik": "Raporlama servisi geçişi ve test planı",
    "ozet": [
        "Raporlama servisi ekim ortasında yeni altyapıya taşınacak.",
        "Test planı üç aşamalı: birim, entegrasyon, kullanıcı kabul.",
    ],
    "kararlar": ["Geçiş tarihi 15 Ekim, geri dönüş planı zorunlu."],
    "aksiyonlar": [
        {"metin": "Geri dönüş planını yaz", "kisi": "Ben", "son_tarih": "2026-09-25"},
        {"metin": "Kabul testi senaryolarını hazırla", "kisi": "Örnek Kişi", "son_tarih": ""},
    ],
    "sorular": ["Eski raporların arşivi kimde kalacak?"],
}


class SahteOzetleyici:
    """Copilot CLI yerine hazir JSON dondurur; model reddi taklit edilebilir."""

    def __init__(
        self,
        cikti: dict[str, Any] | None = None,
        reddedilen: Iterable[str] = (),
        kabuk: str = "İşte not:\n```json\n{govde}\n```\nHazır.",
    ) -> None:
        self.cikti = ORNEK_OZET if cikti is None else cikti
        self.reddedilen = set(reddedilen)
        self.kabuk = kabuk
        self.cagrilar: list[tuple[str, Path]] = []

    def ozetle(self, transkript: Path, model: str, istem: str) -> OzetCikti:
        self.cagrilar.append((model, transkript))
        if model in self.reddedilen:
            return OzetCikti(kod=1, metin="", hata=f"model reddedildi: {model}")
        govde = json.dumps(self.cikti, ensure_ascii=False)
        return OzetCikti(kod=0, metin=self.kabuk.format(govde=govde))


class SahteBildirimci:
    """Gonderilen bildirimleri toplar."""

    def __init__(self) -> None:
        self.mesajlar: list[tuple[str, str]] = []

    def gonder(self, baslik: str, metin: str) -> bool:
        self.mesajlar.append((baslik, metin))
        return True


class SahteKurucu:
    """`yaziyadok.kur`un sahtesi: hicbir test gercekten pip calistirmaz."""

    def __init__(self, kuruldu: bool = True) -> None:
        self.kuruldu = kuruldu
        self.cagrilar: list[str] = []

    def __call__(self, proxy: str = "") -> dict[str, Any]:
        self.cagrilar.append(proxy)
        if self.kuruldu:
            return {
                "kuruldu": True,
                "kaynak": "yerel",
                "mesaj": "faster-whisper kuruldu (yanınızdaki tekerleklerden).",
                "cikti": "",
            }
        return {
            "kuruldu": False,
            "kaynak": "",
            "mesaj": "Kurulum yapılamadı: paket sunucusuna ulaşılamadı.",
            "cikti": "",
        }


# --- sahte ses karti ------------------------------------------------------
#
# `WasapiKayitci` gercek `pyaudiowpatch` yerine bu moduller uzerinden de
# calisabilir (`WasapiKayitci(modul=...)`). Sahadaki hata -- aygit acildi ama
# tek cerceve gelmedi -- ancak boyle sinanabiliyor: gercek ses kartinda
# "susan aygit" uretmenin yolu yok.

# Sahte aygit tablosu: 0 mikrofon, 1 dongu (loopback) aygiti.
SAHTE_AYGIT_BILGISI: dict[int, dict[str, Any]] = {
    0: {
        "index": 0,
        "name": "Örnek Mikrofon",
        "hostApi": 2,
        "defaultSampleRate": 48000.0,
        "maxInputChannels": 1,
        "maxOutputChannels": 0,
        "isLoopbackDevice": False,
    },
    1: {
        "index": 1,
        "name": "Örnek Hoparlör (döngü)",
        "hostApi": 2,
        "defaultSampleRate": 48000.0,
        "maxInputChannels": 2,
        "maxOutputChannels": 2,
        "isLoopbackDevice": True,
    },
}

# Yukaridaki tabloya isaret eden aygit secimi (kimlikler aygit index'idir).
AYGIT_INDEKSLERI = Aygitlar(
    mikrofon="0",
    hoparlor="1",
    mikrofon_ad="Örnek Mikrofon",
    hoparlor_ad="Örnek Hoparlör (döngü)",
)


class SahteAkis:
    """Geri cagri kipinde calisan sahte PortAudio akisi."""

    def __init__(self, kart: "SahteSesKarti", kwargs: dict[str, Any]) -> None:
        self.kart = kart
        self.kwargs = dict(kwargs)
        self.kapandi = False
        self.durduruldu = False
        self.yazilan = 0
        self._dur = threading.Event()
        self._thread: threading.Thread | None = None
        if kwargs.get("stream_callback") is not None:
            self._thread = threading.Thread(target=self._besle, daemon=True)
            self._thread.start()

    # --- giris (kayit) --------------------------------------------------

    def _besle(self) -> None:
        geri_cagri = self.kwargs["stream_callback"]
        kanal = int(self.kwargs.get("channels", 1) or 1)
        blok = self.kart.blok_verisi(kanal)
        kalan = self.kart.bloklar
        while not self._dur.is_set() and (self.kart.sonsuz or kalan > 0):
            geri_cagri(blok, BLOK, None, 0)
            kalan -= 1
            time.sleep(0.001)

    # --- cikis (deneme sinyali) ------------------------------------------

    def write(self, veri: bytes) -> None:
        self.yazilan += 1
        self.kart.calan_blok += 1
        time.sleep(0.002)

    # --- kapanis ---------------------------------------------------------

    def stop_stream(self) -> None:
        self.durduruldu = True
        if not self.kart.sonsuz:
            self._dur.set()

    def close(self) -> None:
        self.kapandi = True
        if not self.kart.sonsuz:
            self._dur.set()

    def durdur(self) -> None:
        """Testin sonunda takilan beslemeyi de keser."""
        self._dur.set()


class SahteSesKarti:
    """`pyaudiowpatch.PyAudio()` yerine gecen sahte ses karti.

    `bloklar=0`: aygit acilir ama tek cerceve gelmez (sahadaki VDI hatasi).
    `acilis_hatasi=n`: ilk n giris acilisi duser (yedek bicim sinanir).
    `sonsuz=True`: durdurma komutu dinlenmez (takilan yazici sinanir).
    """

    def __init__(
        self,
        bloklar: int = 4,
        sessiz: bool = False,
        acilis_hatasi: int = 0,
        sonsuz: bool = False,
        aygitlar: dict[int, dict[str, Any]] | None = None,
    ) -> None:
        self.bloklar = bloklar
        self.sessiz = sessiz
        self.acilis_hatasi = acilis_hatasi
        self.sonsuz = sonsuz
        self.aygitlar = aygitlar or SAHTE_AYGIT_BILGISI
        self.acilislar: list[dict[str, Any]] = []
        self.akislar: list[SahteAkis] = []
        self.sonlandi = False
        self.calan_blok = 0

    # --- pyaudio yuzeyi --------------------------------------------------

    def get_format_from_width(self, genislik: int) -> int:
        return 8  # pyaudio.paInt16

    def get_device_info_by_index(self, index: int) -> dict[str, Any]:
        if int(index) not in self.aygitlar:
            raise OSError(f"[Errno -9996] Invalid device index {index}")
        return dict(self.aygitlar[int(index)])

    def get_default_output_device_info(self) -> dict[str, Any]:
        return dict(self.aygitlar[1])

    def open(self, **kwargs: Any) -> SahteAkis:
        self.acilislar.append(dict(kwargs))
        if kwargs.get("input") and self.acilis_hatasi > 0:
            self.acilis_hatasi -= 1
            raise OSError("[Errno -9997] Invalid sample rate")
        akis = SahteAkis(self, kwargs)
        self.akislar.append(akis)
        return akis

    def terminate(self) -> None:
        self.sonlandi = True
        self.hepsini_durdur()

    # --- yardimcilar -----------------------------------------------------

    def hepsini_durdur(self) -> None:
        for akis in self.akislar:
            akis.durdur()

    def blok_verisi(self, kanal: int) -> bytes:
        """Bir bloktan olusan ham veri (sessiz ya da duyulur sinus)."""
        veri = array.array("h")
        for sira in range(BLOK):
            deger = 0 if self.sessiz else int(9000 * math.sin(2 * math.pi * 440 * sira / 48000))
            for _ in range(max(1, kanal)):
                veri.append(deger)
        return veri.tobytes()

    @property
    def giris_acilislari(self) -> list[dict[str, Any]]:
        return [kwargs for kwargs in self.acilislar if kwargs.get("input")]


class SahtePyAudio:
    """`pyaudiowpatch` modulunun yerine gecer: tek bir sahte kart dondurur."""

    def __init__(self, kart: SahteSesKarti | None = None) -> None:
        self.kart = kart or SahteSesKarti()
        self.cagri = 0

    def PyAudio(self) -> SahteSesKarti:  # noqa: N802 - pyaudio'nun adi
        self.cagri += 1
        return self.kart
