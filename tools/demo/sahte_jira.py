"""Sahte Jira Data Center sunucusu (stdlib `http.server`).

Holocron'un gercekten kullandigi uclari karsilar:

  GET  /rest/api/2/myself
  GET  /rest/api/2/serverInfo
  GET  /rest/api/2/field
  POST /rest/api/2/search          (JQL, startAt/maxResults/fields)
  GET  /rest/api/2/search          (ayni is, sorgu dizesiyle)
  GET  /rest/api/2/issue/{key}
  GET  /rest/api/2/issue/{key}/changelog

Kimlik: `Authorization: Bearer <PAT>` (Basic de kabul edilir). Baslik hic
yoksa 401 doner, cunku Holocron'un "Baglantiyi sina" dugmesi asil bunu
sinar.

JQL destegi Holocron'un urettigi alt kumedir: `project`, `status`,
`statusCategory`, `assignee`, `reporter`, `priority`, `issuetype`, `labels`,
`key`, `summary`, `text`, ozel alanlar; `= != > >= < <= ~ in "not in" is`
islecleri; `currentUser()`, `EMPTY`, `-30d` gibi goreli tarihler; `AND`/`OR`
ve `ORDER BY`. Parantezli mantik gruplari desteklenmez (deger listeleri
disinda parantez beklenmez).
"""

from __future__ import annotations

import json
import random
import re
import threading
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Iterable
from urllib.parse import parse_qs, urlparse

from . import veri

# Kayitlarin "canli" gorunmesi icin iki canlandirma arasinda gecmesi gereken
# en kisa sure. Tek "Guncelle" birkac arama istegi atiyor; soguma suresi
# olmazsa her filo ayri ayri kayit oynatir, akis gurultuye doner.
CANLANDIRMA_SOGUMASI = 8.0

# Her canlandirmada kac kaydin durumu/damgasi oynar.
CANLANDIRMA_ADEDI = 4

SUNUCU_ADI = "Demo Jira (sahte)"
SUNUCU_SURUMU = "9.12.0"


class JqlHatasi(Exception):
    """JQL ayristirilamadi ya da bilinmeyen anahtar istendi (HTTP 400)."""


# --- tokenlestirme ------------------------------------------------------

_TOKEN = re.compile(
    r"""
    \s*(?:
        (?P<tirnak>"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*')
      | (?P<islec>!=|>=|<=|!~|=|>|<|~)
      | (?P<parantez>[(),])
      | (?P<kelime>[^\s(),=!<>~]+)
    )
    """,
    re.VERBOSE,
)


def tokenlestir(metin: str) -> list[tuple[str, str]]:
    """(tur, deger) listesi. Tur: tirnak / islec / parantez / kelime."""
    tokenlar: list[tuple[str, str]] = []
    konum = 0
    uzunluk = len(metin)
    while konum < uzunluk:
        eslesme = _TOKEN.match(metin, konum)
        if eslesme is None:
            kalan = metin[konum:].strip()
            if not kalan:
                break
            raise JqlHatasi(f"JQL çözümlenemedi: '{kalan[:40]}'")
        konum = eslesme.end()
        for tur in ("tirnak", "islec", "parantez", "kelime"):
            deger = eslesme.group(tur)
            if deger is None:
                continue
            if tur == "tirnak":
                tokenlar.append(("deger", _tirnak_coz(deger)))
            else:
                tokenlar.append((tur, deger))
            break
    return tokenlar


def _tirnak_coz(ham: str) -> str:
    govde = ham[1:-1]
    return govde.replace('\\"', '"').replace("\\'", "'").replace("\\\\", "\\")


# --- ayristirma ---------------------------------------------------------


class Kosul:
    """Tek bir JQL kosulu: `alan islec deger(ler)`."""

    def __init__(self, alan: str, islec: str, degerler: list[str]) -> None:
        self.alan = alan
        self.islec = islec
        self.degerler = degerler

    def __repr__(self) -> str:  # pragma: no cover - teshis kolayligi
        return f"Kosul({self.alan!r}, {self.islec!r}, {self.degerler!r})"


class Sorgu:
    """Ayristirilmis JQL: VEYA ile ayrilmis VE gruplari + siralama."""

    def __init__(
        self,
        gruplar: list[list[Kosul]],
        siralama: list[tuple[str, str]],
    ) -> None:
        self.gruplar = gruplar
        self.siralama = siralama

    @property
    def kosullar(self) -> list[Kosul]:
        """Butun kosullar duzlestirilmis halde (sinama kolayligi)."""
        return [kosul for grup in self.gruplar for kosul in grup]


_ISLECLER = {"=", "!=", ">=", "<=", ">", "<", "~", "!~"}
_KELIME_ISLECLERI = {"in", "not", "is", "was"}


def ayristir(jql: str) -> Sorgu:
    """JQL metnini `Sorgu` nesnesine cevirir."""
    tokenlar = tokenlestir(str(jql or ""))
    tokenlar, siralama = _siralamayi_ayir(tokenlar)

    gruplar: list[list[Kosul]] = [[]]
    sira = 0
    while sira < len(tokenlar):
        tur, deger = tokenlar[sira]
        if tur == "kelime" and deger.lower() == "and":
            sira += 1
            continue
        if tur == "kelime" and deger.lower() == "or":
            gruplar.append([])
            sira += 1
            continue
        kosul, sira = _kosul_oku(tokenlar, sira)
        gruplar[-1].append(kosul)

    return Sorgu([grup for grup in gruplar if grup] or [[]], siralama)


def _siralamayi_ayir(
    tokenlar: list[tuple[str, str]],
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    for sira in range(len(tokenlar) - 1):
        birinci = tokenlar[sira]
        ikinci = tokenlar[sira + 1]
        if (
            birinci[0] == "kelime"
            and birinci[1].lower() == "order"
            and ikinci[0] == "kelime"
            and ikinci[1].lower() == "by"
        ):
            return tokenlar[:sira], _siralama_oku(tokenlar[sira + 2 :])
    return tokenlar, []


def _siralama_oku(tokenlar: list[tuple[str, str]]) -> list[tuple[str, str]]:
    siralama: list[tuple[str, str]] = []
    sira = 0
    while sira < len(tokenlar):
        tur, deger = tokenlar[sira]
        if tur == "parantez" and deger == ",":
            sira += 1
            continue
        if tur not in ("kelime", "deger"):
            sira += 1
            continue
        alan = deger
        yon = "asc"
        if sira + 1 < len(tokenlar) and tokenlar[sira + 1][0] == "kelime":
            sonraki = tokenlar[sira + 1][1].lower()
            if sonraki in ("asc", "desc"):
                yon = sonraki
                sira += 1
        siralama.append((alan, yon))
        sira += 1
    return siralama


def _kosul_oku(tokenlar: list[tuple[str, str]], sira: int) -> tuple[Kosul, int]:
    tur, alan = tokenlar[sira]
    if tur not in ("kelime", "deger"):
        raise JqlHatasi(f"Alan adı bekleniyordu: '{alan}'")
    sira += 1
    if sira >= len(tokenlar):
        raise JqlHatasi(f"'{alan}' için işleç eksik.")

    tur, ham = tokenlar[sira]
    if tur == "islec" and ham in _ISLECLER:
        islec = ham
        sira += 1
    elif tur == "kelime" and ham.lower() in _KELIME_ISLECLERI:
        islec = ham.lower()
        sira += 1
        if islec in ("not", "is") and sira < len(tokenlar) and tokenlar[sira][0] == "kelime":
            sonraki = tokenlar[sira][1].lower()
            if islec == "not" and sonraki == "in":
                islec = "not in"
                sira += 1
            elif islec == "is" and sonraki == "not":
                islec = "is not"
                sira += 1
    else:
        raise JqlHatasi(f"'{alan}' için bilinmeyen işleç: '{ham}'")

    degerler, sira = _degerleri_oku(tokenlar, sira)
    return Kosul(alan, islec, degerler), sira


def _degerleri_oku(
    tokenlar: list[tuple[str, str]], sira: int
) -> tuple[list[str], int]:
    if sira >= len(tokenlar):
        raise JqlHatasi("Değer bekleniyordu.")
    tur, ham = tokenlar[sira]
    if tur == "parantez" and ham == "(":
        sira += 1
        degerler: list[str] = []
        while sira < len(tokenlar):
            tur, ham = tokenlar[sira]
            if tur == "parantez" and ham == ")":
                return degerler, sira + 1
            if tur == "parantez" and ham == ",":
                sira += 1
                continue
            deger, sira = _tek_deger_oku(tokenlar, sira)
            degerler.append(deger)
        raise JqlHatasi("Kapanmayan parantez.")
    deger, sira = _tek_deger_oku(tokenlar, sira)
    return [deger], sira


def _tek_deger_oku(tokenlar: list[tuple[str, str]], sira: int) -> tuple[str, int]:
    tur, ham = tokenlar[sira]
    if tur not in ("kelime", "deger"):
        raise JqlHatasi(f"Değer bekleniyordu: '{ham}'")
    sira += 1
    # `currentUser()` gibi islev cagrilari: bos parantez cifti yutulur.
    if (
        sira + 1 < len(tokenlar)
        and tokenlar[sira] == ("parantez", "(")
        and tokenlar[sira + 1] == ("parantez", ")")
    ):
        return f"{ham}()", sira + 2
    return ham, sira


# --- degerlendirme ------------------------------------------------------

# JQL alan adi -> kayit icinden okuma yolu.
_KOK_ALANLAR = {"key", "issuekey", "id"}

_TAKMA_ADLAR = {
    "type": "issuetype",
    "issue": "key",
    "issuekey": "key",
    "sprint": "customfield_10001",
    "takım": "customfield_10002",
    "takim": "customfield_10002",
}

_KISI_ALANLARI = {"assignee", "reporter", "creator"}

_TARIH_ALANLARI = {"created", "updated", "duedate", "resolutiondate"}

_GORELI = re.compile(r"^(?P<isaret>[-+])(?P<sayi>\d+)(?P<birim>[mhdw])$", re.IGNORECASE)


def _kucult(metin: Any) -> str:
    """Turkce I/i tuzagini eriten kucuk harf."""
    return (
        str(metin or "")
        .replace("İ", "i")
        .replace("I", "ı")
        .lower()
    )


def alan_degeri(kayit: dict[str, Any], alan: str) -> Any:
    """Kayittan JQL alanina karsilik gelen ham degeri okur."""
    ad = _TAKMA_ADLAR.get(_kucult(alan), _kucult(alan))
    if ad in _KOK_ALANLAR:
        return kayit.get("key" if ad != "id" else "id")
    alanlar = kayit.get("fields") or {}
    if ad == "statuscategory":
        durum = alanlar.get("status") or {}
        return (durum.get("statusCategory") or {}).get("key")
    # Ozel alan kimlikleri kucultulunce bozulmasin.
    if ad.startswith("customfield_"):
        return alanlar.get(ad)
    for anahtar in alanlar:
        if _kucult(anahtar) == ad:
            return alanlar[anahtar]
    return None


def _metinler(deger: Any, alan: str) -> list[str]:
    """Bir alan degerinin eslestirilebilir metin karsiliklari."""
    if deger is None:
        return []
    if isinstance(deger, list):
        return [parca for oge in deger for parca in _metinler(oge, alan)]
    if isinstance(deger, dict):
        parcalar = [
            deger.get("name"),
            deger.get("key"),
            deger.get("displayName"),
            deger.get("emailAddress"),
            deger.get("value"),
        ]
        return [str(parca) for parca in parcalar if parca]
    return [str(deger)]


def _an(metin: Any) -> datetime | None:
    ham = str(metin or "").strip()
    if not ham:
        return None
    ham = ham.replace("Z", "+0000")
    for bicim in (
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d",
    ):
        try:
            cozulen = datetime.strptime(ham, bicim)
        except ValueError:
            continue
        if cozulen.tzinfo is None:
            cozulen = cozulen.replace(tzinfo=timezone.utc)
        return cozulen
    return None


def tarih_coz(ham: str, simdi: datetime) -> datetime | None:
    """`-30d`, `now()`, `startOfWeek()` ve duz tarihleri cozer."""
    metin = str(ham or "").strip()
    eslesme = _GORELI.match(metin)
    if eslesme:
        sayi = int(eslesme.group("sayi"))
        birim = eslesme.group("birim").lower()
        sure = {
            "m": timedelta(minutes=sayi),
            "h": timedelta(hours=sayi),
            "d": timedelta(days=sayi),
            "w": timedelta(weeks=sayi),
        }[birim]
        return simdi - sure if eslesme.group("isaret") == "-" else simdi + sure
    kucuk = metin.lower()
    if kucuk in ("now()", "now"):
        return simdi
    if kucuk.startswith("startofday"):
        return simdi.replace(hour=0, minute=0, second=0, microsecond=0)
    if kucuk.startswith("startofweek"):
        gun = simdi.replace(hour=0, minute=0, second=0, microsecond=0)
        return gun - timedelta(days=gun.weekday())
    if kucuk.startswith("startofmonth"):
        return simdi.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return _an(metin)


def _beklenen(deger: str, kullanici: str) -> str:
    return kullanici if deger.lower() in ("currentuser()", "currentuser") else deger


def kosul_uyar(
    kosul: Kosul, kayit: dict[str, Any], kullanici: str, simdi: datetime
) -> bool:
    """Tek kosul kayda uyuyor mu?"""
    deger = alan_degeri(kayit, kosul.alan)
    islec = kosul.islec

    if islec in ("is", "is not"):
        bos = deger in (None, "", [], {})
        return bos if islec == "is" else not bos

    beklenenler = [_beklenen(ham, kullanici) for ham in kosul.degerler]

    if _kucult(kosul.alan) in _TARIH_ALANLARI and islec in (">", ">=", "<", "<="):
        solda = _an(deger)
        sagda = tarih_coz(beklenenler[0], simdi) if beklenenler else None
        if solda is None or sagda is None:
            return False
        if islec == ">":
            return solda > sagda
        if islec == ">=":
            return solda >= sagda
        if islec == "<":
            return solda < sagda
        return solda <= sagda

    mevcut = {_kucult(parca) for parca in _metinler(deger, kosul.alan)}
    aranan = {_kucult(parca) for parca in beklenenler}

    if islec in ("=", "in"):
        return bool(mevcut & aranan)
    if islec in ("!=", "not in"):
        return not (mevcut & aranan)
    if islec == "~":
        return any(any(hedef in parca for parca in mevcut) for hedef in aranan)
    if islec == "!~":
        return not any(any(hedef in parca for parca in mevcut) for hedef in aranan)
    raise JqlHatasi(f"Desteklenmeyen işleç: '{islec}'")


def sorgu_uyar(
    sorgu: Sorgu, kayit: dict[str, Any], kullanici: str, simdi: datetime
) -> bool:
    if not sorgu.gruplar or not any(sorgu.gruplar):
        return True
    return any(
        all(kosul_uyar(kosul, kayit, kullanici, simdi) for kosul in grup)
        for grup in sorgu.gruplar
        if grup
    )


def _sirala_anahtari(kayit: dict[str, Any], alan: str) -> Any:
    deger = alan_degeri(kayit, alan)
    if _kucult(alan) in _TARIH_ALANLARI:
        an = _an(deger)
        return an.timestamp() if an else 0.0
    if _kucult(alan) in ("priority",):
        # Oncelik kimligi kucukken daha acil; sayisal sirala.
        try:
            return int((deger or {}).get("id", 99))
        except (TypeError, ValueError, AttributeError):
            return 99
    metinler = _metinler(deger, alan)
    return _kucult(metinler[0]) if metinler else ""


def sirala(kayitlar: list[dict[str, Any]], siralama: list[tuple[str, str]]) -> list[dict[str, Any]]:
    sonuc = list(kayitlar)
    # Cok alanli siralama: en dustan basa dogru kararli siralama.
    for alan, yon in reversed(siralama):
        sonuc.sort(key=lambda kayit: _sirala_anahtari(kayit, alan), reverse=(yon == "desc"))
    return sonuc


def alanlari_suz(kayit: dict[str, Any], istenen: Iterable[str] | None) -> dict[str, Any]:
    """`fields` parametresi: `*navigable` / `*all` hepsini verir."""
    secili = [str(ad) for ad in (istenen or []) if str(ad).strip()]
    if not secili or any(ad in ("*all", "*navigable") for ad in secili):
        return kayit
    alanlar = kayit.get("fields") or {}
    return {
        "id": kayit.get("id"),
        "key": kayit.get("key"),
        "self": kayit.get("self"),
        "fields": {ad: alanlar.get(ad) for ad in secili if ad in alanlar},
    }


# --- sunucu durumu ------------------------------------------------------


class SahteJira:
    """Bellek ici kayit havuzu ve arama mantigi."""

    def __init__(
        self,
        kayitlar: list[dict[str, Any]] | None = None,
        kullanici: dict[str, str] | None = None,
        saat: Callable[[], datetime] | None = None,
    ) -> None:
        self.kayitlar = list(kayitlar if kayitlar is not None else veri.kayitlar_uret())
        self.kullanici = kullanici or veri.BEN
        self._saat = saat or (lambda: datetime.now(timezone.utc))
        self._kilit = threading.RLock()
        self._son_canlandirma = 0.0
        self._rastgele = random.Random(veri.TOHUM + 7)
        # Kac kez kayit oynatildi (sinama ve teshis icin).
        self.canlandirma_sayisi = 0

    # --- sorgular -------------------------------------------------------

    def anahtarlar(self) -> set[str]:
        return {str(kayit["key"]).upper() for kayit in self.kayitlar}

    def kayit(self, anahtar: str) -> dict[str, Any] | None:
        aranan = str(anahtar or "").upper()
        for kayit in self.kayitlar:
            if str(kayit["key"]).upper() == aranan:
                return kayit
        return None

    def ara(
        self,
        jql: str,
        start_at: int = 0,
        max_results: int = 50,
        fields: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        """Jira Server/DC `/search` cevabi: startAt / maxResults / total."""
        sorgu = ayristir(jql)
        self._bilinmeyen_anahtar_denetle(sorgu)
        simdi = self._saat()
        with self._kilit:
            eslesen = [
                kayit
                for kayit in self.kayitlar
                if sorgu_uyar(sorgu, kayit, self.kullanici["kullanici"], simdi)
            ]
        eslesen = sirala(eslesen, sorgu.siralama)
        pencere = eslesen[start_at : start_at + max_results]
        return {
            "startAt": start_at,
            "maxResults": max_results,
            "total": len(eslesen),
            "issues": [alanlari_suz(kayit, fields) for kayit in pencere],
        }

    def _bilinmeyen_anahtar_denetle(self, sorgu: Sorgu) -> None:
        """Gercek Jira gibi: olmayan bir anahtar tum paketi 400 yapar.

        Holocron bunu bekliyor; paketi ikiye bolup saglam anahtarlari
        kurtarma yolu ancak boyle sinaniyor.
        """
        bilinen = self.anahtarlar()
        for kosul in sorgu.kosullar:
            if _TAKMA_ADLAR.get(_kucult(kosul.alan), _kucult(kosul.alan)) != "key":
                continue
            if kosul.islec not in ("=", "in"):
                continue
            eksik = [
                deger for deger in kosul.degerler if str(deger).upper() not in bilinen
            ]
            if eksik:
                raise JqlHatasi(
                    "Şu anahtarlarla kayıt bulunamadı: " + ", ".join(sorted(eksik))
                )

    # --- canlandirma ----------------------------------------------------

    def canlandir(self, zorla: bool = False) -> list[str]:
        """Birkac kaydin durumunu ilerletir, damgasini bugune ceker.

        "Guncelle" dugmesi her basildiginda akis canli gorunsun diye. Tek
        guncelleme birden cok arama istegi attigindan soguma suresi var.
        """
        simdi = time.monotonic()
        with self._kilit:
            if not zorla and simdi - self._son_canlandirma < CANLANDIRMA_SOGUMASI:
                return []
            self._son_canlandirma = simdi
            self.canlandirma_sayisi += 1
            secilen = self._rastgele.sample(
                self.kayitlar, k=min(CANLANDIRMA_ADEDI, len(self.kayitlar))
            )
            degisen: list[str] = []
            for kayit in secilen:
                degisen.append(str(kayit["key"]))
                self._kaydi_oynat(kayit)
            return degisen

    def _kaydi_oynat(self, kayit: dict[str, Any]) -> None:
        alanlar = kayit["fields"]
        simdi = self._saat()
        alanlar["updated"] = veri.zaman(simdi)
        durum_adlari = [durum["name"] for durum in veri.DURUMLAR]
        mevcut = (alanlar.get("status") or {}).get("name")
        sira = durum_adlari.index(mevcut) if mevcut in durum_adlari else 0
        # Cogunlukla bir adim ileri, bazen geri: pano hep ayni yone akmasin.
        yeni_sira = min(sira + 1, len(veri.DURUMLAR) - 1)
        if self._rastgele.random() < 0.25:
            yeni_sira = max(sira - 1, 0)
        durum = veri.DURUMLAR[yeni_sira]
        alanlar["status"] = {
            "id": durum["id"],
            "name": durum["name"],
            "statusCategory": {
                "key": durum["kategori"],
                "name": durum["kategori_adi"],
                "colorName": veri._kategori_rengi(durum["kategori"]),
            },
        }
        if durum["kategori"] == "done":
            alanlar["resolution"] = {"id": "1", "name": "Tamamlandı"}
            alanlar["resolutiondate"] = veri.zaman(simdi)
        else:
            alanlar["resolution"] = None
            alanlar["resolutiondate"] = None


# --- HTTP katmani -------------------------------------------------------


def _json_cevap(govde: Any) -> bytes:
    return json.dumps(govde, ensure_ascii=False).encode("utf-8")


class _Isleyici(BaseHTTPRequestHandler):
    """Tek sahte Jira ornegine baglanan istek isleyicisi."""

    server_version = "SahteJira/1.0"
    # Sahte sunucu, kendi erisim gunlugunu basmasin: demo konsolu temiz kalsin.
    sessiz = True
    durum: SahteJira

    def log_message(self, bicim: str, *argumanlar: Any) -> None:  # noqa: A003
        if not self.sessiz:  # pragma: no cover - teshis kipi
            super().log_message(bicim, *argumanlar)

    # --- yardimcilar ----------------------------------------------------

    def _gonder(self, kod: int, govde: Any) -> None:
        ham = _json_cevap(govde)
        self.send_response(kod)
        self.send_header("Content-Type", "application/json;charset=UTF-8")
        self.send_header("Content-Length", str(len(ham)))
        self.end_headers()
        self.wfile.write(ham)

    def _hata(self, kod: int, mesaj: str) -> None:
        self._gonder(kod, {"errorMessages": [mesaj], "errors": {}})

    def _yetkili_mi(self) -> bool:
        baslik = self.headers.get("Authorization", "")
        return baslik.startswith("Bearer ") or baslik.startswith("Basic ")

    def _govde(self) -> dict[str, Any]:
        uzunluk = int(self.headers.get("Content-Length") or 0)
        if uzunluk <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(uzunluk).decode("utf-8")) or {}
        except (ValueError, UnicodeDecodeError):
            return {}

    # --- yonlendirme ----------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler sozlesmesi
        parca = urlparse(self.path)
        yol = parca.path
        if yol == "/rest/api/2/serverInfo":
            # Sunucu bilgisi kimliksiz de donebilir; gercek Jira de boyle.
            self._gonder(
                200,
                {
                    "baseUrl": f"http://{self.headers.get('Host', '127.0.0.1')}",
                    "version": SUNUCU_SURUMU,
                    "versionNumbers": [9, 12, 0],
                    "deploymentType": "Server",
                    "serverTitle": SUNUCU_ADI,
                },
            )
            return
        if not self._yetkili_mi():
            self._hata(401, "Kimlik doğrulanamadı: Bearer anahtarı eksik.")
            return
        if yol == "/rest/api/2/myself":
            self._gonder(200, veri.kisi_alani(self.durum.kullanici))
            return
        if yol == "/rest/api/2/field":
            self._gonder(200, list(veri.ALAN_KATALOGU))
            return
        if yol == "/rest/api/2/search":
            sorgu = parse_qs(parca.query)
            self._arama(
                jql=(sorgu.get("jql") or [""])[0],
                start_at=int((sorgu.get("startAt") or ["0"])[0]),
                max_results=int((sorgu.get("maxResults") or ["50"])[0]),
                fields=(sorgu.get("fields") or [""])[0].split(",") if sorgu.get("fields") else None,
            )
            return
        eslesme = re.match(r"^/rest/api/2/issue/([A-Za-z][A-Za-z0-9_]*-\d+)(/changelog)?$", yol)
        if eslesme:
            kayit = self.durum.kayit(eslesme.group(1))
            if kayit is None:
                self._hata(404, f"Kayıt bulunamadı: {eslesme.group(1)}")
                return
            if eslesme.group(2):
                self._gonder(200, _changelog(kayit))
                return
            self._gonder(200, kayit)
            return
        self._hata(404, f"Bilinmeyen uç: {yol}")

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler sozlesmesi
        parca = urlparse(self.path)
        if not self._yetkili_mi():
            self._hata(401, "Kimlik doğrulanamadı: Bearer anahtarı eksik.")
            return
        if parca.path != "/rest/api/2/search":
            self._hata(404, f"Bilinmeyen uç: {parca.path}")
            return
        govde = self._govde()
        self._arama(
            jql=str(govde.get("jql") or ""),
            start_at=int(govde.get("startAt") or 0),
            max_results=int(govde.get("maxResults") or 50),
            fields=govde.get("fields"),
        )

    def _arama(
        self,
        jql: str,
        start_at: int,
        max_results: int,
        fields: Iterable[str] | None,
    ) -> None:
        # Her arama akisi biraz oynatir (sogumasi dolduysa).
        self.durum.canlandir()
        try:
            self._gonder(200, self.durum.ara(jql, start_at, max_results, fields))
        except JqlHatasi as hata:
            self._hata(400, str(hata))


def _changelog(kayit: dict[str, Any]) -> dict[str, Any]:
    """Tek girisli sahte gecmis: alan var olsun diye."""
    alanlar = kayit.get("fields") or {}
    return {
        "startAt": 0,
        "maxResults": 1,
        "total": 1,
        "values": [
            {
                "id": "1",
                "created": alanlar.get("updated"),
                "author": alanlar.get("reporter"),
                "items": [
                    {
                        "field": "status",
                        "fromString": "Açık",
                        "toString": (alanlar.get("status") or {}).get("name"),
                    }
                ],
            }
        ],
    }


def sunucu_kur(durum: SahteJira, port: int = 8090, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    """Sunucuyu kurar (henuz dinlemeye baslamaz: `serve_forever` cagiran isi)."""
    isleyici = type("_BagliIsleyici", (_Isleyici,), {"durum": durum})
    sunucu = ThreadingHTTPServer((host, port), isleyici)
    sunucu.daemon_threads = True
    return sunucu


def baslat(
    durum: SahteJira | None = None, port: int = 8090, host: str = "127.0.0.1"
) -> tuple[ThreadingHTTPServer, threading.Thread, SahteJira]:
    """Sahte Jira'yi ayri is parcaciginda baslatir."""
    havuz = durum or SahteJira()
    sunucu = sunucu_kur(havuz, port=port, host=host)
    is_parcacigi = threading.Thread(
        target=sunucu.serve_forever, name="sahte-jira", daemon=True
    )
    is_parcacigi.start()
    return sunucu, is_parcacigi, havuz
