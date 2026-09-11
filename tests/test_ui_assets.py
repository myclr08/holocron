"""Arayuz kancalari ve metin dili: sayfalar duzgun Turkce, kanca kimlikleri yerinde."""

from __future__ import annotations

from pathlib import Path

import pytest

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


# --- Asama 5: tema, fontlar, yildiz alani -------------------------------

FONTS = STATIC / "fonts"

FONT_FILES = (
    "inter-latin.woff2",
    "inter-latin-ext.woff2",
    "inter-arrows.woff2",
    "pathway-gothic-one-latin.woff2",
    "pathway-gothic-one-latin-ext.woff2",
)

LICENSE_FILES = ("inter-OFL.txt", "pathway-gothic-one-OFL.txt")

# Tema bu degiskenler uzerine kurulu; biri kaybolursa ekran renksiz kalir.
CSS_VARIABLES = (
    "--bg:", "--panel:", "--panel-2:", "--line:", "--line-strong:",
    "--text:", "--muted:", "--mono:", "--accent:", "--accent-2:", "--accent-ink:",
    "--saber-blue:", "--saber-green:", "--saber-purple:", "--saber-red:",
    "--saber-yellow:", "--saber-white:",
    "--saber-blue-glow:", "--saber-green-glow:", "--saber-purple-glow:",
    "--saber-red-glow:", "--saber-yellow-glow:", "--saber-white-glow:",
    "--font:", "--display:",
)

SCRIPTS = ("common.js", "app.js", "settings.js", "starfield.js")


def test_fonts_and_their_licenses_ship_with_the_app():
    for name in FONT_FILES:
        path = FONTS / name
        assert path.exists(), name
        assert path.read_bytes()[:4] == b"wOF2", name
    for name in LICENSE_FILES:
        text = (FONTS / name).read_text(encoding="utf-8")
        assert "SIL Open Font License" in text, name


def test_fonts_are_served_over_http(api_client):
    for name in FONT_FILES + LICENSE_FILES:
        response = api_client.get(f"/static/fonts/{name}")
        assert response.status_code == 200, name


def test_font_faces_are_declared_locally_with_swap(api_client):
    css = api_client.get("/static/css/app.css").text
    assert css.count("@font-face") >= len(FONT_FILES)
    assert css.count("font-display: swap") >= len(FONT_FILES)
    assert 'font-family: "Inter"' in css
    assert 'font-family: "Pathway Gothic One"' in css
    for name in FONT_FILES:
        assert f"../fonts/{name}" in css, name
    # Turkce icin latin-ext alt kumesi sart: s g I S G oradan gelir.
    assert "U+0100-02BA" in css


def test_theme_variables_are_defined(api_client):
    css = api_client.get("/static/css/app.css").text
    for name in CSS_VARIABLES:
        assert name in css, name


def test_pages_declare_turkish(api_client):
    for path in ("/", "/settings"):
        assert '<html lang="tr">' in api_client.get(path).text, path


def test_pages_never_reach_outside(api_client):
    """CDN yok, dis kaynak yok: sayfalardaki her src/href yerel olmali."""
    import re

    for path in ("/", "/settings"):
        page = api_client.get(path).text
        for match in re.findall(r'(?:src|href)="([^"]+)"', page):
            assert match.startswith("/") or match.startswith("#"), (path, match)
    css = api_client.get("/static/css/app.css").text
    assert "url(http" not in css
    assert "@import" not in css
    for name in SCRIPTS:
        script = api_client.get(f"/static/js/{name}").text
        assert "cdn." not in script, name
    # Tek istisna: Jira kaydina giden baglanti sablonu.
    app_js = api_client.get("/static/js/app.js").text
    assert "/browse/" in app_js


def test_javascript_files_parse(api_client):
    """node --check: sozdizimi hatasi sessizce bos ekrana donusmesin."""
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("node yok")
    for name in SCRIPTS:
        path = STATIC / "js" / name
        result = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
        assert result.returncode == 0, f"{name}: {result.stderr}"


def test_starfield_respects_the_motion_preference(api_client):
    script = api_client.get("/static/js/starfield.js").text
    assert "prefers-reduced-motion: reduce" in script
    assert "visibilitychange" in script
    assert "requestAnimationFrame" in script
    assert "cancelAnimationFrame" in script
    assert "MAX_DPR = 2" in script
    css = api_client.get("/static/css/app.css").text
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert "html.no-motion" in css


def test_appearance_settings_round_trip(api_client):
    settings = api_client.get("/api/settings").json()["settings"]
    assert settings["ui.starfield"] == "1"
    assert settings["ui.motion"] == "1"
    assert settings["ui.crawl_seen"] == ""

    saved = api_client.put(
        "/api/settings", json={"ui.starfield": "0", "ui.motion": "0", "ui.crawl_seen": "1"}
    ).json()["settings"]
    assert saved["ui.starfield"] == "0"
    assert saved["ui.motion"] == "0"
    assert saved["ui.crawl_seen"] == "1"


def test_opening_crawl_is_on_the_page_and_can_be_skipped(api_client):
    page = api_client.get("/").text
    assert 'id="crawl"' in page
    assert "Bölüm I" in page
    assert "Bir daha gösterme" in page
    script = api_client.get("/static/js/app.js").text
    assert "ui.crawl_seen" in script
    assert "reducedMotion()" in script


def test_opening_crawl_is_slow_enough_to_read(api_client):
    """Ilk surumde 9 sn'de akiyordu, kimse okuyamadi: sabit hizda 24 sn."""
    css = api_client.get("/static/css/app.css").text
    assert "animation: crawl-up 24s linear 1 forwards;" in css
    # Metin gorunur bolgenin altindan girer, opaklik akis boyunca sabit kalir.
    block = css.split("@keyframes crawl-up", 1)[1].split("}", 2)[0]
    assert "top: 100%" in block
    assert "opacity" not in block


def test_opening_crawl_waits_then_fades_out(api_client):
    css = api_client.get("/static/css/app.css").text
    assert "animation: crawl-fade .6s ease 25.5s 1 forwards;" in css
    assert "@keyframes crawl-fade" in css


def test_crawl_timer_matches_the_css_timeline(api_client):
    """JS sayaci ile CSS zaman cizelgesi ayrilirsa panel erken/gec kapanir."""
    script = api_client.get("/static/js/app.js").text
    line = next(row for row in script.splitlines() if row.startswith("const CRAWL_MS"))
    assert line == "const CRAWL_MS = 26100;"  # 24 sn akis + 1.5 sn bekleme + 0.6 sn sonme


def test_crawl_can_still_be_skipped(api_client):
    """Uzayan sure "Gec" ve Esc'yi zorunlu kiliyor, ikisi de yerinde kalmali."""
    page = api_client.get("/").text
    assert 'id="crawl-skip"' in page
    assert "Geç (Esc)" in page
    script = api_client.get("/static/js/app.js").text
    assert 'if (!el("crawl").hidden) closeCrawl();' in script


def test_reduced_motion_still_skips_the_crawl_entirely(api_client):
    """Sure uzadi diye hareket kapaliyken acilis gosterilmemeli."""
    script = api_client.get("/static/js/app.js").text
    body = script.split("function maybeOpenCrawl", 1)[1].split("function closeCrawl", 1)[0]
    assert "if (reducedMotion()) return;" in body
    assert 'if (settings["ui.motion"] === "0") return;' in body
    css = api_client.get("/static/css/app.css").text
    assert "html.no-motion *" in css
    assert "@media (prefers-reduced-motion: reduce)" in css


def test_appearance_section_is_on_the_settings_page(api_client):
    page = api_client.get("/settings").text
    for marker in ("Görünüm", 'id="ui-starfield"', 'id="ui-motion"', "Açılışı tekrar göster"):
        assert marker in page, marker
    script = api_client.get("/static/js/settings.js").text
    assert "ui.starfield" in script
    assert "ui.motion" in script


def test_progress_ring_replaces_the_bar(api_client):
    page = api_client.get("/").text
    assert 'class="death-star"' in page
    assert 'id="progress-ring"' in page
    assert 'id="progress-count"' in page
    assert "progress-fill" not in page
    script = api_client.get("/static/js/app.js").text
    assert "strokeDasharray" in script
    assert "strokeDashoffset" in script
    css = api_client.get("/static/css/app.css").text
    assert ".death-star .ds-progress" in css
    assert ".death-star.is-done .ds-progress" in css


def test_empty_state_draws_our_own_xwing(api_client):
    page = api_client.get("/").text
    assert 'class="xwing"' in page
    assert "Bu sektörde kayıt yok" in page
    css = api_client.get("/static/css/app.css").text
    assert ".xwing .tip" in css
    script = api_client.get("/static/js/app.js").text
    assert "JQL henüz sonuç getirmedi" in script
    assert "Bilinen galakside bulunamadı" in script


def test_status_pills_follow_the_jira_status_category(api_client):
    css = api_client.get("/static/css/app.css").text
    for name in (".status-pill.status-new", ".status-pill.status-indeterminate",
                 ".status-pill.status-done"):
        assert name in css, name
    script = api_client.get("/static/js/app.js").text
    assert "statusCategory" in script


def test_favicon_is_our_own_drawing(api_client):
    svg = api_client.get("/static/favicon.svg").text
    assert svg.startswith("<svg")
    assert "#ffe81f" in svg.lower()
    assert "2e9bff" in svg.lower()


# --- Asama 6: Gorevlerim (kisisel kanban) -------------------------------


def test_tasks_entry_sits_above_the_groups(api_client):
    page = api_client.get("/").text
    entry = page.split('id="group-list"')[0]
    assert 'id="tasks-entry"' in entry, "Görevlerim satırı grup listesinin üstünde olmalı"
    assert "Görevlerim" in entry
    assert 'id="tasks-badge"' in entry
    # Kilic seridi sari: gruplarin renginden ayrilsin.
    assert "color-yellow" in entry


def test_task_board_hooks_are_on_the_page(api_client):
    page = api_client.get("/").text
    for marker in (
        'id="tasks-view"',
        "GÖREVLERİM",
        'id="new-task"',
        "Yeni görev",
        'id="task-search"',
        'id="tasks-export"',
        'id="tasks-old"',
        "Eskileri göster",
        'id="kanban"',
    ):
        assert marker in page, marker


def test_task_script_covers_the_board_and_the_form(api_client):
    script = api_client.get("/static/js/app.js").text
    for marker in (
        "/api/tasks",
        "/api/tasks/summary",
        "tasks/export.xlsx",
        "/api/issues/keys",
        "function taskModal",
        "function renderKanban",
        "function dropIndex",
        '"Yapılacak"',
        '"Yapılıyor"',
        '"Yapıldı"',
        "Buraya sürükle",
        "Bu kayıt henüz çekilmedi",
        "Bu kayıt için görev oluştur",
    ):
        assert marker in script, marker
    # Surukle-birak ve klavye ile tasima birlikte var.
    for marker in ("dragstart", "dragover", "drop", '"←"', '"→"'):
        assert marker in script, marker
    # Ctrl+Enter kaydeder.
    assert "event.ctrlKey || event.metaKey" in script


def test_task_board_styles_are_defined(api_client):
    css = api_client.get("/static/css/app.css").text
    for name in (
        ".tasks-entry",
        ".tasks-entry .count.overdue",
        ".kanban",
        ".kanban-column",
        ".kanban-head",
        ".kanban-drop",
        ".task-card",
        ".task-card.is-done",
        ".due-badge.due-overdue",
        ".due-badge.due-today",
        ".due-badge.due-soon",
    ):
        assert name in css, name
    # Sutun basliklari Pathway Gothic One; Star Wars dozu burada kalir.
    head = css.split(".kanban-name {", 1)[1].split("}", 1)[0]
    assert "font-family: var(--display);" in head
    # 900 altinda alt alta, 1024'te hala yan yana.
    assert "@media (max-width: 900px)" in css
    stacked = css.split("@media (max-width: 900px)", 1)[1].split("}", 2)[0]
    assert "grid-template-columns: 1fr" in stacked


# --- Ag teshisi: kurum agi denetimleri ----------------------------------


def test_network_controls_are_on_the_settings_page(api_client):
    page = api_client.get("/settings").text
    for marker in (
        'id="proxy-mode"',
        'value="direct"',
        "Doğrudan bağlan",
        'id="no-proxy"',
        'id="ipv4-first"',
        "Önce IPv4 dene",
        'id="diagnose"',
        ">Teşhis<",
        'id="diagnosis"',
    ):
        assert marker in page, marker


def test_the_diagnosis_list_is_drawn_and_styled(api_client):
    script = api_client.get("/static/js/settings.js").text
    assert "/api/settings/diagnose" in script
    assert "renderDiagnosis" in script
    assert "net.proxy_mode" in script
    assert "net.ipv4_first" in script
    css = api_client.get("/static/css/app.css").text
    for name in (".diagnosis-advice", ".steps", ".step.fail", ".step-ms"):
        assert name in css, name


def test_the_diagnosis_is_built_from_nodes_not_html(api_client):
    """Sunucudan gelen metin innerHTML'e girmesin: adres ve sertifika adi disaridan."""
    script = api_client.get("/static/js/settings.js").text
    body = script.split("function renderDiagnosis", 1)[1].split("async function diagnose", 1)[0]
    assert "innerHTML" not in body
    assert "textContent" in body


# --- e-posta karti ve posta kaynakli kartlar ----------------------------


def test_mail_card_is_on_the_settings_page(api_client):
    page = api_client.get("/settings").text
    for marker in (
        'id="mail-card"',
        ">E-posta<",
        'id="mail-enabled"',
        'id="mail-from"',
        'id="mail-to"',
        'id="mail-cc"',
        ">Kimden<",
        ">Kime<",
        ">CC<",
        "Gönderen bu adreslerden biriyse.",
        "Alıcılar arasında bu adreslerden biri varsa.",
        "CC'de bu adreslerden biri varsa.",
        'id="mail-days"',
        'id="mail-body-limit"',
        'id="mail-folders"',
        'id="mail-folders-fetch"',
        "Klasörleri getir",
        'id="mail-test"',
        'id="mail-scan"',
        "Şimdi tara",
        'id="mail-unsupported"',
        "Bu özellik yalnız Windows'ta Outlook ile çalışır.",
        "ornek@example.com",
    ):
        assert marker in page, marker
    # Depo aciktir: karttaki her ornek adres uydurma alan adinda olmali.
    import re

    card = page.split('id="mail-card"', 1)[1].split("</div>\n      </div>", 1)[0]
    for address in re.findall(r"[\w.*-]+@[\w.-]+", card):
        assert address.endswith("@example.com"), address


def test_mail_settings_script_saves_and_scans(api_client):
    script = api_client.get("/static/js/settings.js").text
    for marker in (
        "/api/mail/folders",
        "/api/mail/test",
        "/api/mail/scan",
        "mail.enabled",
        "mail.from_addresses",
        "mail.to_addresses",
        "mail.cc_addresses",
        "mail.folders",
        "mail.days",
        "mail.body_limit",
        "mail.scan_on_refresh",
        "mail_supported",
        "function renderFolders",
        "function collectMail",
    ):
        assert marker in script, marker


def test_the_folder_tree_is_built_from_nodes_not_html(api_client):
    """Klasor adlari Outlook'tan gelir: innerHTML'e girmemeli."""
    script = api_client.get("/static/js/settings.js").text
    body = script.split("function folderRow", 1)[1].split("async function saveMail", 1)[0]
    assert "innerHTML" not in body
    assert "textContent" in body


def test_the_board_can_scan_the_mail(api_client):
    page = api_client.get("/").text
    assert 'id="tasks-scan"' in page
    assert "E-postayı tara" in page
    script = api_client.get("/static/js/app.js").text
    for marker in (
        "/api/mail/scan",
        "open-mail",
        "function taskMailLine",
        "function mailSourceBox",
        "Outlook'ta aç",
        '"e-posta"',
        "Kimden: ",
        "mail_count",
        "görev e-postadan",
    ):
        assert marker in script, marker


def test_the_envelope_icon_is_our_own_drawing(api_client):
    common = api_client.get("/static/js/common.js").text
    assert "mail: [" in common
    # Emoji degil, kendi cizdigimiz SVG.
    assert "✉" not in common


def test_mail_card_styles_are_defined(api_client):
    css = api_client.get("/static/css/app.css").text
    for name in (
        ".task-card.is-mail",
        ".task-mail",
        ".mail-badge",
        ".mail-from",
        ".mail-count",
        ".mail-source",
        ".folder-tree",
        ".folder-row",
    ):
        assert name in css, name


def test_the_search_folder_root_cannot_be_ticked(api_client):
    """Outlook'un "Arama Klasörleri" başlığı gerçek klasör değil: seçilemez."""
    script = api_client.get("/static/js/settings.js").text
    assert "node.selectable === false" in script
    assert "selectable !== false" in script
    css = api_client.get("/static/css/app.css").text
    assert ".folder-row.is-virtual" in css
