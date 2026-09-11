"""HTTP API uclari. Is mantigi ince tutulur, disa donuk cagrilar jira_client'ta."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from . import __version__, db
from .context import AppContext
from .jira_client import JiraError
from .lifecycle import BEAT_INTERVAL_SECONDS
from .settings_store import MODE_CLOUD, MODE_SERVER

router = APIRouter(prefix="/api")


def error_response(code: str, message: str, status: int = 400) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def get_context(request: Request) -> AppContext:
    return request.app.state.context


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
        fields = client.fetch_fields()
    except JiraError as exc:
        return error_response(exc.code, exc.message, status=400)

    stored = store_fields(context, fields)
    return {"count": stored, "fetched_at": _now()}


@router.get("/jira/fields")
def list_fields(request: Request) -> dict[str, Any]:
    context = get_context(request)
    rows = context.conn.execute(
        "SELECT id, name, schema_type, custom, fetched_at FROM jira_fields ORDER BY name COLLATE NOCASE"
    ).fetchall()
    return {
        "fields": [
            {
                "id": row["id"],
                "name": row["name"],
                "schema_type": row["schema_type"],
                "custom": bool(row["custom"]),
                "fetched_at": row["fetched_at"],
            }
            for row in rows
        ]
    }


def store_fields(context: AppContext, fields: list[dict[str, Any]]) -> int:
    """Alan katalogunu tazeler. Katalog Jira'nin gercegi oldugu icin tamamen degistirilir."""
    fetched_at = _now()
    rows = []
    for item in fields:
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
    with context.conn:
        context.conn.execute("DELETE FROM jira_fields")
        context.conn.executemany(
            "INSERT INTO jira_fields (id, name, schema_type, custom, raw_json, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
    return len(rows)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()
