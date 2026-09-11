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
from decimal import Decimal, InvalidOperation
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


def parse_moment(value: Any) -> datetime | None:
    """Degeri zaman damgasina cevirir; cevrilemezse None.

    Excel disa aktarimi gercek tarih hucresi yazabilmek icin bunu kullanir.
    """
    return _parse_datetime(value)


def parse_number(value: Any) -> float | None:
    """Degeri sayiya cevirir; cevrilemezse None."""
    return _as_number(value)


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
        return "Evet" if value else "Hayır"
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


# --- yerel (kullanici tanimli) alanlar ----------------------------------
#
# Yerel deger her zaman METIN olarak saklanir; normalizasyon yazma aninda
# yapilir. Boylece okuma tarafi (grid, arama, siralama, sonraki asamada
# Excel) tek bicimli bir metinle calisir.

LOCAL_TEXT = "text"
LOCAL_NUMBER = "number"
LOCAL_DATE = "date"
LOCAL_BOOL = "bool"
LOCAL_SELECT = "select"

LOCAL_TYPES: tuple[str, ...] = (LOCAL_TEXT, LOCAL_NUMBER, LOCAL_DATE, LOCAL_BOOL, LOCAL_SELECT)

LOCAL_TYPE_LABELS: dict[str, str] = {
    LOCAL_TEXT: "Metin",
    LOCAL_NUMBER: "Sayı",
    LOCAL_DATE: "Tarih",
    LOCAL_BOOL: "Evet/Hayır",
    LOCAL_SELECT: "Liste",
}

# Turetilmis (sanal) sutun ekleri: yalnizca gecmisi tutulan alanlarda uretilir.
DERIVED_CHANGED_AT = "changed_at"
DERIVED_CHANGES = "changes"
DERIVED_SUFFIXES: tuple[str, ...] = (DERIVED_CHANGED_AT, DERIVED_CHANGES)
DERIVED_LABELS: dict[str, str] = {
    DERIVED_CHANGED_AT: "son değişim",
    DERIVED_CHANGES: "kaç kez değişti",
}

BOOL_TRUE_TEXT = "Evet"
BOOL_FALSE_TEXT = "Hayır"

_TRUE_WORDS = frozenset({"1", "true", "evet", "yes", "on", "dogru", "e", "x", "✓"})
_FALSE_WORDS = frozenset({"0", "false", "hayir", "no", "off", "yanlis", "h", "-"})

_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_TR_DATE = re.compile(r"^(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})$")


class LocalValueError(ValueError):
    """Yerel alan degeri tipe uymuyor; mesaj dogrudan kullaniciya gider."""


def local_schema(field_type: str) -> dict[str, Any]:
    """Yerel tipi Jira semasi diline cevirir (siralama/bicimlendirme ortak kalsin)."""
    if field_type == LOCAL_NUMBER:
        return {"type": "number"}
    if field_type == LOCAL_DATE:
        return {"type": "date"}
    return {"type": "string"}


def derived_schema(suffix: str) -> dict[str, Any]:
    return {"type": "number"} if suffix == DERIVED_CHANGES else {"type": "datetime"}


def normalize_local_value(field_type: str, value: Any, options: Any = None) -> str:
    """Degeri saklanacak metne cevirir. Bos metin 'degeri sil' demektir."""
    if field_type not in LOCAL_TYPES:
        raise LocalValueError(f"Bilinmeyen alan tipi: {field_type}")
    if value is None:
        return ""

    if field_type == LOCAL_BOOL:
        return _normalize_bool(value)

    text = value if isinstance(value, str) else _plain_scalar(value)
    text = text.strip()
    if not text:
        return ""

    if field_type == LOCAL_TEXT:
        return _clean(text)
    if field_type == LOCAL_NUMBER:
        return _normalize_number_text(text)
    if field_type == LOCAL_DATE:
        return _normalize_date_text(text)
    return _normalize_select_text(text, options)


def format_local_value(field_type: str, stored: Any, tz: tzinfo | None = None) -> str:
    """Saklanan metni ekranda gosterilecek hale getirir."""
    if stored is None:
        return ""
    text = str(stored)
    if not text:
        return ""
    if field_type == LOCAL_BOOL:
        return BOOL_TRUE_TEXT if text == "1" else BOOL_FALSE_TEXT
    if field_type == LOCAL_DATE:
        return _format_date(text)
    return text


def local_sort_key(field_type: str, stored: Any) -> tuple[int, float, str]:
    """Yerel deger icin siralama anahtari; bos deger her zaman sona duser."""
    text = "" if stored is None else str(stored)
    if not text:
        return (1, 0.0, "")
    if field_type == LOCAL_NUMBER:
        number = _as_number(text)
        if number is not None:
            return (0, number, "")
    if field_type == LOCAL_BOOL:
        return (0, 1.0 if text == "1" else 0.0, "")
    if field_type == LOCAL_DATE:
        moment = _parse_datetime(text)
        if moment is not None:
            return (0, moment.timestamp(), "")
    return (0, 0.0, fold(text))


def derived_sort_key(suffix: str, value: Any) -> tuple[int, float, str]:
    """Turetilmis sutun icin siralama anahtari."""
    if suffix == DERIVED_CHANGES:
        return (0, float(value or 0), "")
    moment = _parse_datetime(value)
    if moment is None:
        return (1, 0.0, "")
    return (0, moment.timestamp(), "")


def format_derived_value(suffix: str, value: Any, tz: tzinfo | None = None) -> str:
    if suffix == DERIVED_CHANGES:
        return str(int(value or 0))
    if not value:
        return ""
    return _format_datetime(value, tz)


def normalize_options(field_type: str, options: Any) -> list[str]:
    """Liste tipinde secenekleri temizler; digerlerinde secenek tutulmaz."""
    if field_type != LOCAL_SELECT:
        return []
    if options is None:
        raise LocalValueError("Liste tipinde en az bir seçenek gerekli.")
    if isinstance(options, str):
        candidates = [part for part in options.splitlines()]
    elif isinstance(options, (list, tuple)):
        candidates = [_plain_scalar(item) for item in options]
    else:
        raise LocalValueError("Seçenekler bir liste olmalı.")

    cleaned: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        text = str(item).strip()
        if not text:
            continue
        marker = fold(text)
        if marker in seen:
            continue
        seen.add(marker)
        cleaned.append(text)
    if not cleaned:
        raise LocalValueError("Liste tipinde en az bir seçenek gerekli.")
    return cleaned


# --- yerel alan ic yardimcilari ----------------------------------------


def _plain_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _normalize_bool(value: Any) -> str:
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return "1" if value else "0"
    text = str(value).strip()
    if not text:
        return ""
    marker = fold(text)
    if marker in _TRUE_WORDS:
        return "1"
    if marker in _FALSE_WORDS:
        return "0"
    raise LocalValueError(f"Evet/Hayır bekleniyor, '{text}' anlaşılmadı.")


def _normalize_number_text(text: str) -> str:
    candidate = text.replace(" ", "").replace(" ", "")
    # Turkce klavyede ondalik virgul yaygin; nokta bicimine cevrilir.
    if "," in candidate and "." not in candidate:
        candidate = candidate.replace(",", ".")
    try:
        number = Decimal(candidate)
    except (InvalidOperation, ValueError) as exc:
        raise LocalValueError(f"Sayı bekleniyor, '{text}' anlaşılmadı.") from exc
    if not number.is_finite():
        raise LocalValueError(f"Sayı bekleniyor, '{text}' anlaşılmadı.")
    # normalize() 100 -> 1E+2 uretir; "f" bicimi her zaman duz yazar.
    return format(number.normalize(), "f")


def _normalize_date_text(text: str) -> str:
    iso = _ISO_DATE.match(text)
    if iso:
        year, month, day = (int(part) for part in iso.groups())
    else:
        local = _TR_DATE.match(text)
        if not local:
            raise LocalValueError(f"Tarih bekleniyor (GG.AA.YYYY), '{text}' anlaşılmadı.")
        day, month, year = (int(part) for part in local.groups())
    try:
        return date(year, month, day).isoformat()
    except ValueError as exc:
        raise LocalValueError(f"Geçersiz tarih: '{text}'.") from exc


def _normalize_select_text(text: str, options: Any) -> str:
    choices = [str(item) for item in (options or [])]
    if not choices:
        raise LocalValueError("Bu alanın seçenek listesi boş.")
    marker = fold(text)
    for choice in choices:
        if fold(choice) == marker:
            return choice
    raise LocalValueError(
        f"'{text}' seçenekler arasında yok: " + ", ".join(choices)
    )
