"""Grup kayitlarini Excel ekiyle e-postalamak: sablon cozumu (saf mantik).

Burada veritabani, COM ya da dosya yok: sablon + grup + satirlar girer, gonderime
hazir `{subject, html, to, cc}` cikar. Boylece uretilen HTML Windows'a hic
dokunmadan sinanir.

Yer tutucular `{grup}`, `{tarih}`, `{adet}`, `{jql}` ve yalnizca govdede gecerli
olan `{tablo}`. Bilinmeyen yer tutucu BOS kalir (Teams sablonlariyla ayni kural):
yanlis yazilmis bir ad postanin icinde `{musteri}` diye gorunmesin.

HTML uretimi Outlook'a gore yazilir: `<style>` blogu ve harici css yok, her kural
ilgili etiketin `style` ozniteliginde durur. Outlook'un HTML motoru Word'dur;
sinif secicilerini ve `border-collapse` disindaki cogu tablo kisayolunu yok sayar.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from html import escape
from typing import Any, Iterable, Sequence

# Govdede tabloyu tasiyan isaret; konuda kullanilirsa sessizce dusurulur.
TABLE_TOKEN = "{tablo}"

# Anahtar sutunu: hucre metni Jira kaydina koprulenir.
KEY_COLUMNS: tuple[str, ...] = ("issuekey", "key")

# Kullaniciya gosterilen yer tutucu listesi (Ayarlar kartindaki yardim metni).
PLACEHOLDERS: tuple[tuple[str, str], ...] = (
    ("{grup}", "Grup adı"),
    ("{tarih}", "Bugünün tarihi (GG.AA.YYYY)"),
    ("{adet}", "Gönderilen kayıt sayısı"),
    ("{jql}", "Grubun JQL sorgusu (varsa)"),
    ("{tablo}", "Seçili sütunlarla kayıt tablosu (yalnız gövdede)"),
)

# Goc 0010 bunu bir kez yazar.
SEED_TEMPLATES: tuple[dict[str, Any], ...] = (
    {
        "name": "Haftalık liste",
        "to_addresses": "",
        "cc_addresses": "",
        "subject": "{grup} — {tarih} ({adet} kayıt)",
        "body": (
            "Merhaba,\n\n"
            "{grup} listesinin {tarih} tarihli hâli ektedir.\n\n"
            "{tablo}\n\n"
            "İyi çalışmalar."
        ),
    },
)

# --- Outlook uyumlu ic css ---------------------------------------------

FONT_STACK = "'Segoe UI', Arial, sans-serif"
BODY_STYLE = f"font-family:{FONT_STACK};font-size:13px;color:#202020;"
TABLE_STYLE = (
    f"border-collapse:collapse;font-family:{FONT_STACK};font-size:12px;color:#202020;"
)
HEAD_STYLE = (
    "border:1px solid #b7b7b7;background-color:#eef1f5;"
    "padding:5px 8px;text-align:left;font-weight:bold;"
)
CELL_STYLE = "border:1px solid #d0d0d0;padding:4px 8px;vertical-align:top;"
LINK_STYLE = "color:#0b5cab;"

# "{grup}" gibi tek parcali yer tutucular; ad ASCII harfle baslar.
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
# Govde HTML mi duz metin mi: bir etiket goruntusu yetiyor.
_TAG = re.compile(r"<\s*/?\s*[A-Za-z][A-Za-z0-9]*(\s[^<>]*)?>")
# Adres ayirici: virgul, noktali virgul ve satir sonu. BOSLUK AYIRICI DEGILDIR:
# "Ad Soyad <adres@example.com>" tek bir alicidir, iki degil.
_ADDRESS_SPLIT = re.compile(r"[,;\r\n]+")
# "Ad Soyad <adres@example.com>" -- Outlook ve posta istemcilerinin bicimi.
_ADDRESS_ANGLE = re.compile(r"^\s*(?P<name>.*?)\s*<\s*(?P<email>[^<>]+?)\s*>\s*$")
_QUOTES = re.compile(r"^[\"']|[\"']$")


# --- adresler -----------------------------------------------------------


def parse_addresses(value: Any) -> list[dict[str, str]]:
    """Adres metnini alicilara ayirir: `[{"name", "email"}, ...]`.

    Iki bicim de kabul edilir -- duz adres (`a@x`) ve adli bicim
    (`Ad Soyad <a@x>`) -- cunku sablonlar ikisini de saklayabiliyor ve eski
    kayitlar duz adres tasiyor. Ayirici yalnizca virgul, noktali virgul ve
    satir sonudur: BOSLUKTA BOLUNMEZ, yoksa "Ad Soyad" iki aliciya duserdi.

    `@` icermeyen parca alici sayilmaz ve sessizce dusurulur; arayuz onu
    kirmizi bir uyariyla gosterir.
    """
    seen: set[str] = set()
    picked: list[dict[str, str]] = []
    for part in _ADDRESS_SPLIT.split(str(value or "")):
        piece = part.strip()
        if not piece:
            continue
        match = _ADDRESS_ANGLE.match(piece)
        email = (match.group("email") if match else piece).strip()
        name = _QUOTES.sub("", match.group("name").strip()) if match else ""
        # Adres bosluk tasiyamaz: "a@x b@y" gibi bir parca -- bosluk ayirici
        # olmadigi icin tek parca kalir -- gecerli bir alici degildir.
        if "@" not in email or any(char.isspace() for char in email):
            continue
        if name.casefold() == email.casefold():
            name = ""
        marker = email.casefold()
        if marker in seen:
            continue
        seen.add(marker)
        picked.append({"name": name, "email": email})
    return picked


def split_addresses(value: Any) -> list[str]:
    """Yalnizca adresler: 'Ad <a@x>; b@y' -> ['a@x', 'b@y']."""
    return [item["email"] for item in parse_addresses(value)]


def format_addresses(entries: Iterable[dict[str, str]]) -> str:
    """Saklama bicimi: `;` ile ayrilmis `Ad <adres>` ya da duz adres."""
    parts = []
    for item in entries or []:
        email = str((item or {}).get("email") or "").strip()
        if not email:
            continue
        name = str((item or {}).get("name") or "").strip()
        parts.append(f"{name} <{email}>" if name else email)
    return "; ".join(parts)


def join_addresses(addresses: Iterable[str]) -> str:
    """Outlook'un `To`/`CC` alani noktali virgulle ayrilir."""
    return "; ".join(str(item).strip() for item in addresses or [] if str(item).strip())


# --- yer tutucular ------------------------------------------------------


def date_text(when: date | datetime | None = None) -> str:
    """GG.AA.YYYY; verilmezse bugun."""
    moment = when or date.today()
    if isinstance(moment, datetime):
        moment = moment.date()
    return f"{moment.day:02d}.{moment.month:02d}.{moment.year:04d}"


def values_for(
    group: dict[str, Any] | None, rows: Sequence[Any], when: date | datetime | None = None
) -> dict[str, str]:
    data = group or {}
    return {
        "grup": str(data.get("name") or ""),
        "tarih": date_text(when),
        "adet": str(len(rows or [])),
        "jql": str(data.get("jql") or ""),
    }


def render_text(text: Any, values: dict[str, str]) -> str:
    """Yer tutuculari degistirir; bilinmeyen ad bos kalir."""
    return _PLACEHOLDER.sub(lambda match: values.get(match.group(1), ""), str(text or ""))


def looks_like_html(text: Any) -> bool:
    """Kullanici govdeye HTML yazdiysa kacislama ve <br> cevrimi yapilmaz."""
    return bool(_TAG.search(str(text or "")))


def text_to_html(text: Any) -> str:
    """Duz metin -> HTML: kacislanir, satir sonlari `<br>` olur."""
    return escape(str(text or ""), quote=False).replace("\n", "<br>\n")


# --- tablo --------------------------------------------------------------


def build_table(rows: Sequence[dict[str, Any]], columns: Sequence[dict[str, Any]]) -> str:
    """Secili sutunlarla HTML tablo: baslik koyu, ince kenarlik, anahtar koprulu."""
    heads = list(columns or [])
    if not heads or not rows:
        return ""

    header = "".join(f'<th style="{HEAD_STYLE}">{escape(str(head.get("name") or ""))}</th>'
                     for head in heads)
    lines: list[str] = []
    for row in rows:
        cells = row.get("cells") or []
        drawn = []
        for index, head in enumerate(heads):
            cell = cells[index] if index < len(cells) else {}
            drawn.append(f'<td style="{CELL_STYLE}">{_cell_html(head, cell, row)}</td>')
        lines.append("<tr>" + "".join(drawn) + "</tr>")

    return (
        f'<table cellspacing="0" cellpadding="0" style="{TABLE_STYLE}">'
        f"<thead><tr>{header}</tr></thead>"
        f'<tbody>{"".join(lines)}</tbody>'
        "</table>"
    )


def _cell_html(head: dict[str, Any], cell: dict[str, Any], row: dict[str, Any]) -> str:
    text = str((cell or {}).get("text") or "")
    if str(head.get("id") or "") in KEY_COLUMNS:
        label = text or str(row.get("key") or "")
        url = str(row.get("url") or "")
        if url:
            return (
                f'<a href="{escape(url, quote=True)}" style="{LINK_STYLE}">'
                f"{escape(label)}</a>"
            )
        return escape(label)
    return escape(text).replace("\n", "<br>")


# --- sablon cozumu ------------------------------------------------------


def render_subject(subject: Any, values: dict[str, str]) -> str:
    """Konu tek satirdir; `{tablo}` burada anlamsiz oldugu icin dusurulur."""
    text = str(subject or "").replace(TABLE_TOKEN, "")
    return " ".join(render_text(text, values).split())


def render_body(body: Any, values: dict[str, str], table: str = "") -> str:
    """Govde HTML'i: duz metin kacislanir, `{tablo}` yerine tablo gomulur."""
    raw = str(body or "")
    as_html = looks_like_html(raw)
    parts = [render_text(part, values) for part in raw.split(TABLE_TOKEN)]
    rendered = [part if as_html else text_to_html(part) for part in parts]
    return f'<div style="{BODY_STYLE}">{table.join(rendered)}</div>'


def render_mail(
    template: dict[str, Any] | None,
    group: dict[str, Any] | None,
    rows: Sequence[dict[str, Any]],
    columns: Sequence[dict[str, Any]],
    when: date | datetime | None = None,
    inline_table: bool | None = None,
) -> dict[str, Any]:
    """Sablonu kayitlarla doldurur: konu, HTML govde ve alici listeleri."""
    data = template or {}
    values = values_for(group, rows, when)
    wants_table = data.get("inline_table", True) if inline_table is None else bool(inline_table)
    table = build_table(rows, columns) if wants_table else ""
    return {
        "subject": render_subject(data.get("subject"), values),
        "html": render_body(data.get("body"), values, table),
        "to": split_addresses(data.get("to_addresses")),
        "cc": split_addresses(data.get("cc_addresses")),
        "count": len(rows or []),
        "table": table,
    }


# --- dosya adi ----------------------------------------------------------


def file_name(group_name: Any, when: date | None = None) -> str:
    """`<grup>-<YYYY-AA-GG>.xlsx`, ASCII guvenli: Outlook eki her yerde acilsin."""
    from . import export  # yerel ice aktarim: modul saf kalsin

    stem = export.ascii_stem(export.file_stem(str(group_name or ""), when))
    return f"{stem}.xlsx"
