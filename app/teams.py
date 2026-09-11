"""Teams derin baglantilari: sablon cozumu ve sohbet/kanal baglantisi.

Modul saf tutulur: veritabani, ag ya da saat okumasi yok. Graph API
KULLANILMAZ; kurum makinesinde IT izni gerektirmeyen tek yol derin
baglantidir:

    https://teams.microsoft.com/l/chat/0/0?users=...&message=...

Baglanti Teams'i acar ve mesaji yazma kutusuna koyar. **Gonder'e kullanici
basar**; uygulama gonderimi ne yapar ne de dogrulayabilir. `msteams:`
protokolu yerine `https` kullanilir: tarayici zaten Teams uygulamasina
yonlendirir, kurulu degilse web arayuzu acilir.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from . import fields as field_utils

# Kayit basina Teams kisilerini grid'de ve Excel'de gosteren sanal sutun.
CONTACTS_COLUMN = "teams:contacts"
CONTACTS_COLUMN_NAME = "Teams kişileri"

CHAT_BASE = "https://teams.microsoft.com/l/chat/0/0"

# Tarayici ve Teams uzun adreslerde takiliyor; guvenli sinir 2.000 karakter.
# Asilirsa mesaj kirpilir, tamami ayrica panoya kopyalanir.
URL_LIMIT = 2000
ELLIPSIS = "…"

# Hedef yalnizca kisilerdir. Kanala yazma denendi ve kaldirildi: Teams kanal
# baglantilari mesaj on doldurmayi kabul etmiyor, "panoya kopyala + yapistir"
# akisi de tek tiklik vaadi bozuyordu.
TARGET_PEOPLE = "people"
TARGET_KINDS: tuple[str, ...] = (TARGET_PEOPLE,)

# Adres defterindeki giris turu: kisi ya da dagitim listesi. Kurum rehberi
# ikisini de veriyor; arayuz listeleri rozetle ayirir.
CONTACT_PERSON = "person"
CONTACT_LIST = "list"
CONTACT_KINDS: tuple[str, ...] = (CONTACT_PERSON, CONTACT_LIST)

# Grup sohbetinin konu adi; kullanici Ayarlar'dan degistirebilir.
DEFAULT_TOPIC_FORMAT = "{key}"

# Sablon tohumlari: goc 0006 bunlari bir kez yazar.
SEED_TEMPLATES: tuple[tuple[str, str, int], ...] = (
    (
        "Son durum",
        "Merhaba, {key} ({summary}) kaydının son durumu nedir? Durum: {status}. {url}",
        1,
    ),
    ("Güncelleme rica", "{key} için kısa bir güncelleme rica ederim. {url}", 0),
)

# Kullaniciya gosterilen yer tutucu listesi (Ayarlar kartindaki yardim metni).
PLACEHOLDERS: tuple[tuple[str, str], ...] = (
    ("{key}", "Kayıt anahtarı"),
    ("{summary}", "Özet"),
    ("{status}", "Durum"),
    ("{assignee}", "Atanan"),
    ("{priority}", "Öncelik"),
    ("{url}", "Jira bağlantısı"),
    ("{field:customfield_10016}", "Herhangi bir alan kimliği"),
    ("{local:3}", "Yerel alan (kimlikle)"),
)

# Kok yer tutucularin karsiligi olan alan kimlikleri.
_SIMPLE_FIELDS: dict[str, str] = {
    "summary": "summary",
    "status": "status",
    "assignee": "assignee",
    "priority": "priority",
}

# "{key}", "{field:customfield_10016}", "{local:3}" -- bilinmeyen ad bos kalir.
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)(?::([^{}]*))?\}")

# Adres dogrulamasi bilerek gevsek: kurum adresleri her seklinde geliyor,
# tek beklenen "bir @ ve noktali bir alan adi".
_EMAIL = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")


def clean_email(value: Any) -> str:
    """Adresi tekillestirme icin normalize eder (bosluk yok, casefold)."""
    return str(value or "").strip().casefold()


def valid_email(value: Any) -> bool:
    return bool(_EMAIL.match(clean_email(value)))


def contact_label(contact: dict[str, Any]) -> str:
    """Sanal sutunda ve hedef metninde gorunen ad; ad yoksa e-posta."""
    return str(contact.get("name") or "").strip() or str(contact.get("email") or "")


def contacts_text(contacts: list[dict[str, Any]] | None) -> str:
    """'Teams kişileri' sutununun hucre metni: adlar virgulle."""
    return ", ".join(
        label for label in (contact_label(item) for item in contacts or []) if label
    )


# --- sablon cozumu ------------------------------------------------------


def render_template(
    body: str,
    issue_row: dict[str, Any] | None = None,
    local_values: dict[Any, Any] | None = None,
    base_url: str = "",
    schemas: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Sablon govdesindeki yer tutuculari kaydin degerleriyle doldurur.

    `issue_row` hem depo kaydini (`{"key", "raw"}`) hem de ham Jira kaydini
    (`{"key", "fields"}`) kabul eder. Bilinmeyen yer tutucu BOS kalir: yanlis
    bir ad yuzunden mesajin icinde `{musteri}` gibi bir kalinti gorunmesin.
    """
    raw = _raw_issue(issue_row)
    key = str(raw.get("key") or "")
    values = local_values or {}

    def resolve(match: re.Match[str]) -> str:
        name = match.group(1)
        argument = match.group(2)
        if argument is None:
            if name == "key":
                return key
            if name == "url":
                return issue_url(base_url, key)
            field_id = _SIMPLE_FIELDS.get(name)
            return _field_text(raw, field_id, schemas) if field_id else ""
        if name == "field":
            return _field_text(raw, argument.strip(), schemas)
        if name == "local":
            return _local_text(values, argument.strip())
        return ""

    return _PLACEHOLDER.sub(resolve, str(body or ""))


def issue_url(base_url: str, key: str) -> str:
    base = str(base_url or "").rstrip("/")
    return f"{base}/browse/{key}" if base and key else ""


def _raw_issue(issue_row: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(issue_row, dict):
        return {}
    inner = issue_row.get("raw")
    if isinstance(inner, dict):
        # Depo kaydi: anahtar ust katmanda, alanlar "raw" icinde durabilir.
        if not inner.get("key") and issue_row.get("key"):
            merged = dict(inner)
            merged["key"] = issue_row["key"]
            return merged
        return inner
    return issue_row


def _field_text(
    raw: dict[str, Any], field_id: str, schemas: dict[str, dict[str, Any]] | None
) -> str:
    if not field_id:
        return ""
    value = field_utils.issue_value(raw, field_id)
    return field_utils.format_field_value((schemas or {}).get(field_id), value)


def _local_text(values: dict[Any, Any], marker: str) -> str:
    """Yerel alan degeri; kimlik hem sayi hem metin anahtarla aranir."""
    if marker in values:
        return str(values[marker] or "")
    try:
        numeric = int(marker)
    except (TypeError, ValueError):
        return ""
    return str(values.get(numeric, "") or "")


# --- baglanti kurma -----------------------------------------------------


def build_chat_link(
    emails: list[str], message: str, topic: str | None = None
) -> dict[str, Any]:
    """Kisilere sohbet baglantisi.

    Tek kisiyse dogrudan sohbet, birden fazlaysa grup sohbeti acilir; grup
    sohbetinde `topicName` verilir ki pencerenin bir adi olsun. Adres
    listesindeki virgul ve `@` okunakli kalsin diye kodlanmaz, mesaj tam
    kodlanir. Adres uzunlugu tek basina siniri asiyorsa mesaj tamamen duser
    (baglanti yine acilir, metin panodan yapistirilir).
    """
    people = [clean_email(item) for item in emails or []]
    people = [item for item in people if item]
    users = ",".join(people)
    text = str(message or "")
    label = str(topic or "").strip() if len(people) > 1 else ""

    full = _chat_url(users, text, label)
    if len(full) <= URL_LIMIT:
        return {"url": full, "message": text, "truncated": False}

    if len(_chat_url(users, "", label)) > URL_LIMIT:
        return {"url": _chat_url(users, "", label), "message": "", "truncated": True}

    # Kodlanmis uzunluk karakter basina degismiyor: ikili arama ile en uzun
    # sigan parca bulunur.
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if len(_chat_url(users, text[:middle] + ELLIPSIS, label)) <= URL_LIMIT:
            low = middle
        else:
            high = middle - 1
    short = text[:low].rstrip() + ELLIPSIS
    return {"url": _chat_url(users, short, label), "message": short, "truncated": True}


def _chat_url(users: str, message: str, topic: str = "") -> str:
    url = f"{CHAT_BASE}?users={quote(users, safe='@,')}"
    if message:
        url += "&message=" + quote(message, safe="")
    if topic:
        url += "&topicName=" + quote(topic, safe="")
    return url
