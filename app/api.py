"""HTTP API uclari. Is mantigi ince tutulur: SQL repository'de, ag jira_client'ta."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from . import __version__, db, fields as field_utils, repository
from .context import AppContext
from .jira_client import JiraError
from .lifecycle import BEAT_INTERVAL_SECONDS
from .repository import RepositoryError
from .settings_store import MODE_CLOUD, MODE_SERVER

router = APIRouter(prefix="/api")


def error_response(code: str, message: str, status: int = 400) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def get_context(request: Request) -> AppContext:
    return request.app.state.context


# --- kabuk --------------------------------------------------------------


@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    context = get_context(request)
    return {
        "status": "ok",
        "app": "holocron",
        "version": __version__,
        "schema_version": db.current_version(context.conn),
        "heartbeat": context.heartbeat.status(),
    }


@router.post("/heartbeat")
def heartbeat(request: Request) -> dict[str, Any]:
    context = get_context(request)
    context.heartbeat.beat()
    return {"ok": True, "interval": BEAT_INTERVAL_SECONDS, "timeout": context.heartbeat.timeout}


@router.post("/shutdown")
def shutdown(request: Request) -> dict[str, Any]:
    context = get_context(request)
    context.heartbeat.request_stop()
    context.shutdown_hook()
    return {"ok": True, "message": "Holocron kapatiliyor."}


# --- ayarlar ------------------------------------------------------------


@router.get("/settings")
def read_settings(request: Request) -> dict[str, Any]:
    context = get_context(request)
    return {"settings": context.settings.public_view()}


@router.put("/settings")
def write_settings(request: Request, payload: dict[str, Any] = Body(default_factory=dict)):
    context = get_context(request)
    mode = payload.get("jira.mode")
    if mode is not None and mode not in (MODE_SERVER, MODE_CLOUD):
        return error_response("invalid_mode", "Mod yalnizca 'server' veya 'cloud' olabilir.")
    base_url = payload.get("jira.base_url")
    if base_url and not str(base_url).startswith(("http://", "https://")):
        return error_response("invalid_base_url", "Adres http:// veya https:// ile baslamali.")
    context.settings.apply(payload)
    return {"settings": context.settings.public_view()}


@router.get("/settings/columns")
def read_default_columns(request: Request) -> dict[str, Any]:
    context = get_context(request)
    return {"columns": repository.default_columns(context.conn)}


@router.put("/settings/columns")
def write_default_columns(request: Request, payload: dict[str, Any] = Body(default_factory=dict)):
    context = get_context(request)
    columns = payload.get("columns")
    if not isinstance(columns, list):
        return error_response("invalid_columns", "columns bir liste olmali.")
    with context.db_lock:
        saved = repository.set_default_columns(context.conn, columns)
    return {"columns": saved}


# --- Jira baglantisi ve alan katalogu ------------------------------------


@router.post("/jira/test")
def test_connection(request: Request):
    context = get_context(request)
    config = context.settings.jira_config()
    if not config.base_url:
        return error_response("config_missing", "Once Jira adresini girin.")
    try:
        client = context.client_factory(config)
        return {"result": client.test_connection()}
    except JiraError as exc:
        return error_response(exc.code, exc.message, status=400)


@router.post("/jira/fields/refresh")
def refresh_fields(request: Request):
    context = get_context(request)
    config = context.settings.jira_config()
    if not config.base_url:
        return error_response("config_missing", "Once Jira adresini girin.")
    try:
        client = context.client_factory(config)
        catalog = client.fetch_fields()
    except JiraError as exc:
        return error_response(exc.code, exc.message, status=400)

    with context.db_lock:
        stored = repository.store_fields(context.conn, catalog)
    return {"count": stored, "fetched_at": repository.now_iso()}


@router.get("/jira/fields")
def list_jira_fields(request: Request) -> dict[str, Any]:
    context = get_context(request)
    return {"fields": repository.list_fields(context.conn, include_virtual=False)}


@router.get("/fields")
def list_all_fields(request: Request) -> dict[str, Any]:
    """Katalog + sanal alanlar; sutun secici bu ucu kullanir."""
    context = get_context(request)
    return {"fields": repository.list_fields(context.conn)}


# --- gruplar ------------------------------------------------------------


@router.get("/groups")
def list_groups(request: Request) -> dict[str, Any]:
    context = get_context(request)
    return {
        "groups": repository.list_groups(context.conn),
        "colors": list(repository.GROUP_COLORS),
        "default_columns": repository.default_columns(context.conn),
    }


@router.post("/groups")
def create_group(request: Request, payload: dict[str, Any] = Body(default_factory=dict)):
    context = get_context(request)
    with context.db_lock:
        group = repository.create_group(
            context.conn,
            name=str(payload.get("name") or ""),
            kind=str(payload.get("kind") or repository.KIND_MANUAL),
            jql=payload.get("jql"),
            color=payload.get("color") or repository.DEFAULT_COLOR,
            columns=payload.get("columns"),
            sort=payload.get("sort"),
        )
    return {"group": group}


@router.post("/groups/reorder")
def reorder_groups(request: Request, payload: dict[str, Any] = Body(default_factory=dict)):
    context = get_context(request)
    ids = payload.get("ids")
    if not isinstance(ids, list):
        return error_response("invalid_order", "ids bir liste olmali.")
    with context.db_lock:
        groups = repository.reorder_groups(context.conn, ids)
    return {"groups": groups}


@router.get("/groups/{group_id}")
def read_group(request: Request, group_id: int) -> dict[str, Any]:
    context = get_context(request)
    group = repository.require_group(context.conn, group_id)
    return {"group": group, "columns": repository.group_columns(context.conn, group)}


@router.put("/groups/{group_id}")
def write_group(request: Request, group_id: int, payload: dict[str, Any] = Body(default_factory=dict)):
    context = get_context(request)
    with context.db_lock:
        group = repository.update_group(context.conn, group_id, payload)
    return {"group": group}


@router.delete("/groups/{group_id}")
def drop_group(request: Request, group_id: int) -> dict[str, Any]:
    context = get_context(request)
    with context.db_lock:
        repository.delete_group(context.conn, group_id)
    return {"ok": True}


# --- grup uyeleri -------------------------------------------------------


@router.post("/groups/{group_id}/items")
def add_group_items(
    request: Request,
    group_id: int,
    payload: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    context = get_context(request)
    text = payload.get("text")
    if text is None and isinstance(payload.get("keys"), list):
        text = " ".join(str(item) for item in payload["keys"])
    keys, invalid = repository.parse_keys_report(str(text or ""))
    with context.db_lock:
        result = repository.add_items(context.conn, group_id, keys)
        group = repository.require_group(context.conn, group_id)
    return {
        "added": result["added"],
        "already": result["already"],
        "invalid": invalid,
        "group": group,
    }


@router.delete("/groups/{group_id}/items/{key}")
def drop_group_item(request: Request, group_id: int, key: str) -> dict[str, Any]:
    context = get_context(request)
    with context.db_lock:
        repository.remove_item(context.conn, group_id, key)
        group = repository.require_group(context.conn, group_id)
    return {"ok": True, "group": group}


@router.post("/groups/{group_id}/items/{key}/pin")
def pin_group_item(request: Request, group_id: int, key: str) -> dict[str, Any]:
    context = get_context(request)
    with context.db_lock:
        pinned = repository.toggle_pin(context.conn, group_id, key)
    return {"key": key.strip().upper(), "pinned": pinned}


# --- kayit listesi ve detay ---------------------------------------------


@router.get("/groups/{group_id}/issues")
def group_issues(
    request: Request,
    group_id: int,
    q: str = "",
    sort: str = "",
    dir: str = "",
) -> dict[str, Any]:
    context = get_context(request)
    conn = context.conn
    group = repository.require_group(conn, group_id)
    columns = repository.group_columns(conn, group)
    schemas = repository.field_schemas(conn)

    items = repository.list_items(conn, group_id)
    keys = [item["key"] for item in items]
    stored = repository.get_issues(conn, keys)
    local_fields = repository.local_fields_by_id(conn)
    local_values = repository.local_values_for(conn, keys)
    local_stats = repository.history_stats(conn, keys)
    local_view = _LocalView(local_fields, local_values, local_stats)

    rows = [
        _build_row(item, stored.get(item["key"]), columns, schemas, local_view) for item in items
    ]
    total = len(rows)

    needle = field_utils.fold(q.strip()) if q else ""
    if needle:
        rows = [row for row in rows if _matches(row, needle)]

    sort_field, direction = _sort_choice(group, sort, dir)
    if sort_field:
        rows = _sort_rows(rows, sort_field, direction, schemas, local_view)

    base_url = (context.settings.get("jira.base_url", "") or "").rstrip("/")
    for row in rows:
        row["url"] = f"{base_url}/browse/{row['key']}" if base_url else ""
        for cell in row["cells"]:
            cell["text"] = field_utils.truncate(cell["text"])
        row.pop("_raw", None)
        row.pop("_local_text", None)

    return {
        "group": group,
        "columns": [_column_head(column, schemas, local_view) for column in columns],
        "rows": rows,
        "total": total,
        "shown": len(rows),
        "sort": {"field": sort_field, "dir": direction} if sort_field else None,
        "base_url": base_url,
    }


@router.get("/issues/{key}")
def read_issue(request: Request, key: str) -> dict[str, Any]:
    context = get_context(request)
    conn = context.conn
    clean_key = str(key).strip().upper()
    record = repository.get_issue(conn, clean_key)
    if record is None and not repository.issue_is_known(conn, clean_key):
        raise RepositoryError("issue_not_found", "Kayit henuz cekilmemis.", status=404)

    # Henuz cekilmemis ama bir grupta duran kayit: Jira alanlari bos gelir,
    # yerel alanlar yine de okunup duzenlenebilir.
    if record is None:
        record = {"key": clean_key, "fetched_at": None, "raw": {"key": clean_key, "fields": {}}}

    schemas = repository.field_schemas(conn)
    raw = record["raw"]
    values = [_detail_cell(field_id, schemas, raw) for field_id in field_utils.VIRTUAL_FIELDS]
    values.extend(
        _detail_cell(field_id, schemas, raw)
        for field_id in sorted(
            (raw.get("fields") or {}).keys(),
            key=lambda item: field_utils.fold(repository.field_name(schemas, item)),
        )
    )

    base_url = (context.settings.get("jira.base_url", "") or "").rstrip("/")
    return {
        "key": record["key"],
        "fetched_at": record["fetched_at"],
        "missing": record["fetched_at"] is None,
        "url": f"{base_url}/browse/{record['key']}" if base_url else "",
        "fields": values,
        "local": _local_detail(conn, record["key"]),
    }


# --- yerel alanlar ------------------------------------------------------


@router.get("/local-fields")
def list_local_fields(request: Request) -> dict[str, Any]:
    context = get_context(request)
    return {
        "fields": repository.list_local_fields(context.conn),
        "types": [
            {"id": name, "label": field_utils.LOCAL_TYPE_LABELS[name]}
            for name in field_utils.LOCAL_TYPES
        ],
    }


@router.post("/local-fields")
def create_local_field(request: Request, payload: dict[str, Any] = Body(default_factory=dict)):
    context = get_context(request)
    with context.db_lock:
        field = repository.create_local_field(
            context.conn,
            name=str(payload.get("name") or ""),
            type=str(payload.get("type") or field_utils.LOCAL_TEXT),
            options=payload.get("options"),
            track_history=bool(payload.get("track_history")),
        )
    return {"field": field}


@router.post("/local-fields/reorder")
def reorder_local_fields(request: Request, payload: dict[str, Any] = Body(default_factory=dict)):
    context = get_context(request)
    ids = payload.get("ids")
    if not isinstance(ids, list):
        return error_response("invalid_order", "ids bir liste olmali.")
    with context.db_lock:
        fields = repository.reorder_local_fields(context.conn, ids)
    return {"fields": fields}


@router.get("/local-fields/{field_id}")
def read_local_field(request: Request, field_id: int) -> dict[str, Any]:
    context = get_context(request)
    return {"field": repository.require_local_field(context.conn, field_id)}


@router.put("/local-fields/{field_id}")
def write_local_field(
    request: Request, field_id: int, payload: dict[str, Any] = Body(default_factory=dict)
):
    context = get_context(request)
    with context.db_lock:
        field = repository.update_local_field(context.conn, field_id, payload)
    return {"field": field}


@router.delete("/local-fields/{field_id}")
def drop_local_field(request: Request, field_id: int) -> dict[str, Any]:
    context = get_context(request)
    with context.db_lock:
        return repository.delete_local_field(context.conn, field_id)


# --- kayit basina yerel deger ve gecmis ---------------------------------


@router.put("/issues/{key}/local/{field_id}")
def write_local_value(
    request: Request,
    key: str,
    field_id: int,
    payload: dict[str, Any] = Body(default_factory=dict),
) -> dict[str, Any]:
    context = get_context(request)
    with context.db_lock:
        return repository.set_local_value(context.conn, key, field_id, payload.get("value"))


@router.get("/issues/{key}/local/{field_id}/history")
def read_local_history(request: Request, key: str, field_id: int) -> dict[str, Any]:
    context = get_context(request)
    conn = context.conn
    field = repository.require_local_field(conn, field_id)
    entries = repository.list_local_history(conn, key, field_id)
    return {
        "key": str(key).strip().upper(),
        "field": repository.local_column_id(field["id"]),
        "name": field["name"],
        "track_history": field["track_history"],
        "entries": entries,
        "changes": len(entries),
    }


@router.delete("/issues/{key}/local/{field_id}/history")
def clear_local_history(request: Request, key: str, field_id: int) -> dict[str, Any]:
    context = get_context(request)
    with context.db_lock:
        removed = repository.clear_local_history(context.conn, key, field_id)
    return {"ok": True, "removed": removed}


@router.delete("/issues/{key}/local/{field_id}/history/{history_id}")
def drop_local_history_entry(
    request: Request, key: str, field_id: int, history_id: int
) -> dict[str, Any]:
    context = get_context(request)
    with context.db_lock:
        repository.delete_local_history_entry(context.conn, key, field_id, history_id)
        remaining = repository.list_local_history(context.conn, key, field_id)
    return {"ok": True, "changes": len(remaining)}


# --- Guncelle -----------------------------------------------------------


@router.post("/refresh")
def start_refresh(request: Request, payload: dict[str, Any] = Body(default_factory=dict)):
    context = get_context(request)
    config = context.settings.jira_config()
    if not config.base_url:
        return error_response("config_missing", "Once Ayarlar ekranindan Jira adresini girin.")

    group_id = payload.get("group_id")
    if group_id is not None:
        try:
            group_id = int(group_id)
        except (TypeError, ValueError):
            return error_response("invalid_group", "group_id sayi olmali.")

    return {"status": context.refresh.start(context, group_id)}


@router.get("/refresh/status")
def refresh_status(request: Request) -> dict[str, Any]:
    context = get_context(request)
    return {"status": context.refresh.status()}


@router.post("/refresh/cancel")
def cancel_refresh(request: Request) -> dict[str, Any]:
    context = get_context(request)
    cancelled = context.refresh.cancel()
    return {"cancelled": cancelled, "status": context.refresh.status()}


# --- ic yardimcilar -----------------------------------------------------


class _LocalView:
    """Bir istek boyunca yerel alan/deger/gecmis okumalarini bir arada tutar."""

    def __init__(
        self,
        fields: dict[int, dict[str, Any]],
        values: dict[str, dict[int, dict[str, Any]]],
        stats: dict[str, dict[int, dict[str, Any]]],
    ) -> None:
        self.fields = fields
        self.values = values
        self.stats = stats

    def column(self, column: str) -> tuple[dict[str, Any], str] | None:
        """Sutun kimligi yerel bir alana isaret ediyorsa (alan, turetilmis-ek)."""
        parsed = repository.parse_local_column(column)
        if parsed is None:
            return None
        field = self.fields.get(parsed[0])
        return (field, parsed[1]) if field else None

    def stat(self, key: str, field_id: int) -> dict[str, Any]:
        return self.stats.get(key, {}).get(field_id, {})

    def value(self, key: str, field_id: int) -> str:
        return self.values.get(key, {}).get(field_id, {}).get("value", "")

    def raw_for(self, key: str, field: dict[str, Any], suffix: str) -> Any:
        if not suffix:
            return self.value(key, field["id"])
        stat = self.stat(key, field["id"])
        if suffix == field_utils.DERIVED_CHANGES:
            return int(stat.get("changes", 0))
        return stat.get("changed_at")

    def cell(self, key: str, column: str, field: dict[str, Any], suffix: str) -> dict[str, Any]:
        raw = self.raw_for(key, field, suffix)
        if suffix:
            return {
                "field": column,
                "text": field_utils.format_derived_value(suffix, raw),
                "raw": raw,
                "derived": suffix,
            }
        stat = self.stat(key, field["id"])
        cell: dict[str, Any] = {
            "field": column,
            "text": field_utils.format_local_value(field["type"], raw),
            "raw": raw,
            "editable": True,
            "changes": int(stat.get("changes", 0)),
            "changed_at": stat.get("changed_at"),
        }
        return cell

    def sort_key(
        self, key: str, field: dict[str, Any], suffix: str
    ) -> tuple[int, float, str]:
        raw = self.raw_for(key, field, suffix)
        if suffix:
            return field_utils.derived_sort_key(suffix, raw)
        return field_utils.local_sort_key(field["type"], raw)

    def search_text(self, key: str) -> str:
        """Aramanin kapsadigi yerel metin: sutun secilmemis olsa da gorunur."""
        parts = []
        for field_id, entry in (self.values.get(key) or {}).items():
            field = self.fields.get(field_id)
            if field is None:
                continue
            parts.append(field_utils.format_local_value(field["type"], entry.get("value")))
        return " ".join(part for part in parts if part)


def _column_head(
    column: str, schemas: dict[str, dict[str, Any]], local_view: _LocalView
) -> dict[str, Any]:
    head: dict[str, Any] = {"id": column, "name": repository.field_name(schemas, column)}
    found = local_view.column(column)
    if found is None:
        return head
    field, suffix = found
    head["local"] = field
    head["derived"] = suffix
    head["editable"] = not suffix
    return head


def _build_row(
    item: dict[str, Any],
    record: dict[str, Any] | None,
    columns: list[str],
    schemas: dict[str, dict[str, Any]],
    local_view: _LocalView,
) -> dict[str, Any]:
    raw = record["raw"] if record else {"key": item["key"], "fields": {}}
    cells = []
    for column in columns:
        found = local_view.column(column)
        if found is not None:
            cells.append(local_view.cell(item["key"], column, found[0], found[1]))
            continue
        value = field_utils.issue_value(raw, column)
        cells.append(
            {
                "field": column,
                "text": field_utils.format_field_value(schemas.get(column), value),
                "raw": _jsonable(value),
            }
        )
    return {
        "key": item["key"],
        "pinned": item["pinned"],
        "missing": record is None,
        "fetched_at": record["fetched_at"] if record else None,
        "cells": cells,
        "_raw": raw,
        "_local_text": local_view.search_text(item["key"]),
    }


def _detail_cell(
    field_id: str, schemas: dict[str, dict[str, Any]], raw: dict[str, Any]
) -> dict[str, Any]:
    value = field_utils.issue_value(raw, field_id)
    text = field_utils.format_field_value(schemas.get(field_id), value)
    return {
        "field": field_id,
        "name": repository.field_name(schemas, field_id),
        "text": text,
        "raw": _jsonable(value),
        "empty": text == "",
    }


def _local_detail(conn: Any, key: str) -> list[dict[str, Any]]:
    """Detay cekmecesinin yerel alan bolumu: deger + acik gecmis listesi."""
    values = repository.local_values_for(conn, [key]).get(key, {})
    stats = repository.history_stats(conn, [key]).get(key, {})
    entries: list[dict[str, Any]] = []
    for field in repository.list_local_fields(conn):
        stored = values.get(field["id"], {}).get("value", "")
        stat = stats.get(field["id"], {})
        history = (
            repository.list_local_history(conn, key, field["id"])
            if stat.get("changes")
            else []
        )
        entries.append(
            {
                "field": field["column_id"],
                # Arayuz hem grid hem cekmecede ayni duzenleyiciyi kullanir; her
                # ikisinde de alan kimligi "id" adiyla okunur.
                "id": field["id"],
                "field_id": field["id"],
                "name": field["name"],
                "type": field["type"],
                "type_label": field["type_label"],
                "options": field["options"],
                "track_history": field["track_history"],
                "value": stored,
                "text": field_utils.format_local_value(field["type"], stored),
                "updated_at": values.get(field["id"], {}).get("updated_at"),
                "changes": int(stat.get("changes", 0)),
                "changed_at": stat.get("changed_at"),
                "history": history,
                "empty": stored == "",
            }
        )
    return entries


def _matches(row: dict[str, Any], needle: str) -> bool:
    if needle in field_utils.fold(row["key"]):
        return True
    if any(needle in field_utils.fold(cell["text"]) for cell in row["cells"]):
        return True
    # Yerel degerler sutun secili olmasa da aranir: kullanicinin kendi notu.
    return needle in field_utils.fold(row.get("_local_text", ""))


def _sort_choice(group: dict[str, Any], sort: str, direction: str) -> tuple[str, str]:
    field_id = (sort or "").strip()
    chosen_dir = (direction or "").strip().lower()
    if not field_id:
        group_sort = group.get("sort") or {}
        field_id = str(group_sort.get("field") or "").strip()
        chosen_dir = chosen_dir or str(group_sort.get("dir") or "").lower()
    if chosen_dir not in repository.SORT_DIRECTIONS:
        chosen_dir = "asc"
    return field_id, chosen_dir


def _sort_rows(
    rows: list[dict[str, Any]],
    field_id: str,
    direction: str,
    schemas: dict[str, dict[str, Any]],
    local_view: _LocalView,
) -> list[dict[str, Any]]:
    schema = schemas.get(field_id)
    local = local_view.column(field_id)

    def key(row: dict[str, Any]) -> tuple[Any, ...]:
        if local is not None:
            base = local_view.sort_key(row["key"], local[0], local[1])
        else:
            value = field_utils.issue_value(row["_raw"], field_id)
            base = field_utils.sort_key(schema, value)
        return (*base, field_utils.fold(row["key"]))

    return sorted(rows, key=key, reverse=direction == "desc")


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool, list, dict)):
        return value
    return str(value)
