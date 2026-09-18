"""Yaziya dokme: faster-whisper (CPU, int8) ve iki kanalin harmanlanmasi.

`faster-whisper` artik `requirements.txt` icinde (Windows isaretiyle), yani
normal kurulumda gelir. Yine de gelmemis olabilir: lite pakete v0.10.1'den
once girilmisse ya da kurumsal vekil PyPI'yi kesiyorsa. O durumda not "hata:
faster-whisper yok" durumunda kalir, ses SILINMEZ ve paket kurulunca satir
kendiliginden yeniden kuyruga girer (`kur` + `depo.eksik_paket_hatalarini_kuyruga_al`).

Model dosyasi ayardan verilen klasorden okunur; kurum vekili Hugging Face'i
kesiyorsa kullanici model klasorunu elle kopyalayabilsin diye. Klasor bossa
kitaplik modeli kendi indirir.

Klasor VERILDIYSE aga HIC cikilmaz (`model_cozumle`): kurumsal vekilde
Hugging Face denemesi dakikalarca zaman asimi bekletiyor, kullanici da
"isleniyor"da takili kaliyordu. Klasorde model yoksa once anlasilir bir hata
verilir. faster-whisper `model_size_or_path` olarak yerel klasoru DOGRUDAN
kabul eder (icinde `model.bin` + `config.json`); `download_root` ise HF
onbellek KOKUDUR, acilmis model klasoru degil -- saha hatasi buydu.

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

# Acilmis bir faster-whisper modelinde bu dosya mutlaka vardir.
MODEL_DOSYASI = "model.bin"
# Release'teki zip klasoru boyle adlanir: `faster-whisper-small`.
KLASOR_ONEKI = "faster-whisper-"
# Hugging Face onbellek duzeni: `models--Systran--faster-whisper-small/snapshots/<id>/`.
ONBELLEK_ONEKI = "models--Systran--faster-whisper-"
# Model yukleme hatasi ekranda tek satir kalsin diye kirpilir.
HATA_SINIRI = 200

BEKLENEN_DUZEN = (
    "klasörün içinde model.bin olmalı "
    "(ya doğrudan, ya <model adı> alt klasöründe, ya da Hugging Face önbelleği olarak)"
)


def eksik_paket() -> GorusmeHatasi:
    return GorusmeHatasi(
        "faster_whisper_yok",
        "faster-whisper kurulu değil; görüşme yazıya dökülemez.",
        status=400,
    )


def model_hatasi(hata: BaseException) -> GorusmeHatasi:
    """Kitapligin attigi her seyi kisa, Turkce tek satira indirir."""
    metin = " ".join(str(hata).split()) or hata.__class__.__name__
    return GorusmeHatasi("model_yuklenemedi", f"Model yüklenemedi: {metin[:HATA_SINIRI]}")


def _model_var(yol: Path) -> bool:
    return (yol / MODEL_DOSYASI).is_file()


def _onbellek_var(klasor: Path, model_adi: str) -> bool:
    """Klasor bir HF onbellek koku mu? (`models--Systran--...--/snapshots/*/model.bin`)"""
    kok = klasor / f"{ONBELLEK_ONEKI}{model_adi}" / "snapshots"
    if not kok.is_dir():
        return False
    return any(_model_var(anlik) for anlik in kok.iterdir() if anlik.is_dir())


def model_cozumle(model_adi: str, klasor: str) -> tuple[str, dict[str, Any]]:
    """Ayardan gelen klasoru faster-whisper argumanlarina cevirir.

    Doner: (`model_size_or_path`, ek secenekler). Klasor bossa model adi
    dondurulur ve kitaplik HF'den indirir. Klasor VERILDIYSE ag hic
    denenmez: ya yerel bir yol bulunur ya da anlasilir hata atilir.
    """
    ad = str(model_adi or VARSAYILAN_MODEL).strip() or VARSAYILAN_MODEL
    metin = str(klasor or "").strip()
    if not metin:
        return ad, {}
    yol = Path(metin)
    if not yol.is_dir():
        raise GorusmeHatasi(
            "model_klasoru_yok", f"Model klasörü bulunamadı: {metin}"
        )
    # (a) klasorun kendisi acilmis model.
    if _model_var(yol):
        return str(yol), {}
    # (b) icinde `<model adi>` ya da `faster-whisper-<model adi>` alt klasoru.
    for alt in (yol / ad, yol / f"{KLASOR_ONEKI}{ad}"):
        if _model_var(alt):
            return str(alt), {}
    # (c) HF onbellek koku: kitaplik kendi bulur ama aga CIKMAZ.
    if _onbellek_var(yol, ad):
        return ad, {"download_root": str(yol), "local_files_only": True}
    raise GorusmeHatasi(
        "model_bulunamadi",
        f"Model klasöründe {MODEL_DOSYASI} bulunamadı: {metin}; beklenen düzen: {BEKLENEN_DUZEN}.",
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
        # Cozumlenen yol: "Modeli sına" bunu ekrana yazar.
        self.yol = ""
        self._model: Any = None

    def yukle(self) -> Any:
        """Modeli bir kez yukler; hata Turkce ve kisa doner."""
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel  # yerel ice aktarim
        except ImportError as hata:
            raise eksik_paket() from hata
        hedef, ek = model_cozumle(self.model_adi, self.klasor)
        self.yol = hedef
        secenekler: dict[str, Any] = {"device": "cpu", "compute_type": HESAP_TIPI, **ek}
        try:
            self._model = WhisperModel(hedef, **secenekler)
        except GorusmeHatasi:
            raise
        except Exception as hata:  # noqa: BLE001 - kitaplik her seyi atabilir
            raise model_hatasi(hata) from hata
        return self._model

    def cevir(self, yol: Path, dil: str = VARSAYILAN_DIL) -> list[Segment]:
        model = self.yukle()
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


def sina(
    model: str = VARSAYILAN_MODEL, klasor: str = "", dokucu: Any = None
) -> dict[str, Any]:
    """"Modeli sına": modeli yuklemeyi dener, kuyruga hic dokunmaz.

    Kendi dokucusunu kurar; isci is parcacigi ile ortak durum yoktur, yani
    sinama sirasinda kuyruk calismaya devam eder. Klasor verildiyse ag
    denenmez: hatali yol dakikalarca zaman asimi yerine aninda yanit verir.
    """
    import time

    arac = dokucu if dokucu is not None else default_dokucu(model, klasor)
    basladi = time.monotonic()
    try:
        arac.yukle()
    except GorusmeHatasi as hata:
        return {"calisiyor": False, "yol": "", "sn": 0.0, "kod": hata.code, "mesaj": str(hata)}
    except Exception as hata:  # noqa: BLE001 - kitaplik her seyi atabilir
        temiz = model_hatasi(hata)
        return {"calisiyor": False, "yol": "", "sn": 0.0, "kod": temiz.code, "mesaj": str(temiz)}
    gecen = time.monotonic() - basladi
    yol = str(getattr(arac, "yol", "") or model)
    return {
        "calisiyor": True,
        "yol": yol,
        "sn": round(gecen, 1),
        "kod": "",
        "mesaj": f"hazır: {yol}, {gecen:.1f} sn",
    }


def bos_mu(segmentler: Iterable[Segment]) -> bool:
    return not any(str(parca.metin or "").strip() for parca in segmentler)
