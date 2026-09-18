"""Gorusme notlarinin HTTP uclari.

`api_mailsend.py` / `api_gamify.py` ile ayni desen: ince uc, is mantigi
`app/gorusme/` icinde. Butun yazmalar `context.db_lock` altinda gider.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from . import mailsend, repository
from .context import AppContext
from .gorusme import aygit as gorusme_aygit
from .gorusme import birlestir
from .gorusme import depo, intake as gorusme_intake, ozet as gorusme_ozet
from .gorusme import yaziyadok
from .gorusme import kayit as gorusme_kayit
from .gorusme.kayit import parca_adi  # noqa: F401 - ad disariya acik kalsin
from .gorusme.source import (
    BOLUM_AKSIYON,
    BOLUM_ETIKETLERI,
    BOLUM_KARAR,
    BOLUM_OZET,
    BOLUM_SORU,
    DURUM_HAZIR,
    GorusmeHatasi,
    is_supported as gorusme_supported,
)
from .teamscalls import intake as calls_intake

log = logging.getLogger("holocron.api.gorusme")

router = APIRouter(prefix="/api/gorusme")

# Deneme kaydinin uzunlugu (saniye): tasarimdaki "10 saniyelik deneme".
DENEME_SN = 10.0


def get_context(request: Request) -> AppContext:
    return request.app.state.context


def error_response(code: str, message: str, status: int = 400) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def _servis(context: AppContext) -> Any:
    return getattr(context, "gorusme", None)


def _ayarlar(context: AppContext) -> gorusme_intake.Ayarlar:
    return gorusme_intake.load_config(context.settings)


def _aktif(context: AppContext) -> dict[str, Any] | None:
    servis = _servis(context)
    return servis.aktif() if servis is not None else None


# --- liste ve serit ------------------------------------------------------


@router.get("/view")
def read_view(
    request: Request, days: int = calls_intake.DEFAULT_DAYS, q: str = ""
) -> dict[str, Any]:
    """Ekranin tek yanitta ihtiyaci olan her sey: serit + liste."""
    context = get_context(request)
    conn = context.connection()
    ayarlar = _ayarlar(context)
    return {
        "notlar": gorusme_intake.liste(conn, days=days, q=q),
        "serit": gorusme_intake.serit(conn, ayarlar, _aktif(context)),
        "days": calls_intake._as_days(days),
        "windows": list(calls_intake.WINDOW_DAYS),
        "supported": gorusme_supported(),
        "whisper_var": yaziyadok.kurulu_mu(),
    }


@router.get("/serit")
def read_serit(request: Request) -> dict[str, Any]:
    """Yalnizca serit: canli rozet saniyede bir bunu okur."""
    context = get_context(request)
    return gorusme_intake.serit(context.connection(), _ayarlar(context), _aktif(context))


@router.post("/takip")
def write_takip(request: Request, payload: dict[str, Any] = Body(default_factory=dict)):
    """Takip anahtari: Duraklat / Sürdür."""
    context = get_context(request)
    acik = bool(payload.get("acik"))
    context.settings.set("calls.takip", "1" if acik else "0")
    servis = _servis(context)
    if servis is not None and servis.takipci is not None and not acik:
        # Duraklatildi: acik kayit hemen kapanip kuyruga girsin.
        servis.takipci.yokla()
    return gorusme_intake.serit(context.connection(), _ayarlar(context), _aktif(context))


# --- tek not -------------------------------------------------------------


def _gorevler(context: AppContext, not_id: int) -> list[dict[str, Any]]:
    conn = context.connection()
    kartlar: list[dict[str, Any]] = []
    for gorev_id in depo.gorev_kimlikleri(conn, not_id):
        gorev = repository.get_task(conn, gorev_id)
        if gorev is not None:
            kartlar.append(gorev)
    return kartlar


@router.get("/{not_id}")
def read_not(request: Request, not_id: int) -> dict[str, Any]:
    context = get_context(request)
    conn = context.connection()
    return gorusme_intake.detay(
        conn,
        not_id,
        gorevler=_gorevler(context, not_id),
        transkript_var=bool(depo.transkript(conn, not_id)),
    )


@router.delete("/{not_id}")
def drop_not(request: Request, not_id: int) -> dict[str, Any]:
    context = get_context(request)
    conn = context.connection()
    veri = depo.require_not(conn, not_id)
    with context.db_lock:
        depo.sil(conn, not_id)
    # Ses hala duruyorsa (hata durumunda silinmez) onu da temizleriz.
    if veri["klasor"]:
        birlestir.sil_klasor(Path(veri["klasor"]))
    return {"ok": True, "id": not_id}


@router.post("/{not_id}/yeniden-dene")
def retry_not(request: Request, not_id: int) -> dict[str, Any]:
    context = get_context(request)
    servis = _servis(context)
    if servis is None:
        return error_response("servis_yok", "Görüşme işleyicisi çalışmıyor.")
    with context.db_lock:
        servis.kuyruk.yeniden_dene(not_id)
    return gorusme_intake.detay(context.connection(), not_id)


@router.post("/{not_id}/yeniden-ozetle")
def resummarize(request: Request, not_id: int) -> dict[str, Any]:
    context = get_context(request)
    servis = _servis(context)
    if servis is None:
        return error_response("servis_yok", "Görüşme işleyicisi çalışmıyor.")
    with context.db_lock:
        servis.kuyruk.yeniden_ozetle(not_id)
    return gorusme_intake.detay(
        context.connection(), not_id, gorevler=_gorevler(context, not_id), transkript_var=True
    )


# --- baglar --------------------------------------------------------------


@router.put("/{not_id}/jira")
def write_jira(request: Request, not_id: int, payload: dict[str, Any] = Body(default_factory=dict)):
    """Notu bir Jira kaydina baglar; bos anahtar bagi kaldirir."""
    context = get_context(request)
    conn = context.connection()
    depo.require_not(conn, not_id)
    anahtarlar = repository.parse_issue_keys(str(payload.get("jira_key") or ""))
    with context.db_lock:
        depo.jira_bagla(conn, not_id, anahtarlar[0] if anahtarlar else "")
    return {"ok": True, "jira_key": depo.jira_key(conn, not_id)}


@router.post("/{not_id}/gorev")
def make_task(request: Request, not_id: int, payload: dict[str, Any] = Body(default_factory=dict)):
    """Aksiyondan görev üretir ve nota bağlar."""
    context = get_context(request)
    from .gorusme.kuyruk import gorev_uret

    with context.db_lock:
        gorev = gorev_uret(context, not_id, int(payload.get("bolum_id") or 0))
    return {"ok": True, "gorev": gorev, "gorevler": _gorevler(context, not_id)}


@router.get("/kayit/{jira_key}")
def read_issue_notes(request: Request, jira_key: str) -> dict[str, Any]:
    """Jira kayit detayindaki "Görüşme notları (n)" bolumu."""
    context = get_context(request)
    conn = context.connection()
    kimlikler = depo.kayit_notlari(conn, jira_key)
    return {"notlar": _kartlar(conn, kimlikler), "count": len(kimlikler)}


@router.get("/kisi/{kimlik}")
def read_person_notes(request: Request, kimlik: str) -> dict[str, Any]:
    """Kisiler cekmecesindeki gorusme notlari."""
    context = get_context(request)
    conn = context.connection()
    kimlikler = depo.kisi_notlari(conn, kimlik)
    return {"notlar": _kartlar(conn, kimlikler), "count": len(kimlikler)}


def _kartlar(conn: Any, kimlikler: list[int]) -> list[dict[str, Any]]:
    if not kimlikler:
        return []
    gorevler = depo.gorev_sayilari(conn)
    anahtarlar = depo.jira_anahtarlari(conn)
    return [
        gorusme_intake.kart(
            veri,
            depo.katilimcilar(conn, veri["id"]),
            gorev_sayisi=gorevler.get(veri["id"], 0),
            jira=anahtarlar.get(veri["id"], ""),
        )
        for veri in depo.list_notlar(conn, ids=kimlikler)
    ]


# --- e-posta -------------------------------------------------------------

BOLUM_SIRASI: tuple[str, ...] = (BOLUM_OZET, BOLUM_KARAR, BOLUM_AKSIYON, BOLUM_SORU)


def not_html(baslik: str, gruplar: dict[str, list[dict[str, Any]]]) -> str:
    """Notun e-postaya giden HTML govdesi (Outlook uyumlu, sade)."""
    parcalar = [f"<p><strong>{mailsend.escape(baslik)}</strong></p>"]
    for tur in BOLUM_SIRASI:
        satirlar = gruplar.get(tur) or []
        if not satirlar:
            continue
        parcalar.append(f"<p><strong>{mailsend.escape(BOLUM_ETIKETLERI[tur])}</strong></p><ul>")
        for satir in satirlar:
            metin = mailsend.escape(str(satir.get("metin") or ""))
            kuyruk = " · ".join(
                parca
                for parca in (str(satir.get("kisi") or ""), str(satir.get("son_tarih") or ""))
                if parca
            )
            parcalar.append(f"<li>{metin}{(' — ' + mailsend.escape(kuyruk)) if kuyruk else ''}</li>")
        parcalar.append("</ul>")
    return "".join(parcalar)


@router.post("/{not_id}/eposta")
def send_note(request: Request, not_id: int, payload: dict[str, Any] = Body(default_factory=dict)):
    """Notu e-posta olarak gonderir (mevcut Outlook yolu)."""
    context = get_context(request)
    conn = context.connection()
    veri = depo.require_not(conn, not_id)
    to = mailsend.split_addresses(payload.get("to"))
    if not to:
        return error_response("mail_no_recipient", "Kime alanı boş olamaz.")
    gruplar = gorusme_intake.bolum_gruplari(depo.bolumler(conn, not_id))
    baslik = veri["baslik"] or "Görüşme notu"
    sender = context.sender_factory()
    mode = str(context.settings.get("mailsend.mode", "display") or "display")
    sonuc = sender.send(
        to,
        mailsend.split_addresses(payload.get("cc")),
        str(payload.get("subject") or f"Görüşme notu: {baslik}"),
        not_html(baslik, gruplar),
        [],
        mode,
    )
    return {"ok": True, "mode": mode, "displayed": bool((sonuc or {}).get("displayed", True))}


# --- ayarlar yardimcilari ------------------------------------------------


@router.get("/ayar/aygitlar")
def read_devices(request: Request) -> dict[str, Any]:
    """Aygit listesi ve istege bagli paketlerin durumu (Ayarlar ekrani)."""
    context = get_context(request)
    ayarlar = _ayarlar(context)
    return {
        "aygitlar": gorusme_aygit.aygit_listesi(),
        "secili": gorusme_aygit.secili_aygitlar(ayarlar.mikrofon, ayarlar.hoparlor).sozluk(),
        "paketler": {**gorusme_aygit.kullanilabilir(), "faster_whisper": yaziyadok.kurulu_mu()},
        "supported": gorusme_supported(),
        "calisma_klasoru": str(ayarlar.kok()),
    }


def temiz_hata(hata: BaseException) -> str:
    """Istisnadan tek satirlik, kullaniciya gosterilebilir metin."""
    metin = " ".join(str(hata).split()) or hata.__class__.__name__
    return metin[:300]


def _bos_deneme(klasor: Path, aygitlar: Any) -> dict[str, Any]:
    """Kayit hic baslayamadiginda donen iskelet: arayuz ayni sekli okur."""
    ozet = gorusme_kayit.sonuc_ozeti([], klasor)
    ozet["oneriler"] = []
    ozet["aygitlar"] = aygitlar.sozluk()
    return ozet


@router.post("/ayar/deneme")
def trial_record(request: Request, payload: dict[str, Any] = Body(default_factory=dict)):
    """10 saniyelik deneme kaydi: iki kanalin seviyesini ve teshisini olcer.

    Bu uc HICBIR durumda 500 dondurmez. Deneme kaydinin isi zaten "ses
    gelmiyor"u tespit etmek; ses katmani patladiginda 500 gormek kullaniciyi
    tam da sorunu anlamasi gereken yerde kor birakiyordu. Her yol 200 ve
    ayni sozluk: kanal basina teshis, varsa `hata` metni.
    """
    context = get_context(request)
    ayarlar = _ayarlar(context)
    aygitlar = gorusme_aygit.secili_aygitlar(ayarlar.mikrofon, ayarlar.hoparlor)
    klasor = ayarlar.kok() / "deneme"
    try:
        kayitci = context.gorusme_kayit_factory()
        saniye = float(payload.get("saniye") or DENEME_SN)
        sonuc = dict(kayitci.deneme(saniye, aygitlar, klasor))
    except GorusmeHatasi as hata:
        log.warning("Deneme kaydı yapılamadı: %s", hata)
        cevap = _bos_deneme(klasor, aygitlar)
        cevap["hata"] = str(hata)
        cevap["kod"] = hata.code
        return cevap
    except Exception as hata:  # noqa: BLE001 - ses surucusu her seyi atabilir
        log.warning("Deneme kaydı beklenmedik hata verdi", exc_info=True)
        cevap = _bos_deneme(klasor, aygitlar)
        cevap["hata"] = f"Deneme kaydı yapılamadı: {temiz_hata(hata)}"
        cevap["kod"] = "deneme_dustu"
        return cevap
    sonuc["aygitlar"] = aygitlar.sozluk()
    sonuc.setdefault("hata", "")
    sonuc.setdefault("oneriler", [])
    return sonuc


@router.post("/ayar/whisper-kur")
def install_whisper(request: Request) -> dict[str, Any]:
    """Yaziya dokme paketini kurar (Ayarlar -> Görüşme notları dugmesi).

    Kurulum alt surecte gider ve vekil ayari YALNIZCA o surecin ortamina
    yazilir; Holocron'un kendi baglantisi degismez. Paket gelirse bekleyen
    "hata: faster-whisper yok" satirlari kendiliginden kuyruga doner.
    """
    context = get_context(request)
    ayarlar = _ayarlar(context)
    kurucu = getattr(context, "gorusme_kurucu", None) or yaziyadok.kur
    sonuc = dict(kurucu(proxy=ayarlar.copilot_proxy))
    servis = _servis(context)
    sonuc["yeniden_kuyruga"] = 0
    if sonuc.get("kuruldu") and servis is not None:
        with context.db_lock:
            sonuc["yeniden_kuyruga"] = servis.kuyruk.eksik_paket_islerini_kuyruga_al()
    sonuc["whisper_var"] = yaziyadok.kurulu_mu()
    sonuc["supported"] = gorusme_supported()
    return sonuc


@router.post("/ayar/model-sina")
def test_whisper_model(request: Request, payload: dict[str, Any] = Body(default_factory=dict)):
    """"Modeli sına": yazıya dökme modeli gerçekten yüklenebiliyor mu?

    Ayarlar ekranindaki ALANLARDAN gelen degerlerle calisir (kaydetmeden
    denenebilsin), alan gonderilmediyse ayara duser. Kuyruga hic dokunmaz:
    kendi dokucusunu kurar, veritabani kilidi almaz, isci calismaya devam
    eder. Klasor verildiyse ag DENENMEZ: hatali yol kurumsal vekilde
    dakikalarca zaman asimi beklemek yerine aninda yanit verir.
    """
    context = get_context(request)
    ayarlar = _ayarlar(context)
    model = str(payload.get("model") or "").strip() or ayarlar.whisper_model
    klasor = (
        str(payload.get("klasor") or "").strip()
        if "klasor" in payload
        else ayarlar.whisper_klasor
    )
    fabrika = context.gorusme_dokucu_factory
    dokucu = (
        fabrika(replace(ayarlar, whisper_model=model, whisper_klasor=klasor))
        if fabrika is not None
        else None
    )
    return yaziyadok.sina(model, klasor, dokucu=dokucu)


@router.post("/ayar/copilot-sina")
def test_copilot(request: Request, payload: dict[str, Any] = Body(default_factory=dict)):
    """Copilot CLI kisa bir istekle sinanir (secili model, yol ve vekil ile).

    "Copilot yolu" alani KAYDEDILMEDEN denenebilsin diye ekrandan gelen deger
    ayarin onune gecer. Calisan cozumleme "son bulunan" olarak saklanir:
    kullanici bir dahaki sefere ne bulundugunu ayarda gorur.
    """
    context = get_context(request)
    ayarlar = _ayarlar(context)
    if "yol" in payload:
        ayarlar = replace(ayarlar, copilot_yolu=str(payload.get("yol") or "").strip())
    fabrika = context.gorusme_ozetleyici_factory
    ozetleyici = (
        fabrika(ayarlar)
        if fabrika is not None
        else gorusme_ozet.default_ozetleyici(
            proxy=ayarlar.copilot_proxy,
            jira_base_url=ayarlar.jira_base_url,
            yol=ayarlar.copilot_yolu,
        )
    )
    sonuc = gorusme_ozet.sina(ozetleyici, ayarlar.model_sirasi(), ayarlar.kok())
    if sonuc["calisiyor"]:
        # Calisan model bir sonraki gorusmede basa alinir.
        context.settings.set("calls.ozet_model_son", str(sonuc["model"]))
        if sonuc.get("yol"):
            context.settings.set("calls.copilot_yolu_son", str(sonuc["yol"]))
    # Vekil adresi ekrana geri yazilmaz: yalnizca "ayarli mi" bilgisi doner.
    sonuc["proxy_ayarli"] = bool(ayarlar.copilot_proxy)
    return sonuc


@router.get("/ayar/sablon")
def read_template(request: Request) -> dict[str, Any]:
    """Ozet sablonu: kullanicininki ya da varsayilan."""
    context = get_context(request)
    ayarlar = _ayarlar(context)
    return {
        "sablon": ayarlar.sablon,
        "varsayilan": gorusme_ozet.varsayilan_sablon(),
        "modeller": list(ayarlar.modeller),
        "son_model": ayarlar.son_model,
    }
