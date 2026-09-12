"""Sefer (oyunlastirma) uclari.

Ayri bir router: `app/api.py` zaten uzun ve bu asamanin kendi tablolari, kendi
ekrani ve kendi Excel dokumu var. `app/server.py` tek satirla baglar.

Butun uclar yerel veriyle calisir; hicbiri Jira'ya ya da aga cikmaz.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse, Response

from . import export, gamify
from . import gamify_repo as store
from .context import AppContext

router = APIRouter(prefix="/api")


def get_context(request: Request) -> AppContext:
    return request.app.state.context


def error_response(code: str, message: str, status: int = 400) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


@router.get("/campaign")
def read_campaign(request: Request) -> dict[str, Any]:
    """Panelin tamami: serit, emirler, guc dengesi, rozet duvari, defter."""
    context = get_context(request)
    with context.db_lock:
        return gamify.panel(context)


@router.post("/campaign")
def start_campaign(request: Request, payload: dict[str, Any] = Body(default_factory=dict)):
    """Yeni sefer baslatir (ad, bitis tarihi, hedef XP)."""
    context = get_context(request)
    with context.db_lock:
        gamify.start_campaign(
            context,
            payload.get("name"),
            payload.get("ends_at"),
            payload.get("target_xp", 1000),
        )
        return gamify.panel(context)


@router.post("/campaign/end")
def end_campaign(request: Request) -> dict[str, Any]:
    """Seferi elle bitirir; ozet yazilir, defter sefere bagli kalir."""
    context = get_context(request)
    with context.db_lock:
        ended = gamify.end_campaign(context)
        if ended is None:
            return {"ended": None, "panel": gamify.panel(context)}
        return {"ended": ended["summary"], "panel": gamify.panel(context)}


@router.get("/campaign/ledger")
def read_ledger(request: Request, source: str = "", limit: int = store.LEDGER_LIMIT):
    """XP defteri; kaynak suzgeci arayuzden gelir."""
    context = get_context(request)
    clean = source if source in gamify.SOURCES else ""
    # Okuma gibi gorunse de suresi dolmus seferi kapatabilir (yazma): kilitle.
    with context.db_lock:
        events = gamify.ledger(context, clean, limit)
    return {
        "events": events,
        "sources": [{"id": name, "label": gamify.SOURCE_LABELS[name]} for name in gamify.SOURCES],
    }


@router.get("/campaign/ledger.xlsx")
def export_ledger(request: Request, source: str = ""):
    context = get_context(request)
    clean = source if source in gamify.SOURCES else ""
    with context.db_lock:
        payload = export.build_ledger_workbook(context, source=clean)
    return Response(
        content=payload,
        media_type=export.MEDIA_TYPE,
        headers={"Content-Disposition": export.content_disposition(export.LEDGER_NAME)},
    )


@router.get("/campaign/rules")
def read_rules(request: Request) -> dict[str, Any]:
    context = get_context(request)
    return {
        "rules": gamify.rules_view(context),
        "grace_used": (context.settings.get(gamify.SETTING_GRACE_MONTH, "") or "") != "",
        "grace_month": context.settings.get(gamify.SETTING_GRACE_MONTH, "") or "",
    }


@router.put("/campaign/rules")
def write_rules(request: Request, payload: dict[str, Any] = Body(default_factory=dict)):
    """Puanlar ve acik/kapali durumu; yeni kural yaratilmaz."""
    context = get_context(request)
    rules = payload.get("rules")
    if rules is not None and not isinstance(rules, list):
        return error_response("invalid_rules", "rules bir liste olmalı.")
    with context.db_lock:
        if rules:
            store.update_rules(context.connection(), rules)
    return {"rules": gamify.rules_view(context)}


@router.delete("/campaign/ledger/{event_id}")
def drop_ledger_event(request: Request, event_id: int) -> dict[str, Any]:
    """Defterden bir satir siler; sefer bastan degerlendirilir.

    Kosulu kalmayan rozetler puanlariyla birlikte geri alinir. Ayni olay
    ileride yeniden gerceklesirse yeniden puan yazilir.
    """
    context = get_context(request)
    with context.db_lock:
        result = gamify.delete_event(context, event_id)
        return {
            "deleted": result["event"],
            "revoked": result["revoked"],
            "panel": gamify.panel(context),
        }


@router.get("/campaign/history")
def read_history(request: Request) -> dict[str, Any]:
    context = get_context(request)
    return {"campaigns": gamify.history(context)}


@router.delete("/campaign/history/{campaign_id}")
def drop_history(request: Request, campaign_id: int) -> dict[str, Any]:
    """Biten bir seferi defteri, rozetleri ve emirleriyle siler.

    Suren sefer buradan silinmez; onun yolu `POST /api/campaign/end`.
    """
    context = get_context(request)
    with context.db_lock:
        deleted = gamify.delete_history(context, campaign_id)
        return {"deleted": deleted, "campaigns": gamify.history(context)}


@router.post("/campaign/digest-seen")
def mark_digest_seen(request: Request, payload: dict[str, Any] = Body(default_factory=dict)):
    """Pazartesi ozet karti kapatildi: ayni hafta bir daha cikmasin."""
    context = get_context(request)
    week = str(payload.get("week_start") or "").strip()
    if not week:
        day = gamify.parse_day(gamify.today_of())
        week = gamify.week_start_of(day) if day else ""
    with context.db_lock:
        context.settings.set(gamify.SETTING_DIGEST_WEEK, week)
    return {"ok": True, "week_start": week}
