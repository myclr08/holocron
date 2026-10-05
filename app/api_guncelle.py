"""Guncelleme uclari: denetle, baslat, durum, rozet.

Ayri router: Ayarlar -> "Guncelleme" ekrani ve ana ekrandaki kucuk rozet.
Is mantigi `app/guncelle.py`dedir. Guncelleyici uygulama basina tektir ve
`app.state.updater` icinde durur; testler oraya sahte ag ve sahte yardimci
baslaticisi olan bir ornek koyar.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from . import guncelle
from .context import AppContext

log = logging.getLogger("holocron.guncelle")

router = APIRouter(prefix="/api/guncelleme")


def get_context(request: Request) -> AppContext:
    return request.app.state.context


def _error(exc: guncelle.UpdateError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message}}
    )


def get_updater(request: Request) -> guncelle.Updater:
    updater = getattr(request.app.state, "updater", None)
    if updater is None:
        context = get_context(request)

        def stop() -> None:
            # "Kapat" dugmesiyle ayni yol: nabiz durur, sunucu cikar.
            context.heartbeat.request_stop()
            context.shutdown_hook()

        updater = guncelle.Updater(settings=context.settings, shutdown=stop)
        request.app.state.updater = updater
    return updater


@router.get("")
def read_state(request: Request) -> dict[str, Any]:
    context = get_context(request)
    return {"cached": guncelle.cached(context.settings), "status": get_updater(request).status()}


@router.post("/denetle")
def check_now(request: Request):
    updater = get_updater(request)
    try:
        install = updater.install
        info = guncelle.check(updater.settings, updater._fetcher(), install)  # noqa: SLF001
    except guncelle.UpdateError as exc:
        return _error(exc)
    return {"info": info}


@router.get("/rozet")
def badge(request: Request) -> dict[str, Any]:
    """Ana ekran rozeti: en fazla 12 saatte bir aga cikar, hata sessizdir."""
    updater = get_updater(request)
    settings = updater.settings
    if guncelle.check_due(settings):
        try:
            guncelle.check(settings, updater._fetcher(), updater.install)  # noqa: SLF001
        except guncelle.UpdateError as exc:
            log.info("guncelleme: arka plan denetimi olmadi (%s)", exc.code)
        except Exception:  # noqa: BLE001 - rozet ikincil
            log.warning("guncelleme: arka plan denetimi hata verdi", exc_info=True)
    return guncelle.cached(settings)


@router.post("/baslat")
def start(request: Request):
    updater = get_updater(request)
    try:
        updater.preflight()
        return {"status": updater.start()}
    except guncelle.UpdateError as exc:
        return _error(exc)


@router.get("/durum")
def status(request: Request) -> dict[str, Any]:
    return {"status": get_updater(request).status()}
