"""Grid hucresi (tek satirlik yerel alan duzenleyicisi): Teams yapistirma.

Baglam (Mustafa, 20 Eyl 2026): "satır içi metin düzeltme yaparken
yapıştırdığım linke bir şey yapmıyor" — filo tablosundaki tek satirlik yerel
alan hucresine (app.js -> `localEditor`, `field.type === "text"` dalı) Teams
bloğu yapıştırılınca hiçbir şey olmuyordu; yalnızca çok satırlı alanlar
(`zenginalan.js`) ve "Düzelt" textarea yedeği kapsanmıştı.

Bu dosya `attachTeamsLink`'in tek satırlık `input`'a da bağlandığını, blok
dışı satır sonlarının boşluğa indiğini, kaydedilen değerin belirteç
(`[[teams:…|…]]`) olduğunu ve kaydedilen hucrenin salt-okunur çizimde çip
gösterdiğini GERÇEK `app.js`/`teamslink.js` ile (node altında) doğrular.

Buradaki kimlikler UYDURMADIR (depo aciktir).
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"

KIRACI = "00000000-0000-4000-8000-000000000001"
ADRES = (
    "https://teams.microsoft.com/l/message/"
    "19:2f1c9a7b4d8e4f0b9c3a5d6e7f801234@unq.gbl.spaces/1789557243396"
    f"?groupId=&parentMessageId=1789557243396&tenantId={KIRACI}"
    "&context=%7B%22contextType%22%3A%22chat%22%7D"
)
GONDEREN = "Deniz Akgün-Örnek Bank-Kredi Sistemleri-Yazılım Mühendisi"
ON_SATIR = (
    f"{GONDEREN} | {GONDEREN} 2 sohbetinde gönderildi, "
    "gönderme zamanı: Eyl 16, 2026, 14:14"
)
BELIRTEC = f"[[teams: Deniz Akgün · 16 Eyl 2026 14:14|{ADRES}]]"

# --- kucuk taklit DOM: tek satirlik `input` de kapsar --------------------
#
# `zenginalan.js`/`teamslink.js` testlerindeki (tests/test_zenginalan.py)
# taklidin ayni ailesi; buraya `selectionStart`/`selectionEnd`/
# `setSelectionRange` eklendi, cunku `teamsLinkYaz`in yedek yolu bunlari
# `<input>` uzerinde okur/yazar (execCommand burada hep `false` doner).

SHIM = r"""
function gKardes(dugum, yon) {
  const ust = dugum.parentNode;
  if (!ust) return null;
  const yer = ust.children.indexOf(dugum);
  if (yer < 0) return null;
  return ust.children[yer + yon] || null;
}

function gMetin(deger) {
  return {
    nodeType: 3, nodeValue: String(deger), children: [], parentNode: null,
    get textContent() { return this.nodeValue; },
    set textContent(v) { this.nodeValue = String(v); },
    get firstChild() { return null; },
    get nextSibling() { return gKardes(this, 1); },
    get previousSibling() { return gKardes(this, -1); },
    yazi() { return this.nodeValue; },
  };
}

function gEleman(tag) {
  const siniflar = new Set();
  const node = {
    nodeType: 1, tag: tag, tagName: String(tag).toUpperCase(),
    children: [], parentNode: null, attrs: {}, listeners: {}, style: {}, dataset: {},
    hidden: false, disabled: false, title: "", id: "", value: "", checked: false,
    selectionStart: 0, selectionEnd: 0,
    get childNodes() { return this.children; },
    get firstChild() { return this.children[0] || null; },
    get nextSibling() { return gKardes(this, 1); },
    get previousSibling() { return gKardes(this, -1); },
    get textContent() {
      return this.children.map((kid) => kid.textContent || "").join("");
    },
    set textContent(deger) {
      this.children.splice(0, this.children.length);
      if (deger !== "" && deger !== null && deger !== undefined) {
        this.appendChild(gMetin(deger));
      }
    },
    classList: {
      add(...adlar) { adlar.forEach((ad) => siniflar.add(ad)); },
      remove(...adlar) { adlar.forEach((ad) => siniflar.delete(ad)); },
      contains(ad) { return siniflar.has(ad); },
      toggle(ad, acik) {
        const yeni = acik === undefined ? !siniflar.has(ad) : !!acik;
        if (yeni) siniflar.add(ad);
        else siniflar.delete(ad);
        return yeni;
      },
    },
    siniflari() { return Array.from(siniflar); },
    appendChild(kid) {
      if (kid.tag === "#fragment") {
        kid.children.slice().forEach((ic) => this.appendChild(ic));
        kid.children.splice(0, kid.children.length);
        return kid;
      }
      if (kid.parentNode) kid.parentNode.removeChild(kid);
      kid.parentNode = this;
      this.children.push(kid);
      return kid;
    },
    removeChild(kid) {
      const yer = this.children.indexOf(kid);
      if (yer >= 0) this.children.splice(yer, 1);
      kid.parentNode = null;
      return kid;
    },
    setAttribute(ad, deger) {
      this.attrs[ad] = String(deger);
      if (ad === "value") this.value = String(deger);
    },
    getAttribute(ad) {
      return Object.prototype.hasOwnProperty.call(this.attrs, ad) ? this.attrs[ad] : null;
    },
    removeAttribute(ad) { delete this.attrs[ad]; },
    addEventListener(tur, fn) { (this.listeners[tur] = this.listeners[tur] || []).push(fn); },
    dispatchEvent(olay) {
      (this.listeners[olay.type] || []).forEach((fn) => fn(olay));
      return true;
    },
    at(tur, ek) {
      const olay = Object.assign(
        { type: tur, target: this, preventDefault() { this.onlendi = true; }, stopPropagation() {} },
        ek || {}
      );
      (this.listeners[tur] || []).forEach((fn) => fn(olay));
      return olay;
    },
    setSelectionRange(bas, son) { this.selectionStart = bas; this.selectionEnd = son; },
    focus() {}, blur() {}, select() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
    yazi() { return this.children.map((kid) => (kid.yazi ? kid.yazi() : "")).join(""); },
  };
  Object.defineProperty(node, "className", {
    get: () => Array.from(siniflar).join(" "),
    set: (deger) => {
      siniflar.clear();
      String(deger || "").split(/\s+/).filter(Boolean).forEach((ad) => siniflar.add(ad));
    },
  });
  return node;
}

const gDugumler = {};
globalThis.document = {
  createElement: (tag) => gEleman(tag),
  createElementNS: (ns, tag) => gEleman(tag),
  createTextNode: (deger) => gMetin(deger),
  createDocumentFragment: () => gEleman("#fragment"),
  execCommand: () => false,
  addEventListener() {},
  querySelectorAll: () => [],
  querySelector: () => null,
  getElementById(id) { return (gDugumler[id] = gDugumler[id] || gEleman("div")); },
  body: gEleman("body"),
  hidden: false,
};
globalThis.window = {
  open() {}, location: {}, innerWidth: 1200, innerHeight: 800,
  addEventListener() {}, getSelection: () => null,
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  matchMedia: () => ({ matches: false, addEventListener() {} }),
};
globalThis.localStorage = { getItem: () => null, setItem() {}, removeItem() {} };
globalThis.Event = function (tur, ek) {
  this.type = tur;
  Object.assign(this, ek || {});
};

/** Cip: metin dugumu disindaki `teams-msg` sinifli dugum. */
function gCipBul(dugum) {
  if (dugum.siniflari && dugum.siniflari().includes("teams-msg")) return dugum;
  for (const kid of dugum.children || []) {
    const bulunan = gCipBul(kid);
    if (bulunan) return bulunan;
  }
  return null;
}
"""


def _kos(govde: str) -> object:
    """`teamslink.js` + `app.js` GERCEK halleriyle node altinda kosturulur."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node yok")
    kaynak = "\n".join(
        (STATIC / "js" / ad).read_text(encoding="utf-8") for ad in ("teamslink.js", "app.js")
    )
    sonuc = subprocess.run([node], input=SHIM + kaynak + govde, capture_output=True, text=True)
    assert sonuc.returncode == 0, sonuc.stderr
    return json.loads(sonuc.stdout)


# --- yapistirma: blok tek satira doner, imlec yerine geçer ---------------


def test_pasting_a_teams_block_into_the_grid_cell_input_becomes_one_line_token():
    veri = _kos(
        "const sonuclar = {};"
        "const editor = localEditor({ type: 'text', name: 'Not' }, '', {"
        "  onSave: (v) => { sonuclar.saved = v; },"
        "  onCancel: () => { sonuclar.cancelled = true; },"
        "});"
        "editor.node.at('paste', { clipboardData: { getData: () => "
        + json.dumps(f"{ON_SATIR}\n\n{ADRES}")
        + " } });"
        "sonuclar.value = editor.node.value;"
        "sonuclar.tekSatir = !sonuclar.value.includes('\\n');"
        "sonuclar.imlecSonda = editor.node.selectionStart === editor.node.value.length"
        "  && editor.node.selectionEnd === editor.node.value.length;"
        "process.stdout.write(JSON.stringify(sonuclar));"
    )
    assert veri["value"] == BELIRTEC
    assert veri["tekSatir"] is True
    assert veri["imlecSonda"] is True
    assert "saved" not in veri
    assert "cancelled" not in veri


def test_lines_outside_the_teams_block_collapse_to_a_single_space():
    """Blok dısındaki (ayrı yapıştırılan başka satırların) satır sonları boşluğa iner."""
    pano = f"önce bir satır\n{ON_SATIR}\n\n{ADRES}\nsonra bir satır"
    veri = _kos(
        "const editor = localEditor({ type: 'text', name: 'Not' }, '', {"
        "  onSave: () => {}, onCancel: () => {},"
        "});"
        "editor.node.at('paste', { clipboardData: { getData: () => " + json.dumps(pano) + " } });"
        "process.stdout.write(JSON.stringify({ value: editor.node.value }));"
    )
    assert "\n" not in veri["value"]
    assert veri["value"] == f"önce bir satır {BELIRTEC} sonra bir satır"


def test_a_plain_paste_without_a_teams_link_is_left_to_the_browser():
    """Panoda Teams baglantisi yoksa olaya karisilmaz: `preventDefault` cagrilmaz."""
    veri = _kos(
        "const editor = localEditor({ type: 'text', name: 'Not' }, '', {"
        "  onSave: () => {}, onCancel: () => {},"
        "});"
        "const olay = editor.node.at('paste', { clipboardData: { getData: () => 'düz metin' } });"
        "process.stdout.write(JSON.stringify({"
        "  value: editor.node.value, onlendi: olay.onlendi === true,"
        "}));"
    )
    assert veri == {"value": "", "onlendi": False}


def test_attaching_twice_is_idempotent():
    """Ayni hucreye ikinci `attachTeamsLink` cagrisi zararsizdir (bkz. teamslink.js)."""
    veri = _kos(
        "const editor = localEditor({ type: 'text', name: 'Not' }, '', {"
        "  onSave: () => {}, onCancel: () => {},"
        "});"
        "attachTeamsLink(editor.node, { tekSatir: true });"
        "attachTeamsLink(editor.node, { tekSatir: true });"
        "const sayi = (editor.node.listeners.paste || []).length;"
        "editor.node.at('paste', { clipboardData: { getData: () => "
        + json.dumps(f"{ON_SATIR}\n\n{ADRES}")
        + " } });"
        "process.stdout.write(JSON.stringify({ dinleyici: sayi, deger: editor.node.value }));"
    )
    assert veri["dinleyici"] == 1
    assert veri["deger"] == BELIRTEC


# --- kaydetme: Enter/odak kaybi -> belirtecli deger ----------------------


def test_pressing_enter_saves_the_token_as_the_field_value():
    veri = _kos(
        "const sonuclar = {};"
        "const editor = localEditor({ type: 'text', name: 'Not' }, '', {"
        "  onSave: (v) => { sonuclar.saved = v; }, onCancel: () => {},"
        "});"
        "editor.node.at('paste', { clipboardData: { getData: () => "
        + json.dumps(f"{ON_SATIR}\n\n{ADRES}")
        + " } });"
        "editor.node.at('keydown', { key: 'Enter' });"
        "process.stdout.write(JSON.stringify(sonuclar));"
    )
    assert veri["saved"] == BELIRTEC


def test_losing_focus_saves_the_token_as_the_field_value():
    veri = _kos(
        "const sonuclar = {};"
        "const editor = localEditor({ type: 'text', name: 'Not' }, '', {"
        "  onSave: (v) => { sonuclar.saved = v; }, onCancel: () => {},"
        "});"
        "editor.node.at('paste', { clipboardData: { getData: () => "
        + json.dumps(f"{ON_SATIR}\n\n{ADRES}")
        + " } });"
        "editor.node.at('blur');"
        "process.stdout.write(JSON.stringify(sonuclar));"
    )
    assert veri["saved"] == BELIRTEC


def test_number_fields_are_not_wired_to_teams_paste():
    """Sayi alaninda da tek satirlik `input` kullanilir ama Teams cevirisi gerekmez."""
    veri = _kos(
        "const editor = localEditor({ type: 'number', name: 'Tutar' }, '', {"
        "  onSave: () => {}, onCancel: () => {},"
        "});"
        "process.stdout.write(JSON.stringify({ bagli: !!editor.node.teamsLinkBagli }));"
    )
    assert veri == {"bagli": False}


# --- gosterim: kaydedilen belirtec salt-okunur hucrede cip olur ---------


def test_the_saved_token_is_drawn_as_a_chip_in_the_read_only_cell():
    veri = _kos(
        "const span = document.createElement('span');"
        "metinCiz(span, " + json.dumps(BELIRTEC) + ");"
        "const cip = gCipBul(span);"
        "process.stdout.write(JSON.stringify({"
        "  cip: !!cip,"
        "  yazi: cip ? cip.yazi() : '',"
        "  belirtec: cip ? cip.attrs['data-teams'] || cip.getAttribute('href') : null,"
        "}));"
    )
    assert veri["cip"] is True
    assert "Deniz Akgün" in veri["yazi"]


def test_a_local_cell_row_renders_the_chip_via_render_local_cell():
    """`renderLocalCell` gercek hucre agacini kurar; salt-okunur metin cip icerir."""
    veri = _kos(
        "const td = document.createElement('td');"
        "const row = { key: 'PRJ-1' };"
        "const cell = { text: " + json.dumps(BELIRTEC) + ", raw: " + json.dumps(BELIRTEC) + ", changes: 0 };"
        "const column = { local: { type: 'text', name: 'Not', track_history: false } };"
        "renderLocalCell(td, row, cell, column);"
        "const cip = gCipBul(td);"
        "process.stdout.write(JSON.stringify({ cip: !!cip, hucreSinif: td.siniflari() }));"
    )
    assert veri["cip"] is True
    assert "local-cell" in veri["hucreSinif"]
