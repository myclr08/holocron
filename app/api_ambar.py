"""Ambar uclari: ayarlar, liste, sayi, ambara al / ambardan cikar.

Ayri router: kendi ayarlari, kendi ekrani ve kendi ag/git isleri var.
`app/server.py` tek satirla baglar. Uclar yalnizca 127.0.0.1'de dinlenir.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request

from . import ambar
from .context import AppContext

router = APIRouter(prefix="/api/ambar")


def get_context(request: Request) -> AppContext:
    return request.app.state.context


@router.get("/settings")
def read_settings(request: Request) -> dict[str, Any]:
    return {"ambar": ambar.config_view(get_context(request).settings)}


@router.put("/settings")
def write_settings(request: Request, payload: dict[str, Any] = Body(default_factory=dict)):
    context = get_context(request)
    with context.db_lock:
        return {"ambar": ambar.save_config(context.settings, payload)}


@router.post("/test/{repo_id}")
def test_repo(request: Request, repo_id: str) -> dict[str, Any]:
    return ambar.check_repo(get_context(request).settings, repo_id)


@router.get("/count")
def read_count(request: Request) -> dict[str, Any]:
    """Kenar cubugu: aga cikmadan, klonlardaki son origin haliyle."""
    return ambar.held_count(get_context(request).settings)


@router.get("")
def read_overview(
    request: Request, days: int = ambar.DEFAULT_DAYS, repo: str = "", fetch: int = 1
) -> dict[str, Any]:
    return ambar.overview(get_context(request).settings, days, repo, do_fetch=bool(fetch))


@router.post("/al")
def hold(request: Request, payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return ambar.run(get_context(request), ambar.OP_HOLD, payload.get("items"))


@router.post("/cikar")
def release(request: Request, payload: dict[str, Any] = Body(default_factory=dict)) -> dict[str, Any]:
    return ambar.run(get_context(request), ambar.OP_RELEASE, payload.get("items"))
