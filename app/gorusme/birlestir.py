"""WAV isleri: yazma, olcme, parcalari birlestirme. Yalnizca stdlib `wave`.

ffmpeg YOK: kullanicinin makinesine kurulmasi gereken bir sey eklemiyoruz.
Kayit zaten 16 kHz mono 16 bit uretiyor, o yuzden birlestirme ham cerceveleri
uc uca eklemekten ibaret; aygit degisince acilan parcalar zaman sirasiyla
(dosya adindaki sira numarasiyla) birbirine eklenir.

Iki kanal AYRI dosyada kalir: yaziya dokumde mikrofon kanali "Sen", dongu
kanali "Karsi taraf" olarak etiketlenecek. Tek dosyaya karistirma yalnizca
kullanici sesi saklamak isterse ("mix" ucunda) anlamlidir; orada da basit
ortalama kullanilir.
"""

from __future__ import annotations

import array
import logging
import math
import shutil
import wave
from pathlib import Path
from typing import Iterable, Sequence

from .source import KANAL_SAYISI, ORNEK_GENISLIGI, ORNEK_HIZI

log = logging.getLogger("holocron.gorusme.birlestir")


def sil_klasor(klasor: Path) -> bool:
    """Ses ve ara dosyalarin durdugu klasoru siler (not hazir ya da atlandi)."""
    try:
        if klasor.exists():
            shutil.rmtree(klasor)
            return True
    except OSError:
        log.warning("Görüşme klasörü silinemedi: %s", klasor, exc_info=True)
    return False


def yaz(yol: Path, cerceveler: bytes, hiz: int = ORNEK_HIZI) -> Path:
    """Ham 16 bit mono cerceveleri WAV olarak yazar."""
    yol.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(yol), "wb") as dosya:
        dosya.setnchannels(KANAL_SAYISI)
        dosya.setsampwidth(ORNEK_GENISLIGI)
        dosya.setframerate(hiz)
        dosya.writeframes(cerceveler)
    return yol


def oku(yol: Path) -> tuple[bytes, int]:
    """Cerceveleri ve ornek hizini dondurur; okunamayan dosya BOS doner.

    Burada istisna atmiyoruz ve bu bilincli bir karar: sahada 0 baytlik bir
    WAV (aygit hic veri vermemis, basligi bile yazilmamis) `wave.open`ta
    `EOFError` atip deneme kaydi ucunu 500'e dusuruyordu. Okunamayan dosya
    "ses yok" demektir, cokme sebebi degil.
    """
    try:
        with wave.open(str(yol), "rb") as dosya:
            return dosya.readframes(dosya.getnframes()), dosya.getframerate() or ORNEK_HIZI
    except Exception:  # noqa: BLE001 - bos, eksik ya da bozuk dosya
        log.debug("WAV okunamadı: %s", yol, exc_info=True)
        return b"", ORNEK_HIZI


def cerceve_sayisi(yol: Path) -> int:
    """Dosyadaki cerceve sayisi; okunamayan ya da bos dosyada sifir."""
    try:
        with wave.open(str(yol), "rb") as dosya:
            return int(dosya.getnframes())
    except Exception:  # noqa: BLE001
        return 0


def bos_mu(yol: Path) -> bool:
    """Parca "bos" mu? (dosya yok, 0 bayt, bozuk ya da 0 cerceve)"""
    return cerceve_sayisi(yol) <= 0


def sure_sn(yol: Path) -> float:
    try:
        with wave.open(str(yol), "rb") as dosya:
            hiz = dosya.getframerate() or ORNEK_HIZI
            return dosya.getnframes() / float(hiz)
    except Exception:  # noqa: BLE001 - bos, eksik ya da bozuk dosya
        return 0.0


def sirala(parcalar: Iterable[Path]) -> list[Path]:
    """Dosya adindaki sira numarasina gore zaman sirasi (`mik-01`, `mik-02`)."""

    def anahtar(yol: Path) -> tuple[int, str]:
        kuyruk = yol.stem.rsplit("-", 1)[-1]
        return (int(kuyruk) if kuyruk.isdigit() else 0, yol.name)

    return sorted(parcalar, key=anahtar)


def birlestir(parcalar: Sequence[Path], hedef: Path) -> Path | None:
    """Bir kanalin parcalarini tek WAV'a ekler; parca yoksa `None`."""
    # Bos parca (aygittan veri gelmedi) ATLANIR: 0 cerceveli bir dosya
    # birlestirmeye girerse transkript "konusma bulunamadi" der ve asil
    # sebep -- kanala hic veri gelmemis olmasi -- kaybolur.
    varolan = [yol for yol in sirala(parcalar) if yol.exists() and not bos_mu(yol)]
    if not varolan:
        return None
    toplam = bytearray()
    hiz = ORNEK_HIZI
    for yol in varolan:
        cerceve, dosya_hizi = oku(yol)
        hiz = dosya_hizi or hiz
        toplam.extend(cerceve)
    return yaz(hedef, bytes(toplam), hiz)


def karistir(sol: Path, sag: Path, hedef: Path) -> Path:
    """Iki kanali tek dosyada toplar (basit ortalama, kirpmasiz).

    Yalnizca kullanici sesi saklamak istediginde kullanilir; yaziya dokum
    her zaman kanallari AYRI okur, yoksa kimin konustugu kaybolurdu.
    """
    bir, hiz = oku(sol)
    iki, _ = oku(sag)
    a = array.array("h")
    b = array.array("h")
    a.frombytes(bir[: len(bir) - len(bir) % 2])
    b.frombytes(iki[: len(iki) - len(iki) % 2])
    uzun = max(len(a), len(b))
    sonuc = array.array("h", [0] * uzun)
    for sira in range(uzun):
        birinci = a[sira] if sira < len(a) else 0
        ikinci = b[sira] if sira < len(b) else 0
        sonuc[sira] = int(max(-32768, min(32767, (birinci + ikinci) / 2)))
    return yaz(hedef, sonuc.tobytes(), hiz)


def rms(cerceveler: bytes) -> float:
    """Ortalama karekok seviye (0..1). Deneme kaydinda "ses var mi" bunu okur."""
    if not cerceveler:
        return 0.0
    ornekler = array.array("h")
    ornekler.frombytes(cerceveler[: len(cerceveler) - len(cerceveler) % 2])
    if not ornekler:
        return 0.0
    toplam = sum(float(deger) * float(deger) for deger in ornekler)
    return math.sqrt(toplam / len(ornekler)) / 32768.0


def dosya_rms(yol: Path) -> float:
    cerceve, _ = oku(yol)
    return rms(cerceve)


# Bu esigin altinda "ses yok" denir: tamamen sessiz kayitta RMS sifira cok
# yakin cikiyor, dusuk seviyeli oda gurultusu bile bunun uzerinde.
SES_ESIGI = 0.002


def ses_var(seviye: float) -> bool:
    return seviye >= SES_ESIGI
