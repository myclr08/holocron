"""Gorusme notlari: Teams gorusmesini kaydet, yaziya dok, ozetle.

Katmanlar (Aramalar ve posta paketleriyle birebir ayni desen):

* `source.py`     -- kaynak-bagimsiz model, protokoller, durum adlari
* `algilayici.py` -- Windows mikrofon izin defteri (yalnizca Windows)
* `aygit.py`      -- mikrofon/hoparlor secimi (pyaudiowpatch, pycaw; istege bagli)
* `kayit.py`      -- WASAPI kaydi, iki kanal iki dosya (yalnizca Windows)
* `birlestir.py`  -- WAV yazma/olcme/birlestirme (stdlib `wave`)
* `yaziyadok.py`  -- faster-whisper (istege bagli) ve kanal harmanlama
* `ozet.py`       -- Copilot CLI, model yedek sirasi, JSON ayristirma
* `bildirim.py`   -- Windows bildirimi (winotify; istege bagli)
* `sahte.py`      -- bellek ici sahteler (testler ve Linux'ta uctan uca akis)
* `depo.py`       -- SQL
* `intake.py`     -- ayarlar, esleme, ekran goruntusu (saf is mantigi)
* `takipci.py`    -- yoklama ve kayit yasam dongusu
* `kuyruk.py`     -- sirali isleme kuyrugu

Ses ve transkript kullanicinin makinesinde kalir ve (ayar aksini soylemedikce)
not hazir olunca silinir; disari hicbir sey gitmez. Ozet icin yalnizca
kullanicinin kendi Copilot CLI oturumu kullanilir.
"""

from __future__ import annotations

from typing import Any

from .source import (
    ARA_DURUMLAR,
    BOLUM_AKSIYON,
    BOLUM_ETIKETLERI,
    BOLUM_KARAR,
    BOLUM_OZET,
    BOLUM_SORU,
    BOLUM_TURLERI,
    DURUM_ATLANDI,
    DURUM_BIRLESTIRILIYOR,
    DURUM_HATA,
    DURUM_HAZIR,
    DURUM_KAYDEDILIYOR,
    DURUM_KUYRUKTA,
    DURUM_ETIKETLERI,
    DURUM_OZETLENIYOR,
    DURUM_YAZIYA_DOKULUYOR,
    DURUMLAR,
    AlgilamaDurumu,
    AlgilayiciProtokolu,
    Aygitlar,
    GorusmeHatasi,
    KayitProtokolu,
    OzetCikti,
    OzetleyiciProtokolu,
    Parca,
    Segment,
    YaziyaDokucuProtokolu,
    calisma_koku,
    is_supported,
    unavailable,
)

__all__ = [
    "ARA_DURUMLAR",
    "AlgilamaDurumu",
    "AlgilayiciProtokolu",
    "Aygitlar",
    "BOLUM_AKSIYON",
    "BOLUM_ETIKETLERI",
    "BOLUM_KARAR",
    "BOLUM_OZET",
    "BOLUM_SORU",
    "BOLUM_TURLERI",
    "DURUMLAR",
    "DURUM_ATLANDI",
    "DURUM_BIRLESTIRILIYOR",
    "DURUM_ETIKETLERI",
    "DURUM_HATA",
    "DURUM_HAZIR",
    "DURUM_KAYDEDILIYOR",
    "DURUM_KUYRUKTA",
    "DURUM_OZETLENIYOR",
    "DURUM_YAZIYA_DOKULUYOR",
    "GorusmeHatasi",
    "KayitProtokolu",
    "OzetCikti",
    "OzetleyiciProtokolu",
    "Parca",
    "Segment",
    "YaziyaDokucuProtokolu",
    "calisma_koku",
    "default_algilayici",
    "default_kayitci",
    "is_supported",
    "unavailable",
]


def default_algilayici(mikrofon: str = "", hoparlor: str = "") -> Any:
    """Uretimdeki algilayici: Windows kayit defteri + ses oturumu yedegi.

    Ice aktarim cagri icinde kalir: Linux'ta `winreg` ve `pyaudiowpatch`
    modullerine hic dokunulmaz.
    """
    if not is_supported():
        raise unavailable()
    from .algilayici import default_algilayici as kur

    return kur(mikrofon, hoparlor)


def default_kayitci() -> Any:
    """Uretimdeki kayitci: WASAPI (pyaudiowpatch)."""
    if not is_supported():
        raise unavailable()
    from .kayit import default_kayitci as kur

    return kur()
