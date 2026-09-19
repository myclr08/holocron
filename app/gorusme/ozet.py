"""Ozet: Copilot CLI'ye transkripti okutur, JSON cevabi DOSYADAN alir.

Cagri bicimi TDD Beyin kalibidir:

    copilot --model <model> -p "<istem>" --allow-tool=read --allow-tool=write

Transkript **dosya olarak** verilir ve istem modelden dosyayi okumasini ister:
yarim saatlik bir gorusmenin metni komut satirina sigmaz, kabuk siniriyla
bogusmanin da anlami yok.

Cevap da **dosyadan** alinir. Saha hatasi (19 Eylul 2026, Windows): "Özet
alınamadı: Model JSON döndürmedi." Copilot bulunuyor, model kabul ediliyor,
surec donuyor ama stdout'tan JSON cikmiyor: programatik kipte (`-p`) stdout'a
banner, ilerleme satirlari, arac kullanim dokumu ve ANSI renk kodlari
karisiyor; cevap markdown icinde ya da stderr'de kalabiliyor. TDD Beyin
(`beyin/araclar/beyin.py`) bu yuzden sonucu modelin YAZDIGI dosyadan okur,
stdout'u yalnizca log/teshis icin tutar. Burada da ayni kalip:

* istem modelden JSON'u transkriptin yanindaki `ozet.json`a yazmasini ister,
* surec bitince o dosya okunur (UTF-8, BOM toleransli, ANSI yok),
* dosya yoksa/bozuksa YEDEK yol: stdout + stderr birlestirilir, ANSI kacis
  dizileri temizlenir, kod blogundan ya da duz metinden JSON cekilir,
* o da olmazsa hata metninde ham ciktinin kuyrugu durur ve `holocron.log`a
  WARNING ile daha uzun bir kuyruk yazilir. Ses SILINMEZ.

Model reddedilirse (sifir olmayan cikis kodu ya da ciktida taninabilir bir
hata) sradaki modele gecilir; calisan model "son calisan" olarak ayara
yazilir, bir sonraki gorusme oradan baslar.

Copilot'un KENDISI de aranir: `holocron.bat` uygulamayi `start "" pythonw.exe`
ile actigi icin surec, kullanicinin terminaldeki PATH'ini gormeyebilir (npm'in
global klasoru cogu kez yalnizca kullanici PATH'inde durur ve o PATH oturum
acildiktan sonra degistiyse surece hic ulasmaz). Bu yuzden `copilot_bul`
sirayla ayardaki yolu, `PATH`i, Windows'un bilinen kurulum yerlerini ve
kayit defterinden TAZE okunan kullanici/makine PATH'ini dener.
"""

from __future__ import annotations

import glob
import json
import logging
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit

from .altsurec import sessiz_calistir_ayarlari
from .source import BOLUM_AKSIYON, OzetCikti

log = logging.getLogger("holocron.gorusme.ozet")

KOMUT = "copilot"
# Sahadan 19 Eylul 2026: gpt-5, claude-sonnet-4.5 ve gpt-4.1 kullanicinin
# hesabinda "not available" diye reddedildi; calisan modeller claude-sonnet-5
# ve gpt-5-mini (yedek claude-haiku-4.5). Varsayilan model basa alinir, eski
# adlar da yedek sirada tutulur; baska hesapta calisabilirler.
VARSAYILAN_MODELLER: tuple[str, ...] = (
    "claude-sonnet-5",
    "gpt-5-mini",
    "claude-haiku-4.5",
    "claude-sonnet-4.5",
    "gpt-5",
)
# Copilot bir soruya takilirsa is parcacigi sonsuza kadar beklemesin.
# Ozet uzun surebilir (yarim saatlik transkript), sinama ise kisacik olmali.
ZAMAN_ASIMI = 900
SINAMA_ZAMAN_ASIMI = 300

# Modelin JSON'u yazacagi dosya: transkriptin YANINA dusar, surec bitince
# okunur ve silinir. Birincil yol budur, stdout yedektir.
OZET_DOSYASI = "ozet.json"

# Copilot'a verilen arac izinleri (TDD Beyin ile ayni bicim). `read` olmadan
# model transkripti okuyamaz, `write` olmadan cevabi dosyaya yazamaz.
IZIN_BAYRAKLARI: tuple[str, ...] = ("--allow-tool=read", "--allow-tool=write")

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
    # Sahadan 19 Eylul 2026 (Copilot CLI --model bayragi): "Model "gpt-4.1"
    # from --model flag is not available".
    "from --model flag is not available",
)

SABLON_DOSYASI = Path(__file__).resolve().parent / "sablon.txt"


def varsayilan_sablon() -> str:
    return SABLON_DOSYASI.read_text(encoding="utf-8")


def cikti_yolu(transkript: Path) -> Path:
    """Modelin JSON'u yazacagi dosya: transkriptin yanindaki `ozet.json`.

    Istem de (`{cikti}`), okuyan taraf da (`CopilotOzetleyici.ozetle`) bu tek
    fonksiyonu cagirir: yol iki yerde ayri ayri kurulup ayrisamaz.
    """
    return Path(transkript).parent / OZET_DOSYASI


def dosya_oku(yol: Path) -> str:
    """Modelin yazdigi dosyayi okur; yoksa ya da okunamazsa bos metin.

    `utf-8-sig`: Windows'ta bir arac BOM birakirsa `json.loads` patlamasin.
    Cozulemeyen bayt varsa metin yine de gelsin, teshis icin ise yarar.
    """
    try:
        return Path(yol).read_text(encoding="utf-8-sig", errors="replace")
    except (OSError, ValueError):
        return ""


def dosya_sil(yol: Path) -> None:
    """Ara dosyayi sessizce siler (yoksa dert degil)."""
    try:
        Path(yol).unlink()
    except OSError:
        pass


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
        "{cikti}": str(cikti_yolu(transkript)),
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


def reddedilenler_mesaji(modeller: Sequence[str]) -> str:
    """Reddedilen modelleri tek satirda listeler, ayara yonlendirir.

    Kullanici hangi modelin calistigini bilmiyorsa hesabinda GECERLI bir ad
    goremez; ornek olarak varsayilan modeli gosteririz.
    """
    liste = ", ".join(str(ad).strip() for ad in modeller if str(ad).strip())
    return (
        f"Copilot modelleri reddetti: {liste} — Ayarlar'dan hesabında olan "
        f"bir model seçin (ör. {VARSAYILAN_MODELLER[0]})."
    )


# --- ham cikti: ANSI temizligi, izin reddi, teshis ------------------------

# ANSI kacis dizileri: renk/imlec (CSI), pencere basligi (OSC) ve tek harfli
# kisa diziler. Copilot programatik kipte bile renk basabiliyor; JSON'un
# icine dusen tek bir `\x1b[32m` ayristiriciyi bozar.
_ANSI = re.compile(
    r"\x1b\[[0-9;?]*[ -/]*[@-~]"          # CSI: renk, imlec, silme
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC: pencere basligi
    r"|\x1b[@-Z\\-_]"                      # tek harfli kisa diziler
)


def ansi_temizle(metin: str) -> str:
    """ANSI dizilerini atar, satir basi (`\\r`) ilerlemesini satira cevirir."""
    ham = _ANSI.sub("", str(metin or ""))
    return ham.replace("\r\n", "\n").replace("\r", "\n")


def ham_cikti(cikti: Any) -> str:
    """stdout + stderr, ANSI'den arinmis: yedek ayiklama ve teshis bunu okur."""
    parcalar = [
        str(getattr(cikti, "metin", "") or ""),
        str(getattr(cikti, "hata", "") or ""),
    ]
    return ansi_temizle("\n".join(parca for parca in parcalar if parca.strip()))


# Arac izninin verilmedigini anlatan izler (kucuk harfe indirilmis arama).
# "allow" tek basina YETMEZ: kendi bayragimiz (`--allow-tool=read`) ciktida
# yankilanabilir, acik bir red sozcugu aranir.
IZIN_IZLERI: tuple[str, ...] = (
    "permission denied",
    "permission to",
    "requires permission",
    "requires approval",
    "not allowed",
    "not permitted",
    "tool denied",
    "denied the",
    "denied tool",
    "is denied",
    "was denied",
)


def izin_reddi(ham: str) -> str:
    """Ham ciktida arac izni reddi varsa kullaniciya soylenecek cumle.

    Model dosyayi okuyamadiysa ya da yazamadiysa JSON hic uretilmez; bunu
    "Model JSON döndürmedi" diye gostermek kullaniciyi yanlis yere bakmaya
    gonderir, bu yuzden ayri bir cumle veriyoruz.
    """
    metin = str(ham or "").lower()
    if not any(iz in metin for iz in IZIN_IZLERI):
        return ""
    if "write" in metin:
        arac = "dosyaya yazma"
    elif "read" in metin:
        arac = "dosya okuma"
    else:
        arac = "araç kullanma"
    return f"Copilot {arac} izni vermedi; bayraklar: {' '.join(IZIN_BAYRAKLARI)}"


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


# --- Copilot'u bulmak ----------------------------------------------------

# Windows'ta sirayla bakilan yerler. npm'in global kurulumu `copilot.cmd`
# uretir: sahadan gelen hata tam buradan cikti, o yuzden ilk aday odur.
# Uzantisiz `copilot` node betigidir, Windows'ta dogrudan calismaz; listede
# yalnizca son care olarak durur.
WINDOWS_ADAYLARI: tuple[str, ...] = (
    r"%APPDATA%\npm\copilot.cmd",
    r"%APPDATA%\npm\copilot.exe",
    r"%LOCALAPPDATA%\Microsoft\WinGet\Links\copilot.exe",
    r"%ProgramFiles%\GitHub Copilot CLI\copilot.exe",
    r"%LOCALAPPDATA%\Programs\*copilot*\copilot.exe",
    r"%USERPROFILE%\.local\bin\copilot.exe",
    r"%APPDATA%\npm\node_modules\@github\copilot\copilot.exe",
    r"%APPDATA%\npm\node_modules\@github\copilot\bin\copilot*.exe",
    r"%APPDATA%\npm\copilot",
)

# Kayit defterindeki PATH degerleri (kullanici + makine).
KAYIT_YOLLARI: tuple[tuple[str, str], ...] = (
    ("HKCU", r"Environment"),
    ("HKLM", r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
)

_DEGISKEN = re.compile(r"%([A-Za-z_][A-Za-z0-9_()]*)%")


def _windows_mu(platform: str = "") -> bool:
    """Platform kontrolu cagri aninda okunur (test `sys.platform` degistirir)."""
    return str(platform or sys.platform).startswith("win")


def _genislet(sablon: str, ortam: Mapping[str, str]) -> str:
    """`%APPDATA%` gibi yer tutuculari verilen ortamdan doldurur.

    `os.path.expandvars` KULLANILMAZ: o her zaman `os.environ`a bakar, oysa
    burada testin verdigi sahte ortam da genisletilebilmeli.
    """

    def degistir(esleme: "re.Match[str]") -> str:
        ad = esleme.group(1).lower()
        for anahtar, deger in ortam.items():
            if str(anahtar).lower() == ad:
                return str(deger)
        return esleme.group(0)

    return _DEGISKEN.sub(degistir, str(sablon or ""))


def kayit_defteri_path(platform: str = "") -> list[str]:
    """Kullanici ve makine PATH'i, kayit defterinden TAZE okunur.

    `pythonw.exe` ortamini kendisini acan sureçten devralir. Kullanici PATH'i
    oturum acildiktan sonra degistiyse (npm global klasoru sonradan eklenmis)
    surecin PATH'i eski kalir; terminaldeki `copilot` calisirken Holocron'un
    bulamamasinin sebebi tam budur. Kayit defteri her zaman gunceldir.
    """
    if not _windows_mu(platform):
        return []
    try:
        import winreg  # noqa: PLC0415 - yalnizca Windows'ta var
    except ImportError:  # pragma: no cover - Windows disinda hic calismaz
        return []
    koklar = {"HKCU": winreg.HKEY_CURRENT_USER, "HKLM": winreg.HKEY_LOCAL_MACHINE}
    parcalar: list[str] = []
    for kok_adi, alt in KAYIT_YOLLARI:  # pragma: no cover - Windows'a ozel
        try:
            with winreg.OpenKey(koklar[kok_adi], alt) as anahtar:
                ham, _tur = winreg.QueryValueEx(anahtar, "Path")
        except OSError:
            continue
        parcalar.extend(parca.strip() for parca in str(ham or "").split(";") if parca.strip())
    return list(dict.fromkeys(parcalar))  # pragma: no cover - Windows'a ozel


def _aday_yolu(sablon: str, ortam: Mapping[str, str]) -> str:
    """Gomulu aday: degiskenler doldurulur, ayrac isletim sistemine cevrilir.

    Adaylar Windows yaziliyla (`\\`) durur. Windows'ta bu degisiklik bir sey
    yapmaz; Windows disinda (yalnizca testte) yol gercekten cozulebilir olur.
    """
    return _genislet(sablon, ortam).replace("\\", os.sep)


def _eslesenler(desen: str) -> list[str]:
    """Yildiz varsa glob, yoksa yolun kendisi (sirali, kararli)."""
    if "*" in desen:
        return sorted(glob.glob(desen))
    return [desen]


def copilot_coz(
    ayar_yolu: str = "",
    ad: str = KOMUT,
    ortam: Mapping[str, str] | None = None,
    platform: str = "",
) -> tuple[Path | None, list[str]]:
    """Copilot CLI'yi bulur; bulunan yol ve DENENEN yerlerin listesi doner.

    Denenen liste hata metnine girer: kullanici "nereye baktin" diye sormadan
    gorur ve gerekirse ayara elle yol yazar.
    """
    cevre = dict(os.environ if ortam is None else ortam)
    denenen: list[str] = []

    ham = str(ayar_yolu or "").strip().strip('"')
    if ham:
        aday = Path(_genislet(ham, cevre))
        denenen.append(f"ayar: {aday}")
        if aday.is_file():
            return aday, denenen
        # Ayardaki yol klasorse icindeki calistirilabiliri ara.
        if aday.is_dir():
            bulunan = shutil.which(ad, path=str(aday))
            if bulunan:
                return Path(bulunan), denenen

    denenen.append("PATH")
    bulunan = shutil.which(ad, path=cevre.get("PATH"))
    if bulunan:
        return Path(bulunan), denenen

    if _windows_mu(platform):
        for sablon in WINDOWS_ADAYLARI:
            desen = _aday_yolu(sablon, cevre)
            if "%" in desen:
                continue  # degisken tanimli degil, bu adayin anlami yok
            # Hata metnine dosya degil KLASOR girer: liste okunabilir kalsin.
            klasor = str(Path(desen).parent)
            if klasor not in denenen:
                denenen.append(klasor)
            for eslesen in _eslesenler(desen):
                if Path(eslesen).is_file():
                    return Path(eslesen), denenen
        kayit = kayit_defteri_path(platform)
        if kayit:
            denenen.append("kayıt defteri PATH")
            bulunan = shutil.which(ad, path=os.pathsep.join(kayit))
            if bulunan:
                return Path(bulunan), denenen
    return None, denenen


def copilot_bul(
    ayar_yolu: str = "",
    ad: str = KOMUT,
    ortam: Mapping[str, str] | None = None,
    platform: str = "",
) -> Path | None:
    """Copilot CLI'nin tam yolu ya da `None`."""
    return copilot_coz(ayar_yolu, ad, ortam, platform)[0]


# Hata metnindeki "denenen yerler" listesinin karakter butcesi. Metin ekranda
# kirpiliyor (`HATA_SINIRI`): liste uzarsa asil yonerge kesilir, o yuzden once
# liste kisalir.
DENENEN_SINIRI = 200


def bulunamadi_mesaji(denenen: Sequence[str]) -> str:
    """Kullaniciya nereye bakildigini ve ne yapacagini soyleyen hata metni."""
    yerler: list[str] = []
    uzunluk = 0
    for parca in denenen:
        adres = str(parca).strip()
        if not adres:
            continue
        if uzunluk + len(adres) > DENENEN_SINIRI:
            yerler.append("…")
            break
        yerler.append(adres)
        uzunluk += len(adres) + 2
    return (
        "Copilot CLI bulunamadı. Denenen: "
        + (", ".join(yerler) or "PATH")
        + ". Ayarlar → Görüşme notları → Copilot yolu alanına `where copilot` "
        "çıktısını yazın."
    )


# Kabuk betigi olan adaylar: Windows bunlari dogrudan CALISTIRAMAZ.
KABUK_UZANTILARI: frozenset[str] = frozenset({".cmd", ".bat"})
# Istem dosyasi: `.cmd` yolunda istem argüman olarak degil, dosyadan gecer.
ISTEM_DOSYASI = "copilot-istem.txt"
# `.cmd` yolunda verilen kisa yonlendirme. Icinde cmd.exe'nin ozel gordugu
# hicbir karakter YOK (`%`, `"`, `&`, `|`, `<`, `>`): tirnaklama guvenli.
KISA_ISTEM = (
    "Yonergeler " + ISTEM_DOSYASI + " dosyasinda. O dosyayi oku ve harfiyen uygula."
)


def kabukla_mi(yol: Path, platform: str = "") -> bool:
    """Bu yol `cmd.exe` uzerinden mi calistirilmali?"""
    return _windows_mu(platform) and yol.suffix.lower() in KABUK_UZANTILARI


def komut_kur(
    yol: Path,
    model: str,
    istem: str,
    klasor: Path,
    platform: str = "",
) -> tuple[list[str], Path | None]:
    """Alt surece verilecek arguman listesi (ve varsa yazilan istem dosyasi).

    Windows'ta `CreateProcess` bir `.cmd` dosyasini DOGRUDAN acamaz
    ("%1 is not a valid Win32 application"); npm'in global kurulumu tam da
    `copilot.cmd` birakir. O yuzden bu dosyalar `cmd.exe /c` ile calistirilir.
    Ama cmd komut satirini yeniden ayristirir: istem metninde tirnak, `%` ya
    da `&` varsa komut parcalanir. Bu yuzden `.cmd` yolunda istem ARGÜMAN
    olarak gecmez, transkriptin yanina dosya olarak yazilir ve modele "o
    dosyayi oku" denir (`--allow-tool=read` zaten acik).

    `--allow-tool=write` de verilir: cevap artik stdout'tan degil modelin
    yazdigi `ozet.json`dan okunuyor, izin olmazsa dosya hic olusmaz.
    """
    if kabukla_mi(yol, platform):
        dosya = Path(klasor) / ISTEM_DOSYASI
        dosya.write_text(str(istem), encoding="utf-8")
        argumanlar = [
            "cmd.exe",
            "/c",
            str(yol),
            "--model",
            model,
            "-p",
            KISA_ISTEM,
            *IZIN_BAYRAKLARI,
        ]
        return argumanlar, dosya
    return [str(yol), "--model", model, "-p", istem, *IZIN_BAYRAKLARI], None


class CopilotOzetleyici:
    """`OzetleyiciProtokolu`nun gercek uygulamasi (alt surec)."""

    def __init__(
        self,
        komut: str = KOMUT,
        zaman_asimi: int = ZAMAN_ASIMI,
        proxy: str = "",
        jira_base_url: str = "",
        yol: str = "",
    ) -> None:
        self.komut = komut
        self.zaman_asimi = zaman_asimi
        self.proxy = str(proxy or "").strip()
        self.jira_base_url = str(jira_base_url or "").strip()
        # Ayardaki "Copilot yolu" (bos = otomatik ara).
        self.yol = str(yol or "").strip()
        # Son calisan cozumleme: arayuz bunu "calisiyor · <yol>" diye gosterir.
        self.son_yol = ""

    def ortam(self) -> dict[str, str]:
        return alt_surec_ortami(self.proxy, self.jira_base_url)

    def calistir(self, argumanlar: Sequence[str], klasor: Path) -> Any:
        """Alt surec (testler bunu degistirir, gercek Copilot hic kosmaz)."""
        return subprocess.run(  # noqa: S603 - yol cozumlenmis, istem arguman olarak guvenli
            list(argumanlar),
            capture_output=True,
            text=True,
            timeout=self.zaman_asimi,
            cwd=str(klasor),
            env=self.ortam(),
            **sessiz_calistir_ayarlari(),
        )

    def ozetle(self, transkript: Path, model: str, istem: str) -> OzetCikti:
        bulunan, denenen = copilot_coz(self.yol, self.komut)
        if bulunan is None:
            self.son_yol = ""
            return OzetCikti(kod=127, metin="", hata=bulunamadi_mesaji(denenen))
        self.son_yol = str(bulunan)
        log.info("Copilot CLI bulundu: %s", bulunan)
        klasor = transkript.parent
        hedef = cikti_yolu(transkript)
        # Onceki kosudan kalan dosya TAZE sanilmasin: once sil, sonra calistir.
        dosya_sil(hedef)
        argumanlar, istem_dosyasi = komut_kur(bulunan, model, istem, klasor)
        try:
            sonuc = self.calistir(argumanlar, klasor)
        except FileNotFoundError:
            return OzetCikti(kod=127, metin="", hata=bulunamadi_mesaji(denenen))
        except subprocess.TimeoutExpired:
            return OzetCikti(
                kod=124, metin="", hata=f"Copilot {self.zaman_asimi} sn'de bitmedi."
            )
        except OSError as hata:  # pragma: no cover - isletim sistemi hatasi
            return OzetCikti(kod=1, metin="", hata=str(hata))
        finally:
            if istem_dosyasi is not None:
                dosya_sil(istem_dosyasi)
        # Birincil yol: modelin yazdigi dosya. Okunduktan sonra geride kalmaz.
        yazilan = dosya_oku(hedef)
        dosya_sil(hedef)
        return OzetCikti(
            kod=int(sonuc.returncode or 0),
            metin=ansi_temizle(str(sonuc.stdout or "")),
            hata=ansi_temizle(str(sonuc.stderr or "")),
            dosya=yazilan,
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
    """Modelleri sirayla dener; ilk gecerli JSON kazanir.

    JSON once modelin YAZDIGI dosyadan, o olmazsa stdout+stderr'den cekilir;
    ikisi de vermezse hata metnine ham ciktinin kuyrugu konur ve loga daha
    uzun bir kuyruk yazilir. Cikis kodu sifir olmasa bile gecerli JSON
    geldiyse is bitmistir: Copilot kimi zaman cevabi yazip huysuz cikiyor.
    """
    denenen: list[str] = []
    reddedilenler: list[str] = []
    son_hata = "Özetleyici hiç çalıştırılamadı."
    for model in modeller:
        denenen.append(model)
        cikti = ozetleyici.ozetle(transkript, model, istem)
        veri = veriyi_cek(cikti)
        if veri is not None:
            return OzetSonucu(veri=veri, model=model, denenen=tuple(denenen))
        if reddedildi_mi(cikti):
            reddedilenler.append(model)
            son_hata = (cikti.hata or cikti.metin or "model reddedildi").strip()[:500]
            log.info("Özet modeli reddedildi: %s", model)
            continue
        son_hata = json_yok_mesaji(cikti)
        teshis_logla(model, cikti)
    if reddedilenler:
        # Tek tek model hatalari yerine tek satirlik ozet: kullanici hangi
        # modellerin hesabinda kapali oldugunu bir bakista gorur.
        son_hata = reddedilenler_mesaji(reddedilenler)
    return OzetSonucu(veri={}, denenen=tuple(denenen), hata=son_hata)


# --- JSON cekme ---------------------------------------------------------

_KOD_BLOGU = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def veriyi_cek(cikti: Any) -> dict[str, Any] | None:
    """Ozetleyicinin cevabindan JSON: once dosya, sonra stdout+stderr.

    Dosya birincil yoldur (temiz UTF-8). Model dosyayi yazamadiysa ama
    cevabi ekrana bastiysa not yine de kurtarilir.
    """
    veri = ayristir(str(getattr(cikti, "dosya", "") or ""))
    if veri is not None:
        return veri
    return ayristir(ham_cikti(cikti))


def ayristir(metin: str) -> dict[str, Any] | None:
    """Serbest metnin icinden JSON nesnesini ceker.

    Once ANSI temizlenir (Copilot renk basiyor), sonra sirayla metnin kendisi,
    kod bloklari ve dengeli suslu parantez kumeleri denenir. Model "Işte not:"
    gibi bir giris cumlesi yazsa ya da ilerleme satirlarinin arasina suslu
    parantezli bir sey dusse bile not kaybolmasin.
    """
    ham = ansi_temizle(metin).strip()
    if not ham:
        return None
    for aday in _adaylar(ham):
        try:
            veri = json.loads(aday)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(veri, dict):
            return veri
    return None


def _adaylar(metin: str) -> list[str]:
    adaylar = [metin]
    adaylar.extend(_KOD_BLOGU.findall(metin))
    # Dengeli kumeler UZUNDAN kisaya: ilerleme satirlarindaki kucuk `{...}`
    # parcacigi asil nesnenin onune gecmesin.
    adaylar.extend(sorted(_dengeli_nesneler(metin), key=len, reverse=True))
    return adaylar


def _dengeli_nesneler(metin: str) -> list[str]:
    """Metindeki BUTUN en dis dengeli `{...}` kumeleri (tirnaklara dikkat)."""
    bulunan: list[str] = []
    derinlik = 0
    basla = -1
    tirnak = False
    kacis = False
    for sira, harf in enumerate(metin):
        if kacis:
            kacis = False
            continue
        if tirnak:
            if harf == "\\":
                kacis = True
            elif harf == '"':
                tirnak = False
            continue
        if harf == '"':
            # Tirnak yalnizca bir nesnenin icindeyken metin baslatir.
            tirnak = derinlik > 0
            continue
        if harf == "{":
            if derinlik == 0:
                basla = sira
            derinlik += 1
        elif harf == "}" and derinlik > 0:
            derinlik -= 1
            if derinlik == 0:
                bulunan.append(metin[basla : sira + 1])
    return bulunan


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

SINAMA_DOSYASI = "copilot-sinama.txt"


def sinama_istemi(cikti: Path) -> str:
    """Sinama istemi: ozet yolunun AYNISI, yalnizca kucucuk bir JSON.

    Ozet adimi cevabi dosyadan aliyor; sinama da ayni yoldan gecmezse
    "çalışıyor" der ama gercek ozet yine patlar.
    """
    return (
        'Tek bir iş yap: şu dosyaya {"hazir": true} içeriğini yaz: '
        f"{cikti} — başka hiçbir şey yapma, açıklama yazma."
    )


# Hata metni ekranda gosterilir: uzun ciktinin kuyrugu isimize yaramaz.
HATA_SINIRI = 400
# `holocron.log` daha comert: teshis icin ciktinin daha uzun bir kuyrugu.
LOG_SINIRI = 2000

# Ciktidan temizlenecek izler: anahtar/parola benzeri her sey ekrana cikmasin.
_SIR_IZLERI = re.compile(
    r"(?i)\b(?:gh[posur]_[A-Za-z0-9]{6,}|bearer\s+\S+|api[_-]?key\s*[:=]\s*\S+"
    r"|token\s*[:=]\s*\S+|password\s*[:=]\s*\S+)"
)


def _maskele(metin: str) -> str:
    """ANSI atilmis, tek satira indirilmis, sirlari gizlenmis metin."""
    ham = ansi_temizle(metin).strip()
    if not ham:
        return ""
    return " ".join(_SIR_IZLERI.sub("[gizlendi]", ham).split())


def temizle(metin: str, sinir: int = HATA_SINIRI) -> str:
    """Ekrana cikacak hata metninin BASI: uzun ciktinin gerisi kirpilir."""
    return _maskele(metin)[:sinir]


def son_karakterler(metin: str, sinir: int = HATA_SINIRI) -> str:
    """Metnin SON `sinir` karakteri, maskelenmis ve tek satir.

    Kirpma maskelemeden SONRA yapilir: once kirpilirsa kullanici ciktinin
    basini gorur, oysa hatanin sebebi hep sondadir.
    """
    return _maskele(metin)[-sinir:] if sinir > 0 else ""


def kuyruk(cikti: Any, sinir: int = HATA_SINIRI) -> str:
    """Ozetleyici cevabinin (stdout+stderr) son `sinir` karakteri."""
    return son_karakterler(ham_cikti(cikti), sinir)


def json_yok_mesaji(cikti: Any) -> str:
    """Ekranda gorunecek hata: once izin reddi, sonra ham ciktinin kuyrugu.

    Sahadaki "Özet alınamadı: Model JSON döndürmedi." tek basina kullaniciya
    hicbir sey soylemiyordu; artik Copilot'un son sozleri de yaninda.
    """
    izin = izin_reddi(ham_cikti(cikti))
    if izin:
        return izin
    son = kuyruk(cikti)
    if not son:
        return "Model JSON döndürmedi. Copilot hiçbir çıktı vermedi, holocron.log'a bakın."
    return f"Model JSON döndürmedi. Ham çıktı (son {HATA_SINIRI} karakter): {son}"


def teshis_logla(model: str, cikti: Any) -> None:
    """JSON cikmadiginda `holocron.log`a uzun kuyruk yazar (maskelenmis)."""
    log.warning(
        "Özet JSON'u çıkmadı (model %s, çıkış kodu %s). "
        "stdout son %s karakter: %s | stderr son %s karakter: %s",
        model,
        getattr(cikti, "kod", "?"),
        LOG_SINIRI,
        son_karakterler(getattr(cikti, "metin", ""), LOG_SINIRI) or "(boş)",
        LOG_SINIRI,
        son_karakterler(getattr(cikti, "hata", ""), LOG_SINIRI) or "(boş)",
    )


def sina(ozetleyici: Any, modeller: Sequence[str], klasor: Path) -> dict[str, Any]:
    """Kisa bir istek atar: Copilot CLI ve vekil ayari gercekten calisiyor mu?

    Yedek sira burada da isler: ilk model reddederse sradaki denenir ve
    calisan model "son calisan" olarak geri bildirilir.

    Sinama ozet adiminin AYNI yolunu yurur: model kucuk bir JSON'u
    (`{"hazir": true}`) `ozet.json`a yazar, biz oradan okuruz. Boylece
    "çalışıyor" yazisi dosyaya yazma izninin de verildigini kanitlar.
    """
    import time

    klasor.mkdir(parents=True, exist_ok=True)
    dosya = klasor / SINAMA_DOSYASI
    dosya.write_text("Bu bir bağlantı sınamasıdır.\n", encoding="utf-8")
    hedef = cikti_yolu(dosya)
    istem = sinama_istemi(hedef)
    denenen: list[str] = []
    reddedilenler: list[str] = []
    son_hata = "Copilot CLI hiç çalıştırılamadı."
    try:
        for model in modeller:
            denenen.append(model)
            basladi = time.monotonic()
            cikti = ozetleyici.ozetle(dosya, model, istem)
            gecen = time.monotonic() - basladi
            if veriyi_cek(cikti) is None:
                if reddedildi_mi(cikti):
                    reddedilenler.append(model)
                    son_hata = temizle(cikti.hata or cikti.metin or "model reddedildi")
                    continue
                son_hata = json_yok_mesaji(cikti)
                teshis_logla(model, cikti)
                continue
            # Bulunan tam yol ekranda durur: kullanici hangi Copilot'un
            # calistigini gorur, ayara yazacagi degeri de oradan kopyalar.
            bulunan_yol = str(getattr(ozetleyici, "son_yol", "") or "")
            parcalar = ["çalışıyor"]
            if bulunan_yol:
                parcalar.append(bulunan_yol)
            parcalar.extend([f"model {model}", f"{gecen:.1f} sn"])
            return {
                "calisiyor": True,
                "model": model,
                "yol": bulunan_yol,
                "sn": round(gecen, 1),
                "denenen": denenen,
                "mesaj": " · ".join(parcalar),
            }
    finally:
        dosya_sil(dosya)
        dosya_sil(hedef)
    if reddedilenler:
        son_hata = temizle(reddedilenler_mesaji(reddedilenler))
    return {
        "calisiyor": False,
        "model": "",
        "yol": str(getattr(ozetleyici, "son_yol", "") or ""),
        "sn": 0.0,
        "denenen": denenen,
        "mesaj": son_hata or "Copilot CLI yanıt vermedi.",
    }


def default_ozetleyici(
    proxy: str = "",
    jira_base_url: str = "",
    yol: str = "",
    zaman_asimi: int = ZAMAN_ASIMI,
) -> CopilotOzetleyici:
    return CopilotOzetleyici(
        proxy=proxy, jira_base_url=jira_base_url, yol=yol, zaman_asimi=zaman_asimi
    )
