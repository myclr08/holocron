"""Yaziya dokme: faster-whisper (CPU, int8) ve iki kanalin harmanlanmasi.

`faster-whisper` artik `requirements.txt` icinde (Windows isaretiyle), yani
normal kurulumda gelir. Yine de gelmemis olabilir: lite pakete v0.10.1'den
once girilmisse ya da kurumsal vekil PyPI'yi kesiyorsa. O durumda not "hata:
faster-whisper yok" durumunda kalir, ses SILINMEZ ve paket kurulunca satir
kendiliginden yeniden kuyruga girer (`kur` + `depo.eksik_paket_hatalarini_kuyruga_al`).

Model dosyasi ayardan verilen klasorden okunur; kurum vekili Hugging Face'i
kesiyorsa kullanici model klasorunu elle kopyalayabilsin diye. Klasor bossa
kitaplik modeli kendi indirir.

Iki kanal AYRI cevrilir, sonra zaman damgasiyla harmanlanir: mikrofon kanali
"Sen", dongu kanali "Karsi taraf". Grup aramasinda karsi taraf tek kanaldir,
kisi ayrimi yoktur -- adlar katilimci listesinden bilinir.
"""

from __future__ import annotations

import importlib
import logging
import subprocess
import sys
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
    """faster-whisper var mi? Ayarlar ekrani bunu gosterir.

    Paket uygulama ACIKKEN kurulmus olabilir ("Yazıya dökme paketini kur"):
    ice aktarma onbellegi tazelenmeden yeni klasor gorunmez, bu yuzden ilk
    deneme dustugunde onbellek bosaltilip bir kez daha bakilir.
    """
    try:
        import faster_whisper  # noqa: F401
    except ImportError:
        importlib.invalidate_caches()
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            return False
    return True


# --- kurulum -------------------------------------------------------------

PAKET = "faster-whisper"
# Ag uzerinden kurulum birkac yuz megabayt indiriyor: yarim saat taniyoruz.
KURULUM_ZAMAN_ASIMI = 1800
# Yanimizda gelen tekerlek klasorleri: tam/lite paket `wheels/`, whisper
# zip'i `whisper-wheels/`. Kurumsal vekil PyPI'yi kesiyorsa tek cikis budur.
TEKERLEK_KLASORLERI: tuple[str, ...] = ("wheels", "whisper-wheels")
# Ekranda duracak kaynak adi (ic ad "yerel"/"ağ" olarak kalir).
KAYNAK_METNI: dict[str, str] = {"yerel": "yanınızdaki tekerleklerden", "ağ": "ağdan"}


def kok_klasor() -> Path:
    """Uygulamanin kok klasoru: `holocron.bat`in ve tekerleklerin durdugu yer."""
    return Path(__file__).resolve().parent.parent.parent


def tekerlek_klasorleri(kok: Path | None = None) -> list[Path]:
    """Icinde gercekten tekerlek olan yerel klasorler (sirayla denenir)."""
    taban = Path(kok) if kok is not None else kok_klasor()
    bulunan: list[Path] = []
    for ad in TEKERLEK_KLASORLERI:
        klasor = taban / ad
        if klasor.is_dir() and any(klasor.glob("*.whl")):
            bulunan.append(klasor)
    return bulunan


def pip_komutu(python: str, klasorler: Sequence[Path] = ()) -> list[str]:
    """`pip install` komutu; yerel klasor verildiyse ag hic kullanilmaz."""
    komut = [python, "-m", "pip", "install"]
    if klasorler:
        komut.append("--no-index")
        for klasor in klasorler:
            komut += ["--find-links", str(klasor)]
    komut.append(PAKET)
    return komut


def _pip_calistir(kosucu: Any, komut: Sequence[str], ortam: dict[str, str]) -> tuple[int, str]:
    try:
        sonuc = kosucu(  # noqa: S603 - sabit komut, kullanici girdisi arguman degil
            list(komut),
            capture_output=True,
            text=True,
            timeout=KURULUM_ZAMAN_ASIMI,
            env=ortam,
        )
    except FileNotFoundError:
        return 127, "Python yorumlayicisi bulunamadi."
    except subprocess.TimeoutExpired:
        return 124, "Kurulum zaman aşımına uğradı."
    except OSError as hata:  # pragma: no cover - isletim sistemi hatasi
        return 1, str(hata)
    kod = int(getattr(sonuc, "returncode", 0) or 0)
    metin = f"{getattr(sonuc, 'stdout', '') or ''}\n{getattr(sonuc, 'stderr', '') or ''}"
    return kod, metin


def kur(proxy: str = "", kok: Path | None = None, calistirici: Any = None) -> dict[str, Any]:
    """`faster-whisper`i alt surecte kurar ve temizlenmis bir sonuc dondurur.

    Vekil adresi YALNIZCA alt surecin ortamina yazilir (`ozet.alt_surec_ortami`
    kalibi): Holocron'un kendi Jira baglantisi "doğrudan bağlan" kipinde
    kalir, `os.environ` hic degismez. Once yanimizdaki tekerlekler denenir,
    olmazsa agdan indirilir. Ciktida anahtar/parola benzeri her sey
    `ozet.temizle` ile maskelenir: ekrana kimlik sizmaz.
    """
    from . import ozet as ozet_modulu

    kosucu = calistirici if calistirici is not None else subprocess.run
    ortam = ozet_modulu.alt_surec_ortami(proxy)
    denemeler: list[tuple[str, list[str]]] = []
    klasorler = tekerlek_klasorleri(kok)
    if klasorler:
        denemeler.append(("yerel", pip_komutu(sys.executable, klasorler)))
    denemeler.append(("ağ", pip_komutu(sys.executable)))

    cikti = ""
    for kaynak, komut in denemeler:
        kod, metin = _pip_calistir(kosucu, komut, ortam)
        cikti = ozet_modulu.temizle(metin)
        if kod == 0:
            log.info("faster-whisper kuruldu (%s)", kaynak)
            return {
                "kuruldu": True,
                "kaynak": kaynak,
                "mesaj": f"faster-whisper kuruldu ({KAYNAK_METNI.get(kaynak, kaynak)}).",
                "cikti": cikti,
            }
        log.info("faster-whisper kurulumu olmadi (%s): %s", kaynak, cikti)
    return {
        "kuruldu": False,
        "kaynak": "",
        "mesaj": _kurulum_hatasi(cikti),
        "cikti": cikti,
    }


def _kurulum_hatasi(cikti: str) -> str:
    """Ekranda duracak tek satirlik hata: sik gorulen iki durum adiyla anilir."""
    metin = str(cikti or "")
    if "No module named pip" in metin:
        return (
            "Bu Python kurulumunda pip yok (taşınabilir tam paket): "
            "faster-whisper zaten paketle birlikte gelir, paketi yenileyin."
        )
    if not metin.strip():
        return "Kurulum yapılamadı."
    return "Kurulum yapılamadı: " + metin


def default_dokucu(model: str = VARSAYILAN_MODEL, klasor: str = "") -> WhisperDokucu:
    return WhisperDokucu(model=model, klasor=klasor)


def bos_mu(segmentler: Iterable[Segment]) -> bool:
    return not any(str(parca.metin or "").strip() for parca in segmentler)
