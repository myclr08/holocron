"""Gorusme notlarinin saf is mantigi: ayarlar, esleme, ekran goruntusu.

Ne ses karti var burada ne alt surec: ayar okuma, asgari sure karari,
`teams_calls` ile katilimci eslemesi, arama ve arayuze giden sozlukler.
Windows'a hic bakmadan sinanabilir.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from .. import fields as field_utils
from ..teamscalls import intake as calls_intake
from . import depo
from .ozet import VARSAYILAN_MODELLER, modelleri_coz, varsayilan_sablon
from .source import (
    BOLUM_AKSIYON,
    BOLUM_ETIKETLERI,
    BOLUM_KARAR,
    BOLUM_OZET,
    BOLUM_SORU,
    DURUM_ATLANDI,
    DURUM_ETIKETLERI,
    DURUM_HAZIR,
    DURUM_KAYDEDILIYOR,
    DURUM_KUYRUKTA,
    ARA_DURUMLAR,
    KLASOR_BICIMI,
    calisma_koku,
)
from .yaziyadok import VARSAYILAN_MODEL as VARSAYILAN_WHISPER

# Asgari gorusme suresi: altindaki kayitlar islenmez, listede "atlandi" satiri.
VARSAYILAN_MIN_DAKIKA = 4.0
# Katilimci eslemesi: arama bu kadar kaydirmaya kadar ayni gorusme sayilir.
ESLEME_PAYI_DK = 3
# Sure yakinligi: iki sure arasindaki fark bu orani asarsa esleme kabul edilmez.
ESLEME_SURE_ORANI = 0.5

# Notun basligi henuz yoksa listede ne yazar.
BASLIKSIZ = "—"
KATILIMCI_BEKLENIYOR = "katılımcı bekleniyor"


@dataclass(frozen=True)
class Ayarlar:
    """Ayarlar ekranindan gelen her sey tek yerde."""

    takip: bool = False
    takip_acilista: bool = True
    min_dakika: float = VARSAYILAN_MIN_DAKIKA
    mikrofon: str = ""
    hoparlor: str = ""
    whisper_model: str = VARSAYILAN_WHISPER
    whisper_klasor: str = ""
    modeller: tuple[str, ...] = VARSAYILAN_MODELLER
    son_model: str = ""
    sablon: str = ""
    calisma_klasoru: str = ""
    transkripti_sakla: bool = False
    sesi_sakla: bool = False
    bildirim_baslangic: bool = True
    bildirim_hazir: bool = True
    isleme_gorusme_disinda: bool = True
    copilot_proxy: str = ""
    jira_base_url: str = ""

    @property
    def min_saniye(self) -> float:
        return max(0.0, float(self.min_dakika)) * 60.0

    def kok(self) -> Path:
        return calisma_koku(self.calisma_klasoru)

    def sablon_metni(self) -> str:
        return self.sablon.strip() or varsayilan_sablon()

    def model_sirasi(self) -> list[str]:
        return modelleri_coz(list(self.modeller), self.son_model)


def _bool(deger: Any, varsayilan: bool = False) -> bool:
    metin = str(deger if deger is not None else "").strip().lower()
    if metin == "":
        return varsayilan
    return metin in {"1", "true", "yes", "on", "evet"}


def _sayi(deger: Any, varsayilan: float) -> float:
    try:
        sonuc = float(str(deger).strip())
    except (TypeError, ValueError):
        return varsayilan
    return sonuc if sonuc > 0 else varsayilan


def load_config(settings: Any) -> Ayarlar:
    """Ayar deposundan `Ayarlar` uretir; bozuk deger varsayilana duser."""
    ham_modeller = settings.get("calls.ozet_modelleri", "") or ""
    return Ayarlar(
        takip=_bool(settings.get("calls.takip", "0")),
        takip_acilista=_bool(settings.get("calls.takip_acilista", "1"), True),
        min_dakika=_sayi(settings.get("calls.min_dakika", ""), VARSAYILAN_MIN_DAKIKA),
        mikrofon=str(settings.get("calls.mikrofon", "") or "").strip(),
        hoparlor=str(settings.get("calls.hoparlor", "") or "").strip(),
        whisper_model=str(settings.get("calls.whisper_model", "") or "").strip()
        or VARSAYILAN_WHISPER,
        whisper_klasor=str(settings.get("calls.whisper_klasor", "") or "").strip(),
        modeller=tuple(modelleri_coz(ham_modeller)),
        son_model=str(settings.get("calls.ozet_model_son", "") or "").strip(),
        sablon=str(settings.get("calls.ozet_sablon", "") or ""),
        calisma_klasoru=str(settings.get("calls.calisma_klasoru", "") or "").strip(),
        transkripti_sakla=_bool(settings.get("calls.transkripti_sakla", "0")),
        sesi_sakla=_bool(settings.get("calls.sesi_sakla", "0")),
        bildirim_baslangic=_bool(settings.get("calls.bildirim_baslangic", "1"), True),
        bildirim_hazir=_bool(settings.get("calls.bildirim_hazir", "1"), True),
        isleme_gorusme_disinda=_bool(settings.get("calls.isleme_gorusme_disinda", "1"), True),
        copilot_proxy=str(settings.get("calls.copilot_proxy", "") or "").strip(),
        jira_base_url=str(settings.get("jira.base_url", "") or "").strip(),
    )


def modelleri_yaz(modeller: Sequence[str]) -> str:
    """Ayara yazilan bicim: JSON listesi."""
    temiz = [str(ad).strip() for ad in modeller if str(ad).strip()]
    return json.dumps(temiz or list(VARSAYILAN_MODELLER), ensure_ascii=False)


# --- zaman ---------------------------------------------------------------


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(moment: datetime) -> str:
    return moment.isoformat().replace("+00:00", "Z")


def klasor_adi(baslangic: datetime) -> str:
    return baslangic.strftime(KLASOR_BICIMI)


def sure_metni(saniye: Any) -> str:
    """`27 dk`, `1 sa 5 dk`, `48 sn`."""
    toplam = int(saniye or 0)
    if toplam <= 0:
        return "—"
    if toplam < 60:
        return f"{toplam} sn"
    dakika = toplam // 60
    if dakika < 60:
        return f"{dakika} dk"
    return f"{dakika // 60} sa {dakika % 60} dk"


def uzun_mu(saniye: float, ayarlar: Ayarlar) -> bool:
    """Gorusme asgari sureyi gecti mi?"""
    return float(saniye or 0) >= ayarlar.min_saniye


# --- katilimci eslemesi ---------------------------------------------------


def eslesen_arama(
    aramalar: Iterable[dict[str, Any]], baslangic: str, sure_sn: int
) -> dict[str, Any] | None:
    """Notun zaman araligini `teams_calls` ile esler.

    Kural: baslangic +/- 3 dakika ve sure yakinligi. Birden fazla aday
    varsa baslangici en yakin olan kazanir; hicbiri tutmuyorsa `None`
    doner ve not "katilimci bekleniyor" ile acilir -- sonraki "Aramaları
    çek"te yeniden denenir.
    """
    hedef = calls_intake.parse_utc(baslangic)
    if hedef is None:
        return None
    pay = timedelta(minutes=ESLEME_PAYI_DK)
    en_iyi: tuple[float, dict[str, Any]] | None = None
    for arama in aramalar:
        baslar = calls_intake.parse_utc(arama.get("started_at"))
        if baslar is None:
            continue
        fark = abs((baslar - hedef).total_seconds())
        if fark > pay.total_seconds():
            continue
        arama_sn = int(arama.get("duration_ms") or 0) / 1000.0
        if not _sure_yakin(arama_sn, float(sure_sn or 0)):
            continue
        if en_iyi is None or fark < en_iyi[0]:
            en_iyi = (fark, arama)
    return en_iyi[1] if en_iyi is not None else None


def _sure_yakin(bir: float, iki: float) -> bool:
    """Iki sure birbirine yakin mi? (sifir sure her zaman kabul)"""
    if bir <= 0 or iki <= 0:
        return True
    buyuk = max(bir, iki)
    return abs(bir - iki) / buyuk <= ESLEME_SURE_ORANI


def katilimci_kayitlari(arama: dict[str, Any], me: str = "") -> list[dict[str, str]]:
    """Aramadaki kisiler (kendim haric), ad bilgisiyle.

    Katilimci listesi cogu kayitta yalnizca kimlik tasir; ad karsi taraf
    alanindan ve tarama sirasinda cozulmus adlardan tamamlanir, boylece
    cekmecede ham kimlik gorunmez.
    """
    adlar = calls_intake.names_from_rows([arama])
    karsi = str(arama.get("counterpart_id") or "")
    if karsi:
        adlar.setdefault(karsi, str(arama.get("counterpart_name") or ""))
    ciftler = calls_intake.other_pairs(arama, me)
    if ciftler:
        return [
            {"kimlik": cift["id"], "ad": cift["name"] or adlar.get(cift["id"], "")}
            for cift in ciftler
            if cift.get("id")
        ]
    if not karsi:
        return []
    return [{"kimlik": karsi, "ad": adlar.get(karsi, "")}]


def esle_ve_yaz(
    conn: sqlite3.Connection, not_id: int, aramalar: Sequence[dict[str, Any]], me: str = ""
) -> dict[str, Any] | None:
    """Notu bir aramaya baglar ve katilimcilarini yazar."""
    veri = depo.get_not(conn, not_id)
    if veri is None:
        return None
    arama = eslesen_arama(aramalar, veri["baslangic"], veri["sure_sn"])
    if arama is None:
        return None
    depo.not_guncelle(
        conn,
        not_id,
        call_id=str(arama.get("call_id") or ""),
        tur=str(arama.get("kind") or veri["tur"]),
    )
    depo.katilimcilari_yaz(conn, not_id, katilimci_kayitlari(arama, me))
    return arama


def eksikleri_esle(
    conn: sqlite3.Connection, aramalar: Sequence[dict[str, Any]], me: str = ""
) -> int:
    """Katilimcisi bulunamamis butun notlari yeniden dener (tarama sonrasi)."""
    sayac = 0
    for veri in depo.list_notlar(conn):
        if veri["call_id"]:
            continue
        if esle_ve_yaz(conn, veri["id"], aramalar, me) is not None:
            sayac += 1
    return sayac


# --- ekran goruntusu -----------------------------------------------------


def katilimci_etiketleri(kisiler: Sequence[dict[str, str]]) -> list[str]:
    """Ham kimlik EKRANA CIKMAZ: ad yoksa kimligin kuyrugu yazilir."""
    return [
        calls_intake.person_label(kisi.get("kimlik"), kisi.get("ad"))
        for kisi in kisiler
        if kisi.get("kimlik")
    ]


def kart(
    veri: dict[str, Any],
    kisiler: Sequence[dict[str, str]] = (),
    gorev_sayisi: int = 0,
    jira: str = "",
) -> dict[str, Any]:
    """Listedeki tek satirin arayuze giden hali."""
    etiketler = katilimci_etiketleri(kisiler)
    return {
        "id": veri["id"],
        "call_id": veri["call_id"],
        "baslangic": veri["baslangic"],
        "bitis": veri["bitis"],
        "sure_sn": veri["sure_sn"],
        "sure_text": sure_metni(veri["sure_sn"]),
        "tur": veri["tur"],
        "tur_label": calls_intake.KIND_LABELS.get(veri["tur"], ""),
        "baslik": veri["baslik"] or BASLIKSIZ,
        "durum": veri["durum"],
        "durum_label": DURUM_ETIKETLERI.get(veri["durum"], veri["durum"]),
        "isleniyor": veri["durum"] in ARA_DURUMLAR,
        "hata": veri["hata"],
        "model": veri["model"],
        "katilimcilar": etiketler,
        "katilimci_notu": "" if etiketler else KATILIMCI_BEKLENIYOR,
        "gorev_sayisi": int(gorev_sayisi or 0),
        "jira_key": jira,
    }


def bolum_gruplari(satirlar: Sequence[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Bolumleri turune gore ayirir (ekranda dort sabit kart)."""
    gruplar: dict[str, list[dict[str, Any]]] = {
        BOLUM_OZET: [],
        BOLUM_KARAR: [],
        BOLUM_AKSIYON: [],
        BOLUM_SORU: [],
    }
    for satir in sorted(satirlar, key=lambda item: (item.get("sira", 0), item.get("id", 0))):
        tur = str(satir.get("tur") or "")
        if tur in gruplar:
            gruplar[tur].append(satir)
    return gruplar


def detay(
    conn: sqlite3.Connection,
    not_id: int,
    gorevler: Sequence[dict[str, Any]] = (),
    transkript_var: bool = False,
) -> dict[str, Any]:
    """Not detayi: bolumler, katilimcilar, baglar, kaynak notu."""
    veri = depo.require_not(conn, not_id)
    kisiler = depo.katilimcilar(conn, not_id)
    satirlar = depo.bolumler(conn, not_id)
    gruplar = bolum_gruplari(satirlar)
    return {
        "not": kart(
            veri,
            kisiler,
            gorev_sayisi=len(gorevler),
            jira=depo.jira_key(conn, not_id),
        ),
        "bolumler": {tur: gruplar[tur] for tur in gruplar},
        "bolum_basliklari": dict(BOLUM_ETIKETLERI),
        "katilimcilar": [
            {"kimlik": kisi["kimlik"], "ad": calls_intake.person_label(kisi["kimlik"], kisi["ad"])}
            for kisi in kisiler
        ],
        "gorevler": list(gorevler),
        "transkript_var": bool(transkript_var),
        "kaynaklar": kaynak_notu(veri, transkript_var),
    }


def kaynak_notu(veri: dict[str, Any], transkript_var: bool) -> str:
    """"Kaynaklar" karti: ne silindi, ne kadar surdu, hangi model."""
    parcalar: list[str] = []
    if veri["durum"] == DURUM_HAZIR:
        parcalar.append("Ses dosyaları silindi.")
    parcalar.append(
        "Transkript saklanıyor." if transkript_var else "Transkript saklanmıyor."
    )
    if veri["model"]:
        sure = veri["isleme_sn_ozet"]
        parcalar.append(f"Özet: Copilot CLI, {veri['model']}" + (f" · {sure} sn" if sure else ""))
    if veri["isleme_sn_yaziya_dokme"]:
        parcalar.append(f"Yazıya dökme: {sure_metni(veri['isleme_sn_yaziya_dokme'])}")
    return " ".join(parcalar)


def arama_eslesmesi(veri: dict[str, Any], kisiler: Sequence[dict[str, str]], needle: str) -> bool:
    """FTS yoksa kullanilan yedek arama: baslik ve katilimci adlari."""
    if not needle:
        return True
    havuz = " ".join(
        [str(veri.get("baslik") or ""), *[str(kisi.get("ad") or "") for kisi in kisiler]]
    )
    return needle in field_utils.fold(havuz)


def liste(
    conn: sqlite3.Connection,
    days: int = calls_intake.DEFAULT_DAYS,
    q: str = "",
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Pencere + arama suzgeciyle not listesi."""
    since = calls_intake.iso_text(calls_intake.since_of(days, now))
    kimlikler = depo.ara(conn, q) if q else None
    satirlar = depo.list_notlar(conn, since=since, ids=kimlikler)
    gorevler = depo.gorev_sayilari(conn)
    anahtarlar = depo.jira_anahtarlari(conn)
    needle = field_utils.fold(q.strip()) if q else ""
    kartlar: list[dict[str, Any]] = []
    for veri in satirlar:
        kisiler = depo.katilimcilar(conn, veri["id"])
        # FTS varsa suzgec zaten SQL'de uygulandi; yoksa burada eleriz.
        if kimlikler is None and not arama_eslesmesi(veri, kisiler, needle):
            continue
        kartlar.append(
            kart(
                veri,
                kisiler,
                gorev_sayisi=gorevler.get(veri["id"], 0),
                jira=anahtarlar.get(veri["id"], ""),
            )
        )
    return kartlar


def serit(conn: sqlite3.Connection, ayarlar: Ayarlar, aktif: dict[str, Any] | None = None) -> dict[str, Any]:
    """Takip seridi: anahtar, canli kayit rozeti, isleniyor/kuyrukta sayilari."""
    sayilar = depo.sayim(conn)
    isleniyor = sum(sayilar.get(durum, 0) for durum in ARA_DURUMLAR)
    return {
        "takip": ayarlar.takip,
        "kaydediliyor": bool(aktif),
        "aktif": aktif or None,
        "isleniyor": isleniyor,
        "kuyrukta": sayilar.get(DURUM_KUYRUKTA, 0),
        "hazir": sayilar.get(DURUM_HAZIR, 0),
        "hata": sayilar.get("hata", 0),
        "atlandi": sayilar.get(DURUM_ATLANDI, 0),
        "toplam": sayilar.get("toplam", 0),
        "min_dakika": ayarlar.min_dakika,
    }


def gorev_yuku(bolum: dict[str, Any], not_id: int, baslik: str) -> dict[str, Any]:
    """Aksiyon satirindan "Gorevlerim" kartinin govdesi."""
    kisi = str(bolum.get("kisi") or "").strip()
    aciklama = f"Görüşme notu: {baslik}" if baslik and baslik != BASLIKSIZ else "Görüşme notu"
    if kisi:
        aciklama = f"{aciklama} · {kisi}"
    return {
        "title": str(bolum.get("metin") or "").strip(),
        "description": aciklama,
        "due_date": str(bolum.get("son_tarih") or "").strip(),
    }
