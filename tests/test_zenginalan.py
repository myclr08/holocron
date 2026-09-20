"""Zengin alan: Teams belirtecinin CANLI cipe donustugu duzenleyici.

Tasarim (kullanici onayi, 20 Eylul 2026): "yapistirir yapistirmaz cip olsun,
kaydetmeyi beklemeyeyim". Gorev Aciklama/Son durum/Not alanlari ve Jira
cekmecesindeki cok satirli yerel alanlar artik textarea degil, atomik cip
tasiyan bir `contenteditable` kutudur (`app/static/js/zenginalan.js`).

Kayit bicimi DEGISMEZ: kutu metni yine `[[teams: … |https://…]]` diye
serilestirir. Buradaki butun kimlikler UYDURMADIR (depo aciktir).

Testler betikleri GERCEK haliyle node altinda kosturur; DOM yerine bu
dosyadaki kucuk taklit kullanilir.
"""

from __future__ import annotations

import json
import re
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
KISA_ETIKET = "Deniz Akgün · 16 Eyl 2026 14:14"


# --- taklit DOM ---------------------------------------------------------

SHIM = r"""
function zAKardes(dugum, yon) {
  const ust = dugum.parentNode;
  if (!ust) return null;
  const yer = ust.children.indexOf(dugum);
  if (yer < 0) return null;
  return ust.children[yer + yon] || null;
}

function zAMetin(deger) {
  return {
    nodeType: 3, nodeValue: String(deger), children: [], parentNode: null,
    get textContent() { return this.nodeValue; },
    set textContent(v) { this.nodeValue = String(v); },
    get firstChild() { return null; },
    get nextSibling() { return zAKardes(this, 1); },
    get previousSibling() { return zAKardes(this, -1); },
    yazi() { return this.nodeValue; },
    bul() { return null; },
    dugmeler() { return []; },
    dokum() { return { tag: "#text", sinif: "", metin: this.nodeValue, attrs: {}, cocuklar: [] }; },
  };
}

function zAEleman(tag) {
  const siniflar = new Set();
  const node = {
    nodeType: 1, tag: tag, tagName: String(tag).toUpperCase(),
    children: [], parentNode: null, attrs: {}, listeners: {}, style: {}, dataset: {},
    hidden: false, disabled: false, title: "", id: "", value: "", checked: false,
    get childNodes() { return this.children; },
    get firstChild() { return this.children[0] || null; },
    get nextSibling() { return zAKardes(this, 1); },
    get previousSibling() { return zAKardes(this, -1); },
    get textContent() {
      return this.children.map((kid) => kid.textContent || "").join("");
    },
    set textContent(deger) {
      this.children.splice(0, this.children.length);
      if (deger !== "" && deger !== null && deger !== undefined) {
        this.appendChild(zAMetin(deger));
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
    insertBefore(kid, ref) {
      if (kid.parentNode) kid.parentNode.removeChild(kid);
      const yer = this.children.indexOf(ref);
      this.children.splice(yer < 0 ? this.children.length : yer, 0, kid);
      kid.parentNode = this;
      return kid;
    },
    setAttribute(ad, deger) {
      this.attrs[ad] = String(deger);
      // Gercek DOM'da `value` niteligi alanin degerini de kurar.
      if (ad === "value") this.value = String(deger);
    },
    getAttribute(ad) { return Object.prototype.hasOwnProperty.call(this.attrs, ad) ? this.attrs[ad] : null; },
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
    focus() {}, blur() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
    yazi() { return this.children.map((kid) => (kid.yazi ? kid.yazi() : "")).join(""); },
    bul(sinif) {
      for (const kid of this.children) {
        if (kid.siniflari && kid.siniflari().includes(sinif)) return kid;
        const ic = kid.bul ? kid.bul(sinif) : null;
        if (ic) return ic;
      }
      return null;
    },
    hepsi(sinif) {
      const liste = this.siniflari && this.siniflari().includes(sinif) ? [this] : [];
      this.children.forEach((kid) => liste.push(...(kid.hepsi ? kid.hepsi(sinif) : [])));
      return liste;
    },
    dugmeler() {
      const liste = this.tag === "button" ? [this] : [];
      this.children.forEach((kid) => liste.push(...(kid.dugmeler ? kid.dugmeler() : [])));
      return liste;
    },
    dokum() {
      return {
        tag: this.tag, sinif: this.className, metin: this.yazi(), attrs: this.attrs,
        cocuklar: this.children.map((kid) => (kid.dokum ? kid.dokum() : null)),
      };
    },
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

const zADugumler = {};
globalThis.document = {
  createElement: (tag) => zAEleman(tag),
  createElementNS: (ns, tag) => zAEleman(tag),
  createTextNode: (deger) => zAMetin(deger),
  createDocumentFragment: () => zAEleman("#fragment"),
  execCommand: () => false,
  addEventListener() {},
  querySelectorAll: () => [],
  querySelector: () => null,
  getElementById(id) { return (zADugumler[id] = zADugumler[id] || zAEleman("div")); },
  body: zAEleman("body"),
  hidden: false,
};
globalThis.window = {
  open() {}, location: {}, innerWidth: 1200, innerHeight: 800,
  addEventListener() {}, getSelection: () => null,
  localStorage: { getItem: () => null, setItem() {}, removeItem() {} },
  matchMedia: () => ({ matches: false, addEventListener() {} }),
};
globalThis.Event = function (tur, ek) {
  this.type = tur;
  Object.assign(this, ek || {});
};
globalThis.setTimeout = globalThis.setTimeout;

/** Imleci bir dugumun icine koyar (Backspace/Delete sinamalari icin). */
function zAImlec(kap, yer) {
  window.getSelection = () => ({
    rangeCount: 1,
    getRangeAt: () => ({ collapsed: true, startContainer: kap, startOffset: yer }),
    removeAllRanges() {}, addRange() {},
  });
}

/** Alanin icindeki cipler. */
function zACipler(alan) {
  return alan.hepsi("teams-msg");
}

const bekle = () => new Promise((cozul) => setTimeout(cozul, 0));
"""


def _kos(betikler: tuple[str, ...], govde: str) -> object:
    """Verilen betikleri GERCEK halleriyle node altinda kosturur."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node yok")
    kaynak = "\n".join((STATIC / "js" / ad).read_text(encoding="utf-8") for ad in betikler)
    sonuc = subprocess.run(
        [node], input=SHIM + kaynak + govde, capture_output=True, text=True
    )
    assert sonuc.returncode == 0, sonuc.stderr
    return json.loads(sonuc.stdout)


def _alan(govde: str) -> object:
    return _kos(("teamslink.js", "zenginalan.js"), govde)


# --- serilestirme: DOM <-> metin ----------------------------------------

ORNEK_METINLER: tuple[str, ...] = (
    "",
    "tek satır",
    "üst satır\nalt satır",
    "önce\n\nsonra",
    f"Kaynak mesaj: {BELIRTEC}",
    f"{BELIRTEC} baştaki",
    f"iki {BELIRTEC} ve {BELIRTEC} tane",
    f"bir satır\n{BELIRTEC}\nson satır",
    "sonda satır sonu\n",
)


def test_the_editor_round_trips_text_and_dom():
    """`degerYaz` -> `deger`: metin, satir sonlari ve belirtecler aynen doner."""
    veri = _alan(
        "const alan = zenginAlanYap({});"
        "process.stdout.write(JSON.stringify("
        + json.dumps(list(ORNEK_METINLER))
        + ".map((metin) => { alan.degerYaz(metin); return alan.deger(); })));"
    )
    assert veri == list(ORNEK_METINLER)


def test_the_value_property_behaves_like_a_textarea():
    """`attachDuzelt` ve kaydetme akisi `value` uzerinden calisiyor."""
    veri = _alan(
        "const alan = zenginAlanYap({ deger: " + json.dumps(f"not {BELIRTEC}") + " });"
        "const ilk = alan.value;"
        "alan.value = 'yeni metin';"
        "process.stdout.write(JSON.stringify({ ilk: ilk, sonra: alan.value }));"
    )
    assert veri["ilk"] == f"not {BELIRTEC}"
    assert veri["sonra"] == "yeni metin"


def test_a_browser_made_br_or_div_is_read_as_a_line_break():
    """Tarayici Enter'i `br` ya da `div` ile karsilarsa metin yine dogru.

    Son `br` DOLGUDUR (tarayici son satiri gostermek icin koyar): metne
    fazladan satir sonu eklemez.
    """
    veri = _alan(
        """
const kur = (ic) => {
  const alan = zenginAlanYap({});
  while (alan.firstChild) alan.removeChild(alan.firstChild);
  ic(alan);
  return zenginAlanOku(alan);
};
process.stdout.write(JSON.stringify({
  br: kur((alan) => {
    alan.appendChild(document.createTextNode("üst"));
    alan.appendChild(document.createElement("br"));
    alan.appendChild(document.createTextNode("alt"));
  }),
  dolgu: kur((alan) => {
    alan.appendChild(document.createTextNode("üst"));
    alan.appendChild(document.createElement("br"));
  }),
  blok: kur((alan) => {
    alan.appendChild(document.createTextNode("üst"));
    const blok = document.createElement("div");
    blok.appendChild(document.createTextNode("alt"));
    alan.appendChild(blok);
  }),
}));
"""
    )
    assert veri == {"br": "üst\nalt", "dolgu": "üst", "blok": "üst\nalt"}


# --- yapistirma: blok -> CIP (kaydetmeden) ------------------------------


def test_pasting_a_teams_block_makes_a_chip_right_away():
    veri = _alan(
        "const alan = zenginAlanYap({});"
        "alan.at('paste', { clipboardData: { getData: () => "
        + json.dumps(f"{ON_SATIR}\n\n{ADRES}")
        + " } });"
        "const cipler = zACipler(alan);"
        "process.stdout.write(JSON.stringify({"
        "  deger: alan.deger(),"
        "  sayi: cipler.length,"
        "  etiket: cipler[0] ? cipler[0].yazi() : '',"
        "  duzenlenemez: cipler[0] ? cipler[0].attrs['contenteditable'] : null,"
        "  surtuklenmez: cipler[0] ? cipler[0].attrs['draggable'] : null,"
        "  belirtec: cipler[0] ? cipler[0].attrs['data-teams'] : null,"
        "}));"
    )
    # Kayit bicimi degismedi: alanin degeri yine belirtecli duz metin.
    assert veri["deger"] == BELIRTEC + " "
    assert veri["sayi"] == 1
    # Sohbet adi gondereni tekrar ediyordu: etikette yok.
    assert veri["etiket"] == KISA_ETIKET
    # Cip ATOMIK: imlec icine girmez, surtuklenmez.
    assert veri["duzenlenemez"] == "false"
    assert veri["surtuklenmez"] == "false"
    assert veri["belirtec"] == BELIRTEC


def test_a_paste_without_a_teams_link_stays_plain_text():
    veri = _alan(
        "const alan = zenginAlanYap({});"
        "alan.at('paste', { clipboardData: { getData: () => 'düz metin ve https://ornek.local/x' } });"
        "process.stdout.write(JSON.stringify({ deger: alan.deger(), cip: zACipler(alan).length }));"
    )
    assert veri == {"deger": "düz metin ve https://ornek.local/x", "cip": 0}


def test_pasted_html_is_reduced_to_text_and_never_becomes_markup():
    veri = _alan(
        "const alan = zenginAlanYap({});"
        "const olay = alan.at('paste', { clipboardData: { getData: (tur) =>"
        " tur === 'text/plain' ? '<b>kalın</b> <script>alert(1)</script>' : '<b>kalın</b>' } });"
        "process.stdout.write(JSON.stringify({"
        "  deger: alan.deger(), onlendi: olay.onlendi === true,"
        "  cocuk: alan.children.map((kid) => kid.nodeType),"
        "}));"
    )
    # Tarayicinin kendi yapistirmasi ONLENIR: yalnizca metin girer.
    assert veri["onlendi"] is True
    assert veri["deger"] == "<b>kalın</b> <script>alert(1)</script>"
    assert veri["cocuk"] == [3]


# --- cip atomik: tek Backspace, tek Delete, menu -------------------------


def test_one_backspace_deletes_the_whole_chip():
    veri = _alan(
        "const alan = zenginAlanYap({ deger: " + json.dumps(f"not {BELIRTEC}") + " });"
        # Imlec cipin hemen ardinda (alanin son cocugundan sonra).
        "zAImlec(alan, alan.children.length);"
        "const olay = alan.at('keydown', { key: 'Backspace' });"
        "process.stdout.write(JSON.stringify({"
        "  deger: alan.deger(), cip: zACipler(alan).length, onlendi: olay.onlendi === true,"
        "}));"
    )
    assert veri == {"deger": "not ", "cip": 0, "onlendi": True}


def test_one_delete_removes_the_chip_in_front_of_the_caret():
    veri = _alan(
        "const alan = zenginAlanYap({ deger: " + json.dumps(f"{BELIRTEC} sonu") + " });"
        "zAImlec(alan, 0);"
        "alan.at('keydown', { key: 'Delete' });"
        "process.stdout.write(JSON.stringify({ deger: alan.deger(), cip: zACipler(alan).length }));"
    )
    assert veri == {"deger": " sonu", "cip": 0}


def test_backspace_in_the_middle_of_text_is_left_to_the_browser():
    veri = _alan(
        "const alan = zenginAlanYap({ deger: 'yazı' });"
        "zAImlec(alan.firstChild, 2);"
        "const olay = alan.at('keydown', { key: 'Backspace' });"
        "process.stdout.write(JSON.stringify({ deger: alan.deger(), onlendi: olay.onlendi === true }));"
    )
    assert veri == {"deger": "yazı", "onlendi": False}


def test_clicking_a_chip_opens_a_small_menu_that_can_remove_it():
    veri = _alan(
        "const alan = zenginAlanYap({ deger: " + json.dumps(f"not {BELIRTEC}") + " });"
        "const cip = zACipler(alan)[0];"
        "alan.at('click', { target: cip });"
        "const menu = document.body.children[document.body.children.length - 1];"
        "const yazilar = menu.dugmeler().map((d) => d.textContent);"
        "menu.dugmeler()[1].at('click');"
        "process.stdout.write(JSON.stringify({"
        "  yazilar: yazilar, deger: alan.deger(), acik: document.body.children.length,"
        "}));"
    )
    assert veri["yazilar"] == ["Teams'te aç", "Kaldır"]
    assert veri["deger"] == "not "
    # Menu is bitince kapanir.
    assert veri["acik"] == 0


def test_enter_adds_a_line_and_ctrl_enter_is_left_to_the_owner():
    """Enter yeni satir acar; Ctrl+Enter alanin sahibine (kaydet) kalir."""
    veri = _alan(
        "const alan = zenginAlanYap({ deger: 'ilk' });"
        "let kaydet = 0;"
        "alan.addEventListener('keydown', (olay) => {"
        "  if (olay.key === 'Enter' && olay.ctrlKey) kaydet++;"
        "});"
        "const duz = alan.at('keydown', { key: 'Enter' });"
        "const ctrl = alan.at('keydown', { key: 'Enter', ctrlKey: true });"
        "process.stdout.write(JSON.stringify({"
        "  deger: alan.deger(), onlendi: duz.onlendi === true,"
        "  ctrlOnlendi: ctrl.onlendi === true, kaydet: kaydet,"
        "}));"
    )
    assert veri["deger"] == "ilk\n"
    assert veri["onlendi"] is True
    assert veri["ctrlOnlendi"] is False
    assert veri["kaydet"] == 1


def test_the_editor_tells_when_it_is_empty_for_the_placeholder():
    veri = _alan(
        "const alan = zenginAlanYap({ placeholder: 'Nerede kaldı?' });"
        "const bos = alan.classList.contains('is-bos');"
        "alan.degerYaz('bir şey');"
        "process.stdout.write(JSON.stringify({"
        "  bos: bos, dolu: alan.classList.contains('is-bos'),"
        "  yer: alan.attrs['data-placeholder'], rol: alan.attrs['role'],"
        "  yazilabilir: alan.attrs['contenteditable'],"
        "}));"
    )
    assert veri == {
        "bos": True, "dolu": False, "yer": "Nerede kaldı?",
        "rol": "textbox", "yazilabilir": "true",
    }


# --- Copilot duzeltmesi: belirtec yer tutucuyla korunur -----------------


def _duzelt(govde: str) -> object:
    return _kos(("teamslink.js", "zenginalan.js", "duzelt.js"), govde)


def test_the_token_is_swapped_for_a_placeholder_and_put_back():
    veri = _duzelt(
        "const korunan = duzeltBelirtecSakla(" + json.dumps(f"önce {BELIRTEC} sonra") + ");"
        "process.stdout.write(JSON.stringify({"
        "  giden: korunan.metin, sayi: korunan.belirtecler.length,"
        "  geri: duzeltBelirtecGeri('Önce [[T1]] sonra.', korunan.belirtecler),"
        "  bosluklu: duzeltBelirtecGeri('Önce [[ T1 ]] sonra.', korunan.belirtecler),"
        "  yutuldu: duzeltBelirtecGeri('Önce sonra.', korunan.belirtecler),"
        "}));"
    )
    # Copilot'a giden metinde uzun adres YOK.
    assert veri["giden"] == "önce [[T1]] sonra"
    assert veri["sayi"] == 1
    assert veri["geri"] == f"Önce {BELIRTEC} sonra."
    assert veri["bosluklu"] == f"Önce {BELIRTEC} sonra."
    # Model yer tutucuyu yutarsa belirtec kaybolmaz: metnin sonuna doner.
    assert veri["yutuldu"] == f"Önce sonra.\n{BELIRTEC}"


def test_the_fix_button_works_on_the_rich_field_without_breaking_the_token():
    """Gercek `attachDuzelt` + zengin alan: belirtec Copilot'a gitmez, geri gelir."""
    veri = _duzelt(
        """
let gonderilen = null;
globalThis.api = async (yol, istek) => {
  gonderilen = JSON.parse(istek.body).metin;
  return { metin: "Geçiş 15 Ekim'de yapılacak. [[T1]]", model: "claude-sonnet-5", sn: 2.1 };
};
(async () => {
  const ana = document.createElement("div");
  const alan = zenginAlanYap({ deger: """
        + json.dumps(f"gecis 15 ekimde yapılcak {BELIRTEC}")
        + """ });
  ana.appendChild(alan);
  duzeltAyarla({ "copilot.son_model": "claude-sonnet-5" });
  const sarmal = attachDuzelt(alan, { ad: "Son durum" });
  const dugme = sarmal.dugmeler()[0];
  dugme.at("click");
  await bekle();
  await bekle();
  const panel = sarmal.bul("suggest");
  const fark = panel.bul("cmp");
  const uygula = panel.dugmeler().find((d) => d.textContent === "Uygula");
  uygula.at("click");
  process.stdout.write(JSON.stringify({
    gonderilen: gonderilen,
    deger: alan.deger(),
    cip: zACipler(alan).length,
    farkCip: fark.hepsi("teams-msg").length,
    farkMetin: fark.yazi(),
    panelKapandi: !sarmal.bul("suggest"),
  }));
})();
"""
    )
    # Copilot uzun adresi hic gormedi.
    assert "teams.microsoft.com" not in veri["gonderilen"]
    assert veri["gonderilen"] == "gecis 15 ekimde yapılcak [[T1]]"
    # Belirtec yerine dondu ve alanda yine CIP olarak duruyor.
    assert veri["deger"] == f"Geçiş 15 Ekim'de yapılacak. {BELIRTEC}"
    assert veri["cip"] == 1
    # Fark panelinde de ham belirtec yok: iki yanda birer cip.
    assert veri["farkCip"] == 2
    assert "[[teams:" not in veri["farkMetin"]
    assert veri["panelKapandi"] is True


def test_a_token_only_text_is_not_counted_as_too_long():
    """Alan siniri belirtecin uzunluguyla dolmaz: olcu yer tutuculu metindir."""
    veri = _duzelt(
        "const uzun = " + json.dumps(BELIRTEC) + ".repeat(20);"
        "process.stdout.write(JSON.stringify({"
        "  ham: uzun.length, giden: duzeltBelirtecSakla(uzun).metin.length, sinir: DUZELT_SINIRI,"
        "}));"
    )
    assert veri["ham"] > veri["sinir"]
    assert veri["giden"] < veri["sinir"]


# --- salt-okunur yerler: hucre, cekmece, kart ---------------------------


def _ekran(govde: str) -> object:
    """Sayfadaki butun betikler yuklenmis halde bir sinama."""
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    adlar = [
        ad
        for ad in re.findall(r'<script src="/static/js/([^"]+)"', page)
        if ad in {"common.js", "teamslink.js", "zenginalan.js", "duzelt.js", "app.js"}
    ]
    assert "app.js" in adlar and "zenginalan.js" in adlar
    return _kos(tuple(adlar), govde)


def test_every_read_only_place_draws_the_chip_not_the_raw_token():
    """Filo hucresi, kanban karti ve son durum satiri: hicbirinde ham belirtec yok."""
    veri = _ekran(
        "const METIN = " + json.dumps(f"Kaynak: {BELIRTEC} devam") + ";"
        + r"""
// 1) Filo tablosu: yerel metin sutunu ve turetilmis/Jira sutunu.
state.columns = [
  { id: "local:1", name: "Benim notum", local: { id: 1, name: "Benim notum", type: "text", track_history: false } },
  { id: "summary", name: "Özet" },
];
state.changed = {};
state.selectedKeys = new Set();
const satir = renderRow({
  key: "DEMO-1",
  cells: [
    { field: "local:1", text: METIN, raw: METIN, changes: 0 },
    { field: "summary", text: METIN },
  ],
});
const hucreler = satir.children.slice(1, 3);

// 2) Kanban karti: aciklama onizlemesi ve son durum satiri.
const kart = taskCard({ id: 1, title: "Görev", status: "todo", description: METIN,
                        son_durum: METIN, son_durum_at: "2026-09-16T11:14:00+00:00" }, 0);

// 3) Gorev penceresindeki alanlar canli cip tasir.
taskModal({ id: 1, title: "Görev", description: METIN, son_durum: METIN, note: "" });
const pencere = document.getElementById("modal-body");

const cipSay = (dugum) => dugum.hepsi("teams-msg").length;
process.stdout.write(JSON.stringify({
  hucreCip: hucreler.map(cipSay),
  hucreMetin: hucreler.map((td) => td.yazi()),
  hucreBaslik: hucreler.map((td) => td.attrs["title"] || ""),
  kartCip: cipSay(kart),
  kartMetin: kart.yazi(),
  pencereCip: cipSay(pencere),
  pencereAlan: pencere.hepsi("zengin-alan").length,
}));
"""
    )
    # Her iki hucrede de cip var, ham belirtec yok.
    assert veri["hucreCip"] == [1, 1]
    for metin in veri["hucreMetin"] + [veri["kartMetin"]]:
        assert "[[teams:" not in metin
        assert KISA_ETIKET in metin
    # `title` duz metindir: orada da ham belirtec durmaz.
    for baslik in veri["hucreBaslik"]:
        assert "[[teams:" not in baslik
        assert KISA_ETIKET in baslik
    # Kartta aciklama onizlemesi + son durum satiri: iki cip.
    assert veri["kartCip"] == 2
    # Gorev penceresi: uc zengin alan, ikisinde cip.
    assert veri["pencereAlan"] == 3
    assert veri["pencereCip"] == 2


def test_the_drawer_shows_chips_in_local_values_and_history_lines():
    veri = _ekran(
        "const METIN = " + json.dumps(f"Kaynak: {BELIRTEC}") + ";"
        + r"""
state.group = { id: 1, detail_fields: null };
state.drawerKey = "DEMO-1";
state.drawerLocal = [{
  field: "local:1", id: 1, name: "Benim notum", type: "text", type_label: "Metin",
  text: METIN, value: METIN, empty: false, changes: 1, track_history: true,
  history: [{ changed_at: "2026-09-16T11:14:00+00:00", old_text: "", new_text: METIN }],
}];
const govde = document.createElement("div");
renderDrawerLocal(govde);
const oncesi = govde.hepsi("teams-msg").length;
// Deger dugmesine basilinca duzenleyici acilir: orada da CIP durur.
govde.bul("local-open").at("click");
process.stdout.write(JSON.stringify({
  oncesi: oncesi,
  metin: govde.yazi(),
  duzenleyici: govde.hepsi("zengin-alan").length,
  duzenlemeCip: govde.hepsi("teams-msg").length,
}));
"""
    )
    # Deger + gecmis satirindaki yeni deger: iki cip.
    assert veri["oncesi"] == 2
    assert "[[teams:" not in veri["metin"]
    # Duzenleme kipinde zengin alan acilir ve cip canli durur.
    assert veri["duzenleyici"] == 1
    assert veri["duzenlemeCip"] >= 1
