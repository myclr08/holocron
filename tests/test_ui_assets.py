"""Arayuz kancalari ve metin dili: sayfalar duzgun Turkce, kanca kimlikleri yerinde."""

from __future__ import annotations

from pathlib import Path

from app import repository as repo

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"

# Arayuzde bir daha gormek istemedigimiz ASCII'ye indirilmis kelimeler.
ASCII_TURKISH = (
    "Guncelle",
    "Sutunlar",
    "Kayit ekle",
    "Iptal",
    "Vazgec",
    "Duzenle",
    "Gecmisi temizle",
    "Bos alanlari",
    "Henuz",
    "baglanti",
    "Baglanti",
    "Kisisel",
    "katalogunu",
    "Kayitli",
)


def test_pages_are_utf8_and_declare_it():
    for name in ("index.html", "settings.html"):
        text = (STATIC / name).read_text(encoding="utf-8")
        assert '<meta charset="utf-8" />' in text, name


def test_pages_use_proper_turkish(api_client):
    home = api_client.get("/").text
    for marker in ("Güncelle", "Sütunlar", "Kayıt ekle", "İptal", "Düzenle", "Geçmişi temizle",
                   "Boş alanları da göster", "Bu sektörde kayıt yok"):
        assert marker in home, marker

    settings = api_client.get("/settings").text
    for marker in ("Jira bağlantısı", "Kişisel erişim anahtarı", "Bağlantıyı sına",
                   "Alan kataloğunu çek", "Kayıtlı sırrı sil"):
        assert marker in settings, marker

    for page in (home, settings):
        for word in ASCII_TURKISH:
            assert word not in page, word


def test_javascript_messages_are_turkish(api_client):
    script = api_client.get("/static/js/app.js").text
    for marker in ('"İşlem yapılamadı"', '"Henüz grup yok."', '"Güncelleme bitti"',
                   '"Hangi grupta görüneceğini'):
        assert marker in script, marker
    common = api_client.get("/static/js/common.js").text
    assert "Holocron kapatılsın mı?" in common
    settings_js = api_client.get("/static/js/settings.js").text
    assert "Bağlantı kurulamadı: " in settings_js


def test_hidden_attribute_always_hides(api_client):
    """Iptal dugmesi is calismazken gorunmuyordu: button display kurali [hidden]'i eziyordu."""
    css = api_client.get("/static/css/app.css").text
    assert "[hidden] { display: none !important; }" in css


def test_settings_page_has_no_group_sidebar(api_client):
    page = api_client.get("/settings").text
    assert "grup yok" not in page
    assert "no-sidebar" in page
    assert "Hangar" not in page
    css = api_client.get("/static/css/app.css").text
    assert ".layout.no-sidebar" in css


def test_ca_placeholder_uses_a_single_backslash(api_client):
    page = api_client.get("/settings").text
    assert r"C:\sertifika\kurum-ca.pem" in page
    assert r"C:\\sertifika" not in page


def test_columns_modal_is_wide_and_stacks_the_field_id(api_client):
    css = api_client.get("/static/css/app.css").text
    assert ".modal.wide" in css
    assert ".column-list .entry .label" in css
    script = api_client.get("/static/js/app.js").text
    assert "{ wide: true }" in script
    assert 'el("modal-box").classList.toggle("wide"' in script


def test_export_hooks_are_on_the_page(api_client):
    home = api_client.get("/").text
    for marker in ('id="export-xlsx"', "Excel'e aktar", 'id="catalog-warning"'):
        assert marker in home, marker
    script = api_client.get("/static/js/app.js").text
    assert "exportModal" in script
    assert "export.xlsx" in script
    assert "Geçmiş sayfasını ekle" in script
    assert "Görünen süzgeç ve sıralamayı uygula" in script
    css = api_client.get("/static/css/app.css").text
    assert ".export-columns" in css


def test_grid_reports_whether_the_field_catalog_is_empty(api_client, conn):
    group = api_client.post("/api/groups", json={"name": "Filom"}).json()["group"]
    assert api_client.get(f"/api/groups/{group['id']}/issues").json()["catalog_empty"] is True

    repo.store_fields(conn, [{"id": "summary", "name": "Özet", "schema": {"type": "string"}}])
    assert api_client.get(f"/api/groups/{group['id']}/issues").json()["catalog_empty"] is False
