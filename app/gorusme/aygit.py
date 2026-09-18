"""Hangi mikrofondan ve hangi cikistan kaydedilecek.

Varsayilan **otomatik**tir: Teams'in ses oturumu hangi aygitta aciksa o
kullanilir (pycaw/comtypes ile). Oturum bulunamazsa Windows'un **varsayilan
iletisim aygitlari**na duser; kullanici Ayarlar'dan aygiti sabitleyebilir.

Iki bagimlilik da ISTEGE BAGLIDIR: `pyaudiowpatch` (WASAPI, dongu kaydi) ve
`pycaw` (ses oturumu). Yoksa modul yine yuklenir, yalnizca daha az sey bilir;
Linux'ta hicbiri ice aktarilmaz.
"""

from __future__ import annotations

import logging
from typing import Any

from .source import Aygitlar, is_supported

log = logging.getLogger("holocron.gorusme.aygit")

# Teams surec adlari: ses oturumu bunlardan biriyle eslesirse Teams konusuyor.
TEAMS_SURECLERI: tuple[str, ...] = ("ms-teams.exe", "teams.exe")

# Otomatik secim: ayar bu degerdeyken aygit her gorusmede yeniden bulunur.
OTOMATIK = ""


def _pyaudio() -> Any:
    """`pyaudiowpatch` modulu; yoksa `None` (ozellik sessizce kapanir)."""
    if not is_supported():
        return None
    try:
        import pyaudiowpatch  # yerel ice aktarim: yalnizca Windows paketinde var
    except ImportError:
        return None
    return pyaudiowpatch


def _pycaw() -> Any:
    if not is_supported():
        return None
    try:
        from pycaw import pycaw as modul  # yerel ice aktarim
    except ImportError:
        return None
    return modul


def kullanilabilir() -> dict[str, bool]:
    """Hangi istege bagli parcalar kurulu? Ayarlar ekrani bunu gosterir."""
    return {"ses_yakalama": _pyaudio() is not None, "ses_oturumu": _pycaw() is not None}


def teams_ses_oturumu_var() -> bool:
    """Teams'in acik bir ses oturumu var mi? (yedek algilama sinyali)"""
    modul = _pycaw()
    if modul is None:
        return False
    try:
        for oturum in modul.AudioUtilities.GetAllSessions():
            surec = getattr(oturum, "Process", None)
            if surec is None:
                continue
            ad = str(getattr(surec, "name", lambda: "")() or "").lower()
            if ad in TEAMS_SURECLERI:
                return True
    except Exception:  # noqa: BLE001 - COM her turlu hatayi atabilir
        log.debug("Ses oturumlari okunamadi", exc_info=True)
    return False


def varsayilan_aygitlar() -> Aygitlar:
    """Windows'un varsayilan giris/cikis aygitlari (WASAPI adlariyla)."""
    modul = _pyaudio()
    if modul is None:
        return Aygitlar()
    try:
        with modul.PyAudio() as ses:
            giris = ses.get_default_input_device_info()
            cikis = ses.get_default_wasapi_loopback()
            return Aygitlar(
                mikrofon=str(giris.get("index", "")),
                hoparlor=str(cikis.get("index", "")),
                mikrofon_ad=str(giris.get("name", "")),
                hoparlor_ad=str(cikis.get("name", "")),
            )
    except Exception:  # noqa: BLE001 - aygit yoksa/surucu yoksa
        log.debug("Varsayilan ses aygitlari bulunamadi", exc_info=True)
        return Aygitlar()


def aygit_listesi() -> list[dict[str, Any]]:
    """Ayarlar ekranindaki aygit secici icin ad listesi."""
    modul = _pyaudio()
    if modul is None:
        return []
    bulunan: list[dict[str, Any]] = []
    try:
        with modul.PyAudio() as ses:
            for sira in range(ses.get_device_count()):
                bilgi = ses.get_device_info_by_index(sira)
                bulunan.append(
                    {
                        "kimlik": str(bilgi.get("index", sira)),
                        "ad": str(bilgi.get("name", "")),
                        "giris": int(bilgi.get("maxInputChannels", 0) or 0) > 0,
                        "dongu": bool(bilgi.get("isLoopbackDevice", False)),
                    }
                )
    except Exception:  # noqa: BLE001
        log.debug("Ses aygitlari listelenemedi", exc_info=True)
    return bulunan


def secili_aygitlar(mikrofon_ayari: str = "", hoparlor_ayari: str = "") -> Aygitlar:
    """Ayarda sabitlenmis aygitlar; bos kalanlar otomatik bulunur."""
    otomatik = varsayilan_aygitlar()
    mikrofon = str(mikrofon_ayari or "").strip() or otomatik.mikrofon
    hoparlor = str(hoparlor_ayari or "").strip() or otomatik.hoparlor
    adlar = {satir["kimlik"]: satir["ad"] for satir in aygit_listesi()}
    return Aygitlar(
        mikrofon=mikrofon,
        hoparlor=hoparlor,
        mikrofon_ad=adlar.get(mikrofon, otomatik.mikrofon_ad),
        hoparlor_ad=adlar.get(hoparlor, otomatik.hoparlor_ad),
    )
