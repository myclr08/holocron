"""Grup kayitlarini Excel ekiyle e-postalama uclari (Outlook).

Ayri bir router: `app/api.py` zaten bin satiri gecti ve bu akisin kendi sablon
tablosu, kendi gonderim kaydi ve kendi arayuz dosyasi var. `app/server.py` tek
satirla baglar.

Windows disinda gonderim uclari `feature_unavailable` doner (gonderici fabrikasi
`MailError` atar, sunucunun ortak isleyicisi 400 yapar); sablon yonetimi ve
onizleme her yerde calisir, boylece sablonlar Linux'ta da sinanabilir.
"""

from __future__ import annotations

from typing import Any, Sequence

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse, Response

from . import export, grid, mailsend, mailsend_repo, repository
from .context import AppContext
from .mail.send import MODE_DISPLAY, MODES, clean_mode

router = APIRouter(prefix="/api")


def get_context(request: Request) -> AppContext:
    return request.app.state.context


def error_response(code: str, message: str, status: int = 400) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on", "evet")


def _keys(value: Any) -> list[str] | None:
    """Secili anahtarlar; bos ya da verilmemisse None (tum satirlar)."""
    if not isinstance(value, (list, tuple)):
        return None
    picked = [str(item).strip() for item in value if str(item).strip()]
    return picked or None


def _columns(value: Any) -> list[str] | None:
    if not isinstance(value, (list, tuple)):
        return None
    picked = [str(item).strip() for item in value if str(item).strip()]
    return picked or None


# --- sablonlar ----------------------------------------------------------


@router.get("/mail-templates")
def list_mail_templates(request: Request) -> dict[str, Any]:
    context = get_context(request)
    return {
        "templates": mailsend_repo.list_templates(context.connection()),
        "placeholders": [
            {"token": token, "label": label} for token, label in mailsend.PLACEHOLDERS
        ],
        "mode": clean_mode(context.settings.get("mailsend.mode", MODE_DISPLAY)),
        "modes": list(MODES),
    }


@router.post("/mail-templates")
def create_mail_template(request: Request, payload: dict[str, Any] = Body(default_factory=dict)):
    context = get_context(request)
    with context.db_lock:
        template = mailsend_repo.create_template(context.connection(), payload)
    return {"template": template}


@router.post("/mail-templates/reorder")
def reorder_mail_templates(request: Request, payload: dict[str, Any] = Body(default_factory=dict)):
    context = get_context(request)
    ids = payload.get("ids")
    if not isinstance(ids, list):
        return error_response("invalid_order", "ids bir liste olmalı.")
    with context.db_lock:
        return {"templates": mailsend_repo.reorder_templates(context.connection(), ids)}


@router.get("/mail-templates/{template_id}")
def read_mail_template(request: Request, template_id: int) -> dict[str, Any]:
    context = get_context(request)
    return {"template": mailsend_repo.require_template(context.connection(), template_id)}


@router.put("/mail-templates/{template_id}")
def write_mail_template(
    request: Request, template_id: int, payload: dict[str, Any] = Body(default_factory=dict)
):
    context = get_context(request)
    with context.db_lock:
        template = mailsend_repo.update_template(context.connection(), template_id, payload)
    return {"template": template}


@router.delete("/mail-templates/{template_id}")
def drop_mail_template(request: Request, template_id: int) -> dict[str, Any]:
    context = get_context(request)
    with context.db_lock:
        return mailsend_repo.delete_template(context.connection(), template_id)


@router.put("/groups/{group_id}/mail-template")
def write_group_mail_template(
    request: Request, group_id: int, payload: dict[str, Any] = Body(default_factory=dict)
):
    """Grubun varsayilan sablonu; `null` bagi kaldirir."""
    context = get_context(request)
    conn = context.connection()
    repository.require_group(conn, group_id)
    with context.db_lock:
        return mailsend_repo.set_group_template(conn, group_id, payload.get("template_id"))


# --- onizleme ve gonderim -----------------------------------------------


def _prepare(context: AppContext, group_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """Onizleme ve gonderimin ortak zemini: satirlar, sutunlar, sablon."""
    conn = context.connection()
    repository.require_group(conn, group_id)
    apply_view = _truthy(payload.get("apply_view"), default=True)
    columns = _columns(payload.get("columns"))
    data = grid.build_grid(
        context,
        group_id,
        columns=columns,
        q=str(payload.get("q") or "") if apply_view else "",
        sort=str(payload.get("sort") or "") if apply_view else "",
        direction=str(payload.get("dir") or "") if apply_view else "",
    )
    keys = _keys(payload.get("keys"))
    rows = export.pick_rows(data.rows, keys)
    template = mailsend_repo.template_for_group(conn, group_id, payload.get("template_id"))
    return {
        "data": data,
        "rows": rows,
        "keys": keys,
        "columns": columns,
        "apply_view": apply_view,
        "template": template,
        "filled": _overlay(template, payload),
    }


def _overlay(template: dict[str, Any] | None, payload: dict[str, Any]) -> dict[str, Any] | None:
    """Arayuzde duzenlenen konu/govde sablonun uzerine biner.

    Metin HAM gelir (`{tablo}` dahil) ve burada cozulur: boylece kullanici
    pencerede yer tutucu yazmaya devam edebilir, onizleme ile gonderim ayni
    metinden uretilir.
    """
    written = {key: payload[key] for key in ("subject", "body") if payload.get(key) is not None}
    if template is None and not written:
        return None
    data = dict(template or {})
    data.update({key: str(value) for key, value in written.items()})
    return data


@router.post("/groups/{group_id}/mail-preview")
def preview_mail(
    request: Request, group_id: int, payload: dict[str, Any] = Body(default_factory=dict)
):
    """Cozulmus konu/govde; hicbir sey kaydedilmez, Outlook'a dokunulmaz."""
    context = get_context(request)
    prepared = _prepare(context, group_id, payload)
    template = prepared["template"]
    filled = prepared["filled"]
    if filled is None:
        return error_response("mail_template_missing", "Önce bir e-posta şablonu tanımlayın.")

    data = prepared["data"]
    inline = payload.get("inline_table")
    rendered = mailsend.render_mail(
        filled,
        data.group,
        prepared["rows"],
        data.heads,
        inline_table=None if inline is None else _truthy(inline, True),
    )
    return {
        "template_id": template["id"] if template else None,
        "to": rendered["to"],
        "cc": rendered["cc"],
        "subject": rendered["subject"],
        "html": rendered["html"],
        "count": rendered["count"],
        "file_name": mailsend.file_name(data.group["name"]),
        "attach_excel": bool(template["attach_excel"]) if template else True,
        "inline_table": bool(template["inline_table"]) if template else True,
    }


@router.post("/groups/{group_id}/mail-send")
def send_mail(
    request: Request, group_id: int, payload: dict[str, Any] = Body(default_factory=dict)
):
    """Excel'i uretir, Outlook'ta acar ya da gonderir ve kaydi yazar."""
    context = get_context(request)
    prepared = _prepare(context, group_id, payload)
    template = prepared["template"]
    filled = prepared["filled"]
    data = prepared["data"]
    rows = prepared["rows"]
    if filled is None:
        return error_response("mail_template_missing", "Önce bir e-posta şablonu tanımlayın.")
    if not rows:
        return error_response("mail_no_rows", "Gönderilecek kayıt yok.")

    attach_excel = _truthy(
        payload.get("attach_excel"), default=bool(template and template["attach_excel"])
    )
    inline_table = _truthy(
        payload.get("inline_table"), default=bool(template and template["inline_table"])
    )
    rendered = mailsend.render_mail(
        filled, data.group, rows, data.heads, inline_table=inline_table
    )

    to = _addresses(payload.get("to"), rendered["to"])
    cc = _addresses(payload.get("cc"), rendered["cc"])
    subject = rendered["subject"]
    # Arayuz onizlemedeki HTML'i aynen yollayabilir; yollamazsa burada uretilir.
    html = str(payload.get("html") or rendered["html"])
    if not to:
        return error_response("mail_no_recipient", "Kime alanı boş olamaz.")

    mode = clean_mode(payload.get("mode") or context.settings.get("mailsend.mode", MODE_DISPLAY))

    # Gonderici once kurulur: Windows disinda hicbir dosya uretilmeden
    # `feature_unavailable` doner.
    sender = context.sender_factory()

    attachments: list[Any] = []
    file_name = ""
    if attach_excel:
        from .mail import send as mail_send

        file_name = mailsend.file_name(data.group["name"])
        workbook = export.build_workbook(
            context,
            group_id,
            columns=prepared["columns"],
            q=str(payload.get("q") or "") if prepared["apply_view"] else "",
            sort=str(payload.get("sort") or "") if prepared["apply_view"] else "",
            direction=str(payload.get("dir") or "") if prepared["apply_view"] else "",
            keys=prepared["keys"],
        )
        attachments.append(mail_send.write_attachment(file_name, workbook))

    result = sender.send(to, cc, subject, html, attachments, mode)

    with context.db_lock:
        record = mailsend_repo.record_send(
            context.connection(),
            group_id,
            template["id"] if template else None,
            mailsend.join_addresses(to),
            mailsend.join_addresses(cc),
            subject,
            len(rows),
            file_name,
            mode,
        )
    return {
        "ok": True,
        "mode": record["mode"],
        "file_name": file_name,
        "count": len(rows),
        "displayed": bool((result or {}).get("displayed", mode != "send")),
        "send": record,
    }


def _addresses(value: Any, fallback: Sequence[str]) -> list[str]:
    """Arayuz duzenlenmis listeyi yollar; yollamazsa sablonunki kullanilir."""
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    if value is None:
        return list(fallback)
    return mailsend.split_addresses(value)


@router.get("/groups/{group_id}/mail-sends")
def read_mail_sends(request: Request, group_id: int, limit: int = mailsend_repo.SENDS_LIMIT):
    context = get_context(request)
    conn = context.connection()
    repository.require_group(conn, group_id)
    return {"sends": mailsend_repo.list_sends(conn, group_id, limit)}


@router.get("/groups/{group_id}/export-selected.xlsx")
def export_selected(
    request: Request,
    group_id: int,
    keys: str = "",
    columns: str = "",
    history: str = "",
    q: str = "",
    sort: str = "",
    dir: str = "",
):
    """Yalnizca isaretlenen kayitlarla Excel; `keys` bossa grubun tamami."""
    context = get_context(request)
    group = repository.require_group(context.connection(), group_id)
    payload = export.build_workbook(
        context,
        group_id,
        columns=export.parse_columns(columns),
        include_history=export.truthy(history),
        q=q,
        sort=sort,
        direction=dir,
        keys=export.parse_columns(keys),
    )
    return Response(
        content=payload,
        media_type=export.MEDIA_TYPE,
        headers={"Content-Disposition": export.content_disposition(group["name"])},
    )
