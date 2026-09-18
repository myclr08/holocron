"""WASAPI kaydi: mikrofon ve duyulan ses (hoparlor dongusu) iki ayri dosyaya.

`pyaudiowpatch` Windows'ta WASAPI **loopback** verir; hoparlorden cikan sesi
ekstra bir surucu kurmadan yakalayabilmemizin tek sebebi budur. Paket ISTEGE
BAGLIDIR ve yalnizca Windows tekerleklerinde bulunur (`requirements.txt`
icinde `sys_platform == "win32"` isaretiyle). Yoksa kayit katmani anlasilir
bir hata dondurur, uygulamanin geri kalani calismaya devam eder.

Her kanal kendi is parcaciginda okunur ve dogrudan diske yazilir: bellekte
yarim saatlik ses tutmayiz. Gorusme icinde aygit degisirse `basla` yeni bir
**parca** acar (`mik-02.wav`, `hop-02.wav`); parcalar birlestirme asamasinda
zaman sirasiyla eklenir.
"""

from __future__ import annotations

import logging
import threading
import wave
from pathlib import Path
from typing import Any

from .source import (
    KANAL_HOP,
    KANAL_MIK,
    KANAL_SAYISI,
    ORNEK_GENISLIGI,
    ORNEK_HIZI,
    Aygitlar,
    GorusmeHatasi,
    Parca,
    is_supported,
)
from . import birlestir

log = logging.getLogger("holocron.gorusme.kayit")

# Bir okumada kac cerceve alinir: 1024 cerceve ~64 ms, tepki suresi yeterli.
BLOK = 1024


def parca_adi(kanal: str, sira: int) -> str:
    return f"{kanal}-{sira:02d}.wav"


def eksik_paket() -> GorusmeHatasi:
    return GorusmeHatasi(
        "ses_yakalama_yok",
        "Ses yakalama paketi (pyaudiowpatch) kurulu değil; görüşme kaydı yapılamaz.",
        status=400,
    )


class _KanalYazici:
    """Tek bir kanali okuyup WAV'a yazan is parcacigi."""

    def __init__(self, ses: Any, aygit_index: int, yol: Path, dongu: bool) -> None:
        self._ses = ses
        self._index = aygit_index
        self._yol = yol
        self._dongu = dongu
        self._dur = threading.Event()
        self._thread: threading.Thread | None = None
        self.seviye = 0.0
        self.hata = ""

    def basla(self) -> None:
        self._thread = threading.Thread(
            target=self._calis, name=f"holocron-kayit-{self._yol.stem}", daemon=True
        )
        self._thread.start()

    def bitir(self, zaman_asimi: float = 5.0) -> None:
        self._dur.set()
        if self._thread is not None:
            self._thread.join(timeout=zaman_asimi)

    def _calis(self) -> None:
        try:
            self._yol.parent.mkdir(parents=True, exist_ok=True)
            bilgi = self._ses.get_device_info_by_index(self._index)
            hiz = int(bilgi.get("defaultSampleRate", ORNEK_HIZI) or ORNEK_HIZI)
            kanallar = int(bilgi.get("maxInputChannels", 1) or 1)
            akis = self._ses.open(
                format=self._ses.get_format_from_width(ORNEK_GENISLIGI),
                channels=min(kanallar, 2) if self._dongu else 1,
                rate=hiz,
                input=True,
                input_device_index=self._index,
                frames_per_buffer=BLOK,
            )
        except Exception as hata:  # noqa: BLE001 - surucu her turlu hata atabilir
            self.hata = str(hata)
            log.warning("Kanal acilamadi: %s", self._yol.name, exc_info=True)
            return

        en_yuksek = 0.0
        try:
            with wave.open(str(self._yol), "wb") as dosya:
                dosya.setnchannels(KANAL_SAYISI)
                dosya.setsampwidth(ORNEK_GENISLIGI)
                dosya.setframerate(ORNEK_HIZI)
                while not self._dur.is_set():
                    ham = akis.read(BLOK, exception_on_overflow=False)
                    tek = _tek_kanala(ham, kanallar if self._dongu else 1)
                    kucuk = _yeniden_orneklen(tek, hiz, ORNEK_HIZI)
                    en_yuksek = max(en_yuksek, birlestir.rms(kucuk))
                    dosya.writeframes(kucuk)
        except Exception as hata:  # noqa: BLE001
            self.hata = str(hata)
            log.warning("Kanal okunamadi: %s", self._yol.name, exc_info=True)
        finally:
            self.seviye = en_yuksek
            try:
                akis.stop_stream()
                akis.close()
            except Exception:  # noqa: BLE001 - kapanista hata yutulur
                pass


def _tek_kanala(ham: bytes, kanallar: int) -> bytes:
    """Cok kanalli bloktan mono uretir (kanallarin ortalamasi)."""
    if kanallar <= 1:
        return ham
    import array

    ornekler = array.array("h")
    ornekler.frombytes(ham[: len(ham) - len(ham) % (2 * kanallar)])
    sonuc = array.array("h")
    for sira in range(0, len(ornekler), kanallar):
        dilim = ornekler[sira : sira + kanallar]
        sonuc.append(int(sum(dilim) / len(dilim)))
    return sonuc.tobytes()


def _yeniden_orneklen(ham: bytes, kaynak_hiz: int, hedef_hiz: int) -> bytes:
    """Kaba en yakin komsu indirgeme: 48 kHz -> 16 kHz.

    Ses tanima icin yeterli; kaliteli bir filtre icin ek bagimlilik gerekirdi
    ve konusma anlasilirligi bundan gozle gorulur bicimde etkilenmiyor.
    """
    if kaynak_hiz == hedef_hiz or kaynak_hiz <= 0:
        return ham
    import array

    ornekler = array.array("h")
    ornekler.frombytes(ham[: len(ham) - len(ham) % 2])
    adim = kaynak_hiz / float(hedef_hiz)
    sonuc = array.array("h")
    konum = 0.0
    while int(konum) < len(ornekler):
        sonuc.append(ornekler[int(konum)])
        konum += adim
    return sonuc.tobytes()


class WasapiKayitci:
    """`KayitProtokolu`nun Windows uygulamasi."""

    def __init__(self) -> None:
        self._ses: Any = None
        self._yazicilar: list[tuple[str, _KanalYazici]] = []
        self._parcalar: list[Parca] = []

    # --- yasam dongusu -------------------------------------------------

    def _pyaudio(self) -> Any:
        if not is_supported():
            raise eksik_paket()
        try:
            import pyaudiowpatch  # yerel ice aktarim
        except ImportError as hata:
            raise eksik_paket() from hata
        return pyaudiowpatch

    def basla(self, klasor: Path, sira: int, aygitlar: Aygitlar) -> list[Parca]:
        modul = self._pyaudio()
        self.bitir()
        self._ses = modul.PyAudio()
        self._parcalar = []
        self._yazicilar = []
        for kanal, kimlik, dongu in (
            (KANAL_MIK, aygitlar.mikrofon, False),
            (KANAL_HOP, aygitlar.hoparlor, True),
        ):
            index = _index(kimlik)
            if index is None:
                continue
            yol = klasor / parca_adi(kanal, sira)
            yazici = _KanalYazici(self._ses, index, yol, dongu)
            yazici.basla()
            self._yazicilar.append((kanal, yazici))
            self._parcalar.append(Parca(kanal=kanal, sira=sira, yol=yol))
        return list(self._parcalar)

    def bitir(self) -> list[Parca]:
        for _, yazici in self._yazicilar:
            yazici.bitir()
        self._yazicilar = []
        if self._ses is not None:
            try:
                self._ses.terminate()
            except Exception:  # noqa: BLE001
                pass
            self._ses = None
        uretilen = [parca for parca in self._parcalar if parca.yol.exists()]
        self._parcalar = []
        return uretilen

    # --- deneme kaydi ---------------------------------------------------

    def deneme(self, saniye: float, aygitlar: Aygitlar, klasor: Path) -> dict[str, Any]:
        """Kisa bir kayit alir, iki kanalin seviyesini olcer.

        Ayarlar ekranindaki "Deneme kaydı" dugmesi bunu cagirir: VDI'da hangi
        sanal aygitin ses tasidigini kullanici baska turlu goremiyor.
        """
        import time

        self.basla(klasor, 0, aygitlar)
        time.sleep(max(0.1, float(saniye)))
        parcalar = self.bitir()
        return sonuc_ozeti(parcalar, klasor)


def _index(kimlik: Any) -> int | None:
    metin = str(kimlik or "").strip()
    if not metin:
        return None
    try:
        return int(metin)
    except ValueError:
        return None


def sonuc_ozeti(parcalar: list[Parca], klasor: Path) -> dict[str, Any]:
    """Deneme kaydinin arayuze donen ozeti (seviye + dosya yolu)."""
    kanallar: dict[str, dict[str, Any]] = {}
    for kanal in (KANAL_MIK, KANAL_HOP):
        parca = next((item for item in parcalar if item.kanal == kanal), None)
        seviye = birlestir.dosya_rms(parca.yol) if parca is not None else 0.0
        kanallar[kanal] = {
            "seviye": round(seviye, 5),
            "ses_var": birlestir.ses_var(seviye),
            "yol": str(parca.yol) if parca is not None else "",
            "sure_sn": round(birlestir.sure_sn(parca.yol), 2) if parca is not None else 0.0,
        }
    return {"klasor": str(klasor), "kanallar": kanallar}


def default_kayitci() -> WasapiKayitci:
    return WasapiKayitci()
