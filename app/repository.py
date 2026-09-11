"""Veri katmani: alan katalogu, kayitlar, gruplar ve grup uyelikleri.

API uclari burayi cagirir; SQL baska yerde durmaz. Islevler baglantiyi disaridan
alir, boylece testler kendi gecici veritabaniyla calisir.
"""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass, field as dataclass_field
from datetime import datetime, timezone
from typing import Any, Iterable, Sequence

from . import fields as field_utils

# Isin kilici paleti; grup renk seridi ve rozetler bu adlari kullanir.
GROUP_COLORS: tuple[str, ...] = ("blue", "green", "purple", "red", "yellow", "white")
DEFAULT_COLOR = "blue"

KIND_MANUAL = "manual"
KIND_FILTER = "filter"
GROUP_KINDS: tuple[str, ...] = (KIND_MANUAL, KIND_FILTER)

COLUMNS_SETTING_KEY = "columns.default"
FALLBACK_COLUMNS: tuple[str, ...] = (
    "issuekey",
    "summary",
    "status",
    "assignee",
    "priority",
    "updated",
)

SORT_DIRECTIONS: tuple[str, ...] = ("asc", "desc")

# ORNEK-123 / demo-1 / .../browse/DEMO-1 hepsi ayni kaliba girer.
KEY_PATTERN = re.compile(r"(?<![A-Za-z0-9_])([A-Za-z][A-Za-z0-9_]*)-(\d+)(?![0-9])")
_TOKEN_SPLIT = re.compile(r"[\s,;|]+")


class RepositoryError(Exception):
    """Kullaniciya gosterilecek dogrulama/bulunamadi hatasi."""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass
class UpsertReport:
    """Kayit yazma sonucu; Guncelle ozeti bunun uzerine kurulur."""

    new: list[str] = dataclass_field(default_factory=list)
    updated: list[str] = dataclass_field(default_factory=list)
    unchanged: list[str] = dataclass_field(default_factory=list)
    changed: dict[str, list[str]] = dataclass_field(default_factory=dict)

    @property
    def fetched(self) -> int:
        return len(self.new) + len(self.updated) + len(self.unchanged)

    def merge(self, other: "UpsertReport") -> None:
        self.new.extend(other.new)
        self.updated.extend(other.updated)
        self.unchanged.extend(other.unchanged)
        self.changed.update(other.changed)


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# --- anahtar ayristirma -------------------------------------------------


def parse_issue_keys(text: str | None) -> list[str]:
    """Serbest metinden kayit anahtarlarini sirayla, tekrarsiz cikarir."""
    if not text:
        return []
    seen: set[str] = set()
    keys: list[str] = []
    for match in KEY_PATTERN.finditer(text):
        key = f"{match.group(1).upper()}-{match.group(2)}"
        if key in seen:
            continue
        seen.add(key)
        keys.append(key)
    return keys


def parse_keys_report(text: str | None) -> tuple[list[str], list[str]]:
    """Anahtarlar ve icinde anahtar bulunmayan parcalar."""
    keys = parse_issue_keys(text)
    invalid: list[str] = []
    seen: set[str] = set()
    for token in _TOKEN_SPLIT.split(text or ""):
        token = token.strip()
        if not token or KEY_PATTERN.search(token):
            continue
        if token in seen:
            continue
        seen.add(token)
        invalid.append(token)
    return keys, invalid


# --- alan katalogu ------------------------------------------------------


def store_fields(conn: sqlite3.Connection, catalog: Sequence[dict[str, Any]]) -> int:
    """Alan katalogunu tazeler. Katalog Jira'nin gercegi oldugu icin tamamen degistirilir."""
    fetched_at = now_iso()
    rows = []
    for item in catalog:
        field_id = item.get("id")
        if not field_id:
            continue
        schema = item.get("schema") or {}
        rows.append(
            (
                str(field_id),
                item.get("name") or str(field_id),
                schema.get("type"),
                1 if item.get("custom") else 0,
                json.dumps(item, ensure_ascii=False),
                fetched_at,
            )
        )
    with conn:
        conn.execute("DELETE FROM jira_fields")
        conn.executemany(
            "INSERT INTO jira_fields (id, name, schema_type, custom, raw_json, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
    return len(rows)


def field_count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) AS c FROM jira_fields").fetchone()["c"])


def list_fields(
    conn: sqlite3.Connection, include_virtual: bool = True, include_local: bool = True
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, name, schema_type, custom, fetched_at FROM jira_fields "
        "ORDER BY name COLLATE NOCASE"
    ).fetchall()
    catalog = [
        {
            "id": row["id"],
            "name": row["name"],
            "schema_type": row["schema_type"],
            "custom": bool(row["custom"]),
            "virtual": False,
            "kind": "jira",
            "fetched_at": row["fetched_at"],
        }
        for row in rows
    ]
    if not include_virtual:
        return catalog

    known = {item["id"] for item in catalog}
    virtual = [
        {
            "id": spec["id"],
            "name": spec["name"],
            "schema_type": spec["schema"].get("type"),
            "custom": False,
            "virtual": True,
            "kind": "virtual",
            "fetched_at": None,
        }
        for field_id, spec in field_utils.VIRTUAL_FIELDS.items()
        if field_id not in known
    ]
    local = local_field_columns(conn) if include_local else []
    return virtual + catalog + local


def local_field_columns(
    conn: sqlite3.Connection, only_tracked: bool = True
) -> list[dict[str, Any]]:
    """Sutun secici icin yerel alanlar, hemen ardindan turetilmis sutunlari.

    `only_tracked` kapatilirsa gecmisi sonradan kapatilmis alanlarin turetilmis
    sutunlari da listeye girer; adlari cozulebilsin diye sema tarafi boyle cagirir.
    """
    entries: list[dict[str, Any]] = []
    for field in list_local_fields(conn):
        entries.append(
            {
                "id": field["column_id"],
                "name": field["name"],
                "schema_type": field_utils.local_schema(field["type"]).get("type"),
                "custom": False,
                "virtual": True,
                "kind": "local",
                "fetched_at": None,
                "local": field,
            }
        )
        if only_tracked and not field["track_history"]:
            continue
        for suffix in field_utils.DERIVED_SUFFIXES:
            entries.append(
                {
                    "id": local_column_id(field["id"], suffix),
                    "name": f"{field['name']} ({field_utils.DERIVED_LABELS[suffix]})",
                    "schema_type": field_utils.derived_schema(suffix).get("type"),
                    "custom": False,
                    "virtual": True,
                    "kind": "derived",
                    "derived": suffix,
                    "parent": field["column_id"],
                    "fetched_at": None,
                    "local": field,
                }
            )
    return entries


def field_schemas(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Alan kimligi -> {name, schema} eslemesi; sanal ve yerel alanlar dahil."""
    result: dict[str, dict[str, Any]] = {
        field_id: {"id": field_id, "name": spec["name"], "schema": dict(spec["schema"])}
        for field_id, spec in field_utils.VIRTUAL_FIELDS.items()
    }
    for row in conn.execute("SELECT id, name, raw_json, schema_type FROM jira_fields").fetchall():
        raw = _loads(row["raw_json"]) or {}
        schema = raw.get("schema") if isinstance(raw.get("schema"), dict) else None
        if schema is None:
            schema = {"type": row["schema_type"]} if row["schema_type"] else {}
        result[row["id"]] = {"id": row["id"], "name": row["name"], "schema": schema}
    for entry in local_field_columns(conn, only_tracked=False):
        derived = entry.get("derived", "")
        result[entry["id"]] = {
            "id": entry["id"],
            "name": entry["name"],
            "schema": field_utils.derived_schema(derived)
            if derived
            else field_utils.local_schema(entry["local"]["type"]),
            "local": entry["local"],
            "derived": derived,
        }
    return result


def field_name(schemas: dict[str, dict[str, Any]], field_id: str) -> str:
    entry = schemas.get(field_id)
    return entry["name"] if entry else field_id


# --- genel varsayilan sutunlar ------------------------------------------


def default_columns(conn: sqlite3.Connection) -> list[str]:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (COLUMNS_SETTING_KEY,)).fetchone()
    columns = _loads(row["value"]) if row else None
    if isinstance(columns, list):
        cleaned = [str(item) for item in columns if str(item).strip()]
        if cleaned:
            return cleaned
    return list(FALLBACK_COLUMNS)


def set_default_columns(conn: sqlite3.Connection, columns: Sequence[str]) -> list[str]:
    cleaned = _clean_columns(columns)
    if not cleaned:
        raise RepositoryError("invalid_columns", "En az bir sutun secilmeli.")
    with conn:
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (COLUMNS_SETTING_KEY, json.dumps(cleaned, ensure_ascii=False)),
        )
    return cleaned


# --- kayitlar -----------------------------------------------------------


def upsert_issues(conn: sqlite3.Connection, issues: Iterable[dict[str, Any]]) -> UpsertReport:
    """Kayitlari yazar; yeni/degisen/aynikalan ayrimini ve degisen alanlari dondurur."""
    report = UpsertReport()
    fetched_at = now_iso()
    for issue in issues:
        key = str(issue.get("key") or "").strip().upper()
        if not key:
            continue
        row = conn.execute("SELECT raw_json FROM issues WHERE key = ?", (key,)).fetchone()
        previous = _loads(row["raw_json"]) if row else None
        raw = json.dumps(issue, ensure_ascii=False)
        conn.execute(
            "INSERT INTO issues (key, jira_id, raw_json, fetched_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET jira_id = excluded.jira_id, "
            "raw_json = excluded.raw_json, fetched_at = excluded.fetched_at",
            (key, str(issue.get("id") or "") or None, raw, fetched_at),
        )
        if previous is None:
            report.new.append(key)
            continue
        changed = field_utils.changed_field_ids(previous, issue)
        if changed:
            report.updated.append(key)
            report.changed[key] = changed
        else:
            report.unchanged.append(key)
    conn.commit()
    return report


def get_issue(conn: sqlite3.Connection, key: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT key, jira_id, raw_json, fetched_at FROM issues WHERE key = ?",
        (str(key).strip().upper(),),
    ).fetchone()
    if row is None:
        return None
    return {"key": row["key"], "fetched_at": row["fetched_at"], "raw": _loads(row["raw_json"]) or {}}


def get_issues(conn: sqlite3.Connection, keys: Sequence[str]) -> dict[str, dict[str, Any]]:
    """Coklu okuma; donen sozluk anahtarlari buyuk harflidir."""
    wanted = [str(key).strip().upper() for key in keys if str(key).strip()]
    result: dict[str, dict[str, Any]] = {}
    for start in range(0, len(wanted), 400):  # SQLite degisken sinirinin altinda kal
        chunk = wanted[start : start + 400]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT key, raw_json, fetched_at FROM issues WHERE key IN ({placeholders})",
            chunk,
        ).fetchall()
        for row in rows:
            result[row["key"]] = {
                "key": row["key"],
                "fetched_at": row["fetched_at"],
                "raw": _loads(row["raw_json"]) or {},
            }
    return result


def issue_count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) AS c FROM issues").fetchone()["c"])


def purge_orphan_issues(conn: sqlite3.Connection) -> int:
    """Hicbir grupta gecmeyen kayitlari siler. Otomatik cagrilmaz, istek uzerine.

    Yerel deger ve gecmis normalde kayit gruptan dusse bile korunur (kayit geri
    gelirse bilgi kaybolmasin); temizlik yalnizca burada, acik istek uzerine olur.
    """
    with conn:
        cursor = conn.execute(
            "DELETE FROM issues WHERE key NOT IN (SELECT issue_key FROM group_items)"
        )
        removed = cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0
        conn.execute(
            "DELETE FROM local_values WHERE issue_key NOT IN (SELECT issue_key FROM group_items)"
        )
        conn.execute(
            "DELETE FROM local_value_history "
            "WHERE issue_key NOT IN (SELECT issue_key FROM group_items)"
        )
    return removed


# --- gruplar ------------------------------------------------------------


def create_group(
    conn: sqlite3.Connection,
    name: str,
    kind: str,
    jql: str | None = None,
    color: str = DEFAULT_COLOR,
    columns: Sequence[str] | None = None,
    sort: dict[str, Any] | None = None,
) -> dict[str, Any]:
    clean_name = (name or "").strip()
    if not clean_name:
        raise RepositoryError("invalid_name", "Grup adi bos olamaz.")
    if kind not in GROUP_KINDS:
        raise RepositoryError("invalid_kind", "Grup turu 'manual' ya da 'filter' olmali.")
    clean_jql = (jql or "").strip()
    if kind == KIND_FILTER and not clean_jql:
        raise RepositoryError("invalid_jql", "Filtre grubu icin JQL zorunlu.")
    if kind == KIND_MANUAL:
        clean_jql = ""

    position = conn.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 AS p FROM groups"
    ).fetchone()["p"]

    with conn:
        cursor = conn.execute(
            "INSERT INTO groups (name, kind, jql, color, columns_json, sort_json, position, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                clean_name,
                kind,
                clean_jql or None,
                _clean_color(color),
                _dump_columns(columns),
                _dump_sort(sort),
                int(position),
                now_iso(),
            ),
        )
    return get_group(conn, int(cursor.lastrowid))  # type: ignore[return-value]


def list_groups(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT g.*, (SELECT COUNT(*) FROM group_items i WHERE i.group_id = g.id) AS item_count "
        "FROM groups g ORDER BY g.position, g.id"
    ).fetchall()
    return [_group_dict(row) for row in rows]


def get_group(conn: sqlite3.Connection, group_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT g.*, (SELECT COUNT(*) FROM group_items i WHERE i.group_id = g.id) AS item_count "
        "FROM groups g WHERE g.id = ?",
        (int(group_id),),
    ).fetchone()
    return _group_dict(row) if row else None


def require_group(conn: sqlite3.Connection, group_id: int) -> dict[str, Any]:
    group = get_group(conn, group_id)
    if group is None:
        raise RepositoryError("group_not_found", "Grup bulunamadi.", status=404)
    return group


def update_group(conn: sqlite3.Connection, group_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    group = require_group(conn, group_id)
    updates: dict[str, Any] = {}

    if "name" in payload:
        clean_name = str(payload["name"] or "").strip()
        if not clean_name:
            raise RepositoryError("invalid_name", "Grup adi bos olamaz.")
        updates["name"] = clean_name

    kind = group["kind"]
    if "kind" in payload and payload["kind"] is not None:
        kind = str(payload["kind"])
        if kind not in GROUP_KINDS:
            raise RepositoryError("invalid_kind", "Grup turu 'manual' ya da 'filter' olmali.")
        updates["kind"] = kind

    if "jql" in payload or "kind" in payload:
        clean_jql = str(payload.get("jql", group["jql"]) or "").strip()
        if kind == KIND_FILTER and not clean_jql:
            raise RepositoryError("invalid_jql", "Filtre grubu icin JQL zorunlu.")
        updates["jql"] = (clean_jql or None) if kind == KIND_FILTER else None

    if "color" in payload:
        updates["color"] = _clean_color(payload["color"])

    if "columns" in payload:
        columns = payload["columns"]
        updates["columns_json"] = _dump_columns(columns) if columns else None

    if "sort" in payload:
        updates["sort_json"] = _dump_sort(payload["sort"])

    if not updates:
        return group

    assignments = ", ".join(f"{column} = ?" for column in updates)
    with conn:
        conn.execute(
            f"UPDATE groups SET {assignments} WHERE id = ?",
            (*updates.values(), int(group_id)),
        )
    return require_group(conn, group_id)


def delete_group(conn: sqlite3.Connection, group_id: int) -> bool:
    require_group(conn, group_id)
    with conn:
        conn.execute("DELETE FROM groups WHERE id = ?", (int(group_id),))
    return True


def reorder_groups(conn: sqlite3.Connection, ids: Sequence[int]) -> list[dict[str, Any]]:
    known = {group["id"] for group in list_groups(conn)}
    ordered: list[int] = []
    for value in ids:
        try:
            group_id = int(value)
        except (TypeError, ValueError) as exc:
            raise RepositoryError("invalid_order", "Sira listesi yalnizca grup kimligi icerir.") from exc
        if group_id not in known or group_id in ordered:
            continue
        ordered.append(group_id)
    # Listede gecmeyen gruplar mevcut sirasini koruyarak sona eklenir.
    ordered.extend(group_id for group_id in sorted(known) if group_id not in ordered)

    with conn:
        for position, group_id in enumerate(ordered):
            conn.execute("UPDATE groups SET position = ? WHERE id = ?", (position, group_id))
    return list_groups(conn)


def group_columns(conn: sqlite3.Connection, group: dict[str, Any]) -> list[str]:
    """Grubun sutunlari; bos ise genel varsayilan."""
    columns = group.get("columns") or []
    return list(columns) if columns else default_columns(conn)


# --- grup uyeleri -------------------------------------------------------


def list_items(conn: sqlite3.Connection, group_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT issue_key, pinned FROM group_items WHERE group_id = ? ORDER BY rowid",
        (int(group_id),),
    ).fetchall()
    return [{"key": row["issue_key"], "pinned": bool(row["pinned"])} for row in rows]


def list_item_keys(conn: sqlite3.Connection, group_id: int) -> list[str]:
    return [item["key"] for item in list_items(conn, group_id)]


def all_member_keys(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        "SELECT DISTINCT issue_key FROM group_items ORDER BY issue_key"
    ).fetchall()
    return [row["issue_key"] for row in rows]


def add_items(
    conn: sqlite3.Connection,
    group_id: int,
    keys: Sequence[str],
    pinned: bool | None = None,
) -> dict[str, list[str]]:
    """Toplu ekleme. Filtre grubunda elle eklenen kayit varsayilan olarak iglenir."""
    group = require_group(conn, group_id)
    if pinned is None:
        pinned = group["kind"] == KIND_FILTER

    existing = {item["key"] for item in list_items(conn, group_id)}
    added: list[str] = []
    already: list[str] = []
    with conn:
        for raw_key in keys:
            key = str(raw_key).strip().upper()
            if not key:
                continue
            if key in existing:
                already.append(key)
                continue
            conn.execute(
                "INSERT INTO group_items (group_id, issue_key, pinned) VALUES (?, ?, ?)",
                (int(group_id), key, 1 if pinned else 0),
            )
            existing.add(key)
            added.append(key)
    return {"added": added, "already": already}


def remove_item(conn: sqlite3.Connection, group_id: int, key: str) -> bool:
    require_group(conn, group_id)
    with conn:
        cursor = conn.execute(
            "DELETE FROM group_items WHERE group_id = ? AND issue_key = ?",
            (int(group_id), str(key).strip().upper()),
        )
    if not cursor.rowcount:
        raise RepositoryError("item_not_found", "Kayit bu grupta yok.", status=404)
    return True


def toggle_pin(conn: sqlite3.Connection, group_id: int, key: str) -> bool:
    require_group(conn, group_id)
    clean_key = str(key).strip().upper()
    row = conn.execute(
        "SELECT pinned FROM group_items WHERE group_id = ? AND issue_key = ?",
        (int(group_id), clean_key),
    ).fetchone()
    if row is None:
        raise RepositoryError("item_not_found", "Kayit bu grupta yok.", status=404)
    new_state = 0 if row["pinned"] else 1
    with conn:
        conn.execute(
            "UPDATE group_items SET pinned = ? WHERE group_id = ? AND issue_key = ?",
            (new_state, int(group_id), clean_key),
        )
    return bool(new_state)


def replace_filter_members(
    conn: sqlite3.Connection,
    group_id: int,
    keys: Sequence[str],
) -> dict[str, Any]:
    """JQL sonucunu uyelige yansitir; iglenmis kayitlar sonuctan dusse bile kalir."""
    require_group(conn, group_id)
    current = list_items(conn, group_id)
    pinned = [item["key"] for item in current if item["pinned"]]
    existing = {item["key"] for item in current}
    incoming: list[str] = []
    seen: set[str] = set()
    for raw_key in keys:
        key = str(raw_key).strip().upper()
        if key and key not in seen:
            seen.add(key)
            incoming.append(key)

    keep = set(pinned) | seen
    removed = sorted(existing - keep)
    added = [key for key in incoming if key not in existing]

    with conn:
        if removed:
            placeholders = ",".join("?" for _ in removed)
            conn.execute(
                f"DELETE FROM group_items WHERE group_id = ? AND pinned = 0 "
                f"AND issue_key IN ({placeholders})",
                (int(group_id), *removed),
            )
        for key in added:
            conn.execute(
                "INSERT OR IGNORE INTO group_items (group_id, issue_key, pinned) VALUES (?, ?, 0)",
                (int(group_id), key),
            )
    return {"added": len(added), "removed": len(removed), "pinned_kept": len(pinned)}


# --- yerel (kullanici tanimli) alanlar ----------------------------------
#
# Alanlar GENEL tanimlanir (grup ozelinde degil), deger kayit basina tektir.
# Sutun kimligi "local:<id>"; gecmisi tutulan alanlarda ayrica turetilmis
# "local:<id>:changed_at" ve "local:<id>:changes" sanal sutunlari uretilir.

LOCAL_PREFIX = "local:"

LOCAL_NAME_LIMIT = 60


def local_column_id(field_id: int, suffix: str = "") -> str:
    base = f"{LOCAL_PREFIX}{int(field_id)}"
    return f"{base}:{suffix}" if suffix else base


def parse_local_column(column: str) -> tuple[int, str] | None:
    """'local:3' -> (3, ''), 'local:3:changes' -> (3, 'changes'); digerleri None."""
    text = str(column or "")
    if not text.startswith(LOCAL_PREFIX):
        return None
    rest = text[len(LOCAL_PREFIX) :]
    suffix = ""
    if ":" in rest:
        rest, suffix = rest.split(":", 1)
        if suffix not in field_utils.DERIVED_SUFFIXES:
            return None
    try:
        return int(rest), suffix
    except ValueError:
        return None


def list_local_fields(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT f.*, "
        "(SELECT COUNT(*) FROM local_values v WHERE v.field_id = f.id AND v.value <> '') AS value_count, "
        "(SELECT COUNT(*) FROM local_value_history h WHERE h.field_id = f.id) AS history_count "
        "FROM local_fields f ORDER BY f.position, f.id"
    ).fetchall()
    usage = _local_field_usage(conn)
    return [_local_field_dict(row, usage.get(row["id"], 0)) for row in rows]


def local_fields_by_id(conn: sqlite3.Connection) -> dict[int, dict[str, Any]]:
    return {item["id"]: item for item in list_local_fields(conn)}


def get_local_field(conn: sqlite3.Connection, field_id: int) -> dict[str, Any] | None:
    try:
        wanted = int(field_id)
    except (TypeError, ValueError):
        return None
    for item in list_local_fields(conn):
        if item["id"] == wanted:
            return item
    return None


def require_local_field(conn: sqlite3.Connection, field_id: int) -> dict[str, Any]:
    field = get_local_field(conn, field_id)
    if field is None:
        raise RepositoryError("local_field_not_found", "Yerel alan bulunamadi.", status=404)
    return field


def create_local_field(
    conn: sqlite3.Connection,
    name: str,
    type: str,
    options: Any = None,
    track_history: bool = False,
) -> dict[str, Any]:
    clean_name = _clean_local_name(conn, name)
    clean_type = str(type or "").strip().lower()
    if clean_type not in field_utils.LOCAL_TYPES:
        raise RepositoryError(
            "invalid_type", "Alan tipi su listeden olmali: " + ", ".join(field_utils.LOCAL_TYPES)
        )
    clean_options = _clean_local_options(clean_type, options)

    position = conn.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 AS p FROM local_fields"
    ).fetchone()["p"]
    with conn:
        cursor = conn.execute(
            "INSERT INTO local_fields (name, type, options_json, position, track_history, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                clean_name,
                clean_type,
                json.dumps(clean_options, ensure_ascii=False) if clean_options else None,
                int(position),
                1 if track_history else 0,
                now_iso(),
            ),
        )
    return require_local_field(conn, int(cursor.lastrowid))  # type: ignore[arg-type]


def update_local_field(
    conn: sqlite3.Connection, field_id: int, payload: dict[str, Any]
) -> dict[str, Any]:
    """Tip degismez; ad, secenekler ve gecmis anahtari degisebilir."""
    field = require_local_field(conn, field_id)
    if "type" in payload and payload["type"] is not None:
        wanted = str(payload["type"]).strip().lower()
        if wanted != field["type"]:
            raise RepositoryError("type_immutable", "Alan tipi sonradan degistirilemez.")

    updates: dict[str, Any] = {}
    if "name" in payload:
        updates["name"] = _clean_local_name(conn, payload["name"], skip_id=field["id"])
    if "options" in payload and field["type"] == field_utils.LOCAL_SELECT:
        cleaned = _clean_local_options(field["type"], payload["options"])
        updates["options_json"] = json.dumps(cleaned, ensure_ascii=False)
    if "track_history" in payload:
        updates["track_history"] = 1 if payload["track_history"] else 0

    if updates:
        assignments = ", ".join(f"{column} = ?" for column in updates)
        with conn:
            conn.execute(
                f"UPDATE local_fields SET {assignments} WHERE id = ?",
                (*updates.values(), field["id"]),
            )
    return require_local_field(conn, field["id"])


def delete_local_field(conn: sqlite3.Connection, field_id: int) -> dict[str, Any]:
    """Alani, degerlerini ve gecmisini siler; sutun listelerinden de duser."""
    field = require_local_field(conn, field_id)
    columns = [local_column_id(field["id"], suffix) for suffix in ("", *field_utils.DERIVED_SUFFIXES)]
    with conn:
        conn.execute("DELETE FROM local_value_history WHERE field_id = ?", (field["id"],))
        conn.execute("DELETE FROM local_values WHERE field_id = ?", (field["id"],))
        conn.execute("DELETE FROM local_fields WHERE id = ?", (field["id"],))
    dropped = drop_columns_everywhere(conn, columns)
    return {
        "ok": True,
        "values": field["value_count"],
        "history": field["history_count"],
        "groups": dropped,
    }


def reorder_local_fields(conn: sqlite3.Connection, ids: Sequence[int]) -> list[dict[str, Any]]:
    known = {item["id"] for item in list_local_fields(conn)}
    ordered: list[int] = []
    for value in ids:
        try:
            local_id = int(value)
        except (TypeError, ValueError) as exc:
            raise RepositoryError("invalid_order", "Sira listesi yalnizca alan kimligi icerir.") from exc
        if local_id not in known or local_id in ordered:
            continue
        ordered.append(local_id)
    ordered.extend(local_id for local_id in sorted(known) if local_id not in ordered)
    with conn:
        for position, local_id in enumerate(ordered):
            conn.execute("UPDATE local_fields SET position = ? WHERE id = ?", (position, local_id))
    return list_local_fields(conn)


def drop_columns_everywhere(conn: sqlite3.Connection, columns: Sequence[str]) -> int:
    """Verilen sutunlari grup sutunlarindan, siralamadan ve varsayilandan duser."""
    unwanted = {str(column) for column in columns}
    touched = 0
    with conn:
        for row in conn.execute("SELECT id, columns_json, sort_json FROM groups").fetchall():
            updates: dict[str, Any] = {}
            current = _loads(row["columns_json"])
            if isinstance(current, list):
                kept = [str(item) for item in current if str(item) not in unwanted]
                if len(kept) != len(current):
                    updates["columns_json"] = (
                        json.dumps(kept, ensure_ascii=False) if kept else None
                    )
            sort = _loads(row["sort_json"])
            if isinstance(sort, dict) and str(sort.get("field") or "") in unwanted:
                updates["sort_json"] = None
            if updates:
                assignments = ", ".join(f"{column} = ?" for column in updates)
                conn.execute(
                    f"UPDATE groups SET {assignments} WHERE id = ?",
                    (*updates.values(), row["id"]),
                )
                touched += 1

        stored = default_columns(conn)
        kept = [column for column in stored if column not in unwanted]
        if kept and len(kept) != len(stored):
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (COLUMNS_SETTING_KEY, json.dumps(kept, ensure_ascii=False)),
            )
    return touched


# --- yerel degerler ve gecmis -------------------------------------------


def issue_is_known(conn: sqlite3.Connection, key: str) -> bool:
    """Kayit ya cekilmis ya da bir grupta uye olmali; yazim hatasi boylece yakalanir."""
    clean = str(key).strip().upper()
    if not clean:
        return False
    row = conn.execute(
        "SELECT 1 FROM issues WHERE key = ? "
        "UNION ALL SELECT 1 FROM group_items WHERE issue_key = ? LIMIT 1",
        (clean, clean),
    ).fetchone()
    return row is not None


def local_values_for(
    conn: sqlite3.Connection, keys: Sequence[str]
) -> dict[str, dict[int, dict[str, Any]]]:
    """Coklu okuma: {KAYIT: {alan_id: {'value':..., 'updated_at':...}}}."""
    wanted = [str(key).strip().upper() for key in keys if str(key).strip()]
    result: dict[str, dict[int, dict[str, Any]]] = {}
    for start in range(0, len(wanted), 400):
        chunk = wanted[start : start + 400]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT issue_key, field_id, value, updated_at FROM local_values "
            f"WHERE issue_key IN ({placeholders})",
            chunk,
        ).fetchall()
        for row in rows:
            bucket = result.setdefault(row["issue_key"], {})
            bucket[row["field_id"]] = {
                "value": row["value"] or "",
                "updated_at": row["updated_at"],
            }
    return result


def history_stats(
    conn: sqlite3.Connection, keys: Sequence[str]
) -> dict[str, dict[int, dict[str, Any]]]:
    """Turetilmis sutunlarin kaynagi: hucre basina degisim sayisi ve son an."""
    wanted = [str(key).strip().upper() for key in keys if str(key).strip()]
    result: dict[str, dict[int, dict[str, Any]]] = {}
    for start in range(0, len(wanted), 400):
        chunk = wanted[start : start + 400]
        placeholders = ",".join("?" for _ in chunk)
        rows = conn.execute(
            f"SELECT issue_key, field_id, COUNT(*) AS changes, MAX(changed_at) AS last_change "
            f"FROM local_value_history WHERE issue_key IN ({placeholders}) "
            f"GROUP BY issue_key, field_id",
            chunk,
        ).fetchall()
        for row in rows:
            bucket = result.setdefault(row["issue_key"], {})
            bucket[row["field_id"]] = {
                "changes": int(row["changes"]),
                "changed_at": row["last_change"],
            }
    return result


def get_local_value(conn: sqlite3.Connection, key: str, field_id: int) -> str:
    row = conn.execute(
        "SELECT value FROM local_values WHERE issue_key = ? AND field_id = ?",
        (str(key).strip().upper(), int(field_id)),
    ).fetchone()
    return (row["value"] or "") if row else ""


def set_local_value(
    conn: sqlite3.Connection, key: str, field_id: int, value: Any
) -> dict[str, Any]:
    """Degeri dogrular, yazar ve (gerekiyorsa) gecmise satir ekler."""
    clean_key = str(key).strip().upper()
    field = require_local_field(conn, field_id)
    if not issue_is_known(conn, clean_key):
        raise RepositoryError("issue_not_found", "Kayit bulunamadi.", status=404)
    try:
        normalized = field_utils.normalize_local_value(field["type"], value, field["options"])
    except field_utils.LocalValueError as exc:
        raise RepositoryError("invalid_value", str(exc)) from exc

    previous = get_local_value(conn, clean_key, field["id"])
    changed = normalized != previous
    stamp = now_iso()
    with conn:
        if not normalized:
            conn.execute(
                "DELETE FROM local_values WHERE issue_key = ? AND field_id = ?",
                (clean_key, field["id"]),
            )
        else:
            conn.execute(
                "INSERT INTO local_values (issue_key, field_id, value, updated_at) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(issue_key, field_id) DO UPDATE SET "
                "value = excluded.value, updated_at = excluded.updated_at",
                (clean_key, field["id"], normalized, stamp),
            )
        # Gecmis yalnizca acik alanlarda ve deger gercekten degistiyse yazilir.
        if changed and field["track_history"]:
            conn.execute(
                "INSERT INTO local_value_history "
                "(issue_key, field_id, old_value, new_value, changed_at) VALUES (?, ?, ?, ?, ?)",
                (clean_key, field["id"], previous or None, normalized or None, stamp),
            )

    stats = history_stats(conn, [clean_key]).get(clean_key, {}).get(field["id"], {})
    return {
        "key": clean_key,
        "field": local_column_id(field["id"]),
        "field_id": field["id"],
        "value": normalized,
        "text": field_utils.format_local_value(field["type"], normalized),
        "updated_at": stamp if normalized else None,
        "changed": changed,
        "changes": int(stats.get("changes", 0)),
        "changed_at": stats.get("changed_at"),
    }


def list_local_history(
    conn: sqlite3.Connection, key: str, field_id: int
) -> list[dict[str, Any]]:
    """Zaman cizelgesi: yeniden eskiye."""
    field = require_local_field(conn, field_id)
    rows = conn.execute(
        "SELECT id, old_value, new_value, changed_at FROM local_value_history "
        "WHERE issue_key = ? AND field_id = ? ORDER BY id DESC",
        (str(key).strip().upper(), field["id"]),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "old_value": row["old_value"] or "",
            "new_value": row["new_value"] or "",
            "old_text": field_utils.format_local_value(field["type"], row["old_value"]),
            "new_text": field_utils.format_local_value(field["type"], row["new_value"]),
            "changed_at": row["changed_at"],
        }
        for row in rows
    ]


def delete_local_history_entry(
    conn: sqlite3.Connection, key: str, field_id: int, history_id: int
) -> bool:
    require_local_field(conn, field_id)
    with conn:
        cursor = conn.execute(
            "DELETE FROM local_value_history WHERE id = ? AND issue_key = ? AND field_id = ?",
            (int(history_id), str(key).strip().upper(), int(field_id)),
        )
    if not cursor.rowcount:
        raise RepositoryError("history_not_found", "Gecmis satiri bulunamadi.", status=404)
    return True


def clear_local_history(conn: sqlite3.Connection, key: str, field_id: int) -> int:
    require_local_field(conn, field_id)
    with conn:
        cursor = conn.execute(
            "DELETE FROM local_value_history WHERE issue_key = ? AND field_id = ?",
            (str(key).strip().upper(), int(field_id)),
        )
    return cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0


def _local_field_usage(conn: sqlite3.Connection) -> dict[int, int]:
    """Alanin kac grubun sutun listesinde gectigi (turetilmisler dahil)."""
    usage: dict[int, int] = {}
    for row in conn.execute("SELECT columns_json FROM groups").fetchall():
        columns = _loads(row["columns_json"])
        if not isinstance(columns, list):
            continue
        seen: set[int] = set()
        for column in columns:
            parsed = parse_local_column(str(column))
            if parsed is not None:
                seen.add(parsed[0])
        for local_id in seen:
            usage[local_id] = usage.get(local_id, 0) + 1
    return usage


def _local_field_dict(row: sqlite3.Row, group_count: int) -> dict[str, Any]:
    options = _loads(row["options_json"])
    field_type = row["type"]
    return {
        "id": row["id"],
        "column_id": local_column_id(row["id"]),
        "name": row["name"],
        "type": field_type,
        "type_label": field_utils.LOCAL_TYPE_LABELS.get(field_type, field_type),
        "options": [str(item) for item in options] if isinstance(options, list) else [],
        "track_history": bool(row["track_history"]),
        "position": row["position"],
        "created_at": row["created_at"],
        "value_count": row["value_count"] if "value_count" in row.keys() else 0,
        "history_count": row["history_count"] if "history_count" in row.keys() else 0,
        "group_count": group_count,
    }


def _clean_local_name(conn: sqlite3.Connection, name: Any, skip_id: int | None = None) -> str:
    text = str(name or "").strip()
    if not text:
        raise RepositoryError("invalid_name", "Alan adi bos olamaz.")
    if len(text) > LOCAL_NAME_LIMIT:
        raise RepositoryError("invalid_name", f"Alan adi en fazla {LOCAL_NAME_LIMIT} karakter.")
    if text.startswith(LOCAL_PREFIX):
        raise RepositoryError("invalid_name", "Alan adi 'local:' ile baslayamaz.")
    marker = field_utils.fold(text)
    rows = conn.execute("SELECT id, name FROM local_fields").fetchall()
    for row in rows:
        if skip_id is not None and row["id"] == skip_id:
            continue
        if field_utils.fold(row["name"]) == marker:
            raise RepositoryError("duplicate_name", f"'{text}' adinda bir alan zaten var.")
    return text


def _clean_local_options(field_type: str, options: Any) -> list[str]:
    try:
        return field_utils.normalize_options(field_type, options)
    except field_utils.LocalValueError as exc:
        raise RepositoryError("invalid_options", str(exc)) from exc


# --- ic yardimcilar -----------------------------------------------------


def _group_dict(row: sqlite3.Row) -> dict[str, Any]:
    columns = _loads(row["columns_json"])
    sort = _loads(row["sort_json"])
    return {
        "id": row["id"],
        "name": row["name"],
        "kind": row["kind"],
        "jql": row["jql"] or "",
        "color": row["color"] or DEFAULT_COLOR,
        "columns": [str(item) for item in columns] if isinstance(columns, list) else [],
        "sort": sort if isinstance(sort, dict) else None,
        "position": row["position"],
        "created_at": row["created_at"],
        "count": row["item_count"] if "item_count" in row.keys() else 0,
    }


def _clean_color(color: Any) -> str:
    text = str(color or "").strip().lower()
    if not text:
        return DEFAULT_COLOR
    if text not in GROUP_COLORS:
        raise RepositoryError("invalid_color", "Renk paletin disinda: " + ", ".join(GROUP_COLORS))
    return text


def _clean_columns(columns: Sequence[str] | None) -> list[str]:
    if not columns:
        return []
    cleaned: list[str] = []
    for item in columns:
        text = str(item).strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def _dump_columns(columns: Sequence[str] | None) -> str | None:
    cleaned = _clean_columns(columns)
    return json.dumps(cleaned, ensure_ascii=False) if cleaned else None


def _dump_sort(sort: Any) -> str | None:
    if not sort:
        return None
    if not isinstance(sort, dict):
        raise RepositoryError("invalid_sort", "Siralama {'field': ..., 'dir': ...} olmali.")
    field_id = str(sort.get("field") or "").strip()
    if not field_id:
        return None
    direction = str(sort.get("dir") or "asc").strip().lower()
    if direction not in SORT_DIRECTIONS:
        raise RepositoryError("invalid_sort", "Siralama yonu 'asc' ya da 'desc' olmali.")
    return json.dumps({"field": field_id, "dir": direction}, ensure_ascii=False)


def _loads(value: Any) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None
