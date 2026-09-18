"""Gorusme notlari: algilama, kayit, kuyruk, ozet ve notun kendisi.

Test edilen sorular:

* Mikrofon izin defterindeki hangi satir "Teams su an konusuyor" der?
* Takip duraklatilmisken kayit basliyor mu? (baslamamali)
* Gorusme icinde aygit degisirse ikinci parca aciliyor mu?
* Asgari surenin altindaki gorusme isleniyor mu? (islenmemeli, "atlandi")
* Kuyruk sirali mi, uygulama yarim isi acilista surduruyor mu?
* Model reddedilirse sradaki deneniyor ve "son calisan" yaziliyor mu?
* faster-whisper yoksa ses siliniyor mu? (SILINMEMELI)
* Copilot vekili alt surecin ortamina yaziliyor, Holocron'unkine yazilmiyor mu?

Gercek ses karti, faster-whisper ve Copilot CLI hicbir testte calismaz:
`app/gorusme/sahte.py` bellek ici karsiliklaridir.
"""

from __future__ import annotations

import os
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app import db
from app.gorusme import algilayici as algilayici_modulu
from app.gorusme import birlestir, depo, intake as gorusme_intake, ozet, sahte, yaziyadok
from app.gorusme.algilayici import DefterSatiri, KayitDefteriAlgilayici, mikrofon_acik, teams_mi
from app.gorusme.source import (
    DURUM_ATLANDI,
    DURUM_KUYRUKTA,
    KANAL_HOP,
    KANAL_MIK,
    GorusmeHatasi,
    Segment,
    calisma_koku,
)
from app.gorusme.takipci import Takipci

NOW = datetime(2026, 9, 18, 14, 10, tzinfo=timezone.utc)


class Saat:
    """Testlerin ilerlettigi saat: hicbir test gercekten beklemez."""

    def __init__(self, baslangic: datetime = NOW) -> None:
        self.simdi = baslangic

    def __call__(self) -> datetime:
        return self.simdi

    def ilerle(self, saniye: float) -> None:
        self.simdi = self.simdi + timedelta(seconds=saniye)


def kur_takipci(context, fake_gorusme, saat: Saat, kuyruk=None) -> Takipci:
    return Takipci(
        context,
        algilayici=fake_gorusme["algilayici"],
        kayitci=fake_gorusme["kayitci"],
        kuyruk=kuyruk,
        bildirimci=fake_gorusme["bildirimci"],
        saat=saat,
    )


def takibi_ac(context, min_dakika: str = "4") -> None:
    context.settings.set("calls.takip", "1")
    context.settings.set("calls.min_dakika", min_dakika)


# --- Asama A: algilama ---------------------------------------------------


def test_registry_key_names_are_recognised_for_both_teams_flavours():
    assert teams_mi("MSTeams_8wekyb3d8bbwe!MSTeams")
    assert teams_mi(r"C:#Program Files#WindowsApps#MSTeams#ms-teams.exe")
    assert teams_mi("C:#Users#ornek#AppData#Local#Microsoft#Teams#current#Teams.exe")
    assert not teams_mi("Microsoft.WindowsCamera_8wekyb3d8bbwe")
    assert not teams_mi("")


def test_microphone_is_in_use_only_when_stop_is_zero():
    satirlar = [
        DefterSatiri("MSTeams_8wekyb3d8bbwe!MSTeams", baslangic=133, bitis=0),
        DefterSatiri("Microsoft.WindowsCamera_8wekyb3d8bbwe", baslangic=1, bitis=0),
    ]
    assert mikrofon_acik(satirlar)
    # Teams mikrofonu birakmis: Stop dolu.
    kapali = [DefterSatiri("MSTeams_8wekyb3d8bbwe!MSTeams", baslangic=133, bitis=140)]
    assert not mikrofon_acik(kapali)
    # Baslangic damgasi yoksa "kullanimda" denmez.
    assert not mikrofon_acik([DefterSatiri("MSTeams_8wekyb3d8bbwe", baslangic=0, bitis=0)])


def test_detector_falls_back_to_the_audio_session_and_never_dies_on_it():
    cagrilar: list[str] = []

    def patlayan() -> bool:
        cagrilar.append("oturum")
        raise RuntimeError("comtypes patladi")

    dedektor = KayitDefteriAlgilayici(
        okuyucu=lambda: [], aygit_cozucu=lambda: sahte.AYGITLAR, oturum_sinyali=patlayan
    )
    assert dedektor.durum().aktif is False
    assert cagrilar == ["oturum"]

    calisan = KayitDefteriAlgilayici(
        okuyucu=lambda: [], aygit_cozucu=lambda: sahte.AYGITLAR, oturum_sinyali=lambda: True
    )
    durum = calisan.durum()
    assert durum.aktif is True
    assert durum.kaynak == "ses_oturumu"
    assert durum.aygitlar.mikrofon == sahte.AYGITLAR.mikrofon


@pytest.mark.skipif(os.name == "nt", reason="Windows'ta winreg zaten var")
def test_registry_reader_never_touches_windows_api_on_linux():
    """Linux'ta `winreg` hic ice aktarilmaz; okuma bos liste doner."""
    assert algilayici_modulu._winreg() is None
    assert algilayici_modulu.defteri_oku() == []


# --- Asama A: kayit ve takip ---------------------------------------------


def test_paused_tracking_records_nothing(context, fake_gorusme):
    saat = Saat()
    takipci = kur_takipci(context, fake_gorusme, saat)
    fake_gorusme["algilayici"].basla()
    context.settings.set("calls.takip", "0")

    assert takipci.yokla() is None
    assert fake_gorusme["kayitci"].basla_cagrisi == 0
    assert depo.list_notlar(context.conn) == []


def test_a_call_is_recorded_and_queued_when_long_enough(context, fake_gorusme):
    saat = Saat()
    takipci = kur_takipci(context, fake_gorusme, saat)
    takibi_ac(context)

    fake_gorusme["algilayici"].basla()
    ozet_satiri = takipci.yokla()
    assert ozet_satiri is not None
    not_id = ozet_satiri["id"]
    kayit = depo.require_not(context.conn, not_id)
    assert kayit["durum"] == "kaydediliyor"
    assert Path(kayit["klasor"]).exists()
    # Kayit basladi bildirimi dustu.
    assert fake_gorusme["bildirimci"].mesajlar[0][0] == "Görüşme kaydediliyor"

    saat.ilerle(27 * 60)
    fake_gorusme["algilayici"].bitir()
    assert takipci.yokla() is None

    kayit = depo.require_not(context.conn, not_id)
    assert kayit["durum"] == DURUM_KUYRUKTA
    assert kayit["sure_sn"] == 27 * 60


def test_a_short_call_is_skipped_and_its_folder_is_deleted(context, fake_gorusme):
    saat = Saat()
    takipci = kur_takipci(context, fake_gorusme, saat)
    takibi_ac(context)

    fake_gorusme["algilayici"].basla()
    not_id = takipci.yokla()["id"]
    klasor = Path(depo.require_not(context.conn, not_id)["klasor"])
    assert klasor.exists()

    # Dort dakikanin altinda: islenmez.
    saat.ilerle(90)
    fake_gorusme["algilayici"].bitir()
    takipci.yokla()

    kayit = depo.require_not(context.conn, not_id)
    assert kayit["durum"] == DURUM_ATLANDI
    assert not klasor.exists()


def test_a_device_change_opens_a_second_part(context, fake_gorusme):
    saat = Saat()
    takipci = kur_takipci(context, fake_gorusme, saat)
    takibi_ac(context)

    fake_gorusme["algilayici"].basla()
    not_id = takipci.yokla()["id"]
    klasor = Path(depo.require_not(context.conn, not_id)["klasor"])
    assert sorted(yol.name for yol in klasor.glob("*.wav")) == ["hop-01.wav", "mik-01.wav"]

    # Kulaklik degisti: yoklama farki gorur, yeni parca acar.
    saat.ilerle(120)
    fake_gorusme["algilayici"].basla(sahte.IKINCI_AYGITLAR)
    takipci.yokla()
    assert sorted(yol.name for yol in klasor.glob("*.wav")) == [
        "hop-01.wav",
        "hop-02.wav",
        "mik-01.wav",
        "mik-02.wav",
    ]


def test_pausing_during_a_call_closes_the_recording(context, fake_gorusme):
    saat = Saat()
    takipci = kur_takipci(context, fake_gorusme, saat)
    takibi_ac(context)
    fake_gorusme["algilayici"].basla()
    not_id = takipci.yokla()["id"]

    saat.ilerle(10 * 60)
    context.settings.set("calls.takip", "0")
    assert takipci.yokla() is None
    assert depo.require_not(context.conn, not_id)["durum"] == DURUM_KUYRUKTA
    assert fake_gorusme["kayitci"].bitir_cagrisi >= 1


def test_the_trial_recording_measures_both_channels(tmp_path):
    kayitci = sahte.SahteKayitci(parca_sn=0.3)
    sonuc = kayitci.deneme(0.3, sahte.AYGITLAR, tmp_path / "deneme")
    assert sonuc["kanallar"][KANAL_MIK]["ses_var"] is True
    assert sonuc["kanallar"][KANAL_HOP]["ses_var"] is True

    sessiz = sahte.SahteKayitci(parca_sn=0.3, sessiz=True)
    bos = sessiz.deneme(0.3, sahte.AYGITLAR, tmp_path / "sessiz")
    assert bos["kanallar"][KANAL_MIK]["ses_var"] is False


# --- Asama B: WAV birlestirme --------------------------------------------


def test_parts_are_merged_in_time_order(tmp_path):
    for sira, ton in ((1, 220), (2, 440)):
        birlestir.yaz(tmp_path / f"mik-{sira:02d}.wav", sahte.sentetik_cerceve(0.25, ton))
    hedef = birlestir.birlestir(list(tmp_path.glob("mik-*.wav")), tmp_path / "mik.wav")
    assert hedef is not None
    with wave.open(str(hedef), "rb") as dosya:
        assert dosya.getframerate() == 16000
        assert dosya.getnchannels() == 1
    assert birlestir.sure_sn(hedef) == pytest.approx(0.5, abs=0.02)


def test_merging_nothing_returns_none(tmp_path):
    assert birlestir.birlestir([], tmp_path / "bos.wav") is None


def test_channels_are_labelled_you_and_the_other_side():
    metin = yaziyadok.harmanla(
        [Segment(0.0, 2.0, "Merhaba")], [Segment(3.0, 5.0, "Merhaba, buyurun")]
    )
    assert metin.splitlines() == [
        "[00:00:00] Sen: Merhaba",
        "[00:00:03] Karşı taraf: Merhaba, buyurun",
    ]


# --- Asama B: kuyruk ve hat ----------------------------------------------


# --- ozet ciktisi ---------------------------------------------------------


def test_json_is_pulled_out_of_free_text():
    govde = '{"baslik": "Deneme", "ozet": ["bir"]}'
    for ham in (
        govde,
        f"İşte not:\n```json\n{govde}\n```\nHazır.",
        f"Buyurun {govde} bitti",
    ):
        assert ozet.ayristir(ham) == {"baslik": "Deneme", "ozet": ["bir"]}
    assert ozet.ayristir("hiç JSON yok") is None
    # Metin icindeki suslu parantez dengeyi bozmamali.
    icice = '{"baslik": "a { b }", "ozet": []}'
    assert ozet.ayristir(f"önsöz {icice}")["baslik"] == "a { b }"


def test_sections_survive_a_sloppy_model_answer():
    veri = {
        "baslik": "  Başlık  ",
        "ozet": "- birinci\n- ikinci",
        "kararlar": ["karar"],
        "aksiyonlar": ["düz metin aksiyon", {"metin": "iş", "kisi": "Ali", "son_tarih": ""}],
        "sorular": [],
    }
    satirlar = ozet.bolumler(veri)
    turler = {satir["tur"] for satir in satirlar}
    assert turler == {"ozet", "karar", "aksiyon"}
    assert ozet.maddeler(veri, "ozet") == ["birinci", "ikinci"]
    assert ozet.aksiyonlar(veri)[1] == {"metin": "iş", "kisi": "Ali", "son_tarih": ""}
    assert ozet.baslik(veri) == "Başlık"


def test_model_order_puts_the_last_working_model_first():
    assert ozet.modelleri_coz('["a", "b", "c"]', son_calisan="c") == ["c", "a", "b"]
    assert ozet.modelleri_coz("", son_calisan="") == list(ozet.VARSAYILAN_MODELLER)
    assert ozet.modelleri_coz("bozuk json [", son_calisan="") == ["bozuk json ["]


def test_the_prompt_keeps_the_json_schema_intact(tmp_path):
    istem = ozet.istem_kur(
        ozet.varsayilan_sablon(), tmp_path / "transkript.txt", ["Örnek Kişi"], "2026-09-18", "27 dk"
    )
    assert str(tmp_path / "transkript.txt") in istem
    assert "Örnek Kişi" in istem
    # Sema oldugu gibi durmali: `str.format` kullanilmiyor.
    assert '"aksiyonlar"' in istem and '{"metin"' in istem


# --- Copilot vekili -------------------------------------------------------


def test_the_proxy_only_reaches_the_copilot_subprocess(store, monkeypatch):
    """Vekil alt surecin ortamina yazilir; Holocron'un kendi ortami degismez."""
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)
    store.set("calls.copilot_proxy", "http://vekil.ornek.local:8080")
    store.set("jira.base_url", "https://jira.ornek.local")
    ayarlar = gorusme_intake.load_config(store)

    ozetleyici = ozet.default_ozetleyici(
        proxy=ayarlar.copilot_proxy, jira_base_url=ayarlar.jira_base_url
    )
    ortam = ozetleyici.ortam()
    assert ortam["HTTPS_PROXY"] == "http://vekil.ornek.local:8080"
    assert ortam["https_proxy"] == "http://vekil.ornek.local:8080"
    # Jira dogrudan goruluyor: alt surecte bile vekile gitmesin.
    assert "jira.ornek.local" in ortam["NO_PROXY"]
    # Holocron'un kendi ortami ASLA degismez.
    assert "HTTPS_PROXY" not in os.environ


def test_the_jira_client_stays_direct_while_the_copilot_proxy_is_set(store):
    from app.jira_client import create_client
    from app.settings_store import PROXY_DIRECT

    store.set("calls.copilot_proxy", "http://vekil.ornek.local:8080")
    store.set("net.proxy_mode", PROXY_DIRECT)
    store.set("jira.base_url", "https://jira.ornek.local")
    client = create_client(store.jira_config())
    assert client.session.proxies == {}
    assert client.session.trust_env is False


def test_an_empty_proxy_leaves_the_environment_alone():
    ortam = ozet.alt_surec_ortami("", "", taban={"PATH": "/usr/bin"})
    assert ortam == {"PATH": "/usr/bin"}


def test_the_connection_test_reports_the_working_model_and_hides_secrets(tmp_path):
    ozetleyici = sahte.SahteOzetleyici(reddedilen=["gpt-5"], kabuk="hazır")
    sonuc = ozet.sina(ozetleyici, ["gpt-5", "claude-sonnet-4.5"], tmp_path)
    assert sonuc["calisiyor"] is True
    assert sonuc["model"] == "claude-sonnet-4.5"
    assert "çalışıyor · model claude-sonnet-4.5" in sonuc["mesaj"]
    # Sinama dosyasi geride kalmaz.
    assert not (tmp_path / ozet.SINAMA_DOSYASI).exists()

    hepsi_red = sahte.SahteOzetleyici(reddedilen=["gpt-5"])
    basarisiz = ozet.sina(hepsi_red, ["gpt-5"], tmp_path)
    assert basarisiz["calisiyor"] is False
    assert basarisiz["mesaj"]


def test_error_text_masks_anything_that_looks_like_a_secret():
    assert "[gizlendi]" in ozet.temizle("hata: Authorization: Bearer abc.def.ghi")
    assert "[gizlendi]" in ozet.temizle("api_key=cok-gizli-bir-deger")
    assert ozet.temizle("") == ""


# --- katilimci eslemesi ---------------------------------------------------


def test_a_call_far_away_in_time_or_length_never_matches():
    aramalar = [
        {
            "call_id": "uzak",
            "started_at": (NOW + timedelta(hours=2)).isoformat().replace("+00:00", "Z"),
            "duration_ms": 600000,
        },
        {
            "call_id": "kisa",
            "started_at": NOW.isoformat().replace("+00:00", "Z"),
            "duration_ms": 30000,
        },
    ]
    hedef = NOW.isoformat().replace("+00:00", "Z")
    assert gorusme_intake.eslesen_arama(aramalar, hedef, 27 * 60) is None


# --- depo, arama ve gorevler ----------------------------------------------


def test_the_migration_creates_the_meeting_note_tables(conn):
    assert {
        "gorusme_notu",
        "gorusme_bolum",
        "gorusme_katilimci",
        "gorusme_bag",
        "gorusme_transkript",
    } <= db.table_names(conn)
    assert db.SCHEMA_VERSION == 15



def test_the_working_folder_never_leaves_the_data_directory(isolated_home):
    assert calisma_koku("") == isolated_home / "gorusme"
    assert calisma_koku(str(isolated_home / "baska")) == isolated_home / "baska"
