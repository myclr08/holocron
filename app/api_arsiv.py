"""Arsiv uclari: belge listesi, yukleme, baglar, acma, klasorde gosterme, silme.

Ayri router: kendi klasoru ve kendi ekrani var. `app/server.py` tek satirla
baglar. Yukleme `multipart` degil, ham govdedir (dosya adi `X-File-Name`
basliginda, URL kodlu): yeni bagimlilik gerekmesin, kurum makinesinde PyPI
kapali.

Testler gercek dosya acicisi calistirmasin diye `app.state.arsiv_launcher`
verilebilir; yoksa isletim sisteminin acicisi kullanilir.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import unquote

from fastapi import APIRouter, Body, Request

from . import arsiv, paths
from .context import AppContext
from .repository import RepositoryError

log = logging.getLogger("holocron.arsiv")

router = APIRouter(prefix="/api/arsiv")


def get_context(request: Request) -> AppContext:
    return request.app.state.context


def _launcher(request: Request) -> Any:
    return getattr(request.app.state, "arsiv_launcher", None)


def _folder() -> Any:
    return paths.archive_dir()


@router.get("")
def read_archive(
    request: Request, q: str = "", kind: str = "", state: str = "", scan: int = 1
) -> dict[str, Any]:
    context = get_context(request)
    folder = _folder()
    if scan:
        with context.db_lock:
            arsiv.rescan(context.connection(), folder)
    conn = context.connection()
    return {
        "documents": arsiv.list_documents(conn, q=q, kind=kind, state=state, folder=folder),
        "counts": arsiv.counts(conn),
        "folder": str(folder),
        "kinds": [{"id": key, "label": arsiv.KIND_LABELS[key]} for key in arsiv.KINDS],
        "max_size": arsiv.MAX_SIZE,
    }


@router.get("/count")
def read_count(request: Request) -> dict[str, Any]:
    """Kenar cubugu: taramadan, kayitli belge sayisi."""
    return arsiv.counts(get_context(request).connection())


@router.post("/rescan")
def rescan(request: Request) -> dict[str, Any]:
    context = get_context(request)
    with context.db_lock:
        result = arsiv.rescan(context.connection(), _folder())
    return {**result, "counts": arsiv.counts(context.connection())}


@router.get("/for/{target_type}/{target_id}")
def read_for(request: Request, target_type: str, target_id: str) -> dict[str, Any]:
    context = get_context(request)
    return {"documents": arsiv.documents_for(context.connection(), target_type, target_id, _folder())}


@router.post("/upload")
async def upload(request: Request, task: str = "", issue: str = "") -> dict[str, Any]:
    """Ham govdeyi Arsiv'e alir; `task`/`issue` verilmisse bag da kurulur.

    Hedef ONCE dogrulanir: gecersiz goreve yuklenen dosya Arsiv'de sahipsiz
    kalmasin.
    """
    context = get_context(request)
    name = unquote(request.headers.get("x-file-name", "") or "").strip()
    if not name:
        raise RepositoryError("missing_name", "Dosya adı gelmedi (X-File-Name).")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > arsiv.MAX_SIZE:
        raise RepositoryError(
            "too_large", f"Belge çok büyük: sınır {arsiv.MAX_SIZE // (1024 * 1024)} MB.", 413
        )
    target: tuple[str, str] | None = None
    if task or issue:
        kind = arsiv.TARGET_TASK if task else arsiv.TARGET_ISSUE
        target = arsiv.clean_target(context.connection(), kind, task or issue)

    part = arsiv.PartWriter(_folder())
    try:
        async for chunk in request.stream():
            part.write(chunk)
    except BaseException:
        part.discard()
        raise
    with context.db_lock:
        conn = context.connection()
        document, duplicate = arsiv.adopt(conn, part, name)
        linked = False
        if target is not None:
            linked = arsiv.link(conn, document["id"], target[0], target[1])
    log.info("arsiv: belge alindi (id=%s, yineleme=%s)", document["id"], duplicate)
    return {"document": document, "duplicate": duplicate, "linked": linked}


@router.post("/{document_id}/links")
def add_link(
    request: Request, document_id: int, payload: dict[str, Any] = Body(default_factory=dict)
) -> dict[str, Any]:
    context = get_context(request)
    with context.db_lock:
        added = arsiv.link(context.connection(), document_id, payload.get("type"), payload.get("target"))
    return {"ok": True, "added": added}


@router.delete("/{document_id}/links/{target_type}/{target_id}")
def drop_link(request: Request, document_id: int, target_type: str, target_id: str) -> dict[str, Any]:
    context = get_context(request)
    with context.db_lock:
        removed = arsiv.unlink(context.connection(), document_id, target_type, target_id)
    return {"ok": True, "removed": removed}


@router.post("/{document_id}/open")
def open_document(request: Request, document_id: int) -> dict[str, Any]:
    context = get_context(request)
    arsiv.open_document(context.connection(), document_id, _folder(), _launcher(request))
    return {"ok": True}


@router.post("/{document_id}/reveal")
def reveal_document(request: Request, document_id: int) -> dict[str, Any]:
    context = get_context(request)
    arsiv.reveal_document(context.connection(), document_id, _folder(), _launcher(request))
    return {"ok": True}


@router.post("/folder")
def reveal_folder(request: Request) -> dict[str, Any]:
    folder = arsiv.reveal_folder(_folder(), _launcher(request))
    return {"ok": True, "folder": str(folder)}


@router.delete("/{document_id}")
def delete_document(request: Request, document_id: int) -> dict[str, Any]:
    context = get_context(request)
    with context.db_lock:
        document = arsiv.delete_document(context.connection(), document_id, _folder())
    log.info("arsiv: belge silindi (id=%s)", document["id"])
    return {"ok": True}
