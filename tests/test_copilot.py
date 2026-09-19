"""Copilot CLI koprusu: bulma, cagirma, cevabi dosyadan okuma, sinama.

Bu testler 0.11.0'da `tests/test_gorusme.py` icinden tasindi. Gorusme notlari
ozelligi gitti; Copilot koprusu (`app/copilot.py`) genel bir yardimci olarak
kaldi ve sahadan gelen butun hatalarin karsiligi burada duruyor.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from pathlib import Path

from app import copilot
from app.copilot import CopilotCikti
from tests.fake_copilot import SahteCopilot


def _sahte_copilot(klasor: Path, ad: str = "copilot") -> Path:
    klasor.mkdir(parents=True, exist_ok=True)
    yol = klasor / ad
    yol.write_text("@echo off\n", encoding="utf-8")
    yol.chmod(0o755)
    return yol


class SahteKosu:
    """`subprocess.run` sonucunun yerine gecen en kucuk nesne."""

    def __init__(self, kod: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = kod
        self.stdout = stdout
        self.stderr = stderr


# --- model sirasi ve red tespiti -----------------------------------------


def test_model_order_puts_the_last_working_model_first():
    assert copilot.modelleri_coz('["a", "b", "c"]', son_calisan="c") == ["c", "a", "b"]
    assert copilot.modelleri_coz("", son_calisan="") == list(copilot.VARSAYILAN_MODELLER)
    assert copilot.modelleri_coz("bozuk json [", son_calisan="") == ["bozuk json ["]


def test_the_default_model_is_the_one_working_on_the_users_account():
    # Sahadan 19 Eylul 2026: gpt-5, claude-sonnet-4.5, gpt-4.1 reddedildi;
    # varsayilan sirali liste artik hesapta calisan modelle basliyor.
    assert copilot.VARSAYILAN_MODELLER[0] == "claude-sonnet-5"
    assert "gpt-5-mini" in copilot.VARSAYILAN_MODELLER
    assert "claude-haiku-4.5" in copilot.VARSAYILAN_MODELLER


def test_the_copilot_cli_model_flag_rejection_is_recognized():
    """Sahadan gelen tam metin: `Model "gpt-4.1" from --model flag is not available`."""
    cikti = CopilotCikti(kod=1, metin="", hata='Model "gpt-4.1" from --model flag is not available')
    assert copilot.reddedildi_mi(cikti)
    assert not copilot.reddedildi_mi(CopilotCikti(kod=0, metin="hepsi yolunda"))


def test_rejected_models_are_summarized_on_one_line(tmp_path):
    sahte = SahteCopilot(reddedilen=["gpt-5", "claude-sonnet-4.5", "gpt-4.1"])
    sonuc = copilot.sina(sahte, ["gpt-5", "claude-sonnet-4.5", "gpt-4.1"], tmp_path)

    assert sonuc["calisiyor"] is False
    assert sonuc["mesaj"] == (
        "Copilot modelleri reddetti: gpt-5, claude-sonnet-4.5, gpt-4.1 — "
        "Ayarlar'dan hesabında olan bir model seçin (ör. claude-sonnet-5)."
    )


# --- cevap: once dosya, sonra stdout -------------------------------------
#
# Saha hatasi (19 Eylul 2026, Windows): "Model JSON döndürmedi." Copilot
# bulunuyor, model kabul ediliyor, surec donuyor ama stdout'ta banner,
# ilerleme satirlari ve ANSI renk kodlari var; JSON ya kayboluyor ya da
# markdown/stderr icinde kaliyor. Cozum TDD Beyin kalibi: cevabi modelin
# YAZDIGI dosyadan al, stdout'u yalnizca teshis icin tut.


def test_the_answer_is_read_from_the_file_the_model_writes(tmp_path, monkeypatch):
    """(a) Model dosyayi yazar: stdout gurultu olsa da cevap cikar."""
    kopilot = _sahte_copilot(tmp_path / "npm", "copilot.exe")
    klasor = tmp_path / "is"
    klasor.mkdir()
    hedef = copilot.cikti_yolu(klasor)
    # Onceki kosudan kalan BAYAT dosya: taze sanilmamali.
    hedef.write_text('{"baslik": "eski koşu"}', encoding="utf-8")

    def calistir(self, argumanlar, is_klasoru):
        # Copilot calistigi anda bayat dosya silinmis olmali.
        assert not hedef.exists()
        hedef.write_text('\ufeff{"hazir": true, "not": "yeni"}', encoding="utf-8")
        return SahteKosu(stdout="\x1b[1mGitHub Copilot CLI\x1b[0m\n● Reading…\r● Done\n")

    monkeypatch.setattr(copilot.CopilotCalistirici, "calistir", calistir)
    cikti = copilot.CopilotCalistirici(yol=str(kopilot)).sor(klasor, "gpt-5", "istem")

    # BOM'lu dosya da okunur, ANSI stdout'tan silinir, dosya geride kalmaz.
    assert copilot.veriyi_cek(cikti) == {"hazir": True, "not": "yeni"}
    assert "\x1b" not in cikti.metin
    assert not hedef.exists()


def test_an_ansi_coloured_json_on_stdout_is_the_fallback(tmp_path):
    """(b) Dosya yok, JSON kod blogu icinde ve ANSI'li: yine de ayiklanir."""
    sonuc = copilot.sina(SahteCopilot(kip="stdout"), ["gpt-5"], tmp_path)
    assert sonuc["calisiyor"] is True


def test_the_longest_balanced_object_wins_over_a_progress_fragment():
    ham = '● Reading {"file": 1}\nSonuç: {"hazir": true, "kaynak": "dosya"}\n'
    assert copilot.ayristir(ham) == {"hazir": True, "kaynak": "dosya"}
    assert copilot.ayristir("hiç süslü parantez yok") is None


def test_a_model_that_returns_no_json_shows_the_raw_output_tail(tmp_path, caplog):
    """(c) Hic JSON yok: kullanici ham ciktinin kuyrugunu ekranda gorur."""
    with caplog.at_level(logging.WARNING, logger="holocron.copilot"):
        sonuc = copilot.sina(SahteCopilot(kip="bos"), ["gpt-5"], tmp_path)

    assert sonuc["calisiyor"] is False
    assert sonuc["mesaj"].startswith("Model JSON döndürmedi. Ham çıktı (son 400 karakter): ")
    assert "Üzgünüm, dosyayı bulamadım." in sonuc["mesaj"]
    # Ekranda ANSI kodu gorunmez.
    assert "\x1b" not in sonuc["mesaj"]
    # Teshis loga da duser.
    assert any("Copilot JSON'u çıkmadı" in kayit.message for kayit in caplog.records)


def test_the_error_shows_the_end_of_a_long_output_not_the_beginning():
    """Hatanin sebebi hep SONDA: kirpma baştan değil sondan sayılır."""
    cikti = CopilotCikti(kod=0, metin="A" * 5000 + " SON SÖZ: dosyayı yazamadım")
    mesaj = copilot.json_yok_mesaji(cikti)

    assert mesaj.endswith("SON SÖZ: dosyayı yazamadım")
    onek = "Model JSON döndürmedi. Ham çıktı (son 400 karakter): "
    assert len(mesaj) == len(onek) + 400
    # Logdaki kuyruk daha uzun ama o da SONDAN sayilir.
    assert copilot.son_karakterler("A" * 5000 + " son", 2000).endswith(" son")


def test_a_denied_tool_permission_is_named_instead_of_blaming_the_json(tmp_path):
    """(d) Izin reddi: "Model JSON döndürmedi" demek kullaniciyi yaniltir."""
    sonuc = copilot.sina(SahteCopilot(kip="izin"), ["gpt-5"], tmp_path)

    assert sonuc["calisiyor"] is False
    assert sonuc["mesaj"] == (
        "Copilot dosyaya yazma izni vermedi; bayraklar: "
        "--allow-tool=read --allow-tool=write"
    )
    assert copilot.izin_reddi("hepsi yolunda") == ""


def test_a_valid_answer_survives_a_grumpy_exit_code():
    """Copilot cevabi yazip sifir olmayan kodla cikabiliyor; cevap kaybolmasin."""
    cikti = CopilotCikti(kod=1, metin="", dosya='{"hazir": true}')
    assert copilot.veriyi_cek(cikti) == {"hazir": True}


def test_the_timeout_says_how_long_it_waited(tmp_path, monkeypatch):
    kopilot = _sahte_copilot(tmp_path / "npm", "copilot.exe")

    def calistir(self, argumanlar, klasor):
        raise subprocess.TimeoutExpired(cmd="copilot", timeout=self.zaman_asimi)

    monkeypatch.setattr(copilot.CopilotCalistirici, "calistir", calistir)
    calistirici = copilot.CopilotCalistirici(yol=str(kopilot), zaman_asimi=300)
    cikti = calistirici.sor(tmp_path / "is", "gpt-5", "istem")

    assert cikti.kod == 124
    assert cikti.hata == "Copilot 300 sn'de bitmedi."


def test_secrets_are_masked_in_the_raw_output_tail():
    cikti = CopilotCikti(kod=0, metin="Authorization: Bearer gizli.jeton.burada")
    mesaj = copilot.json_yok_mesaji(cikti)
    assert "[gizlendi]" in mesaj
    assert "gizli.jeton.burada" not in mesaj


def test_error_text_masks_anything_that_looks_like_a_secret():
    assert "[gizlendi]" in copilot.temizle("hata: Authorization: Bearer abc.def.ghi")
    assert "[gizlendi]" in copilot.temizle("api_key=cok-gizli-bir-deger")
    assert copilot.temizle("") == ""


# --- Copilot vekili ------------------------------------------------------


def test_the_proxy_only_reaches_the_copilot_subprocess(monkeypatch):
    monkeypatch.delenv("HTTPS_PROXY", raising=False)
    monkeypatch.delenv("NO_PROXY", raising=False)

    ortam = copilot.alt_surec_ortami(
        "http://vekil.kurum.local:8080",
        "https://jira.kurum.local",
        taban={"PATH": "/bin"},
    )

    assert ortam["HTTPS_PROXY"] == "http://vekil.kurum.local:8080"
    assert ortam["https_proxy"] == "http://vekil.kurum.local:8080"
    # Jira konagi alt surecin muafiyet listesine girer.
    assert ortam["NO_PROXY"] == "jira.kurum.local"
    assert ortam["no_proxy"] == "jira.kurum.local"
    # Holocron'un kendi sureci hic degismez.
    import os

    assert "HTTPS_PROXY" not in os.environ


def test_an_empty_proxy_leaves_the_environment_alone():
    ortam = copilot.alt_surec_ortami("", "", taban={"PATH": "/bin"})
    assert ortam == {"PATH": "/bin"}


def test_the_jira_host_is_pulled_out_of_any_shape_of_address():
    assert copilot.jira_konagi("https://jira.kurum.local/rest") == "jira.kurum.local"
    assert copilot.jira_konagi("jira.kurum.local:8443") == "jira.kurum.local"
    assert copilot.jira_konagi("") == ""


# --- Copilot'u bulmak ----------------------------------------------------
#
# Saha hatasi (19 Eylul 2026, Windows VDI): kullanici terminalde `copilot`
# calistirabiliyor ama Holocron "Copilot CLI bulunamadi" diyor. Sebep:
# `holocron.bat` uygulamayi `start "" pythonw.exe` ile aciyor, o surec
# kullanicinin guncel PATH'ini gormuyor.


def test_the_configured_path_wins_over_the_search(tmp_path):
    hedef = _sahte_copilot(tmp_path / "elle", "copilot.exe")
    bulunan = copilot.copilot_bul(str(hedef), ortam={"PATH": str(tmp_path / "bos")})
    assert bulunan == hedef
    # Tirnakli yapistirma da kabul edilir ("where copilot" ciktisi kopyalanir).
    assert copilot.copilot_bul(f'"{hedef}"', ortam={"PATH": str(tmp_path / "bos")}) == hedef


def test_copilot_is_found_in_the_npm_folder_when_the_process_path_is_stale(tmp_path):
    """Ayar bos, PATH'te yok, `%APPDATA%\\npm\\copilot.cmd` var: bulunmali."""
    appdata = tmp_path / "AppData" / "Roaming"
    hedef = _sahte_copilot(appdata / "npm", "copilot.cmd")
    # Uzantisiz node betigi de yaninda durur; Windows'ta calistirilamaz,
    # o yuzden `.cmd` once gelmeli.
    _sahte_copilot(appdata / "npm", "copilot")
    ortam = {"PATH": str(tmp_path / "bos"), "APPDATA": str(appdata)}

    yol, denenen = copilot.copilot_coz("", ortam=ortam, platform="win32")

    assert yol == hedef
    assert "PATH" in denenen


def test_the_user_path_is_read_back_from_the_registry(tmp_path, monkeypatch):
    """Surecin PATH'i bayat: kayit defterindeki kullanici PATH'i taze okunur."""
    hedef = _sahte_copilot(tmp_path / "kayit")
    monkeypatch.setattr(
        copilot, "kayit_defteri_path", lambda platform="": [str(tmp_path / "kayit")]
    )

    yol, denenen = copilot.copilot_coz(
        "", ortam={"PATH": str(tmp_path / "bos")}, platform="win32"
    )

    assert yol == hedef
    assert "kayıt defteri PATH" in denenen


def test_the_registry_reader_never_touches_windows_api_on_linux():
    assert copilot.kayit_defteri_path(platform="linux") == []


def test_the_error_says_where_it_looked_and_what_to_do(tmp_path):
    yol, denenen = copilot.copilot_coz(
        "", ortam={"PATH": str(tmp_path / "bos")}, platform="win32"
    )
    assert yol is None
    mesaj = copilot.bulunamadi_mesaji(denenen)
    assert "Copilot CLI bulunamadı" in mesaj
    assert "Denenen: PATH" in mesaj
    assert "Copilot yolu" in mesaj and "where copilot" in mesaj


def test_a_cmd_file_is_run_through_cmd_exe_and_the_prompt_stays_off_the_command_line(
    tmp_path,
):
    """`.cmd` dosyasini CreateProcess dogrudan acamaz; cmd.exe araya girer.

    Araya cmd.exe girince komut satiri YENIDEN ayristirilir: istemdeki tirnak,
    `%` ya da `&` komutu parcalar. Bu yuzden istem argüman olarak degil dosya
    olarak gecer.
    """
    yol = _sahte_copilot(tmp_path / "npm", "copilot.cmd")
    istem = 'Oku: {"aksiyonlar": []} & %PATH% | "tırnak"'

    argumanlar, dosya = copilot.komut_kur(yol, "gpt-5", istem, tmp_path, platform="win32")

    assert argumanlar[:5] == ["cmd.exe", "/c", str(yol), "--model", "gpt-5"]
    # Okuma izni verilen dosyalar icin, yazma izni cevabin yazilmasi icin.
    assert argumanlar[-2:] == ["--allow-tool=read", "--allow-tool=write"]
    assert istem not in " ".join(argumanlar)
    assert dosya is not None and dosya.read_text(encoding="utf-8") == istem
    kisa = argumanlar[argumanlar.index("-p") + 1]
    assert copilot.ISTEM_DOSYASI in kisa
    # Kisa yonlendirmede cmd.exe'nin ozel gordugu tek karakter bile yok.
    assert not set('"%&|<>^') & set(kisa)


def test_an_exe_is_run_directly_with_the_prompt_as_an_argument(tmp_path):
    yol = _sahte_copilot(tmp_path / "npm", "copilot.exe")
    argumanlar, dosya = copilot.komut_kur(yol, "gpt-5", "kısa istem", tmp_path, platform="win32")
    assert argumanlar == [
        str(yol),
        "--model",
        "gpt-5",
        "-p",
        "kısa istem",
        "--allow-tool=read",
        "--allow-tool=write",
    ]
    assert dosya is None
    # Windows disinda `.cmd` diye bir sey yok: kabuk dalina hic girilmez.
    assert copilot.kabukla_mi(Path("/usr/bin/copilot.cmd"), platform="linux") is False


def test_the_runner_resolves_the_path_and_remembers_it(tmp_path, monkeypatch):
    hedef = _sahte_copilot(tmp_path / "npm", "copilot.exe")
    calistirici = copilot.CopilotCalistirici(yol=str(hedef))
    cagrilar: list[list[str]] = []

    def calistir(self, argumanlar, klasor):
        cagrilar.append(list(argumanlar))
        return SahteKosu(stdout='{"hazir": true}')

    monkeypatch.setattr(copilot.CopilotCalistirici, "calistir", calistir)
    klasor = tmp_path / "is"

    cikti = calistirici.sor(klasor, "gpt-5", "istem")

    assert cikti.kod == 0
    assert calistirici.son_yol == str(hedef)
    assert cagrilar[0][0] == str(hedef)
    # Istem dosyasi (varsa) geride kalmaz.
    assert not (klasor / copilot.ISTEM_DOSYASI).exists()


def test_the_copilot_subprocess_never_pops_a_console_window_on_windows(tmp_path, monkeypatch):
    """Saha hatasi 19 Eylul 2026: konsolsuz surecte Copilot cagrisi bos bir
    konsol penceresi aciyordu. `sessiz_calistir_ayarlari` gercek
    `subprocess.run` cagrisina gecmeli; Windows dalini Linux'ta da kanitlamak
    icin STARTUPINFO sahte olarak eklenir (gercek Windows'ta zaten var)."""

    class SahteStartupInfo:
        def __init__(self) -> None:
            self.dwFlags = 0
            self.wShowWindow = None

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "STARTUPINFO", SahteStartupInfo, raising=False)
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(subprocess, "STARTF_USESHOWWINDOW", 1, raising=False)
    monkeypatch.setattr(subprocess, "SW_HIDE", 0, raising=False)

    yakalanan: dict = {}

    def sahte_run(argumanlar, **kwargs):
        yakalanan.update(kwargs)
        return SahteKosu()

    monkeypatch.setattr(copilot.subprocess, "run", sahte_run)

    sonuc = copilot.CopilotCalistirici().calistir(["copilot", "--model", "gpt-5"], tmp_path)

    assert sonuc.returncode == 0
    assert yakalanan["stdin"] == subprocess.DEVNULL
    assert yakalanan["creationflags"] == 0x08000000
    assert isinstance(yakalanan["startupinfo"], SahteStartupInfo)
    assert yakalanan["startupinfo"].wShowWindow == 0


def test_the_quiet_settings_are_empty_outside_windows(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert copilot.sessiz_calistir_ayarlari() == {"stdin": subprocess.DEVNULL}


def test_a_missing_copilot_tells_the_user_where_to_look(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "bos"))
    cikti = copilot.CopilotCalistirici().sor(tmp_path / "is", "gpt-5", "istem")
    assert cikti.kod == 127
    assert "Copilot CLI bulunamadı" in cikti.hata
    assert "Denenen" in cikti.hata


# --- sinama --------------------------------------------------------------


def test_the_connection_test_walks_the_same_file_path(tmp_path):
    """"Copilot'u sına" da dosyaya yazdirir: yazma izni orada da kanitlanir."""
    sahte = SahteCopilot()
    sonuc = copilot.sina(sahte, ["claude-sonnet-5"], tmp_path)

    assert sonuc["calisiyor"] is True
    istem = sahte.istemler[0]
    assert str(tmp_path / copilot.CIKTI_DOSYASI) in istem
    assert '{"hazir": true}' in istem
    # Cevap dosyasi geride kalmaz.
    assert not (tmp_path / copilot.CIKTI_DOSYASI).exists()


def test_the_connection_test_reports_the_working_model(tmp_path):
    sahte = SahteCopilot(reddedilen=["gpt-5"], son_yol=r"C:\npm\copilot.cmd")
    sonuc = copilot.sina(sahte, ["gpt-5", "claude-sonnet-4.5"], tmp_path)

    assert sonuc["calisiyor"] is True
    assert sonuc["model"] == "claude-sonnet-4.5"
    assert sonuc["mesaj"].startswith("çalışıyor · C:\\npm\\copilot.cmd · model claude-sonnet-4.5")
    assert sonuc["denenen"] == ["gpt-5", "claude-sonnet-4.5"]


# --- ayarlar -------------------------------------------------------------


def test_the_setting_carries_the_path_into_the_subprocess(store):
    store.set("copilot.yolu", r"C:\Users\ornek\AppData\Roaming\npm\copilot.cmd")
    store.set("jira.base_url", "https://jira.kurum.local")
    ayarlar = copilot.load_config(store)

    assert ayarlar.yolu.endswith("copilot.cmd")
    assert ayarlar.jira_base_url == "https://jira.kurum.local"
    calistirici = copilot.default_calistirici(yol=ayarlar.yolu)
    assert calistirici.yol == ayarlar.yolu


def test_the_model_order_comes_from_the_setting(store):
    store.set("copilot.modeller", json.dumps(["bir", "iki"]))
    store.set("copilot.son_model", "iki")
    ayarlar = copilot.load_config(store)
    assert ayarlar.model_sirasi() == ["iki", "bir"]


def test_the_working_folder_never_leaves_the_data_directory(isolated_home, store):
    kok = copilot.load_config(store).kok()
    assert str(kok).startswith(str(isolated_home))


def test_the_old_calls_settings_are_carried_over_by_the_migration(tmp_path):
    """Goc 0016: kullanicinin yazdigi Copilot yolu/vekili kaybolmamali."""
    from app import db

    yol = tmp_path / "eski.db"
    conn = db.connect(yol)
    # 15'e kadar goc: eski anahtarlar `calls.*` altinda yaziliyordu.
    db.migrate(conn, migrations=db.MIGRATIONS[:15])
    conn.executemany(
        "INSERT INTO settings (key, value) VALUES (?, ?)",
        [
            ("calls.copilot_yolu", r"C:\npm\copilot.cmd"),
            ("calls.copilot_proxy", "http://vekil:8080"),
            ("calls.ozet_model_son", "gpt-5-mini"),
            ("calls.min_dakika", "7"),
        ],
    )
    conn.commit()

    db.migrate(conn)

    okunan = dict(conn.execute("SELECT key, value FROM settings").fetchall())
    assert okunan["copilot.yolu"] == r"C:\npm\copilot.cmd"
    assert okunan["copilot.proxy"] == "http://vekil:8080"
    assert okunan["copilot.son_model"] == "gpt-5-mini"
    # Gorusmeye ozel her sey gider.
    assert not any(anahtar.startswith("calls.") for anahtar in okunan)
    # Tablolar da gider.
    assert "teams_calls" not in db.table_names(conn)
    assert "gorusme_notu" not in db.table_names(conn)
    conn.close()


# --- uc ------------------------------------------------------------------


def test_copilot_can_be_tested_from_the_settings_page(api_client, context, fake_copilot):
    fake_copilot.reddedilen = {"claude-sonnet-5"}
    yanit = api_client.post("/api/copilot/sina").json()

    assert yanit["calisiyor"] is True
    assert yanit["model"] == "gpt-5-mini"
    assert context.settings.get("copilot.son_model") == "gpt-5-mini"
    # Vekil adresi asla geri yazilmaz, yalnizca "ayarli mi" bilgisi doner.
    assert yanit["proxy_ayarli"] is False
    assert "proxy" not in yanit["mesaj"].lower()


def test_the_copilot_path_can_be_tested_and_is_remembered(api_client, context, fake_copilot):
    """"Copilot'u sına" ekrandaki yolu kullanir, bulunan yolu gosterir ve saklar."""
    yol = r"C:\Users\ornek\AppData\Roaming\npm\copilot.cmd"
    gorulen: dict[str, str] = {}

    def fabrika(ayarlar):
        gorulen["yol"] = ayarlar.yolu
        fake_copilot.son_yol = yol
        return fake_copilot

    context.copilot_factory = fabrika

    yanit = api_client.post("/api/copilot/sina", json={"yol": yol}).json()

    assert yanit["calisiyor"] is True
    # Alan kaydedilmeden denenebilir: deger uca ekrandan gitti.
    assert gorulen["yol"] == yol
    # Basarida bulunan tam yol ekranda durur ve ayara "son bulunan" yazilir.
    assert yanit["yol"] == yol
    assert yanit["mesaj"].startswith("çalışıyor · " + yol + " · model ")
    assert context.settings.get("copilot.yolu_son") == yol


def test_a_failed_test_reports_the_reason_without_a_500(api_client, fake_copilot):
    fake_copilot.kip = "bos"
    yanit = api_client.post("/api/copilot/sina")
    assert yanit.status_code == 200
    assert yanit.json()["calisiyor"] is False
    assert "Ham çıktı" in yanit.json()["mesaj"]


def test_the_copilot_settings_survive_a_round_trip(api_client):
    api_client.put(
        "/api/settings",
        json={
            "copilot.yolu": r"C:\npm\copilot.cmd",
            "copilot.proxy": "http://vekil:8080",
            "copilot.modeller": '["bir", "iki"]',
        },
    )
    ayarlar = api_client.get("/api/settings").json()["settings"]
    assert ayarlar["copilot.yolu"] == r"C:\npm\copilot.cmd"
    assert ayarlar["copilot.proxy"] == "http://vekil:8080"
    assert ayarlar["copilot.modeller"] == '["bir", "iki"]'


# --- arayuz --------------------------------------------------------------


def test_the_copilot_card_is_on_the_settings_page(api_client):
    page = api_client.get("/settings").text
    for marker in (
        'id="copilot-card"',
        ">Copilot<",
        'id="copilot-yol"',
        'id="copilot-proxy"',
        'id="copilot-modeller"',
        'id="copilot-test"',
        "Copilot'u sına",
        'id="copilot-sonuc"',
        "where copilot",
        "set HTTPS_PROXY=",
    ):
        assert marker in page, marker


def test_the_copilot_script_saves_every_field(api_client):
    script = api_client.get("/static/js/settings.js").text
    for marker in (
        '"copilot.yolu"',
        '"copilot.proxy"',
        '"copilot.modeller"',
        '"copilot.son_model"',
        '"copilot.yolu_son"',
        "/api/copilot/sina",
        "function saveCopilot",
        "function testCopilot",
    ):
        assert marker in script, marker
