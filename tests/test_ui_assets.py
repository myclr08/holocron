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


def test_jira_detail_field_picker_is_group_scoped_and_keeps_other_sections(api_client):
    home = api_client.get("/").text
    assert 'id="drawer-fields"' in home
    assert "Alanları seç" in home
    script = api_client.get("/static/js/app.js").text
    for marker in (
        'api("/api/jira/fields")',
        "detail_fields: detailFields",
        'text: "Hiçbiri"',
        'text: "Varsayılana dön"',
        'item.kind === "local" ? "Yerel alanlar" : "Jira alanları"',
        "state.activeId !== groupId || state.drawerKey !== drawerKey",
    ):
        assert marker in script
    assert "renderDrawerTeams(body);" in script


def test_local_detail_visibility_runs_in_the_real_javascript():
    """Gerçek render fonksiyonu null/[]/local:id ayrımını uygular."""
    import json
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("node yok")
    script = (STATIC / "js" / "app.js").read_text(encoding="utf-8")
    probe = r"""
const fakeNode = () => ({
  children: [], firstChild: null, classList: { add() {}, contains() { return false; } },
  appendChild(child) { this.children.push(child); this.firstChild ||= child; return child; },
  removeChild() { this.children.shift(); this.firstChild = this.children[0] || null; },
  setAttribute() {}, addEventListener() {}, focus() {},
});
globalThis.document = {
  addEventListener() {}, createElement() { return fakeNode(); },
  createTextNode(text) { return { textContent: text }; }, getElementById() { return fakeNode(); },
};
function count(selection) {
  state.group = { detail_fields: selection };
  state.drawerKey = "DEMO-1";
  state.drawerLocal = [
    { field: "local:1", name: "Not", type_label: "Metin", text: "saklı", value: "saklı", history: [] },
    { field: "local:2", name: "Puan", type_label: "Sayı", text: "5", value: "5", history: [] },
  ];
  const body = fakeNode();
  renderDrawerLocal(body);
  return body.children.length ? body.children[0].children.length - 1 : 0;
}
process.stdout.write(JSON.stringify([count(null), count([]), count(["local:2"])]));
"""
    setup, exercise = probe.split("function count", 1)
    result = subprocess.run(
        [node], input=setup + script + "\nfunction count" + exercise,
        capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == [2, 0, 1]


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

SCRIPTS = (
    "common.js",
    "duzelt.js",
    "app.js",
    "settings.js",
    "starfield.js",
    "addressbox.js",
    "mailsend.js",
    "mailsend-settings.js",
    "campaign.js",
    "campaign-settings.js",
)


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


def test_no_page_loads_two_scripts_with_the_same_global_name():
    """Ayni sayfadaki iki betik ayni ust duzey adi tanimlamasin.

    `campaign.js` `app.js`'ten sonra yuklendigi icin kendi `renderHistory`
    fonksiyonu digerini eziyordu: yerel alan gecmisi popover'i "Okunuyor..."
    yazisinda kaliyor, girdiler sessizce gizli sefer paneline cizilyordu.
    Hata firlamadigi icin `openHistory`'nin catch'i de hic calismiyordu.
    """
    import re
    from collections import defaultdict

    declaration = re.compile(
        r"^(?:async\s+)?(?:function|class)\s+([A-Za-z_$][\w$]*)"
        r"|^(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*="
    )
    for page in sorted(STATIC.glob("*.html")):
        scripts = re.findall(r'<script src="/static/js/([^"]+)"', page.read_text(encoding="utf-8"))
        seen: dict[str, list[str]] = defaultdict(list)
        for name in scripts:
            for line in (STATIC / "js" / name).read_text(encoding="utf-8").splitlines():
                found = declaration.match(line)
                if found:
                    seen[found.group(1) or found.group(2)].append(name)
        clashes = {key: files for key, files in seen.items() if len(files) > 1}
        assert not clashes, f"{page.name}: {clashes}"


def test_local_history_popover_renders_with_the_real_javascript():
    """Saat ikonuna basinca popover gercekten dolsun: "Okunuyor..." kalmasin.

    `openHistory` -> `renderHistory` zinciri sayfadaki butun betikler
    yuklendikten sonraki global adlarla calistirilir; boylece ad cakismasi
    tekrar olursa bu test duser.
    """
    import json
    import re
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("node yok")

    page = (STATIC / "index.html").read_text(encoding="utf-8")
    scripts = [
        name
        for name in re.findall(r'<script src="/static/js/([^"]+)"', page)
        if name in {"app.js", "campaign.js"}
    ]
    assert "app.js" in scripts and "campaign.js" in scripts

    shim = r"""
const nodes = {};
const fakeNode = (id) => ({
  id: id || "", hidden: false, style: {}, children: [], firstChild: null, textContent: "",
  disabled: false, className: "",
  classList: { add() {}, contains() { return false; } },
  appendChild(child) { this.children.push(child); this.firstChild ||= child; return child; },
  removeChild() { this.children.shift(); this.firstChild = this.children[0] || null; },
  setAttribute() {}, addEventListener() {}, focus() {},
  getBoundingClientRect() { return { left: 0, top: 0, bottom: 0, right: 0 }; },
  text() { return this.children.map((kid) => kid.textContent || kid.text()).join(" "); },
});
globalThis.window = { innerWidth: 1200, innerHeight: 800, addEventListener() {} };
globalThis.document = {
  addEventListener() {}, createElement() { return fakeNode(); },
  createTextNode(text) { return { textContent: text }; },
  getElementById(id) { return (nodes[id] ||= fakeNode(id)); },
};
globalThis.api = async () => ({
  entries: [
    { id: 2, old_text: "müşteriye soruldu", new_text: "çözüldü", changed_at: "2026-01-02T10:00:00+00:00" },
    { id: 1, old_text: "", new_text: "müşteriye soruldu", changed_at: "2026-01-01T09:00:00+00:00" },
  ],
});
"""
    probe = r"""
(async () => {
  await openHistory(document.getElementById("clock"), "DEMO-1", { id: 1, name: "Müşteri durumu" });
  const body = document.getElementById("history-body");
  process.stdout.write(JSON.stringify({ lines: body.children.length, text: body.text() }));
})();
"""
    source = "\n".join((STATIC / "js" / name).read_text(encoding="utf-8") for name in scripts)
    result = subprocess.run(
        [node], input=shim + source + probe, capture_output=True, text=True, check=True
    )
    report = json.loads(result.stdout)
    assert report["lines"] == 2, report
    assert "Okunuyor" not in report["text"], report
    assert "çözüldü" in report["text"], report


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


def test_the_drawer_can_be_widened_and_remembers_it(api_client):
    script = api_client.get("/static/js/common.js").text
    for marker in (
        'DRAWER_WIDTH_KEY = "holocron.drawer.width"',
        "DRAWER_DEFAULT_WIDTH = 440",
        "DRAWER_MIN_WIDTH = 360",
        "DRAWER_STEP = 20",
        "pointerdown",
        "pointermove",
        "pointerup",
        "setPointerCapture",
        "dblclick",
        "ArrowLeft",
        "ArrowRight",
        "localStorage.getItem",
        "localStorage.setItem",
        "function bindDrawerResize",
    ):
        assert marker in script, marker
    # Depolama kapaliysa sessizce gecilir.
    assert script.count("try {") >= 4
    # En fazla 90vw / 1400px.
    assert "Math.min(window.innerWidth * 0.9, 1400)" in script
    # Tutamak her cekmeceye takilir ve acilista uygulanir.
    assert "document.querySelectorAll(\".drawer\").forEach(addDrawerHandle)" in script
    assert "bindDrawerResize();" in script

    css = api_client.get("/static/css/app.css").text
    assert ".drawer-handle" in css
    assert "cursor: col-resize" in css
    assert "width: 6px" in css
    assert "var(--drawer-width" in css
    # Surüklerken gecis kapali.
    resizing = css.split("html.drawer-resizing .drawer {", 1)[1].split("}", 1)[0]
    assert "transition: none" in resizing
    assert "animation: none" in resizing


# --- Ayarlar duzeni: gruplar, dikey sekmeler, iki sutunlu alanlar -------
#
# Kullanici sikayeti (19 Eylul 2026): "ayarlar kismi cok karisik, sola
# sikisik". Sayfa artik gruplara bolunmus, genis ekranin tamamini kullaniyor
# ve her kartta alanlar iki sutuna diziliyor.

# Sekmelerin sirasi kullanicinin verdigi siradir; degistirmeden once sorulmali.
SETTINGS_GROUPS = (
    ("jira", "Jira bağlantısı"),
    ("ag", "Ağ"),
    ("kisiler", "Kişiler"),
    ("eposta", "E-posta"),
    ("teams", "Teams"),
    ("copilot", "Copilot"),
    ("sefer", "Sefer"),
    ("gorunum", "Görünüm"),
)


def test_the_settings_page_is_split_into_groups_in_order(api_client):
    page = api_client.get("/settings").text
    nav = page.split('id="settings-nav"', 1)[1].split("</nav>", 1)[0]
    for name, label in SETTINGS_GROUPS:
        assert f'data-group="{name}"' in nav, name
        assert f">{label}<" in nav, label
        assert f'id="grup-{name}"' in page, name
    # Sira menude kullanicinin istedigi gibi durmali.
    yerler = [nav.index(f'data-group="{name}"') for name, _ in SETTINGS_GROUPS]
    assert yerler == sorted(yerler)
    # Her grubun bir cumlelik tanimi var.
    assert page.count('class="group-lead"') == len(SETTINGS_GROUPS)


def test_every_card_sits_in_exactly_one_group(api_client):
    """Kart bir gruba ait olmali; grupsuz kart ekranda kaybolurdu."""
    page = api_client.get("/settings").text
    govde = page.split('class="settings-panes"', 1)[1]
    for kart in ("mail-card", "mailsend-card", "teams-card", "contacts-card",
                 "copilot-card", "campaign-card", "appearance", "net-card"):
        assert f'id="{kart}"' in govde, kart
    # Gruplarin disinda kart kalmadi.
    assert page.split('class="settings-panes"', 1)[0].count('class="card"') == 0


def test_the_contacts_group_owns_the_address_book(api_client):
    """Kullanici "Kişiler ayrı grup olsun" dedi: rehber Teams kartindan cikti."""
    page = api_client.get("/settings").text
    kisiler = page.split('id="grup-kisiler"', 1)[1].split("</section>", 1)[0]
    for marker in ('id="teams-contacts"', 'id="teams-gal"', "Rehberi Outlook'tan yenile",
                   'id="teams-gal-unsupported"', "Adres defteri"):
        assert marker in kisiler, marker
    # Teams grubunda artik yalnizca mesajlasma var.
    teams = page.split('id="grup-teams"', 1)[1].split("</section>", 1)[0]
    assert 'id="teams-templates"' in teams
    assert 'id="teams-contacts"' not in teams


def test_the_mail_group_carries_both_cards(api_client):
    page = api_client.get("/settings").text
    eposta = page.split('id="grup-eposta"', 1)[1].split("</section>", 1)[0]
    assert 'id="mail-card"' in eposta
    assert 'id="mailsend-card"' in eposta


def test_the_group_menu_is_sticky_and_the_page_uses_the_width(api_client):
    css = api_client.get("/static/css/app.css").text
    shell = css.split(".settings-shell {", 1)[1].split("}", 1)[0]
    assert "max-width: 1180px" in shell
    assert "margin: 0 auto" in shell
    assert "grid-template-columns: 208px minmax(0, 1fr)" in shell
    main = css.split(".settings-main {", 1)[1].split("}", 1)[0]
    assert "32px" in main
    nav = css.split(".settings-nav {", 1)[1].split("}", 1)[0]
    assert "position: sticky" in nav
    # Secili sekme sari serit tasir.
    on = css.split(".settings-tab.on {", 1)[1].split("}", 1)[0]
    assert "var(--accent)" in on


def test_card_fields_are_two_columns_and_collapse_on_narrow_screens(api_client):
    css = api_client.get("/static/css/app.css").text
    grid = css.split(".field-grid {", 1)[1].split("}", 1)[0]
    assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in grid
    dar = css.split("@media (max-width: 760px) {", 1)[1]
    assert ".field-grid { grid-template-columns: 1fr" in dar
    # Kaydet/sina dugmeleri kartin sag altinda.
    actions = css.split(".settings-panes .card .actions {", 1)[1].split("}", 1)[0]
    assert "justify-content: flex-end" in actions


def test_settings_headings_are_not_forced_to_uppercase(api_client):
    """Turkce metne `text-transform: uppercase` uygulanmaz."""
    css = api_client.get("/static/css/app.css").text
    baslik = css.split(".settings-panes .card h2 {", 1)[1].split("}", 1)[0]
    assert "text-transform: none" in baslik


def test_the_selected_group_is_deep_linked_and_remembered(api_client):
    script = api_client.get("/static/js/settings.js").text
    for marker in (
        'GROUP_KEY = "holocron.settings.group"',
        "function showGroup",
        "function bindGroups",
        "localStorage.getItem",
        "localStorage.setItem",
        "window.history.replaceState",
        "hashchange",
        'window.location.hash.replace("#", "")',
    ):
        assert marker in script, marker
    # Depolama kapali olabilir: okuma da yazma da try/catch icinde.
    gezinme = script.split("function rememberedGroup", 1)[1].split("function showGroup", 1)[0]
    assert gezinme.count("try {") == 2


def test_the_network_card_saves_and_diagnoses_into_its_own_box(api_client):
    page = api_client.get("/settings").text
    ag = page.split('id="grup-ag"', 1)[1].split("</section>", 1)[0]
    for marker in ('id="net-save"', 'id="net-diagnose"', 'id="net-status"', 'id="net-diagnosis"'):
        assert marker in ag, marker
    script = api_client.get("/static/js/settings.js").text
    assert 'saveSettings("net-status")' in script
    assert 'diagnose("net-status", "net-diagnosis")' in script


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


# --- nabiz: dondurulan sekme uygulamayi oldurmesin ----------------------


def test_heartbeat_chains_timeouts_and_wakes_with_the_tab(api_client):
    """setInterval dondurulmus sekmede birikip patliyordu; zincir + uyanma olaylari."""
    script = api_client.get("/static/js/common.js").text
    assert "setInterval(" not in script  # yalnizca yorumda anilir
    assert "scheduleHeartbeat" in script
    for marker in (
        'document.addEventListener("visibilitychange"',
        'window.addEventListener("focus"',
        'window.addEventListener("pageshow"',
    ):
        assert marker in script, marker
    # Uyanan sekmede odak + gorunurluk birlikte gelir: tek istege insmeli.
    assert "heartbeatInFlight" in script


def test_server_gone_banner_text_and_style(api_client):
    script = api_client.get("/static/js/common.js").text
    assert "Holocron kapanmış görünüyor. holocron.bat ile yeniden başlatıp sayfayı yenileyin." in script
    assert "HEARTBEAT_FAIL_LIMIT = 2" in script
    css = api_client.get("/static/css/app.css").text
    assert ".server-gone {" in css
    assert "html.server-gone-open body" in css
    assert "--offline-bar-height" in css


def test_server_gone_banner_runs_in_the_real_javascript():
    """Serit iki basarisiz nabizda cikar, sunucu donunce kalkar, kapatilabilir."""
    import json
    import shutil
    import subprocess

    node = shutil.which("node")
    if node is None:
        pytest.skip("node yok")
    script = (STATIC / "js" / "common.js").read_text(encoding="utf-8")
    probe = r"""
const fakeEl = (tag) => ({
  tag, id: "", className: "", type: "", title: "", textContent: "", hidden: false,
  children: [], attrs: {}, listeners: {},
  appendChild(child) { this.children.push(child); return child; },
  insertBefore(child) { this.children.unshift(child); return child; },
  setAttribute(key, value) { this.attrs[key] = value; },
  addEventListener(name, fn) { (this.listeners[name] = this.listeners[name] || []).push(fn); },
});
const made = [];
const body = fakeEl("body");
const classes = new Set();
globalThis.document = {
  body, hidden: false,
  documentElement: { classList: {
    add(name) { classes.add(name); }, remove(name) { classes.delete(name); },
    contains(name) { return classes.has(name); }, toggle() {},
  } },
  createElement(tag) { const el = fakeEl(tag); made.push(el); return el; },
  createElementNS(ns, tag) { return fakeEl(tag); },
  getElementById(id) { return made.find((el) => el.id === id) || null; },
  addEventListener() {}, querySelector() { return null; }, querySelectorAll() { return []; },
};
globalThis.window = { addEventListener() {}, innerWidth: 1200 };
let serverUp = false;
globalThis.fetch = async () => {
  if (!serverUp) throw new Error("baglanti yok");
  return { ok: true, json: async () => ({ ok: true }) };
};
async function probe() {
  const shown = () => {
    const bar = document.getElementById("server-gone");
    return !!bar && bar.hidden === false && classes.has("server-gone-open");
  };
  const steps = [];
  await beat();
  steps.push(shown());            // 1. hata: serit yok
  await beat();
  steps.push(shown());            // 2. hata: serit var
  serverUp = true;
  await beat();
  steps.push(shown());            // sunucu dondu: serit kalkti
  serverUp = false;
  await beat();
  await beat();
  steps.push(shown());            // yine dustu: serit geri geldi
  const bar = document.getElementById("server-gone");
  bar.children.find((child) => child.tag === "button").listeners.click[0]();
  steps.push(shown());            // kullanici kapatti
  await beat();
  steps.push(shown());            // kapatilan serit geri gelmez
  stopHeartbeat();
  process.stdout.write(JSON.stringify(steps));
}
probe();
"""
    setup, exercise = probe.split("async function probe", 1)
    result = subprocess.run(
        [node], input=setup + script + "\nasync function probe" + exercise,
        capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == [False, True, False, True, False, False]


# --- "Düzelt": alanin kosesindeki dugme ---------------------------------
#
# Tasarim (kullanici onayi): Gorevlerim'deki Aciklama/Not alanlari ve Jira
# kaydi cekmecesindeki cok satirli yerel alanlar. Davranis testleri
# `tests/test_copilot_duzelt.py` icinde gercek JavaScript ile kosuyor; burada
# yalnizca kancalar ve metin dili denetleniyor.


def test_the_fix_component_is_loaded_on_the_main_page(api_client):
    page = api_client.get("/").text
    assert '<script src="/static/js/duzelt.js"></script>' in page
    # Bilesen `app.js`ten ONCE yuklenir: pencere cizilirken hazir olmali.
    assert page.index("duzelt.js") < page.index("js/app.js")


def test_the_fix_script_carries_the_button_the_states_and_the_shortcut(api_client):
    script = api_client.get("/static/js/duzelt.js").text
    for marker in (
        "function attachDuzelt",
        "function duzeltFark",
        "function duzeltAyarla",
        '"/api/copilot/duzelt"',
        '"Düzelt"',
        '"Düzeltiliyor…"',
        '"Düzeltme önerisi"',
        '"Vazgeç"',
        '"Yeniden dene"',
        '"Uygula"',
        '"Copilot ayarlı değil"',
        "Ayarlar'da sına",
        '"Düzeltme uygulandı"',
        "Geri al: Ctrl+Z",
        "DUZELT_SINIRI = 4000",
    ):
        assert marker in script, marker
    # Kisayol Ctrl+Shift+D, geri alma Ctrl+Z.
    assert 'event.ctrlKey && event.shiftKey && (event.key === "D"' in script
    # Once tarayicinin KENDI geri alma yigini denenir.
    assert 'document.execCommand("insertText"' in script
    # Cipler: ikisi varsayilan acik, ikisi istege bagli.
    assert '{ id: "imla", ad: "İmla ve noktalama", varsayilan: true }' in script
    assert '{ id: "kisa", ad: "Kısalt", varsayilan: false }' in script


def test_the_fix_button_is_wired_into_the_task_form_and_the_local_fields(api_client):
    script = api_client.get("/static/js/app.js").text
    assert "function duzeltKutusu" in script
    # Gorev penceresi: alanin kendisi degil, sarmalayici kutusu konur.
    assert 'field("Açıklama", descBox)' in script
    assert 'field("Not", noteBox)' in script
    # Jira kaydi cekmecesi: yerel metin alani cok satirli acilir.
    assert "{ multiline: true }" in script
    assert 'class: "local-input local-area"' in script
    assert "value.appendChild(editor.box);" in script
    # Panel aciksa odak kaybi kaydi tetiklemesin.
    assert "if (node.duzeltAktif) return;" in script


def test_the_fix_styles_exist_and_never_shout_in_turkish(api_client):
    css = api_client.get("/static/css/app.css").text
    for name in (
        ".fix-wrap",
        ".fix {",
        ".fix.on",
        ".fix.busy",
        ".fix-spin",
        ".fix-error",
        ".suggest {",
        ".suggest-meta",
        ".cmp {",
        ".cmp del",
        ".cmp ins",
        ".suggest .chip",
        ".local-input.local-area",
    ):
        assert name in css, name
    # Donen halka hareket tercihine saygi duyar.
    assert "@keyframes fix-spin" in css
    donen = css.split(".fix-spin {", 1)[1]
    assert "animation: none" in donen
    # Turkce metne `text-transform: uppercase` uygulanmaz.
    for blok in (".fix {", ".cmp h4 {", ".suggest .chip {"):
        govde = css.split(blok, 1)[1].split("}", 1)[0]
        assert "text-transform: none" in govde, blok
    # Bilesenin butun bloklari: hicbirinde buyuk harfe zorlama yok.
    bolum = css.split("/* --- \"Düzelt\"", 1)[1]
    assert "text-transform: uppercase" not in bolum


def test_the_fix_settings_are_on_the_copilot_card(api_client):
    page = api_client.get("/settings").text
    kart = page.split('id="copilot-card"', 1)[1].split("</section>", 1)[0]
    for marker in (
        ">Metin düzeltme<",
        'id="copilot-duzelt-acik"',
        'id="copilot-duzelt-ton"',
        'value="notr"',
        'value="resmi"',
        'id="copilot-duzelt-sablon"',
        'id="copilot-duzelt-varsayilan"',
        'id="copilot-duzelt-save"',
        "Ctrl+Shift+D",
    ):
        assert marker in kart, marker
    script = api_client.get("/static/js/settings.js").text
    for marker in (
        "copilot.duzelt_acik",
        "copilot.duzelt_ton",
        "copilot.duzelt_sablon",
        "copilot_duzelt_sablon_varsayilan",
        "function collectDuzelt",
        "function resetDuzeltTemplate",
    ):
        assert marker in script, marker

