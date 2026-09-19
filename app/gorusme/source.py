"""Gorusme notlarinin kaynak-bagimsiz modeli ve sozlesmeleri.

Burada ne Windows var ne ses kitapligi: veri tipleri, protokoller ve durum
adlari duruyor. Gercek uygulamalar (`algilayici.py`, `kayit.py`,
`yaziyadok.py`, `ozet.py`) ve testlerin sahteleri (`sahte.py`) ayni
sozlesmeyi uygular; is mantigi (`intake.py`, `kuyruk.py`, `takipci.py`)
hicbirine bakmadan sinanabilir.

Kayitlar kullanicinin KENDI makinesinde kalir; ses de transkript de
varsayilan olarak not hazir olunca silinir.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from ..teamscalls.source import CallsError


class GorusmeHatasi(CallsError):
    """Kullaniciya gosterilecek gorusme notu hatasi.

    `CallsError`'dan tureme: sunucunun mevcut hata isleyicisi bu ailenin
    tamamini ayni bicimde yanitliyor, yeni bir isleyici gerekmiyor.
    """


def unavailable() -> GorusmeHatasi:
    return GorusmeHatasi(
        "feature_unavailable",
        "Görüşme kaydı yalnız Windows'ta çalışır.",
        status=400,
    )


def is_supported() -> bool:
    """Algilama ve kayit yalnizca Windows'ta calisir."""
    return sys.platform.startswith("win")


# --- durumlar -----------------------------------------------------------

DURUM_KAYDEDILIYOR = "kaydediliyor"
DURUM_KUYRUKTA = "kuyrukta"
DURUM_BIRLESTIRILIYOR = "birlestiriliyor"
DURUM_YAZIYA_DOKULUYOR = "yaziya_dokuluyor"
DURUM_OZETLENIYOR = "ozetleniyor"
DURUM_HAZIR = "hazir"
DURUM_HATA = "hata"
DURUM_ATLANDI = "atlandi"

DURUMLAR: tuple[str, ...] = (
    DURUM_KAYDEDILIYOR,
    DURUM_KUYRUKTA,
    DURUM_BIRLESTIRILIYOR,
    DURUM_YAZIYA_DOKULUYOR,
    DURUM_OZETLENIYOR,
    DURUM_HAZIR,
    DURUM_HATA,
    DURUM_ATLANDI,
)

DURUM_ETIKETLERI: dict[str, str] = {
    DURUM_KAYDEDILIYOR: "kaydediliyor",
    DURUM_KUYRUKTA: "kuyrukta",
    DURUM_BIRLESTIRILIYOR: "birleştiriliyor",
    DURUM_YAZIYA_DOKULUYOR: "yazıya dökülüyor",
    DURUM_OZETLENIYOR: "özetleniyor",
    DURUM_HAZIR: "hazır",
    DURUM_HATA: "hata",
    DURUM_ATLANDI: "atlandı",
}

# Isleme sirasinda gecilen ara durumlar: uygulama bunlarin ortasinda
# kapanirsa is yarim kalmistir, acilista kuyruga geri alinir.
ARA_DURUMLAR: tuple[str, ...] = (
    DURUM_BIRLESTIRILIYOR,
    DURUM_YAZIYA_DOKULUYOR,
    DURUM_OZETLENIYOR,
)

# Bolum turleri (`gorusme_bolum.tur`).
BOLUM_OZET = "ozet"
BOLUM_KARAR = "karar"
BOLUM_AKSIYON = "aksiyon"
BOLUM_SORU = "soru"
BOLUM_TURLERI: tuple[str, ...] = (BOLUM_OZET, BOLUM_KARAR, BOLUM_AKSIYON, BOLUM_SORU)

BOLUM_ETIKETLERI: dict[str, str] = {
    BOLUM_OZET: "Özet",
    BOLUM_KARAR: "Kararlar",
    BOLUM_AKSIYON: "Aksiyonlar",
    BOLUM_SORU: "Açık sorular",
}

# Kanal adlari: mikrofon "Sen", hoparlor dongusu "Karsi taraf".
KANAL_MIK = "mik"
KANAL_HOP = "hop"
KANAL_ETIKETLERI: dict[str, str] = {KANAL_MIK: "Sen", KANAL_HOP: "Karşı taraf"}
# Ayni kanallarin AYGIT adlari: teshis ve hata metinleri bunu kullanir
# ("Sen" / "Karsi taraf" transkript etiketidir, mikrofonun adi degil).
KANAL_AYGIT_ETIKETLERI: dict[str, str] = {KANAL_MIK: "mikrofon", KANAL_HOP: "hoparlör"}

# Kayit bicimi: faster-whisper 16 kHz mono bekler, dosya da kucuk kalir.
ORNEK_HIZI = 16000
KANAL_SAYISI = 1
ORNEK_GENISLIGI = 2  # 16 bit

# Klasor adi bicimi: <YYYYMMDD-HHMMSS>
KLASOR_BICIMI = "%Y%m%d-%H%M%S"


def calisma_koku(ayar: str = "") -> Path:
    """Ses ve ara dosyalarin durdugu klasor.

    Ayar bossa Windows'ta `%LOCALAPPDATA%\\Holocron\\gorusme`, baska yerde
    (gelistirme ve testler) veri klasorunun altindaki `gorusme`. Windows disi
    yol bilerek `paths.home_dir()`e bagli: `HOLOCRON_HOME` ile tasinabildigi
    icin hicbir test gercek kullanici klasorune ses yazmaz.
    """
    secilen = str(ayar or "").strip()
    if secilen:
        return Path(secilen).expanduser()
    if is_supported():
        for isim in ("LOCALAPPDATA", "APPDATA"):
            deger = os.environ.get(isim)
            if deger:
                return Path(deger) / "Holocron" / "gorusme"
    from .. import paths

    return paths.home_dir() / "gorusme"


# --- veri tipleri -------------------------------------------------------


@dataclass(frozen=True)
class Aygitlar:
    """Kaydin yapilacagi iki aygit: mikrofon ve duyulan ses (dongu)."""

    mikrofon: str = ""
    hoparlor: str = ""
    mikrofon_ad: str = ""
    hoparlor_ad: str = ""

    def anahtar(self) -> str:
        """Aygit degisimini yakalamak icin karsilastirilan imza."""
        return f"{self.mikrofon}|{self.hoparlor}"

    def sozluk(self) -> dict[str, str]:
        return {
            "mikrofon": self.mikrofon,
            "hoparlor": self.hoparlor,
            "mikrofon_ad": self.mikrofon_ad or self.mikrofon,
            "hoparlor_ad": self.hoparlor_ad or self.hoparlor,
        }


@dataclass(frozen=True)
class AlgilamaDurumu:
    """Yoklamanin tek bir andaki cevabi."""

    aktif: bool = False
    aygitlar: Aygitlar = field(default_factory=Aygitlar)
    # "kayit_defteri" ya da "ses_oturumu": hangi sinyal soyledi.
    kaynak: str = ""


@dataclass(frozen=True)
class Parca:
    """Bir kanalin tek bir kayit parcasi (aygit degisince yenisi acilir).

    `bos`: aygit acildi ama tek cerceve gelmedi (VDI'nin sanal aygiti ya da
    ses calinmayan dongu). Birlestirme boyle bir parcayi atlar.
    """

    kanal: str
    sira: int
    yol: Path
    bos: bool = False


@dataclass(frozen=True)
class Segment:
    """Yaziya dokumun tek bir parcasi; zaman saniye cinsinden."""

    baslangic: float
    bitis: float
    metin: str


# --- sozlesmeler --------------------------------------------------------


@runtime_checkable
class AlgilayiciProtokolu(Protocol):
    """Gorusme basladi mi, hangi aygitlarla?"""

    def durum(self) -> AlgilamaDurumu: ...


@runtime_checkable
class KayitProtokolu(Protocol):
    """Iki kanali ayri dosyalara yazan kayitci."""

    def basla(self, klasor: Path, sira: int, aygitlar: Aygitlar) -> list[Parca]: ...

    def bitir(self) -> list[Parca]: ...

    def deneme(self, saniye: float, aygitlar: Aygitlar, klasor: Path) -> dict[str, Any]: ...


@runtime_checkable
class YaziyaDokucuProtokolu(Protocol):
    """Tek bir WAV dosyasini zaman damgali segmentlere cevirir."""

    def cevir(self, yol: Path, dil: str = "tr") -> list[Segment]: ...


@runtime_checkable
class OzetleyiciProtokolu(Protocol):
    """Transkript dosyasini modele verir, ham cikti dondurur."""

    def ozetle(self, transkript: Path, model: str, istem: str) -> "OzetCikti": ...


@dataclass(frozen=True)
class OzetCikti:
    """Ozetleyicinin ham cevabi: kod sifir degilse model reddetmis demektir.

    `dosya` BIRINCIL yoldur: model JSON'u stdout'a degil kendi yazdigi
    `ozet.json` dosyasina koyar; orasi ANSI renk kodlarindan, ilerleme
    satirlarindan ve banner'dan temizdir. `metin` (stdout) ile `hata`
    (stderr) yedek ayiklama ve teshis icindir.
    """

    kod: int
    metin: str
    hata: str = ""
    dosya: str = ""

    @property
    def basarili(self) -> bool:
        return self.kod == 0 and bool((self.dosya or self.metin).strip())


@runtime_checkable
class BildirimProtokolu(Protocol):
    """Masaustu bildirimi; desteklenmeyen yerde sessizce yutar."""

    def gonder(self, baslik: str, metin: str) -> bool: ...
