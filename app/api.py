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
    stored = repository.get_issues(conn, [item["key"] for item in items])

    rows = [_build_row(item, stored.get(item["key"]), columns, schemas) for item in items]
    total = len(rows)

    needle = field_utils.fold(q.strip()) if q else ""
    if needle:
        rows = [row for row in rows if _matches(row, needle)]

    sort_field, direction = _sort_choice(group, sort, dir)
    if sort_field:
        rows = _sort_rows(rows, sort_field, direction, schemas)

    base_url = (context.settings.get("jira.base_url", "") or "").rstrip("/")
    for row in rows:
        row["url"] = f"{base_url}/browse/{row['key']}" if base_url else ""
        for cell in row["cells"]:
            cell["text"] = field_utils.truncate(cell["text"])
        row.pop("_raw", None)

    return {
        "group": group,
        "columns": [
            {"id": column, "name": repository.field_name(schemas, column)} for column in columns
        ],
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
    record = repository.get_issue(conn, key)
    if record is None:
        raise RepositoryError("issue_not_found", "Kayit henuz cekilmemis.", status=404)

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
        "url": f"{base_url}/browse/{record['key']}" if base_url else "",
        "fields": values,
    }


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


def _build_row(
    item: dict[str, Any],
    record: dict[str, Any] | None,
    columns: list[str],
    schemas: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    raw = record["raw"] if record else {"key": item["key"], "fields": {}}
    cells = []
    for column in columns:
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


def _matches(row: dict[str, Any], needle: str) -> bool:
    if needle in field_utils.fold(row["key"]):
        return True
    return any(needle in field_utils.fold(cell["text"]) for cell in row["cells"])


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
) -> list[dict[str, Any]]:
    schema = schemas.get(field_id)

    def key(row: dict[str, Any]) -> tuple[Any, ...]:
        value = field_utils.issue_value(row["_raw"], field_id)
        return (*field_utils.sort_key(schema, value), field_utils.fold(row["key"]))

    return sorted(rows, key=key, reverse=direction == "desc")


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool, list, dict)):
        return value
    return str(value)
