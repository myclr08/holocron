"""Ozet: Copilot CLI'ye transkripti okutur, JSON cevabi dayanikli ayristirir.

Cagri bicimi TDD Beyin kalibidir:

    copilot --model <model> -p "<istem>" --allow-tool=read

Transkript **dosya olarak** verilir ve istem modelden dosyayi okumasini ister:
yarim saatlik bir gorusmenin metni komut satirina sigmaz, kabuk siniriyla
bogusmanin da anlami yok.

Model reddedilirse (sifir olmayan cikis kodu ya da ciktida taninabilir bir
hata) sradaki modele gecilir; calisan model "son calisan" olarak ayara
yazilir, bir sonraki gorusme oradan baslar.

Cikti serbest metin icinde gelebilir ("Işte not: ```json {...}```"): JSON
metnin icinden cekilir, olmazsa hata verilir ve ses SILINMEZ.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Sequence
from urllib.parse import urlsplit

from .source import BOLUM_AKSIYON, OzetCikti

log = logging.getLogger("holocron.gorusme.ozet")

KOMUT = "copilot"
VARSAYILAN_MODELLER: tuple[str, ...] = ("gpt-5", "claude-sonnet-4.5", "gpt-4.1")
# Copilot bir soruya takilirsa is parcacigi sonsuza kadar beklemesin.
ZAMAN_ASIMI = 900

# Modelin reddedildigini anlatan ciktilar (kucuk harfe indirilmis arama).
RED_IZLERI: tuple[str, ...] = (
    "model not supported",
    "model is not available",
    "unknown model",
    "unsupported model",
    "access denied",
    "not entitled",
    "rate limit",
    "quota exceeded",
)

SABLON_DOSYASI = Path(__file__).resolve().parent / "sablon.txt"


def varsayilan_sablon() -> str:
    return SABLON_DOSYASI.read_text(encoding="utf-8")


def istem_kur(
    sablon: str,
    transkript: Path,
    katilimcilar: Sequence[str] = (),
    tarih: str = "",
    sure: str = "",
) -> str:
    """Sablondaki yer tutuculari doldurur.

    `str.format` KULLANILMAZ: sablonun icinde JSON semasi var, suslu
    parantezler bicimlendiriciyi patlatirdi.
    """
    degerler = {
        "{transkript}": str(transkript),
        "{katilimcilar}": ", ".join(katilimcilar) or "bilinmiyor",
        "{tarih}": tarih or "bilinmiyor",
        "{sure}": sure or "bilinmiyor",
    }
    metin = str(sablon or "")
    for anahtar, deger in degerler.items():
        metin = metin.replace(anahtar, deger)
    return metin


def reddedildi_mi(cikti: OzetCikti) -> bool:
    """Model kullanilamiyor mu? (sifir olmayan kod ya da taninabilir hata)"""
    if cikti.kod != 0:
        return True
    metin = f"{cikti.metin}\n{cikti.hata}".lower()
    return any(iz in metin for iz in RED_IZLERI)


# Alt surece verilen vekil degiskenleri: buyuk ve kucuk harfli yazimlarin
# ikisi de gerekir, kitapliklarin hangisini okudugu degisiyor.
PROXY_DEGISKENLERI: tuple[str, ...] = ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy")
NO_PROXY_DEGISKENLERI: tuple[str, ...] = ("NO_PROXY", "no_proxy")


def jira_konagi(base_url: str) -> str:
    """Jira adresinin yalnizca konak adi (`NO_PROXY` bunu ister)."""
    metin = str(base_url or "").strip()
    if not metin:
        return ""
    if "://" not in metin:
        metin = "http://" + metin
    return str(urlsplit(metin).hostname or "")


def alt_surec_ortami(
    proxy: str = "", jira_base_url: str = "", taban: dict[str, str] | None = None
) -> dict[str, str]:
    """Copilot alt surecinin ortami.

    Sirket kurulumunda Copilot **vekil sunucudan** cikiyor, Jira ise
    **dogrudan** goruluyor. Vekili Holocron'un kendi surecine yazarsak Jira
    istekleri de oradan gecmeye calisir ve kopar; bu yuzden vekil YALNIZCA
    alt surecin ortamina konur, `os.environ` hic degismez. Jira konagi da
    alt surecin `NO_PROXY` listesine eklenir: Copilot bir sebeple Jira'ya
    dokunursa o istek de vekile gitmesin.
    """
    ortam = dict(os.environ if taban is None else taban)
    adres = str(proxy or "").strip()
    if adres:
        for ad in PROXY_DEGISKENLERI:
            ortam[ad] = adres
    konak = jira_konagi(jira_base_url)
    if konak:
        mevcut = [
            parca.strip()
            for ad in NO_PROXY_DEGISKENLERI
            for parca in str(ortam.get(ad, "")).split(",")
            if parca.strip()
        ]
        if konak not in mevcut:
            mevcut.append(konak)
        birlesik = ",".join(dict.fromkeys(mevcut))
        for ad in NO_PROXY_DEGISKENLERI:
            ortam[ad] = birlesik
    return ortam


class CopilotOzetleyici:
    """`OzetleyiciProtokolu`nun gercek uygulamasi (alt surec)."""

    def __init__(
        self,
        komut: str = KOMUT,
        zaman_asimi: int = ZAMAN_ASIMI,
        proxy: str = "",
        jira_base_url: str = "",
    ) -> None:
        self.komut = komut
        self.zaman_asimi = zaman_asimi
        self.proxy = str(proxy or "").strip()
        self.jira_base_url = str(jira_base_url or "").strip()

    def ortam(self) -> dict[str, str]:
        return alt_surec_ortami(self.proxy, self.jira_base_url)

    def ozetle(self, transkript: Path, model: str, istem: str) -> OzetCikti:
        argumanlar = [self.komut, "--model", model, "-p", istem, "--allow-tool=read"]
        try:
            sonuc = subprocess.run(  # noqa: S603 - sabit komut, kullanici girdisi arguman degil
                argumanlar,
                capture_output=True,
                text=True,
                timeout=self.zaman_asimi,
                cwd=str(transkript.parent),
                env=self.ortam(),
            )
        except FileNotFoundError:
            return OzetCikti(kod=127, metin="", hata="Copilot CLI bulunamadı (copilot).")
        except subprocess.TimeoutExpired:
            return OzetCikti(kod=124, metin="", hata="Copilot CLI zaman aşımına uğradı.")
        except OSError as hata:  # pragma: no cover - isletim sistemi hatasi
            return OzetCikti(kod=1, metin="", hata=str(hata))
        return OzetCikti(
            kod=int(sonuc.returncode or 0),
            metin=str(sonuc.stdout or ""),
            hata=str(sonuc.stderr or ""),
        )


@dataclass
class OzetSonucu:
    """Sirayla denenen modellerin sonucu."""

    veri: dict[str, Any]
    model: str = ""
    denenen: tuple[str, ...] = ()
    hata: str = ""

    @property
    def basarili(self) -> bool:
        return not self.hata


def modelleri_coz(ayar: Any, son_calisan: str = "") -> list[str]:
    """Ayardaki sirali model listesi; "son calisan" basa alinir."""
    liste: list[str] = []
    ham = ayar
    if isinstance(ham, str):
        metin = ham.strip()
        if metin.startswith("["):
            try:
                ham = json.loads(metin)
            except json.JSONDecodeError:
                ham = [parca for parca in metin.split(",")]
        else:
            ham = [parca for parca in metin.split(",")]
    if isinstance(ham, (list, tuple)):
        liste = [str(parca).strip() for parca in ham if str(parca).strip()]
    if not liste:
        liste = list(VARSAYILAN_MODELLER)
    tercih = str(son_calisan or "").strip()
    if tercih and tercih in liste:
        liste = [tercih] + [ad for ad in liste if ad != tercih]
    elif tercih:
        liste = [tercih] + liste
    return liste


def calistir(
    ozetleyici: Any,
    transkript: Path,
    modeller: Sequence[str],
    istem: str,
) -> OzetSonucu:
    """Modelleri sirayla dener; ilk gecerli JSON kazanir."""
    denenen: list[str] = []
    son_hata = "Özetleyici hiç çalıştırılamadı."
    for model in modeller:
        denenen.append(model)
        cikti = ozetleyici.ozetle(transkript, model, istem)
        if reddedildi_mi(cikti):
            son_hata = (cikti.hata or cikti.metin or "model reddedildi").strip()[:500]
            log.info("Özet modeli reddedildi: %s", model)
            continue
        veri = ayristir(cikti.metin)
        if veri is None:
            son_hata = "Model JSON döndürmedi."
            continue
        return OzetSonucu(veri=veri, model=model, denenen=tuple(denenen))
    return OzetSonucu(veri={}, denenen=tuple(denenen), hata=son_hata)


# --- JSON cekme ---------------------------------------------------------

_KOD_BLOGU = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def ayristir(metin: str) -> dict[str, Any] | None:
    """Serbest metnin icinden JSON nesnesini ceker.

    Once dogrudan okunur, sonra kod blogu, sonra ilk dengeli suslu parantez
    kumesi denenir. Model "Işte not:" gibi bir giris cumlesi yazsa bile not
    kaybolmasin.
    """
    ham = str(metin or "").strip()
    if not ham:
        return None
    for aday in _adaylar(ham):
        try:
            veri = json.loads(aday)
        except json.JSONDecodeError:
            continue
        if isinstance(veri, dict):
            return veri
    return None


def _adaylar(metin: str) -> list[str]:
    adaylar = [metin]
    adaylar.extend(_KOD_BLOGU.findall(metin))
    dengeli = _dengeli_nesne(metin)
    if dengeli:
        adaylar.append(dengeli)
    return adaylar


def _dengeli_nesne(metin: str) -> str:
    """Ilk `{` ile esleyen `}` arasini dondurur (metin icindeki tirnaklara dikkat)."""
    basla = metin.find("{")
    if basla < 0:
        return ""
    derinlik = 0
    tirnak = False
    kacis = False
    for sira in range(basla, len(metin)):
        harf = metin[sira]
        if kacis:
            kacis = False
            continue
        if harf == "\\":
            kacis = True
            continue
        if harf == '"':
            tirnak = not tirnak
            continue
        if tirnak:
            continue
        if harf == "{":
            derinlik += 1
        elif harf == "}":
            derinlik -= 1
            if derinlik == 0:
                return metin[basla : sira + 1]
    return ""


# --- cikti duzeltme -----------------------------------------------------

# Bir notta en fazla kac madde saklanir: model kacarsa tablo sismesin.
MADDE_SINIRI = 40


def maddeler(veri: dict[str, Any], anahtar: str) -> list[str]:
    ham = veri.get(anahtar)
    if isinstance(ham, str):
        ham = [satir for satir in ham.splitlines()]
    if not isinstance(ham, (list, tuple)):
        return []
    temiz = [str(parca).strip().lstrip("-•* ").strip() for parca in ham]
    return [parca for parca in temiz if parca][:MADDE_SINIRI]


def aksiyonlar(veri: dict[str, Any]) -> list[dict[str, str]]:
    """Aksiyon satirlari: metin zorunlu, kisi ve son tarih istege bagli."""
    ham = veri.get("aksiyonlar")
    if not isinstance(ham, (list, tuple)):
        return []
    sonuc: list[dict[str, str]] = []
    for parca in ham:
        if isinstance(parca, str):
            metin, kisi, son_tarih = parca.strip(), "", ""
        elif isinstance(parca, dict):
            metin = str(parca.get("metin") or parca.get("text") or "").strip()
            kisi = str(parca.get("kisi") or parca.get("kişi") or "").strip()
            son_tarih = str(parca.get("son_tarih") or "").strip()
        else:
            continue
        if not metin:
            continue
        sonuc.append({"metin": metin, "kisi": kisi, "son_tarih": son_tarih})
        if len(sonuc) >= MADDE_SINIRI:
            break
    return sonuc


def baslik(veri: dict[str, Any], yedek: str = "") -> str:
    metin = str(veri.get("baslik") or "").strip()
    return metin or yedek


def bolumler(veri: dict[str, Any]) -> list[dict[str, Any]]:
    """Notun butun bolumlerini tek listeye cevirir (veritabanina yazilacak sira)."""
    satirlar: list[dict[str, Any]] = []
    for tur, anahtar in (("ozet", "ozet"), ("karar", "kararlar"), ("soru", "sorular")):
        for sira, metin in enumerate(maddeler(veri, anahtar)):
            satirlar.append({"tur": tur, "sira": sira, "metin": metin, "kisi": "", "son_tarih": ""})
    for sira, aksiyon in enumerate(aksiyonlar(veri)):
        satirlar.append(
            {
                "tur": BOLUM_AKSIYON,
                "sira": sira,
                "metin": aksiyon["metin"],
                "kisi": aksiyon["kisi"],
                "son_tarih": aksiyon["son_tarih"],
            }
        )
    return satirlar


# --- sinama --------------------------------------------------------------

SINAMA_ISTEMI = "Yalnızca tek kelime yaz: hazır"
SINAMA_DOSYASI = "copilot-sinama.txt"
# Hata metni ekranda gosterilir: uzun ciktinin kuyrugu isimize yaramaz.
HATA_SINIRI = 400

# Ciktidan temizlenecek izler: anahtar/parola benzeri her sey ekrana cikmasin.
_SIR_IZLERI = re.compile(
    r"(?i)\b(?:gh[posur]_[A-Za-z0-9]{6,}|bearer\s+\S+|api[_-]?key\s*[:=]\s*\S+"
    r"|token\s*[:=]\s*\S+|password\s*[:=]\s*\S+)"
)


def temizle(metin: str) -> str:
    """Ekrana cikacak hata metni: anahtar benzeri diziler maskelenir."""
    ham = str(metin or "").strip()
    if not ham:
        return ""
    gizli = _SIR_IZLERI.sub("[gizlendi]", ham)
    tek_satir = " ".join(gizli.split())
    return tek_satir[:HATA_SINIRI]


def sina(ozetleyici: Any, modeller: Sequence[str], klasor: Path) -> dict[str, Any]:
    """Kisa bir istek atar: Copilot CLI ve vekil ayari gercekten calisiyor mu?

    Yedek sira burada da isler: ilk model reddederse sradaki denenir ve
    calisan model "son calisan" olarak geri bildirilir.
    """
    import time

    klasor.mkdir(parents=True, exist_ok=True)
    dosya = klasor / SINAMA_DOSYASI
    dosya.write_text("Bu bir bağlantı sınamasıdır.\n", encoding="utf-8")
    denenen: list[str] = []
    son_hata = "Copilot CLI hiç çalıştırılamadı."
    try:
        for model in modeller:
            denenen.append(model)
            basladi = time.monotonic()
            cikti = ozetleyici.ozetle(dosya, model, SINAMA_ISTEMI)
            gecen = time.monotonic() - basladi
            if reddedildi_mi(cikti):
                son_hata = temizle(cikti.hata or cikti.metin or "model reddedildi")
                continue
            return {
                "calisiyor": True,
                "model": model,
                "sn": round(gecen, 1),
                "denenen": denenen,
                "mesaj": f"çalışıyor · model {model} · {gecen:.1f} sn",
            }
    finally:
        try:
            dosya.unlink()
        except OSError:
            pass
    return {
        "calisiyor": False,
        "model": "",
        "sn": 0.0,
        "denenen": denenen,
        "mesaj": son_hata or "Copilot CLI yanıt vermedi.",
    }


def default_ozetleyici(proxy: str = "", jira_base_url: str = "") -> CopilotOzetleyici:
    return CopilotOzetleyici(proxy=proxy, jira_base_url=jira_base_url)
