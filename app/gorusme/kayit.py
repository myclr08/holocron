"""WASAPI kaydi: mikrofon ve duyulan ses (hoparlor dongusu) iki ayri dosyaya.

`pyaudiowpatch` Windows'ta WASAPI **loopback** verir; hoparlorden cikan sesi
ekstra bir surucu kurmadan yakalayabilmemizin tek sebebi budur. Paket ISTEGE
BAGLIDIR ve yalnizca Windows tekerleklerinde bulunur (`requirements.txt`
icinde `sys_platform == "win32"` isaretiyle). Yoksa kayit katmani anlasilir
bir hata dondurur, uygulamanin geri kalani calismaya devam eder.

Okuma **geri cagri (callback)** kipindedir: surucu hazir bloku kendi is
parcaciginda birakir, bizim yazici is parcacigi kuyruktan alip diske yazar.
Bloklayan `stream.read()` bilerek terk edildi -- VDI'nin sanal aygitlarinda
(ornegin "Remote Audio") ve ses calinmayan bir dongu aygitinda o cagri hic
donmuyor, kayit is parcacigi orada kilitleniyor ve dosya 0 bayt kaliyordu.
Geri cagri kipinde aygit susarsa yalnizca "cerceve gelmedi" olur: dosya
duzgun kapanir (basligi yazilmis, 0 cerceve), kullanici da neyin olmadigini
gorur.

Her kanal kendi dosyasina yazilir: bellekte yarim saatlik ses tutmayiz.
Gorusme icinde aygit degisirse `basla` yeni bir **parca** acar (`mik-02.wav`,
`hop-02.wav`); parcalar birlestirme asamasinda zaman sirasiyla eklenir.
"""

from __future__ import annotations

import logging
import math
import queue
import threading
import time
import wave
from pathlib import Path
from typing import Any

from .source import (
    KANAL_AYGIT_ETIKETLERI,
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

# Bir blokta kac cerceve: 1024 cerceve ~64 ms, tepki suresi yeterli.
BLOK = 1024

# PortAudio geri cagri donusleri (pyaudio.paContinue / paComplete). Sabitleri
# modulden okumak yerine burada tutuyoruz: geri cagri, modul referansi
# olmadan da dogru degeri dondurebilsin.
PA_CONTINUE = 0
PA_COMPLETE = 1

# Acilis basarisiz olursa denenen yedek bicim: kart kendi varsayilanini
# vermiyorsa 44.1 kHz / stereo neredeyse her zaman kabul edilir.
YEDEK_HIZ = 44100
YEDEK_KANAL = 2

# Deneme kaydinda hoparlore verilen duyulmayacak sinyalin genligi (-60 dB).
SESSIZ_GENLIK = int(32768 * (10 ** (-60 / 20)))  # ~32
SESSIZ_TON = 440


def parca_adi(kanal: str, sira: int) -> str:
    return f"{kanal}-{sira:02d}.wav"


def eksik_paket() -> GorusmeHatasi:
    return GorusmeHatasi(
        "ses_yakalama_yok",
        "Ses yakalama paketi (pyaudiowpatch) kurulu değil; görüşme kaydı yapılamaz.",
        status=400,
    )


class VeriGelmediHatasi(GorusmeHatasi):
    """Kanal acildi ama aygittan tek cerceve gelmedi.

    Ayri bir sinif: "aygit yok" ile "aygit var ama susuyor" bambaska iki
    sorundur, kullaniciya da bambaska sey soylemek gerekir.
    """


def veri_gelmedi(kanal: str, dongu: bool, saniye: float) -> VeriGelmediHatasi:
    etiket = KANAL_AYGIT_ETIKETLERI.get(kanal, kanal)
    ipucu = " Kayıt sırasında bir ses çalın." if dongu else ""
    return VeriGelmediHatasi(
        "veri_gelmedi",
        f"{etiket}: veri gelmedi ({saniye:.0f} sn).{ipucu}",
        status=400,
    )


def aygit_bilgisi(ses: Any, index: int) -> dict[str, Any]:
    """Aygitin teshis icin gereken kimligi (ad, hostApi, hiz, kanal)."""
    ham: dict[str, Any] = {}
    try:
        ham = dict(ses.get_device_info_by_index(index) or {})
    except Exception:  # noqa: BLE001 - surucu her turlu hata atabilir
        log.debug("Aygıt bilgisi okunamadı: %s", index, exc_info=True)
    return {
        "index": int(ham.get("index", index) or index),
        "ad": str(ham.get("name", "") or ""),
        "hostApi": int(ham.get("hostApi", -1) or -1),
        "defaultSampleRate": int(float(ham.get("defaultSampleRate", 0) or 0)),
        "maxInputChannels": int(ham.get("maxInputChannels", 0) or 0),
        "dongu": bool(ham.get("isLoopbackDevice", False)),
    }


def _kanal_sayisi(bilgi: dict[str, Any]) -> int:
    """Aygitin giris kanali (1-2). WASAPI paylasimli kipte aygitin kendi
    kanal sayisini istemek gerekiyor; mono zorlamak dongu aygitlarinda
    acilisi dusuruyordu."""
    return max(1, min(int(bilgi.get("maxInputChannels", 1) or 1), 2))


class _KanalYazici:
    """Tek bir kanali geri cagri kipinde okuyup WAV'a yazan yazici."""

    def __init__(self, ses: Any, aygit_index: int, yol: Path, dongu: bool, kanal: str = "") -> None:
        self._ses = ses
        self._index = aygit_index
        self._yol = yol
        self._dongu = dongu
        self._kanal = kanal or (KANAL_HOP if dongu else KANAL_MIK)
        self._dur = threading.Event()
        self._kuyruk: queue.Queue[bytes | None] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._akis: Any = None
        self._dosya: Any = None
        self._kilit = threading.Lock()
        self._hiz = ORNEK_HIZI
        self._kanallar = 1
        self._basladi = 0.0
        # --- disariya acik teshis ---
        self.acildi = False
        self.cerceve = 0
        self.seviye = 0.0
        self.hata = ""
        self.aygit: dict[str, Any] = {}

    # --- yasam dongusu -------------------------------------------------

    def basla(self) -> bool:
        """Dosyayi ve akisi acar. Hicbir durumda istisna sizdirmaz."""
        self._basladi = time.monotonic()
        self.aygit = aygit_bilgisi(self._ses, self._index)
        if not self._dosya_ac():
            return False
        if not self._akis_ac():
            self._dosyayi_kapat()
            return False
        self.acildi = True
        self._thread = threading.Thread(
            target=self._yaz_dongusu, name=f"holocron-kayit-{self._yol.stem}", daemon=True
        )
        self._thread.start()
        log.info(
            "Kayıt kanalı açıldı (%s): %s [index %s, hostApi %s, %s Hz, %s kanal]",
            self._kanal,
            self.aygit.get("ad", ""),
            self.aygit.get("index"),
            self.aygit.get("hostApi"),
            self._hiz,
            self._kanallar,
        )
        return True

    def bitir(self, zaman_asimi: float = 5.0) -> None:
        """Akisi kapatir, yaziciyi bekler, dosyayi her durumda kapatir."""
        self._dur.set()
        try:
            if self._akis is not None:
                self._akis.stop_stream()
                self._akis.close()
        except Exception:  # noqa: BLE001 - kapanista hata yutulur
            log.debug("Akış kapatılamadı: %s", self._yol.name, exc_info=True)
        finally:
            self._akis = None
        self._kuyruk.put(None)
        if self._thread is not None:
            self._thread.join(timeout=zaman_asimi)
            if self._thread.is_alive() and not self.hata:
                self.hata = f"kayıt iş parçacığı {zaman_asimi:.0f} sn içinde kapanmadı"
        # Dosya bizim elimizde: yazici takilsa bile basligi yazilip kapanir.
        self._dosyayi_kapat()
        gecen = max(0.0, time.monotonic() - self._basladi)
        if self.acildi and self.cerceve == 0 and not self.hata:
            self.hata = str(veri_gelmedi(self._kanal, self._dongu, gecen))
        log.info(
            "Kayıt kanalı kapandı (%s): %s çerçeve, %.1f sn%s",
            self._kanal,
            self.cerceve,
            gecen,
            f", hata: {self.hata}" if self.hata else "",
        )

    def teshis(self) -> dict[str, Any]:
        return {
            "acildi": self.acildi,
            "cerceve": self.cerceve,
            "hata": self.hata,
            "aygit": dict(self.aygit),
            "seviye": round(self.seviye, 5),
        }

    # --- acilis ---------------------------------------------------------

    def _dosya_ac(self) -> bool:
        try:
            self._yol.parent.mkdir(parents=True, exist_ok=True)
            dosya = wave.open(str(self._yol), "wb")
            dosya.setnchannels(KANAL_SAYISI)
            dosya.setsampwidth(ORNEK_GENISLIGI)
            dosya.setframerate(ORNEK_HIZI)
        except Exception as hata:  # noqa: BLE001
            self.hata = f"dosya açılamadı: {hata}"
            log.warning("Kayıt dosyası açılamadı: %s", self._yol, exc_info=True)
            return False
        self._dosya = dosya
        return True

    def _denemeler(self) -> list[tuple[int, int]]:
        hiz = int(self.aygit.get("defaultSampleRate") or ORNEK_HIZI) or ORNEK_HIZI
        kanal = _kanal_sayisi(self.aygit)
        sira = [(hiz, kanal)]
        if (YEDEK_HIZ, YEDEK_KANAL) not in sira:
            sira.append((YEDEK_HIZ, YEDEK_KANAL))
        return sira

    def _akis_ac(self) -> bool:
        son_hata = ""
        for hiz, kanal in self._denemeler():
            try:
                akis = self._ses.open(
                    format=self._ses.get_format_from_width(ORNEK_GENISLIGI),
                    channels=kanal,
                    rate=hiz,
                    input=True,
                    input_device_index=self._index,
                    frames_per_buffer=BLOK,
                    stream_callback=self._geri_cagri,
                )
            except Exception as hata:  # noqa: BLE001 - surucu her turlu hata atabilir
                son_hata = str(hata)
                log.warning(
                    "Kanal açılamadı (%s, %s Hz, %s kanal): %s",
                    self._yol.name,
                    hiz,
                    kanal,
                    hata,
                )
                continue
            self._akis = akis
            self._hiz = hiz
            self._kanallar = kanal
            return True
        self.hata = son_hata or "aygıt açılamadı"
        return False

    # --- veri akisi ------------------------------------------------------

    def _geri_cagri(self, veri: bytes, cerceve_sayisi: int, zaman: Any, durum: Any) -> tuple:
        """Surucunun is parcaciginda calisir: sadece kuyruga birakir."""
        if veri:
            self._kuyruk.put(veri)
            sayi = int(cerceve_sayisi or 0) or (len(veri) // (2 * max(1, self._kanallar)))
            self.cerceve += sayi
        return (None, PA_COMPLETE if self._dur.is_set() else PA_CONTINUE)

    def _yaz_dongusu(self) -> None:
        en_yuksek = 0.0
        try:
            while True:
                try:
                    ham = self._kuyruk.get(timeout=0.2)
                except queue.Empty:
                    if self._dur.is_set():
                        break
                    continue
                if ham is None:
                    break
                tek = _tek_kanala(ham, self._kanallar)
                kucuk = _yeniden_orneklen(tek, self._hiz, ORNEK_HIZI)
                en_yuksek = max(en_yuksek, birlestir.rms(kucuk))
                with self._kilit:
                    if self._dosya is None:
                        break
                    self._dosya.writeframes(kucuk)
        except Exception as hata:  # noqa: BLE001
            self.hata = str(hata)
            log.warning("Kanal yazılamadı: %s", self._yol.name, exc_info=True)
        finally:
            self.seviye = en_yuksek

    def _dosyayi_kapat(self) -> None:
        with self._kilit:
            dosya, self._dosya = self._dosya, None
        if dosya is None:
            return
        try:
            # Hic cerceve gelmese bile `close` gecerli bir basligi yazar:
            # dosya 0 bayt kalmaz, okuyucular EOFError almaz.
            dosya.close()
        except Exception:  # noqa: BLE001
            log.debug("Kayıt dosyası kapatılamadı: %s", self._yol, exc_info=True)


class _SessizSinyal:
    """Deneme kaydinda hoparlore duyulmayacak bir ton calar.

    WASAPI dongu aygiti hoparlorden ses CIKMIYORKEN hicbir cerceve
    uretmiyor; boyle bir makinede deneme kaydi hep "ses yok" derdi. -60 dB'lik
    bu sinyal duyulmaz ama dongunun akmasi icin yeter. Hata olursa sessizce
    vazgeciliyor: deneme kaydi bu yuzden dusmemeli.
    """

    def __init__(self, ses: Any) -> None:
        self._ses = ses
        self._dur = threading.Event()
        self._thread: threading.Thread | None = None

    def basla(self) -> None:
        self._thread = threading.Thread(
            target=self._calis, name="holocron-deneme-ton", daemon=True
        )
        self._thread.start()

    def bitir(self, zaman_asimi: float = 2.0) -> None:
        self._dur.set()
        if self._thread is not None:
            self._thread.join(timeout=zaman_asimi)
            self._thread = None

    def _calis(self) -> None:
        akis = None
        try:
            bilgi = dict(self._ses.get_default_output_device_info() or {})
            hiz = int(float(bilgi.get("defaultSampleRate", 0) or 0)) or YEDEK_HIZ
            kanal = max(1, min(int(bilgi.get("maxOutputChannels", 2) or 2), 2))
            akis = self._ses.open(
                format=self._ses.get_format_from_width(ORNEK_GENISLIGI),
                channels=kanal,
                rate=hiz,
                output=True,
                frames_per_buffer=BLOK,
            )
            blok = ton_blok(hiz, kanal)
            while not self._dur.is_set():
                akis.write(blok)
        except Exception:  # noqa: BLE001 - ses cikisi yoksa sessizce vazgec
            log.debug("Deneme sinyali çalınamadı", exc_info=True)
        finally:
            try:
                if akis is not None:
                    akis.stop_stream()
                    akis.close()
            except Exception:  # noqa: BLE001
                pass


def ton_blok(hiz: int, kanal: int) -> bytes:
    """Bir bloktan (BLOK cerceve) olusan cok dusuk seviyeli sinus."""
    import array

    veri = array.array("h")
    for sira in range(BLOK):
        deger = int(SESSIZ_GENLIK * math.sin(2 * math.pi * SESSIZ_TON * sira / max(1, hiz)))
        for _ in range(max(1, kanal)):
            veri.append(deger)
    return veri.tobytes()


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

    def __init__(self, modul: Any = None) -> None:
        # `modul`: testlerin sahte PyAudio'su. Uretimde her zaman None.
        self._modul = modul
        self._ses: Any = None
        self._yazicilar: list[tuple[str, _KanalYazici]] = []
        self._parcalar: list[Parca] = []
        self._teshis: dict[str, dict[str, Any]] = {}

    # --- yasam dongusu -------------------------------------------------

    def _pyaudio(self) -> Any:
        if self._modul is not None:
            return self._modul
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
        self._teshis = {}
        for kanal, kimlik, dongu in (
            (KANAL_MIK, aygitlar.mikrofon, False),
            (KANAL_HOP, aygitlar.hoparlor, True),
        ):
            index = _index(kimlik)
            if index is None:
                self._teshis[kanal] = {
                    "acildi": False,
                    "cerceve": 0,
                    "hata": "aygıt seçilmedi",
                    "aygit": {},
                    "seviye": 0.0,
                }
                continue
            yol = klasor / parca_adi(kanal, sira)
            yazici = _KanalYazici(self._ses, index, yol, dongu, kanal=kanal)
            yazici.basla()
            self._yazicilar.append((kanal, yazici))
            self._parcalar.append(Parca(kanal=kanal, sira=sira, yol=yol))
        return list(self._parcalar)

    def bitir(self) -> list[Parca]:
        for kanal, yazici in self._yazicilar:
            yazici.bitir()
            self._teshis[kanal] = yazici.teshis()
        self._yazicilar = []
        if self._ses is not None:
            try:
                self._ses.terminate()
            except Exception:  # noqa: BLE001
                pass
            self._ses = None
        uretilen: list[Parca] = []
        for parca in self._parcalar:
            if not parca.yol.exists():
                continue
            cerceve = int(self._teshis.get(parca.kanal, {}).get("cerceve", 0) or 0)
            # Veri gelmemis parca "bos" isaretlenir: birlestirme onu atlar,
            # yerine 0 cerceveden olusan bir WAV koymaz.
            uretilen.append(
                Parca(kanal=parca.kanal, sira=parca.sira, yol=parca.yol, bos=cerceve == 0)
            )
        self._parcalar = []
        return uretilen

    @property
    def teshis(self) -> dict[str, dict[str, Any]]:
        """Son kaydin kanal basina teshisi (deneme kaydi ekrani bunu okur)."""
        return dict(self._teshis)

    # --- deneme kaydi ---------------------------------------------------

    def deneme(self, saniye: float, aygitlar: Aygitlar, klasor: Path) -> dict[str, Any]:
        """Kisa bir kayit alir, iki kanalin seviyesini ve teshisini dondurur.

        Ayarlar ekranindaki "Deneme kaydı" dugmesi bunu cagirir: VDI'da hangi
        sanal aygitin ses tasidigini kullanici baska turlu goremiyor. Kayit
        boyunca hoparlore duyulmayan bir sinyal calinir, yoksa dongu aygiti
        sessiz makinede hic cerceve uretmez.
        """
        self.basla(klasor, 0, aygitlar)
        besleyici = _SessizSinyal(self._ses) if self._ses is not None else None
        if besleyici is not None:
            besleyici.basla()
        try:
            time.sleep(max(0.1, float(saniye)))
        finally:
            if besleyici is not None:
                besleyici.bitir()
        parcalar = self.bitir()
        return sonuc_ozeti(parcalar, klasor, self._teshis)


def _index(kimlik: Any) -> int | None:
    metin = str(kimlik or "").strip()
    if not metin:
        return None
    try:
        return int(metin)
    except ValueError:
        return None


def oneriler(kanallar: dict[str, dict[str, Any]]) -> list[str]:
    """Deneme sonucuna gore kullaniciya ne yapacagini soyler."""
    satirlar: list[str] = []
    mik = kanallar.get(KANAL_MIK) or {}
    hop = kanallar.get(KANAL_HOP) or {}
    if not mik.get("acildi") or not int(mik.get("cerceve", 0) or 0):
        satirlar.append(
            "Mikrofondan veri gelmedi: Ayarlar'da mikrofonu sabitleyin "
            "(Otomatik seçim VDI'da yanlış aygıta düşebilir)."
        )
    elif not mik.get("ses_var"):
        satirlar.append("Mikrofon açıldı ama sessiz: konuşarak yeniden deneyin.")
    if not hop.get("acildi") or not int(hop.get("cerceve", 0) or 0):
        satirlar.append(
            "Duyduğunuz sesin döngü aygıtından veri gelmedi: kayıt sırasında bir ses "
            "çalın ve \"Duyduğum ses\" aygıtını Ayarlar'da sabitleyin."
        )
    elif not hop.get("ses_var"):
        satirlar.append("Döngü kanalı açıldı ama sessiz: deneme sırasında bir ses çalın.")
    return satirlar


def sonuc_ozeti(
    parcalar: list[Parca], klasor: Path, teshis: dict[str, dict[str, Any]] | None = None
) -> dict[str, Any]:
    """Deneme kaydinin arayuze donen ozeti (seviye + teshis).

    `teshis` verilmezse (sahte kayitci) dosyanin kendisinden cikarilir.
    Hicbir dal istisna atmaz: bozuk ya da bos WAV da bir satir uretir.
    """
    kanallar: dict[str, dict[str, Any]] = {}
    veriler = teshis or {}
    for kanal in (KANAL_MIK, KANAL_HOP):
        parca = next((item for item in parcalar if item.kanal == kanal), None)
        bilgi = dict(veriler.get(kanal) or {})
        yol = parca.yol if parca is not None else None
        seviye = birlestir.dosya_rms(yol) if yol is not None else 0.0
        cerceve = (
            int(bilgi.get("cerceve", 0) or 0)
            if "cerceve" in bilgi
            else (birlestir.cerceve_sayisi(yol) if yol is not None else 0)
        )
        acildi = bool(bilgi.get("acildi")) if "acildi" in bilgi else yol is not None
        kanallar[kanal] = {
            "etiket": KANAL_AYGIT_ETIKETLERI.get(kanal, kanal),
            "acildi": acildi,
            "cerceve": cerceve,
            "hata": str(bilgi.get("hata", "") or ""),
            "aygit": dict(bilgi.get("aygit") or {}),
            "seviye": round(seviye, 5),
            "ses_var": birlestir.ses_var(seviye),
            "yol": str(yol) if yol is not None else "",
            "sure_sn": round(birlestir.sure_sn(yol), 2) if yol is not None else 0.0,
        }
    return {
        "klasor": str(klasor),
        "kanallar": kanallar,
        "oneriler": oneriler(kanallar),
        "hata": "",
    }


def default_kayitci() -> WasapiKayitci:
    return WasapiKayitci()
