"""Teams gorusmesinin basladigini/bittigini anlayan yoklama.

Ana sinyal Windows'un kendi **mikrofon izin defteri**dir:

    HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\CapabilityAccessManager
        \\ConsentStore\\microphone\\<uygulama>

Her uygulamanin altinda `LastUsedTimeStart` ve `LastUsedTimeStop` durur
(FILETIME). **`Stop == 0` ise mikrofon SU AN kullanimda demektir**; Windows
gizlilik ayarlarindaki "son kullanim" listesi de bunu okur. Teams iki yerde
gorunebilir: paketli surum `MSTeams_8wekyb3d8bbwe...` anahtarinda, klasik
surum ise `NonPackaged` altinda yolu bozulmus (`\\` yerine `#`) bir anahtarda
(`...#ms-teams.exe`, `...#Teams.exe`).

Yedek sinyal: Teams surecinin aktif ses oturumu (`aygit.py`, pycaw). Kayit
defteri bir sey soylemiyorsa ona bakilir; o da yoksa sessizce "gorusme yok"
denir -- yanlis pozitif, kaydedilmemis gorusmeden daha kotudur.

`winreg` ice aktarimi platform korumalidir: Linux'ta bu modul yuklenebilir
ama hicbir Windows API'sine dokunmaz.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from .source import AlgilamaDurumu, Aygitlar, is_supported

log = logging.getLogger("holocron.gorusme.algilayici")

CONSENT_YOLU = (
    r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager"
    r"\ConsentStore\microphone"
)
NONPACKAGED = "NonPackaged"

# Paketli yeni Teams: anahtar adi bu onekle baslar.
PAKET_ONEKI = "MSTeams_8wekyb3d8bbwe"
# Klasik Teams ve yeni Teams'in exe adlari (NonPackaged anahtarlarinda gecer).
EXE_ADLARI: tuple[str, ...] = ("ms-teams.exe", "teams.exe")

KAYNAK_DEFTER = "kayit_defteri"
KAYNAK_OTURUM = "ses_oturumu"


@dataclass(frozen=True)
class DefterSatiri:
    """Tek bir uygulamanin mikrofon kullanim kaydi."""

    ad: str
    baslangic: int = 0
    bitis: int = 0

    @property
    def kullanimda(self) -> bool:
        """Stop == 0 ve bir baslangic damgasi varsa mikrofon aciktir."""
        return self.bitis == 0 and self.baslangic > 0


def teams_mi(ad: str) -> bool:
    """Anahtar adi Teams'i mi gosteriyor?

    Paketli surumde anahtar `MSTeams_8wekyb3d8bbwe...`, klasik surumde
    yol bozulmus bicimde biter (`...#ms-teams.exe`). Karsilastirma kucuk
    harfe indirilir: defterdeki yazim surumden surume degisiyor.
    """
    metin = str(ad or "").strip().lower()
    if not metin:
        return False
    if metin.startswith(PAKET_ONEKI.lower()):
        return True
    kuyruk = metin.replace("/", "#").replace("\\", "#").rsplit("#", 1)[-1]
    return kuyruk in EXE_ADLARI


def teams_satirlari(satirlar: Iterable[DefterSatiri]) -> list[DefterSatiri]:
    return [satir for satir in satirlar if teams_mi(satir.ad)]


def mikrofon_acik(satirlar: Iterable[DefterSatiri]) -> bool:
    """Teams satirlarindan herhangi biri "su an kullanimda" diyorsa acik."""
    return any(satir.kullanimda for satir in teams_satirlari(satirlar))


def _winreg() -> Any:
    """`winreg` modulu; Windows disinda `None`."""
    if not is_supported():
        return None
    try:
        import winreg  # yerel ice aktarim: Windows disinda dokunulmaz
    except ImportError:  # pragma: no cover - Windows'ta her zaman var
        return None
    return winreg


def defteri_oku() -> list[DefterSatiri]:
    """Mikrofon izin defterindeki butun uygulamalari okur.

    Okuma basarisiz olursa bos liste doner: algilama ikincil bir sinyale
    duser, uygulama hicbir zaman bu yuzden durmaz.
    """
    winreg = _winreg()
    if winreg is None:
        return []
    satirlar: list[DefterSatiri] = []
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CONSENT_YOLU) as kok:
            for ad in _alt_anahtarlar(winreg, kok):
                if ad == NONPACKAGED:
                    satirlar.extend(_nonpackaged(winreg, kok))
                    continue
                satir = _satir(winreg, kok, ad)
                if satir is not None:
                    satirlar.append(satir)
    except OSError:
        log.debug("Mikrofon izin defteri okunamadi", exc_info=True)
    return satirlar


def _alt_anahtarlar(winreg: Any, kok: Any) -> list[str]:
    adlar: list[str] = []
    sira = 0
    while True:
        try:
            adlar.append(winreg.EnumKey(kok, sira))
        except OSError:
            break
        sira += 1
    return adlar


def _nonpackaged(winreg: Any, kok: Any) -> list[DefterSatiri]:
    try:
        with winreg.OpenKey(kok, NONPACKAGED) as dal:
            return [
                satir
                for ad in _alt_anahtarlar(winreg, dal)
                if (satir := _satir(winreg, dal, ad)) is not None
            ]
    except OSError:
        return []


def _satir(winreg: Any, ust: Any, ad: str) -> DefterSatiri | None:
    try:
        with winreg.OpenKey(ust, ad) as anahtar:
            return DefterSatiri(
                ad=ad,
                baslangic=_deger(winreg, anahtar, "LastUsedTimeStart"),
                bitis=_deger(winreg, anahtar, "LastUsedTimeStop"),
            )
    except OSError:
        return None


def _deger(winreg: Any, anahtar: Any, ad: str) -> int:
    try:
        deger, _ = winreg.QueryValueEx(anahtar, ad)
    except OSError:
        return 0
    try:
        return int(deger)
    except (TypeError, ValueError):
        return 0


class KayitDefteriAlgilayici:
    """`AlgilayiciProtokolu`nun gercek uygulamasi.

    Okuyucular disaridan verilebilir: testler Windows API'sine hic dokunmadan
    defter satirlarini ve aygit cozumlemesini sahteleyebilir.
    """

    def __init__(
        self,
        okuyucu: Callable[[], list[DefterSatiri]] | None = None,
        aygit_cozucu: Callable[[], Aygitlar] | None = None,
        oturum_sinyali: Callable[[], bool] | None = None,
    ) -> None:
        self._okuyucu = okuyucu or defteri_oku
        self._aygit_cozucu = aygit_cozucu
        self._oturum_sinyali = oturum_sinyali

    def durum(self) -> AlgilamaDurumu:
        kaynak = ""
        if mikrofon_acik(self._okuyucu()):
            kaynak = KAYNAK_DEFTER
        elif self._oturum_sinyali is not None and self._sessizce(self._oturum_sinyali):
            # Yedek sinyal: defter bazi kurulumlarda hic guncellenmiyor.
            kaynak = KAYNAK_OTURUM
        if not kaynak:
            return AlgilamaDurumu(aktif=False)
        return AlgilamaDurumu(aktif=True, aygitlar=self._aygitlar(), kaynak=kaynak)

    def _aygitlar(self) -> Aygitlar:
        if self._aygit_cozucu is None:
            from . import aygit

            return aygit.secili_aygitlar()
        return self._aygit_cozucu()

    @staticmethod
    def _sessizce(cagri: Callable[[], bool]) -> bool:
        """Istege bagli bagimliliklar yuzunden algilama olmemeli."""
        try:
            return bool(cagri())
        except Exception:  # noqa: BLE001 - pycaw/comtypes her turlu hatayi atabilir
            log.debug("Ses oturumu sinyali okunamadi", exc_info=True)
            return False


def default_algilayici(
    mikrofon_ayari: str = "", hoparlor_ayari: str = ""
) -> KayitDefteriAlgilayici:
    """Uretimdeki algilayici: defter + (varsa) ses oturumu yedegi."""
    from . import aygit

    return KayitDefteriAlgilayici(
        aygit_cozucu=lambda: aygit.secili_aygitlar(mikrofon_ayari, hoparlor_ayari),
        oturum_sinyali=aygit.teams_ses_oturumu_var,
    )
