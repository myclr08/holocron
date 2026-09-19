"""Metin duzeltme: istem kurulumu, cevabin okunmasi, uc ve arayuz bileseni.

Tasarim (kullanici onayi, 19 Eylul 2026): Gorevlerim'deki Aciklama/Not
alanlarinin ve Jira kaydi cekmecesindeki cok satirli yerel alanlarin sag alt
kosesinde "Düzelt" dugmesi durur. Copilot'tan JSON degil DUZ METIN istenir,
fark tarayicida kelime duzeyinde hesaplanir.

Hicbir test gercek Copilot CLI'yi kosturmaz: `tests/fake_copilot.py`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app import copilot
from app.copilot import CopilotCikti
from tests.fake_copilot import ORNEK_DUZELTME, SahteCopilot

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"

BOZUK = "gecis 15 ekimde yapılcak eger sorun olursa eski servise geri donmemiz lazım"


# --- istem: sablon, cipler, ton -----------------------------------------


def test_the_prompt_names_both_files_and_the_rules(tmp_path):
    istem = copilot.duzelt_istemi(tmp_path / "girdi.txt", tmp_path / "cikti.txt", ["imla"])
    assert str(tmp_path / "girdi.txt") in istem
    assert str(tmp_path / "cikti.txt") in istem
    assert "{girdi}" not in istem and "{cikti}" not in istem and "{kurallar}" not in istem
    # Tasarimin kurallari sablonda duruyor.
    assert "yeni bilgi EKLEME" in istem
    assert "PRJ-1432" in istem


def test_the_chips_reach_the_prompt(tmp_path):
    """Secilen cipler istemdeki kural satirlarina donusur."""
    istem = copilot.duzelt_istemi(tmp_path / "g", tmp_path / "c", ["resmi", "kisa"])
    assert copilot.DUZELT_SECENEK_KURALLARI["resmi"] in istem
    assert copilot.DUZELT_SECENEK_KURALLARI["kisa"] in istem
    assert copilot.DUZELT_SECENEK_KURALLARI["anlam"] not in istem


def test_no_chip_means_the_two_default_ones(tmp_path):
    assert copilot.duzelt_secenekleri([]) == ["imla", "anlam"]
    assert copilot.duzelt_secenekleri(["bilinmeyen"]) == ["imla", "anlam"]
    # Sira her zaman ayni: istem kararli kurulur.
    assert copilot.duzelt_secenekleri(["kisa", "imla"]) == ["imla", "kisa"]


def test_the_formal_tone_setting_adds_the_rule_even_without_the_chip():
    assert "resmi" in copilot.duzelt_secenekleri(["imla"], ton="resmi")
    assert "resmi" not in copilot.duzelt_secenekleri(["imla"], ton="notr")


def test_a_custom_template_wins_but_only_if_it_keeps_the_placeholders(tmp_path):
    ozel = "Metni {girdi} dosyasından oku, düzeltip {cikti} dosyasına yaz. {kurallar}"
    assert copilot.duzelt_sablonu(ozel) == ozel
    # `{cikti}` unutulursa model cevabi nereye yazacagini bilemez: gomulu sablon.
    assert copilot.duzelt_sablonu("yalnızca {girdi} var") == copilot.duzelt_sablonu()
    assert copilot.duzelt_sablonu("") == copilot.duzelt_sablonu()


def test_the_builtin_template_ships_inside_the_package():
    yol = Path(copilot.__file__).resolve().parent / copilot.DUZELT_SABLON_DOSYASI
    assert yol.is_file()
    assert "{girdi}" in yol.read_text(encoding="utf-8")


# --- cevabin okunmasi: dosya, stdout, temizlik --------------------------


def test_the_corrected_text_is_read_from_the_file_the_model_writes(tmp_path):
    sahte = SahteCopilot(kip="metin")
    sonuc = copilot.duzelt(sahte, ["claude-sonnet-5"], tmp_path, BOZUK)

    assert sonuc["metin"] == ORNEK_DUZELTME
    assert sonuc["model"] == "claude-sonnet-5"
    assert isinstance(sonuc["sn"], float)
    assert "hata" not in sonuc
    # Metin modele DOSYA ile gecer, komut satirinda durmaz.
    assert BOZUK not in sahte.istemler[0]
    assert copilot.DUZELT_GIRDI_DOSYASI in sahte.istemler[0]
    # Ara dosyalar geride kalmaz.
    assert not (tmp_path / copilot.DUZELT_GIRDI_DOSYASI).exists()
    assert not (tmp_path / copilot.DUZELT_CIKTI_DOSYASI).exists()


def test_a_model_that_only_prints_to_stdout_is_the_fallback(tmp_path):
    """Dosya yazilmadi ama metin stdout'ta kod blogu ve ANSI icinde."""
    sonuc = copilot.duzelt(SahteCopilot(kip="metin-stdout"), ["gpt-5-mini"], tmp_path, BOZUK)
    assert sonuc["metin"] == ORNEK_DUZELTME
    assert "\x1b" not in sonuc["metin"]
    assert "GitHub Copilot" not in sonuc["metin"]


def test_an_empty_answer_becomes_an_error_not_an_empty_field(tmp_path):
    sonuc = copilot.duzelt(SahteCopilot(kip="sessiz"), ["gpt-5-mini"], tmp_path, BOZUK)
    assert "metin" not in sonuc
    assert "hiçbir çıktı vermedi" in sonuc["hata"]


def test_a_denied_tool_permission_is_named(tmp_path):
    sonuc = copilot.duzelt(SahteCopilot(kip="izin"), ["gpt-5-mini"], tmp_path, BOZUK)
    assert "yazma izni vermedi" in sonuc["hata"]


def test_a_rejected_model_falls_through_to_the_next_one(tmp_path):
    sahte = SahteCopilot(kip="metin", reddedilen=["gpt-5"])
    sonuc = copilot.duzelt(sahte, ["gpt-5", "claude-sonnet-5"], tmp_path, BOZUK)
    assert sonuc["model"] == "claude-sonnet-5"
    assert sahte.modeller == ["gpt-5", "claude-sonnet-5"]


def test_every_model_rejected_is_summarized_on_one_line(tmp_path):
    sahte = SahteCopilot(kip="metin", reddedilen=["a", "b"])
    sonuc = copilot.duzelt(sahte, ["a", "b"], tmp_path, BOZUK)
    assert sonuc["hata"].startswith("Copilot modelleri reddetti: a, b")


@pytest.mark.parametrize(
    "ham, beklenen",
    [
        ("```\nGeçiş 15 Ekim'de.\n```", "Geçiş 15 Ekim'de."),
        ("```text\nGeçiş 15 Ekim'de.\n```", "Geçiş 15 Ekim'de."),
        ("Düzeltilmiş metin:\nGeçiş 15 Ekim'de.", "Geçiş 15 Ekim'de."),
        ("Düzeltilmiş metin: Geçiş 15 Ekim'de.", "Geçiş 15 Ekim'de."),
        ('"Geçiş 15 Ekim\'de."', "Geçiş 15 Ekim'de."),
        ("Sonuç:\n```\nGeçiş 15 Ekim'de.\n```", "Geçiş 15 Ekim'de."),
        ("\x1b[32mGeçiş 15 Ekim'de.\x1b[0m", "Geçiş 15 Ekim'de."),
        ("", ""),
    ],
)
def test_the_wrappers_the_model_adds_are_peeled_off(ham, beklenen):
    assert copilot.duzelt_ciktisi_temizle(ham) == beklenen


def test_a_multi_line_answer_keeps_its_line_breaks(tmp_path):
    govde = "Birinci madde.\n\n- İkinci madde\n- Üçüncü madde"
    sonuc = copilot.duzelt(
        SahteCopilot(kip="metin", duzeltme=govde), ["m"], tmp_path, BOZUK
    )
    assert sonuc["metin"] == govde


def test_the_stdout_fallback_never_returns_copilot_chatter():
    """Ilerleme satirlari kullanicinin metninin yerine gecmesin."""
    cikti = CopilotCikti(kod=0, metin="● Reading file\n● Done\n", hata="")
    assert copilot.duzelt_stdout_yedegi(cikti) == ""


# --- sinirlar ------------------------------------------------------------


def test_an_empty_text_is_refused_before_copilot_is_called(tmp_path):
    sahte = SahteCopilot(kip="metin")
    sonuc = copilot.duzelt(sahte, ["m"], tmp_path, "   \n  ")
    assert sonuc["hata"] == "Düzeltilecek metin boş."
    assert sahte.modeller == []


def test_a_text_longer_than_the_limit_is_refused(tmp_path):
    sahte = SahteCopilot(kip="metin")
    uzun = "a" * (copilot.DUZELT_SINIRI + 1)
    sonuc = copilot.duzelt(sahte, ["m"], tmp_path, uzun)
    assert "çok uzun" in sonuc["hata"]
    assert str(copilot.DUZELT_SINIRI) in sonuc["hata"]
    assert sahte.modeller == []
    # Tam sinirdaki metin gecer.
    assert "metin" in copilot.duzelt(sahte, ["m"], tmp_path, "a" * copilot.DUZELT_SINIRI)


def test_the_timeout_is_short_enough_for_someone_waiting():
    assert copilot.DUZELT_ZAMAN_ASIMI == 30
    assert copilot.DUZELT_ZAMAN_ASIMI < copilot.SINAMA_ZAMAN_ASIMI


# --- HTTP ucu ------------------------------------------------------------


def test_the_endpoint_returns_the_corrected_text(api_client, context, fake_copilot):
    fake_copilot.kip = "metin"
    cevap = api_client.post(
        "/api/copilot/duzelt", json={"metin": BOZUK, "secenekler": ["imla", "kisa"]}
    )
    assert cevap.status_code == 200
    veri = cevap.json()
    assert veri["metin"] == ORNEK_DUZELTME
    assert veri["model"]
    # Calisan model bir sonraki istekte basa alinsin diye saklanir.
    assert context.settings.get("copilot.son_model") == veri["model"]
    # Cipler isteme gercekten gitti.
    assert copilot.DUZELT_SECENEK_KURALLARI["kisa"] in fake_copilot.istemler[0]


def test_an_empty_text_is_a_400(api_client):
    cevap = api_client.post("/api/copilot/duzelt", json={"metin": "  "})
    assert cevap.status_code == 400
    assert cevap.json()["error"]["code"] == "empty_text"


def test_a_too_long_text_is_a_400(api_client):
    cevap = api_client.post(
        "/api/copilot/duzelt", json={"metin": "a" * (copilot.DUZELT_SINIRI + 1)}
    )
    assert cevap.status_code == 400
    assert cevap.json()["error"]["code"] == "text_too_long"


def test_the_feature_can_be_switched_off(api_client, context):
    context.settings.set("copilot.duzelt_acik", "0")
    cevap = api_client.post("/api/copilot/duzelt", json={"metin": BOZUK})
    assert cevap.status_code == 400
    assert cevap.json()["error"]["code"] == "feature_disabled"


def test_a_copilot_failure_is_a_clean_200_not_a_500(api_client, fake_copilot):
    """Alan bozulmasin: hata paneli degil tek satir, ama istek basarili doner."""
    fake_copilot.kip = "sessiz"
    cevap = api_client.post("/api/copilot/duzelt", json={"metin": BOZUK})
    assert cevap.status_code == 200
    assert cevap.json()["hata"]
    assert "metin" not in cevap.json()


def test_the_tone_setting_reaches_the_prompt(api_client, context, fake_copilot):
    fake_copilot.kip = "metin"
    context.settings.set("copilot.duzelt_ton", "resmi")
    api_client.post("/api/copilot/duzelt", json={"metin": BOZUK, "secenekler": ["imla"]})
    assert copilot.DUZELT_SECENEK_KURALLARI["resmi"] in fake_copilot.istemler[0]


def test_a_saved_custom_template_is_used(api_client, context, fake_copilot):
    fake_copilot.kip = "metin"
    context.settings.set(
        "copilot.duzelt_sablon",
        "KENDİ ŞABLONUM: {girdi} oku, {cikti} yaz. {kurallar}",
    )
    api_client.post("/api/copilot/duzelt", json={"metin": BOZUK})
    assert fake_copilot.istemler[0].startswith("KENDİ ŞABLONUM:")


def test_the_settings_carry_the_fix_keys_and_the_builtin_template(api_client):
    ayarlar = api_client.get("/api/settings").json()["settings"]
    assert ayarlar["copilot.duzelt_acik"] == "1"
    assert ayarlar["copilot.duzelt_ton"] == "notr"
    assert ayarlar["copilot.duzelt_sablon"] == ""
    # "Şablonu varsayılana döndür" gomulu sablonu buradan okur.
    assert "{girdi}" in ayarlar["copilot_duzelt_sablon_varsayilan"]

    kayitli = api_client.put(
        "/api/settings",
        json={"copilot.duzelt_acik": False, "copilot.duzelt_ton": "resmi"},
    ).json()["settings"]
    assert kayitli["copilot.duzelt_acik"] == "0"
    assert kayitli["copilot.duzelt_ton"] == "resmi"


# --- tarayici tarafi: fark hesabi ve bilesen ----------------------------


def _node_kos(govde: str) -> object:
    """`duzelt.js` gercek haliyle node altinda kosturulur."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node yok")
    betik = (STATIC / "js" / "duzelt.js").read_text(encoding="utf-8")
    sonuc = subprocess.run(
        [node], input=betik + govde, capture_output=True, text=True
    )
    assert sonuc.returncode == 0, sonuc.stderr
    return json.loads(sonuc.stdout)


SHIM = r"""
const fakeNode = (tag) => {
  const siniflar = new Set();
  const node = {
    tag: tag, children: [], parentNode: null, attrs: {}, listeners: {},
    textContent: "", value: "", hidden: false, disabled: false, title: "",
    dataset: {},
    get firstChild() { return this.children[0] || null; },
    classList: {
      add: (...names) => names.forEach((n) => siniflar.add(n)),
      remove: (...names) => names.forEach((n) => siniflar.delete(n)),
      contains: (n) => siniflar.has(n),
      toggle: (n, on) => (on ? siniflar.add(n) : siniflar.delete(n)),
    },
    siniflari: () => Array.from(siniflar),
    appendChild(kid) {
      if (kid.parentNode) kid.parentNode.removeChild(kid);
      kid.parentNode = this; this.children.push(kid); return kid;
    },
    removeChild(kid) {
      const yer = this.children.indexOf(kid);
      if (yer >= 0) this.children.splice(yer, 1);
      kid.parentNode = null; return kid;
    },
    insertBefore(kid, ref) {
      if (kid.parentNode) kid.parentNode.removeChild(kid);
      const yer = this.children.indexOf(ref);
      this.children.splice(yer < 0 ? this.children.length : yer, 0, kid);
      kid.parentNode = this; return kid;
    },
    setAttribute(key, value) { this.attrs[key] = value; },
    addEventListener(name, fn) { (this.listeners[name] = this.listeners[name] || []).push(fn); },
    dispatchEvent(ev) { (this.listeners[ev.type] || []).forEach((fn) => fn(ev)); return true; },
    focus() {}, select() {}, setSelectionRange() {},
    at(name, extra) {
      const ev = Object.assign(
        { type: name, preventDefault() {}, stopPropagation() {} }, extra || {}
      );
      (this.listeners[name] || []).forEach((fn) => fn(ev));
    },
    yazi() {
      return (this.textContent || "") +
        this.children.map((kid) => (kid.yazi ? kid.yazi() : kid.textContent || "")).join("");
    },
    bul(sinif) {
      for (const kid of this.children) {
        if (kid.className === sinif || (kid.siniflari && kid.siniflari().includes(sinif))) return kid;
        const ic = kid.bul ? kid.bul(sinif) : null;
        if (ic) return ic;
      }
      return null;
    },
    dugmeler() {
      const liste = this.tag === "button" ? [this] : [];
      this.children.forEach((kid) => liste.push(...(kid.dugmeler ? kid.dugmeler() : [])));
      return liste;
    },
  };
  // Gercek DOM'da `className` ile `classList` ayni kumedir; kirilirsa
  // `class: "chip on"` ile yazilan sinifi `classList.contains` gormez.
  Object.defineProperty(node, "className", {
    get: () => Array.from(siniflar).join(" "),
    set: (value) => {
      siniflar.clear();
      String(value || "").split(/\s+/).filter(Boolean).forEach((ad) => siniflar.add(ad));
    },
  });
  return node;
};
globalThis.document = {
  createElement: (tag) => fakeNode(tag),
  createElementNS: (ns, tag) => fakeNode(tag),
  createTextNode: (text) => ({ tag: "#text", textContent: text, children: [], yazi: () => text,
                               bul: () => null, dugmeler: () => [] }),
  execCommand: () => false,
  addEventListener() {},
};
const bekle = () => new Promise((cozul) => setTimeout(cozul, 0));
"""


def test_the_word_diff_marks_only_what_changed():
    ops = _node_kos(
        SHIM
        + """
const ops = duzeltFark("bi plan yazılması gerekiyo", "bir plan yazılması gerekiyor");
process.stdout.write(JSON.stringify({
  ops: ops.map((op) => [op.tur, op.metin]),
  sayi: duzeltFarkSayisi(ops),
}));
"""
    )
    # "plan yazılması" dokunulmadan duruyor; iki yerde degisiklik var.
    assert ["ayni", " plan yazılması "] in [list(op) for op in ops["ops"]]
    assert ops["sayi"] == 2
    cikan = "".join(m for t, m in ops["ops"] if t == "del")
    gelen = "".join(m for t, m in ops["ops"] if t == "ins")
    assert cikan == "bigerekiyo"
    assert gelen == "birgerekiyor"


def test_the_diff_rebuilds_both_sides_without_losing_a_character():
    veri = _node_kos(
        SHIM
        + """
const once = "gecis 15 ekimde yapılcak, deniz ile konusuldu";
const sonra = "Geçiş 15 Ekim'de yapılacak. Deniz ile konuşuldu.";
const ops = duzeltFark(once, sonra);
process.stdout.write(JSON.stringify({
  once: ops.filter((op) => op.tur !== "ins").map((op) => op.metin).join(""),
  sonra: ops.filter((op) => op.tur !== "del").map((op) => op.metin).join(""),
}));
"""
    )
    assert veri["once"] == "gecis 15 ekimde yapılcak, deniz ile konusuldu"
    assert veri["sonra"] == "Geçiş 15 Ekim'de yapılacak. Deniz ile konuşuldu."


def test_the_diff_handles_the_empty_and_the_identical_case():
    veri = _node_kos(
        SHIM
        + """
process.stdout.write(JSON.stringify({
  bos: duzeltFark("", ""),
  ayni: duzeltFark("aynı metin", "aynı metin").map((op) => op.tur),
  sayi: duzeltFarkSayisi(duzeltFark("aynı metin", "aynı metin")),
  silindi: duzeltFark("tek", "").map((op) => op.tur),
}));
"""
    )
    assert veri["bos"] == []
    assert veri["ayni"] == ["ayni"]
    assert veri["sayi"] == 0
    assert veri["silindi"] == ["del"]


def test_only_whitespace_changes_are_not_counted_as_edits():
    veri = _node_kos(
        SHIM
        + """
process.stdout.write(JSON.stringify(duzeltFarkSayisi(duzeltFark("a  b", "a b"))));
"""
    )
    assert veri == 0


def test_the_component_draws_the_button_applies_and_undoes():
    """Gercek `attachDuzelt`: dugme, oneri paneli, Uygula ve Ctrl+Z."""
    adimlar = _node_kos(
        SHIM
        + """
globalThis.api = async () => ({
  metin: "Geçiş 15 Ekim'de yapılacak.", model: "claude-sonnet-5", sn: 4.2,
});
(async () => {
  const adimlar = {};
  const ana = document.createElement("div");
  const alan = document.createElement("textarea");
  ana.appendChild(alan);
  alan.value = "gecis 15 ekimde yapılcak";
  duzeltAyarla({ "copilot.son_model": "claude-sonnet-5" });

  const sarmal = attachDuzelt(alan, { ad: "Açıklama" });
  adimlar.sarmal = sarmal.className;
  adimlar.alanIcerde = alan.parentNode === sarmal;
  adimlar.tekrarAyniKutu = attachDuzelt(alan) === sarmal;
  adimlar.sarmalCocuk = sarmal.children.length;   // alan + dugme

  const dugme = sarmal.dugmeler()[0];
  adimlar.etiket = dugme.yazi();
  adimlar.sari = dugme.classList.contains("on");

  dugme.at("click");
  adimlar.mesgul = dugme.classList.contains("busy");
  await bekle();
  await bekle();

  const panel = sarmal.bul("suggest");
  adimlar.panelVar = !!panel;
  adimlar.alanGizli = alan.hidden === true;
  adimlar.meta = panel.bul("suggest-meta").textContent;
  adimlar.once = panel.bul("cmp").children[0].yazi();
  adimlar.sonra = panel.bul("cmp").children[1].yazi();
  adimlar.cipler = panel.bul("chips").children.map((c) => [c.textContent, c.classList.contains("on")]);

  const uygula = panel.dugmeler().find((b) => b.textContent === "Uygula");
  uygula.at("click");
  adimlar.uygulandi = alan.value;
  adimlar.panelKapandi = !sarmal.bul("suggest");
  adimlar.alanGorundu = alan.hidden === false;

  alan.at("keydown", { key: "z", ctrlKey: true, shiftKey: false });
  adimlar.geriAlindi = alan.value;

  process.stdout.write(JSON.stringify(adimlar));
})();
"""
    )
    assert adimlar["sarmal"] == "fix-wrap"
    assert adimlar["alanIcerde"] is True
    assert adimlar["tekrarAyniKutu"] is True, "bilesen iki kez takilmamali"
    assert adimlar["sarmalCocuk"] == 2
    assert adimlar["etiket"] == "Düzelt"
    assert adimlar["sari"] is True
    assert adimlar["mesgul"] is True
    assert adimlar["panelVar"] is True
    assert adimlar["alanGizli"] is True
    assert adimlar["meta"] == "claude-sonnet-5 · 4.2 sn · 3 değişiklik"
    assert adimlar["once"] == "Öncegecis 15 ekimde yapılcak"
    assert adimlar["sonra"] == "SonraGeçiş 15 Ekim'de yapılacak."
    assert adimlar["cipler"] == [
        ["İmla ve noktalama", True],
        ["Anlam düşüklüğü", True],
        ["Daha resmi", False],
        ["Kısalt", False],
    ]
    assert adimlar["uygulandi"] == "Geçiş 15 Ekim'de yapılacak."
    assert adimlar["panelKapandi"] is True
    assert adimlar["alanGorundu"] is True
    # Ctrl+Z eski metni geri getirir (tarayicinin kendi yigini calismadiginda).
    assert adimlar["geriAlindi"] == "gecis 15 ekimde yapılcak"


def test_the_component_says_when_copilot_is_not_configured():
    adimlar = _node_kos(
        SHIM
        + """
globalThis.api = async () => { throw new Error("çağrılmamalıydı"); };
(async () => {
  const adimlar = {};
  const ana = document.createElement("div");
  const alan = document.createElement("textarea");
  ana.appendChild(alan);
  alan.value = "bir metin";
  duzeltAyarla({});                       // Copilot hiç ayarlanmamış
  const sarmal = attachDuzelt(alan);
  const dugme = sarmal.dugmeler()[0];
  adimlar.sari = dugme.classList.contains("on");
  adimlar.ipucu = dugme.title;
  dugme.at("click");
  await bekle();
  const hata = sarmal.bul("fix-error");
  adimlar.hata = hata.yazi();
  adimlar.bag = hata.children[1].attrs.href;
  adimlar.alanBozulmadi = alan.value;

  // Ozellik kapaliysa dugme hic gorunmez.
  const alan2 = document.createElement("textarea");
  document.createElement("div").appendChild(alan2);
  alan2.value = "bir metin";
  duzeltAyarla({ "copilot.duzelt_acik": "0" });
  adimlar.kapali = attachDuzelt(alan2).dugmeler()[0].hidden;

  process.stdout.write(JSON.stringify(adimlar));
})();
"""
    )
    assert adimlar["sari"] is False
    assert adimlar["ipucu"] == "Copilot ayarlı değil"
    assert adimlar["hata"] == "Copilot ayarlı değil.Ayarlar → Copilot"
    assert adimlar["bag"] == "/settings#copilot"
    assert adimlar["alanBozulmadi"] == "bir metin"
    assert adimlar["kapali"] is True


def test_a_failed_call_leaves_the_text_alone_and_shows_one_line():
    adimlar = _node_kos(
        SHIM
        + """
globalThis.api = async () => ({ hata: "Copilot düzeltilmiş metni yazmadı." });
(async () => {
  const adimlar = {};
  const alan = document.createElement("textarea");
  document.createElement("div").appendChild(alan);
  alan.value = "gecis 15 ekimde";
  duzeltAyarla({ "copilot.son_model": "gpt-5-mini" });
  const sarmal = attachDuzelt(alan);
  sarmal.dugmeler()[0].at("click");
  await bekle();
  await bekle();
  adimlar.panelYok = !sarmal.bul("suggest");
  adimlar.hata = sarmal.bul("fix-error").yazi();
  adimlar.metin = alan.value;
  adimlar.alanGorunur = alan.hidden === false;
  process.stdout.write(JSON.stringify(adimlar));
})();
"""
    )
    assert adimlar["panelYok"] is True
    assert adimlar["hata"] == "Copilot düzeltilmiş metni yazmadı.Ayarlar'da sına"
    assert adimlar["metin"] == "gecis 15 ekimde"
    assert adimlar["alanGorunur"] is True


def test_the_button_is_disabled_when_the_text_is_empty_or_too_long():
    adimlar = _node_kos(
        SHIM
        + """
const adimlar = {};
const alan = document.createElement("textarea");
document.createElement("div").appendChild(alan);
duzeltAyarla({ "copilot.son_model": "gpt-5-mini" });
const dugme = attachDuzelt(alan).dugmeler()[0];
adimlar.bos = [dugme.disabled, dugme.title];
alan.value = "kısa metin";
alan.at("input");
adimlar.dolu = [dugme.disabled, dugme.title];
alan.value = "a".repeat(DUZELT_SINIRI + 1);
alan.at("input");
adimlar.uzun = [dugme.disabled, dugme.title.includes("çok uzun")];
process.stdout.write(JSON.stringify(adimlar));
"""
    )
    assert adimlar["bos"] == [True, "Önce bir şeyler yazın"]
    assert adimlar["dolu"] == [False, "Metni düzelt (Ctrl+Shift+D)"]
    assert adimlar["uzun"] == [True, True]


def test_the_shortcut_is_ctrl_shift_d():
    adimlar = _node_kos(
        SHIM
        + """
let cagrildi = 0;
globalThis.api = async () => { cagrildi++; return { metin: "Düzeltildi.", model: "m", sn: 1 }; };
(async () => {
  const alan = document.createElement("textarea");
  document.createElement("div").appendChild(alan);
  alan.value = "duzeltilecek metin";
  duzeltAyarla({ "copilot.son_model": "m" });
  attachDuzelt(alan);
  alan.at("keydown", { key: "D", ctrlKey: true, shiftKey: true });
  await bekle();
  await bekle();
  process.stdout.write(JSON.stringify({ cagrildi: cagrildi, gizli: alan.hidden }));
})();
"""
    )
    assert adimlar["cagrildi"] == 1
    assert adimlar["gizli"] is True


def test_only_one_correction_runs_at_a_time():
    """Tasarim: "Aynı anda ikinci düzeltme başlatılamaz"."""
    adimlar = _node_kos(
        SHIM
        + """
let cagri = 0;
globalThis.api = async () => {
  cagri++;
  await new Promise((cozul) => setTimeout(cozul, 5));
  return { metin: "Düzeltildi.", model: "m", sn: 1 };
};
(async () => {
  const kur = (metin) => {
    const alan = document.createElement("textarea");
    document.createElement("div").appendChild(alan);
    alan.value = metin;
    return attachDuzelt(alan);
  };
  duzeltAyarla({ "copilot.son_model": "m" });
  const bir = kur("birinci metin");
  const iki = kur("ikinci metin");
  bir.dugmeler()[0].at("click");
  iki.dugmeler()[0].at("click");
  await new Promise((cozul) => setTimeout(cozul, 30));
  process.stdout.write(JSON.stringify({ cagri: cagri }));
})();
"""
    )
    assert adimlar["cagri"] == 1
