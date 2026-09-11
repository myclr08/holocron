"""Jira alan degerlerinin metne cevrilmesi ve siralanmasi.

Modul saf tutulur: veritabani, ag ya da saat okumasi yok. Ayni girdi her
zaman ayni ciktiyi verir; zaman dilimi disaridan verilebildigi icin testler
makinenin yereline bagli kalmaz.
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import date, datetime, timezone, tzinfo
from typing import Any

# Grid hucresinde gosterilecek en fazla karakter; tamami detay cekmecesinde.
GRID_TEXT_LIMIT = 200

DATE_FORMAT = "%d.%m.%Y"
DATETIME_FORMAT = "%d.%m.%Y %H:%M"

# Adi "name" alaninda duran Jira nesneleri.
NAMED_TYPES = frozenset(
    {
        "status",
        "priority",
        "issuetype",
        "resolution",
        "project",
        "version",
        "component",
        "securitylevel",
    }
)

# Kok seviyedeki alanlar Jira'nin alan katalogunda "fields" altinda gelmez.
# Katalogda "Key" alani issuekey kimligiyle durur, Data Center'da ise deger
# kaydin kokunde "key" olarak gelir; sanal alanlar bu farki kapatir.
VIRTUAL_FIELDS: dict[str, dict[str, Any]] = {
    "issuekey": {"id": "issuekey", "name": "Anahtar", "schema": {"type": "string"}},
}

_ROOT_ALIASES = {"issuekey": "key", "key": "key", "id": "id", "self": "self"}

_TRAILING_ZEROS = re.compile(r"\.0+$|(\.\d*[1-9])0+$")

# Buyuk/kucuk harf duyarsiz arama icin noktali/noktasiz i ayrimini eritir.
_FOLD_MAP = {
    ord("İ"): "i",
    ord("I"): "i",
    ord("ı"): "i",
    ord("̇"): "",  # casefold("İ") -> "i" + birlesen nokta
}


def fold(text: str) -> str:
    """Arama ve siralama icin normalize eder (Turkce I/i tuzagi dahil)."""
    if not text:
        return ""
    return unicodedata.normalize("NFC", text).translate(_FOLD_MAP).casefold()


def schema_of(field_schema: dict[str, Any] | None) -> dict[str, Any]:
    """Hem ham sema hem de katalog kaydi kabul eder."""
    if not isinstance(field_schema, dict):
        return {}
    inner = field_schema.get("schema")
    if isinstance(inner, dict):
        return inner
    return field_schema


def schema_type(field_schema: dict[str, Any] | None) -> str:
    value = schema_of(field_schema).get("type")
    return str(value or "").lower()


def issue_value(issue: dict[str, Any], field_id: str) -> Any:
    """Kaydin icinden alan degerini cikarir; kok alanlar da desteklenir."""
    if not isinstance(issue, dict):
        return None
    root = _ROOT_ALIASES.get(field_id)
    if root is not None and root in issue:
        return issue.get(root)
    fields = issue.get("fields")
    if isinstance(fields, dict):
        if field_id in fields:
            return fields.get(field_id)
        if root is not None:
            return fields.get(root)
    return None


def format_field_value(
    field_schema: dict[str, Any] | None,
    value: Any,
    tz: tzinfo | None = None,
) -> str:
    """Bir alan degerini kullaniciya gosterilecek metne cevirir."""
    if value is None:
        return ""

    schema = schema_of(field_schema)
    kind = schema_type(schema)

    if kind == "array" or isinstance(value, list):
        item_type = schema.get("items")
        item_schema = {"type": item_type} if item_type else None
        parts = [format_field_value(item_schema, item, tz=tz) for item in _as_list(value)]
        return ", ".join(part for part in parts if part)

    if kind == "user":
        return _user_text(value)

    if kind in NAMED_TYPES or kind == "option":
        return _named_text(value)

    if kind == "date":
        return _format_date(value)

    if kind == "datetime":
        return _format_datetime(value, tz)

    if kind == "number":
        return _format_number(value)

    if kind == "string":
        return plain_text(value)

    # Sema yoksa ya da ozel alan tipi taninmiyorsa degerin sekline bakilir.
    return _generic_text(value, tz)


def sort_key(field_schema: dict[str, Any] | None, value: Any) -> tuple[int, float, str]:
    """Siralama anahtari. Bos degerler her zaman sona duser (asc)."""
    kind = schema_type(field_schema)
    text = format_field_value(field_schema, value)
    if not text:
        return (1, 0.0, "")

    if kind == "number":
        number = _as_number(value)
        if number is not None:
            return (0, number, "")

    if kind in ("date", "datetime"):
        moment = _parse_datetime(value)
        if moment is not None:
            return (0, moment.timestamp(), "")

    return (0, 0.0, fold(text))


def truncate(text: str, limit: int = GRID_TEXT_LIMIT) -> str:
    """Grid hucresi icin kirpar; tam metin detay cekmecesinde kalir."""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def plain_text(value: Any) -> str:
    """ADF / wiki metnini duz metne indirir."""
    if value is None:
        return ""
    if isinstance(value, str):
        return _clean(value)
    if isinstance(value, dict):
        if _looks_like_adf(value):
            return _clean(adf_to_text(value))
        return _generic_text(value, None)
    if isinstance(value, list):
        return ", ".join(plain_text(item) for item in value)
    return _clean(str(value))


def adf_to_text(node: Any) -> str:
    """Atlassian Document Format agacindan metin toplar."""
    chunks: list[str] = []
    _walk_adf(node, chunks)
    text = "".join(chunks)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def changed_field_ids(old_issue: dict[str, Any] | None, new_issue: dict[str, Any]) -> list[str]:
    """Iki kayit surumu arasinda degeri degisen alan kimlikleri."""
    if not old_issue:
        return []
    old_fields = old_issue.get("fields") or {}
    new_fields = new_issue.get("fields") or {}
    if not isinstance(old_fields, dict) or not isinstance(new_fields, dict):
        return []
    changed = [
        field_id
        for field_id in sorted(set(old_fields) | set(new_fields))
        if _normalize(old_fields.get(field_id)) != _normalize(new_fields.get(field_id))
    ]
    return changed


# --- ic yardimcilar ----------------------------------------------------


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    return [value]


def _user_text(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("displayName", "name", "emailAddress", "accountId"):
            text = value.get(key)
            if text:
                return str(text)
        return ""
    return str(value)


def _named_text(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("name", "value", "displayName"):
            text = value.get(key)
            if text:
                return str(text)
        return _short_json(value)
    return str(value)


def _format_date(value: Any) -> str:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.strftime(DATE_FORMAT)
    moment = _parse_datetime(value)
    if moment is None:
        return str(value)
    return moment.strftime(DATE_FORMAT)


def _format_datetime(value: Any, tz: tzinfo | None) -> str:
    moment = _parse_datetime(value)
    if moment is None:
        return str(value)
    local = moment.astimezone(tz) if tz is not None else moment.astimezone()
    return local.strftime(DATETIME_FORMAT)


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    # Jira "+0300" yazar; fromisoformat 3.11'de kabul eder, eski bicimde de
    # iki nokta eklenerek denenir.
    for candidate in (text, re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", text)):
        try:
            parsed = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.replace(",", ".").strip())
        except ValueError:
            return None
    return None


def _format_number(value: Any) -> str:
    number = _as_number(value)
    if number is None:
        return str(value)
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if number.is_integer():
        return str(int(number))
    # Binlik ayirici yok, ondalik ayirici nokta: Excel ve arama tarafi ayni
    # metni ayristirabilsin diye bilincli tercih.
    text = f"{number:.6f}"
    return _TRAILING_ZEROS.sub(lambda m: m.group(1) or "", text)


def _generic_text(value: Any, tz: tzinfo | None) -> str:
    if isinstance(value, bool):
        return "Evet" if value else "Hayir"
    if isinstance(value, (int, float)):
        return _format_number(value)
    if isinstance(value, list):
        parts = [_generic_text(item, tz) for item in value]
        return ", ".join(part for part in parts if part)
    if isinstance(value, dict):
        if _looks_like_adf(value):
            return _clean(adf_to_text(value))
        for key in ("displayName", "name", "value"):
            text = value.get(key)
            if text:
                return str(text)
        return _short_json(value)
    if isinstance(value, str):
        moment_like = _parse_datetime(value) if _ISO_HINT.match(value) else None
        if moment_like is not None:
            return _format_datetime(value, tz)
        return _clean(value)
    return _clean(str(value))


_ISO_HINT = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}")


def _looks_like_adf(value: dict[str, Any]) -> bool:
    return value.get("type") == "doc" and "content" in value


_ADF_BLOCKS = frozenset({"paragraph", "heading", "listItem", "blockquote", "codeBlock", "rule"})


def _walk_adf(node: Any, chunks: list[str]) -> None:
    if isinstance(node, list):
        for item in node:
            _walk_adf(item, chunks)
        return
    if not isinstance(node, dict):
        return

    kind = node.get("type")
    if kind == "text":
        chunks.append(str(node.get("text") or ""))
        return
    if kind == "hardBreak":
        chunks.append("\n")
        return
    if kind == "mention":
        attrs = node.get("attrs") or {}
        chunks.append(str(attrs.get("text") or attrs.get("displayName") or ""))
        return
    if kind == "emoji":
        attrs = node.get("attrs") or {}
        chunks.append(str(attrs.get("text") or attrs.get("shortName") or ""))
        return

    _walk_adf(node.get("content"), chunks)
    if kind in _ADF_BLOCKS:
        chunks.append("\n")


_WIKI_NOISE = re.compile(r"\{(?:color|panel|quote|noformat|code)(?::[^}]*)?\}")


def _clean(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _WIKI_NOISE.sub("", text)
    return text.strip()


def _short_json(value: Any) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        text = str(value)
    return truncate(text, 120)


def _normalize(value: Any) -> str:
    """Karsilastirma icin kararli gosterim (anahtar sirasi onemsiz)."""
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(value)
