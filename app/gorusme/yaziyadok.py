"""Yaziya dokme: faster-whisper (CPU, int8) ve iki kanalin harmanlanmasi.

`faster-whisper` ISTEGE BAGLI bagimliliktir. Kurulu degilse not "hata:
faster-whisper yok" durumunda kalir, ses SILINMEZ ve kullanici paketi kurup
"Yeniden dene" diyebilir (README'de kurulum anlatiliyor).

Model dosyasi ayardan verilen klasorden okunur; kurum vekili Hugging Face'i
kesiyorsa kullanici model klasorunu elle kopyalayabilsin diye. Klasor bossa
kitaplik modeli kendi indirir.

Iki kanal AYRI cevrilir, sonra zaman damgasiyla harmanlanir: mikrofon kanali
"Sen", dongu kanali "Karsi taraf". Grup aramasinda karsi taraf tek kanaldir,
kisi ayrimi yoktur -- adlar katilimci listesinden bilinir.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Iterable, Sequence

from .source import (
    KANAL_ETIKETLERI,
    KANAL_HOP,
    KANAL_MIK,
    GorusmeHatasi,
    Segment,
)

log = logging.getLogger("holocron.gorusme.yaziyadok")

VARSAYILAN_MODEL = "small"
VARSAYILAN_DIL = "tr"
# CPU'da bellek ve hiz dengesi: int8 kuantizasyon.
HESAP_TIPI = "int8"


def eksik_paket() -> GorusmeHatasi:
    return GorusmeHatasi(
        "faster_whisper_yok",
        "faster-whisper kurulu değil; görüşme yazıya dökülemez.",
        status=400,
    )


def zaman_metni(saniye: float) -> str:
    """`[00:12:34]` bicimi: transkriptte satirin basinda durur."""
    toplam = max(0, int(saniye))
    return f"{toplam // 3600:02d}:{(toplam % 3600) // 60:02d}:{toplam % 60:02d}"


def harmanla(
    mik: Sequence[Segment], hop: Sequence[Segment], etiketler: dict[str, str] | None = None
) -> str:
    """Iki kanalin segmentlerini zaman sirasina dizip tek metin uretir."""
    adlar = etiketler or KANAL_ETIKETLERI
    satirlar: list[tuple[float, str]] = []
    for kanal, segmentler in ((KANAL_MIK, mik), (KANAL_HOP, hop)):
        etiket = adlar.get(kanal, kanal)
        for parca in segmentler:
            metin = str(parca.metin or "").strip()
            if not metin:
                continue
            satirlar.append(
                (parca.baslangic, f"[{zaman_metni(parca.baslangic)}] {etiket}: {metin}")
            )
    satirlar.sort(key=lambda satir: satir[0])
    return "\n".join(metin for _, metin in satirlar)


class WhisperDokucu:
    """`YaziyaDokucuProtokolu`nun faster-whisper uygulamasi."""

    def __init__(self, model: str = VARSAYILAN_MODEL, klasor: str = "") -> None:
        self.model_adi = str(model or VARSAYILAN_MODEL).strip() or VARSAYILAN_MODEL
        self.klasor = str(klasor or "").strip()
        self._model: Any = None

    def _yukle(self) -> Any:
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel  # yerel ice aktarim
        except ImportError as hata:
            raise eksik_paket() from hata
        secenekler: dict[str, Any] = {"device": "cpu", "compute_type": HESAP_TIPI}
        if self.klasor:
            # Vekil engelinde kullanici klasoru elle kopyalayabilsin.
            secenekler["download_root"] = self.klasor
        self._model = WhisperModel(self.model_adi, **secenekler)
        return self._model

    def cevir(self, yol: Path, dil: str = VARSAYILAN_DIL) -> list[Segment]:
        model = self._yukle()
        segmentler, _ = model.transcribe(str(yol), language=dil or VARSAYILAN_DIL)
        return [
            Segment(
                baslangic=float(getattr(parca, "start", 0.0) or 0.0),
                bitis=float(getattr(parca, "end", 0.0) or 0.0),
                metin=str(getattr(parca, "text", "") or "").strip(),
            )
            for parca in segmentler
        ]


def kurulu_mu() -> bool:
    """faster-whisper var mi? Ayarlar ekrani bunu gosterir."""
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        return False
    return True


def default_dokucu(model: str = VARSAYILAN_MODEL, klasor: str = "") -> WhisperDokucu:
    return WhisperDokucu(model=model, klasor=klasor)


def bos_mu(segmentler: Iterable[Segment]) -> bool:
    return not any(str(parca.metin or "").strip() for parca in segmentler)
