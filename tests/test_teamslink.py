"""Teams mesaj belirteci: yapistirma cevirisi, gosterim ve disari cikista silinme.

Tasarim (kullanici onayi, 20 Eylul 2026): Teams'te bir mesaja sag tik ->
"Bağlantıyı kopyala" panoya bir on satir ve bir derin baglanti koyar. Gorev
aciklama/son durum/not alanlarina ve Jira cekmecesindeki yerel metin
alanlarina yapistirilinca blok tek satirlik bir belirtece doner:

    [[teams: Deniz Akgün · Ödeme ekibi · 16 Eyl 2026 14:14|https://teams…]]

Belirtec uygulamanin ICINDE cipe donusur; Excel'e, e-postaya ve Teams mesaj
govdesine HIC girmez.

Buradaki butun kimlikler UYDURMADIR (depo aciktir): kiracı
`00000000-0000-4000-8000-000000000001`, sohbet `19:…@unq.gbl.spaces`,
ad "Deniz Akgün".
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app import mailsend, teams, teamslink

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"

KIRACI = "00000000-0000-4000-8000-000000000001"
GRUP = "00000000-0000-4000-8000-000000000002"

SOHBET_ADRESI = (
    "https://teams.microsoft.com/l/message/"
    "19:2f1c9a7b4d8e4f0b9c3a5d6e7f801234@unq.gbl.spaces/1789557243396"
    f"?groupId=&parentMessageId=1789557243396&tenantId={KIRACI}"
    "&context=%7B%22contextType%22%3A%22chat%22%7D"
)
KANAL_ADRESI = (
    "https://teams.microsoft.com/l/message/"
    "19:7b3d5e9a1c2f4068b8d7e6a5c4b30099@thread.tacv2/1789461612874"
    f"?tenantId={KIRACI}&groupId={GRUP}&parentMessageId="
    "&teamName=%C3%96demeler&channelName=Duyurular&createdTime="
)

ON_SATIR = (
    "Deniz Akgün-Örnek A.Ş.-Ödemeler-Yazılım Uzmanı | Ödeme ekibi sohbetinde "
    "gönderildi, gönderme zamanı: Eyl 16, 2026, 14:14"
)

BELIRTEC = f"[[teams: Deniz Akgün · Ödeme ekibi · 16 Eyl 2026 14:14|{SOHBET_ADRESI}]]"


# --- tarayici tarafi: node altinda gercek betik --------------------------

SHIM = r"""
const fakeNode = (tag) => {
  const node = {
    tag: tag, children: [], parentNode: null, attrs: {}, listeners: {},
    className: "", textContent: "",
    get firstChild() { return this.children[0] || null; },
    appendChild(kid) {
      if (kid.tag === "#fragment") {
        kid.children.slice().forEach((ic) => this.appendChild(ic));
        kid.children = [];
        return kid;
      }
      if (kid.parentNode) kid.parentNode.removeChild(kid);
      kid.parentNode = this; this.children.push(kid); return kid;
    },
    removeChild(kid) {
      const yer = this.children.indexOf(kid);
      if (yer >= 0) this.children.splice(yer, 1);
      kid.parentNode = null; return kid;
    },
    setAttribute(key, value) { this.attrs[key] = value; },
    addEventListener(name, fn) { (this.listeners[name] = this.listeners[name] || []).push(fn); },
    at(name, extra) {
      const ev = Object.assign(
        { type: name, preventDefault() {}, stopPropagation() {} }, extra || {}
      );
      (this.listeners[name] || []).forEach((fn) => fn(ev));
    },
    yazi() {
      return (this.textContent || "") +
        this.children.map((kid) => (kid.yazi ? kid.yazi() : "")).join("");
    },
    dokum() {
      return {
        tag: this.tag, sinif: this.className, metin: this.yazi(), attrs: this.attrs,
        cocuklar: this.children.map((kid) => (kid.dokum ? kid.dokum() : null)),
      };
    },
  };
  return node;
};
globalThis.document = {
  createElement: (tag) => fakeNode(tag),
  createElementNS: (ns, tag) => fakeNode(tag),
  createTextNode: (text) => ({
    tag: "#text", textContent: text, children: [], attrs: {}, className: "",
    yazi: () => text,
    dokum: () => ({ tag: "#text", sinif: "", metin: text, attrs: {}, cocuklar: [] }),
  }),
  createDocumentFragment: () => fakeNode("#fragment"),
  execCommand: () => false,
};
globalThis.window = { open() {}, location: {} };
"""


def _node_kos(govde: str) -> object:
    """`teamslink.js` gercek haliyle node altinda kosturulur."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("node yok")
    betik = (STATIC / "js" / "teamslink.js").read_text(encoding="utf-8")
    sonuc = subprocess.run([node], input=betik + govde, capture_output=True, text=True)
    assert sonuc.returncode == 0, sonuc.stderr
    return json.loads(sonuc.stdout)


def _yapistir(pano: str) -> str:
    return _node_kos(
        SHIM
        + "process.stdout.write(JSON.stringify(teamsLinkYapistir("
        + json.dumps(pano)
        + ")));"
    )


# --- yapistirma: blok -> belirtec ---------------------------------------


def test_a_chat_message_block_becomes_one_token():
    assert _yapistir(f"{ON_SATIR}\n\n{SOHBET_ADRESI}") == BELIRTEC


def test_a_channel_message_uses_the_team_and_channel_names():
    belirtec = _yapistir(KANAL_ADRESI)
    # On satir yok: ad "Teams mesajı", sohbet adi parametrelerden gelir.
    assert belirtec.startswith("[[teams: Teams mesajı · Ödemeler / Duyurular · ")
    assert belirtec.endswith(f"|{KANAL_ADRESI}]]")


def test_a_bare_url_still_carries_a_time_from_the_message_id():
    """On satir yoksa zaman mesaj kimliginden (ms epoch) turetilir."""
    veri = _node_kos(
        SHIM
        + "process.stdout.write(JSON.stringify({"
        + "belirtec: teamsLinkYapistir(" + json.dumps(SOHBET_ADRESI) + "),"
        + "beklenen: teamsLinkZamanYaz(new Date(1789557243396)),"
        + "}));"
    )
    assert veri["belirtec"] == f"[[teams: Teams mesajı · {veri['beklenen']}|{SOHBET_ADRESI}]]"


def test_english_month_names_and_the_twelve_hour_clock_are_understood():
    on = (
        "Deniz Akgün-Contoso-Payments-Engineer | Ödeme ekibi chat, "
        "sent on Sep 16, 2026 2:14 PM"
    )
    assert _yapistir(f"{on}\n\n{SOHBET_ADRESI}") == BELIRTEC


def test_a_preamble_without_a_time_falls_back_to_the_message_id():
    on = "Deniz Akgün-Örnek A.Ş.-Ödemeler-Uzman | Ödeme ekibi sohbetinde gönderildi"
    veri = _node_kos(
        SHIM
        + "process.stdout.write(JSON.stringify({"
        + "belirtec: teamsLinkYapistir(" + json.dumps(f"{on}\n\n{SOHBET_ADRESI}") + "),"
        + "beklenen: teamsLinkZamanYaz(new Date(1789557243396)),"
        + "}));"
    )
    assert veri["belirtec"] == (
        f"[[teams: Deniz Akgün · Ödeme ekibi · {veri['beklenen']}|{SOHBET_ADRESI}]]"
    )


def test_only_the_block_changes_the_rest_of_the_clipboard_is_untouched():
    pano = f"Şunu incele:\n\n{ON_SATIR}\n\n{SOHBET_ADRESI}\n\nYarın konuşuruz."
    assert _yapistir(pano) == f"Şunu incele:\n\n{BELIRTEC}\n\nYarın konuşuruz."


def test_a_line_that_is_not_a_teams_preamble_is_kept_as_text():
    pano = f"önceki satır\n{SOHBET_ADRESI}"
    cikti = _yapistir(pano)
    assert cikti.startswith("önceki satır\n[[teams: Teams mesajı · ")


def test_a_clipboard_without_a_teams_link_is_returned_unchanged():
    pano = "Sadece düz metin ve https://ornek.local/sayfa adresi."
    assert _yapistir(pano) == pano


def test_the_msteams_form_is_accepted_and_both_forms_convert_into_each_other():
    uygulama = "msteams:/l/message/19:abc@unq.gbl.spaces/1789557243396?tenantId=" + KIRACI
    veri = _node_kos(
        SHIM
        + "process.stdout.write(JSON.stringify({"
        + "belirtec: teamsLinkYapistir(" + json.dumps(uygulama) + "),"
        + "uygulama: teamsLinkUygulamaAdresi(" + json.dumps(SOHBET_ADRESI) + "),"
        + "web: teamsLinkWebAdresi(" + json.dumps(uygulama) + "),"
        + "}));"
    )
    assert veri["belirtec"].endswith(f"|{uygulama}]]")
    assert veri["uygulama"] == "msteams:" + SOHBET_ADRESI.split("teams.microsoft.com", 1)[1]
    assert veri["web"] == "https://teams.microsoft.com" + uygulama.split("msteams:", 1)[1]


# --- gosterim: belirtec -> cip ------------------------------------------


def _ciz(metin: str) -> object:
    return _node_kos(
        SHIM
        + "const kutu = document.createElement('div');"
        + "teamsLinkDoldur(kutu, " + json.dumps(metin) + ");"
        + "process.stdout.write(JSON.stringify(kutu.dokum()));"
    )


def test_the_token_is_drawn_as_a_clickable_chip():
    kutu = _ciz(f"önce {BELIRTEC} sonra")
    turler = [(kid["tag"], kid["sinif"]) for kid in kutu["cocuklar"]]
    assert turler == [("#text", ""), ("a", "teams-msg"), ("#text", "")]
    cip = kutu["cocuklar"][1]
    # Cipte gonderen ve zaman; sohbet adi `title`'da durur.
    assert cip["metin"] == "Deniz Akgün · 16 Eyl 2026 14:14"
    assert cip["attrs"]["title"] == "Ödeme ekibi"
    assert cip["attrs"]["href"] == SOHBET_ADRESI
    # Ikon marka logosu degil, kendi cizdigimiz balon.
    assert cip["cocuklar"][0]["tag"] == "svg"
    assert kutu["metin"] == "önce Deniz Akgün · 16 Eyl 2026 14:14 sonra"


def test_a_plain_https_link_is_clickable_too():
    kutu = _ciz("Takvim: https://ornek.local/wiki/surum burada.")
    baglar = [kid for kid in kutu["cocuklar"] if kid["tag"] == "a"]
    assert len(baglar) == 1
    assert baglar[0]["sinif"] == "text-link"
    assert baglar[0]["attrs"]["href"] == "https://ornek.local/wiki/surum"
    assert baglar[0]["metin"] == "https://ornek.local/wiki/surum"


def test_markup_in_the_value_stays_text_and_is_never_html():
    kutu = _ciz(f"<script>alert(1)</script> {BELIRTEC}")
    ilk = kutu["cocuklar"][0]
    assert ilk["tag"] == "#text"
    assert ilk["metin"] == "<script>alert(1)</script> "
    # Tiklanabilir olan yalnizca bizim urettigimiz cip.
    assert [kid["tag"] for kid in kutu["cocuklar"] if kid["tag"] == "a"] == ["a"]


# --- temizleme: JavaScript ve Python esler ------------------------------

TEMIZ_ORNEKLER: tuple[tuple[str, str], ...] = (
    (f"önce {BELIRTEC} sonra", "önce sonra"),
    (BELIRTEC, ""),
    (f"Kaynak mesaj: {BELIRTEC}", "Kaynak mesaj:"),
    (f"üst satır\n{BELIRTEC}\nalt satır", "üst satır\nalt satır"),
    (f"{BELIRTEC} baştaki", "baştaki"),
    ("belirteç yok", "belirteç yok"),
    (f"iki {BELIRTEC} {BELIRTEC} tane", "iki tane"),
    ("https://ornek.local/sayfa kalır", "https://ornek.local/sayfa kalır"),
)


@pytest.mark.parametrize("ham, beklenen", TEMIZ_ORNEKLER)
def test_python_strips_the_token_and_keeps_the_spacing(ham, beklenen):
    assert teamslink.temizle(ham) == beklenen


def test_javascript_strips_the_token_exactly_like_python():
    veri = _node_kos(
        SHIM
        + "process.stdout.write(JSON.stringify("
        + json.dumps([ham for ham, _ in TEMIZ_ORNEKLER])
        + ".map(teamsLinkTemizle)));"
    )
    assert veri == [beklenen for _, beklenen in TEMIZ_ORNEKLER]


def test_the_helper_reports_whether_a_token_is_there():
    assert teamslink.var_mi(f"bak {BELIRTEC}")
    assert not teamslink.var_mi("düz metin")
    assert teamslink.etiket(BELIRTEC) == "Deniz Akgün · Ödeme ekibi · 16 Eyl 2026 14:14"


# --- disari cikan metin: Excel, e-posta, Teams govdesi ------------------


def test_the_token_never_reaches_a_teams_message_body():
    govde = teams.render_template(
        "Merhaba, {key} kaydında son durum: {local:3}",
        {"key": "DEMO-1", "raw": {"key": "DEMO-1", "fields": {}}},
        {3: f"Kanal duyurusu: {BELIRTEC} sonrası netleşti."},
    )
    assert "[[teams:" not in govde
    assert "teams.microsoft.com" not in govde
    assert "Teams mesajı" not in govde
    assert govde == "Merhaba, DEMO-1 kaydında son durum: Kanal duyurusu: sonrası netleşti."


def test_the_token_never_reaches_a_mail_body_or_its_table():
    html = mailsend.render_body(f"Durum: {BELIRTEC} devam ediyor.", {})
    assert "[[teams:" not in html and "teams.microsoft.com" not in html
    assert "Durum: devam ediyor." in html

    tablo = mailsend.build_table(
        [{"key": "DEMO-1", "url": "", "cells": [{"text": f"not {BELIRTEC} sonu"}]}],
        [{"id": "local:3", "name": "Benim notum"}],
    )
    assert "[[teams:" not in tablo and "teams.microsoft.com" not in tablo
    assert "not sonu" in tablo
