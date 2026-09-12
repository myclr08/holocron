"""HTTP API uclari. Is mantigi ince tutulur: SQL repository'de, ag jira_client'ta."""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse, Response

from . import (
    __version__,
    db,
    desktop,
    export,
    fields as field_utils,
    grid,
    repository,
    tasks as task_utils,
    teams,
)
from .context import AppContext
from .diagnose import run_diagnostics
from .jira_client import JiraError
from .mail import MailError
from .mail import intake as mail_intake
from .teamscalls import is_supported as calls_supported
from .teamscalls import intake as calls_intake
from .lifecycle import BEAT_INTERVAL_SECONDS
from .repository import RepositoryError
from .settings_store import MODE_CLOUD, MODE_SERVER, PROXY_MODES

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
        "schema_version": db.current_version(context.connection()),
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
    return {"ok": True, "message": "Holocron kapatılıyor."}


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
        return error_response("invalid_mode", "Mod yalnızca 'server' veya 'cloud' olabilir.")
    base_url = payload.get("jira.base_url")
    if base_url and not str(base_url).startswith(("http://", "https://")):
        return error_response("invalid_base_url", "Adres http:// veya https:// ile başlamalı.")
    proxy_mode = payload.get("net.proxy_mode")
    if proxy_mode is not None and proxy_mode not in PROXY_MODES:
        return error_response(
            "invalid_proxy_mode",
            "Vekil sunucu kipi yalnızca 'system', 'manual' veya 'direct' olabilir.",
        )
    context.settings.apply(payload)
    return {"settings": context.settings.public_view()}


@router.post("/settings/diagnose")
def diagnose_connection(request: Request) -> dict[str, Any]:
    """Adim adim ag teshisi.

    "Baglanti kurulamadi" cumlesi kullaniciyi bos biraktigi icin var: DNS mi,
    TCP mi, TLS mi, vekil sunucu mu takildi, hangi adimda kac milisaniye
    gecti, hepsi tek listede doner. Cikti kimlik bilgisi tasimaz.
    """
    context = get_context(request)
    config = context.settings.jira_config()
    return run_diagnostics(config, client_factory=context.client_factory)


@router.get("/settings/columns")
def read_default_columns(request: Request) -> dict[str, Any]:
    context = get_context(request)
    return {"columns": repository.default_columns(context.connection())}


@router.put("/settings/columns")
def write_default_columns(request: Request, payload: dict[str, Any] = Body(default_factory=dict)):
    context = get_context(request)
    columns = payload.get("columns")
    if not isinstance(columns, list):
        return error_response("invalid_columns", "columns bir liste olmalı.")
    with context.db_lock:
        saved = repository.set_default_columns(context.connection(), columns)
    return {"columns": saved}


# --- Jira baglantisi ve alan katalogu ------------------------------------


@router.post("/jira/test")
def test_connection(request: Request):
    context = get_context(request)
    config = context.settings.jira_config()
    if not config.base_url:
        return error_response("config_missing", "Önce Jira adresini girin.")
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
        return error_response("config_missing", "Önce Jira adresini girin.")
    try:
        client = context.client_factory(config)
        catalog = client.fetch_fields()
    except JiraError as exc:
        return error_response(exc.code, exc.message, status=400)

    with context.db_lock:
        stored = repository.store_fields(context.connection(), catalog)
    return {"count": stored, "fetched_at": repository.now_iso()}


@router.get("/jira/fields")
def list_jira_fields(request: Request) -> dict[str, Any]:
    context = get_context(request)
    return {"fields": repository.list_fields(context.connection(), include_virtual=False)}


@router.get("/fields")
def list_all_fields(request: Request) -> dict[str, Any]:
    """Katalog + sanal alanlar; sutun secici bu ucu kullanir."""
    context = get_context(request)
    return {"fields": repository.list_fields(context.connection())}


# --- gruplar ------------------------------------------------------------


@router.get("/groups")
def list_groups(request: Request) -> dict[str, Any]:
    context = get_context(request)
    return {
        "groups": repository.list_groups(context.connection()),
        "colors": list(repository.GROUP_COLORS),
        "default_columns": repository.default_columns(context.connection()),
    }


@router.post("/groups")
def create_group(request: Request, payload: dict[str, Any] = Body(default_factory=dict)):
    context = get_context(request)
    with context.db_lock:
        group = repository.create_group(
            context.connection(),
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
        return error_response("invalid_order", "ids bir liste olmalı.")
    with context.db_lock:
        groups = repository.reorder_groups(context.connection(), ids)
    return {"groups": groups}


@router.get("/groups/{group_id}")
def read_group(request: Request, group_id: int) -> dict[str, Any]:
    context = get_context(request)
    group = repository.require_group(context.connection(), group_id)
    return {"group": group, "columns": repository.group_columns(context.connection(), group)}


@router.put("/groups/{group_id}")
def write_group(request: Request, group_id: int, payload: dict[str, Any] = Body(default_factory=dict)):
    context = get_context(request)
    with context.db_lock:
        group = repository.update_group(context.connection(), group_id, payload)
    return {"group": group}


@router.delete("/groups/{group_id}")
def drop_group(request: Request, group_id: int) -> dict[str, Any]:
    context = get_context(request)
    with context.db_lock:
        repository.delete_group(context.connection(), group_id)
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
        result = repository.add_items(context.connection(), group_id, keys)
        group = repository.require_group(context.connection(), group_id)
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
        repository.remove_item(context.connection(), group_id, key)
        group = repository.require_group(context.connection(), group_id)
    return {"ok": True, "group": group}


@router.post("/groups/{group_id}/items/{key}/pin")
def pin_group_item(request: Request, group_id: int, key: str) -> dict[str, Any]:
    context = get_context(request)
    with context.db_lock:
        pinned = repository.toggle_pin(context.connection(), group_id, key)
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
    data = grid.build_grid(context, group_id, q=q, sort=sort, direction=dir)

    rows = data.rows
    for row in rows:
        for cell in row["cells"]:
            cell["text"] = field_utils.truncate(cell["text"])
        row.pop("_raw", None)
        row.pop("_local_text", None)

    return {
        "group": data.group,
        "columns": data.heads,
        "rows": rows,
        "total": data.total,
        "shown": len(rows),
        "sort": {"field": data.sort_field, "dir": data.sort_dir} if data.sort_field else None,
        "base_url": data.base_url,
        # Katalog bosken basliklar ham alan kimligine duser; arayuz uyari gosterir.
        "catalog_empty": repository.field_count(context.connection()) == 0,
    }


@router.get("/groups/{group_id}/export.xlsx")
def export_group(
    request: Request,
    group_id: int,
    columns: str = "",
    history: str = "",
    q: str = "",
    sort: str = "",
    dir: str = "",
):
    """Grubu Excel dosyasi olarak indirir; satirlar grid ile birebir aynidir."""
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
    )
    return Response(
        content=payload,
        media_type=export.MEDIA_TYPE,
        headers={"Content-Disposition": export.content_disposition(group["name"])},
    )


@router.get("/issues/keys")
def list_issue_keys(request: Request, q: str = "", limit: int = 20) -> dict[str, Any]:
    """Gorev formundaki 'Jira kaydi' alaninin otomatik tamamlama listesi.

    Bu uc `/issues/{key}`'den ONCE tanimlanir; sonra gelirse "keys" bir kayit
    anahtari sanilir ve 404 doner.
    """
    context = get_context(request)
    capped = max(1, min(int(limit or 20), 50))
    return {"keys": repository.known_issue_keys(context.connection(), q, capped)}


@router.get("/issues/{key}")
def read_issue(request: Request, key: str) -> dict[str, Any]:
    context = get_context(request)
    conn = context.connection()
    clean_key = str(key).strip().upper()
    record = repository.get_issue(conn, clean_key)
    if record is None and not repository.issue_is_known(conn, clean_key):
        raise RepositoryError("issue_not_found", "Kayıt henüz çekilmemiş.", status=404)

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


# --- gorevler (kisisel kanban) ------------------------------------------
#
# Yol sirasi onemli: "summary", "export.xlsx" ve "reorder" sabit yollari
# "/tasks/{task_id}"den once gelmeli.


@router.get("/tasks")
def read_tasks(request: Request, q: str = "", include_old_done: str = "") -> dict[str, Any]:
    context = get_context(request)
    board = task_utils.build_board(context, q=q, include_old_done=export.truthy(include_old_done))
    return _board_payload(board)


@router.post("/tasks")
def create_task(request: Request, payload: dict[str, Any] = Body(default_factory=dict)):
    context = get_context(request)
    with context.db_lock:
        task = repository.create_task(
            context.connection(),
            title=payload.get("title"),
            description=payload.get("description"),
            note=payload.get("note"),
            due_date=payload.get("due_date"),
            status=payload.get("status"),
            issue_key=payload.get("issue_key"),
        )
    return {"task": _task_payload(context, task)}


@router.get("/tasks/summary")
def read_tasks_summary(request: Request) -> dict[str, Any]:
    """Kenar cubugu rozeti; pano yuklenmeden de okunur."""
    context = get_context(request)
    return task_utils.summary_counts(context.connection())


@router.get("/tasks/export.xlsx")
def export_tasks(request: Request, status: str = "all", q: str = ""):
    context = get_context(request)
    if status and status != "all":
        repository.clean_task_status(status)
    payload = export.build_tasks_workbook(context, status=status or "all", q=q)
    return Response(
        content=payload,
        media_type=export.MEDIA_TYPE,
        headers={"Content-Disposition": export.content_disposition(export.TASKS_NAME)},
    )


@router.post("/tasks/reorder")
def reorder_tasks(request: Request, payload: dict[str, Any] = Body(default_factory=dict)):
    context = get_context(request)
    ids = payload.get("ids")
    if not isinstance(ids, list):
        return error_response("invalid_order", "ids bir liste olmalı.")
    with context.db_lock:
        repository.reorder_tasks(context.connection(), ids, payload.get("status"))
    return _board_payload(task_utils.build_board(context))


@router.put("/tasks/{task_id}")
def write_task(request: Request, task_id: int, payload: dict[str, Any] = Body(default_factory=dict)):
    context = get_context(request)
    with context.db_lock:
        task = repository.update_task(context.connection(), task_id, payload)
    return {"task": _task_payload(context, task)}


@router.delete("/tasks/{task_id}")
def drop_task(request: Request, task_id: int) -> dict[str, Any]:
    context = get_context(request)
    with context.db_lock:
        repository.delete_task(context.connection(), task_id)
    return {"ok": True}


@router.post("/tasks/{task_id}/move")
def move_task(request: Request, task_id: int, payload: dict[str, Any] = Body(default_factory=dict)):
    context = get_context(request)
    with context.db_lock:
        task = repository.move_task(
            context.connection(),
            task_id,
            status=payload.get("status"),
            position=payload.get("position"),
        )
    return {"task": _task_payload(context, task)}


def _board_payload(board: task_utils.Board) -> dict[str, Any]:
    return {
        "columns": board.columns,
        "old_done_count": board.old_done_count,
        "summary": board.summary,
        "base_url": board.base_url,
        "statuses": [
            {"id": name, "label": repository.TASK_STATUS_LABELS[name]}
            for name in repository.TASK_STATUSES
        ],
    }


def _task_payload(context: AppContext, task: dict[str, Any]) -> dict[str, Any]:
    """Tek gorev: kart ile ayni bicimde (due_state + bagli kayit)."""
    card = dict(task)
    card["due_state"] = task_utils.due_state(task["due_date"])
    base_url = (context.settings.get("jira.base_url", "") or "").rstrip("/")
    if task["issue_key"]:
        record = repository.get_issue(context.connection(), task["issue_key"])
        card["issue"] = task_utils.issue_view(task["issue_key"], record, base_url)
    else:
        card["issue"] = None
    return card


# --- e-posta (Outlook) --------------------------------------------------
#
# Windows disinda uclar `feature_unavailable` doner: COM yok, sahte bir
# basari da uretilmez. Sifre saklanmaz; acik Outlook oturumu kullanilir.


def mail_source(context: AppContext, config: Any = None) -> Any:
    """Yapilandirilmis posta kaynagi (uretimde Outlook, testte sahte kaynak)."""
    return context.mail_factory(getattr(config, "body_limit", None))


@router.post("/mail/test")
def test_mail(request: Request) -> dict[str, Any]:
    """Baglantiyi sinar: Outlook surumu, hesap adi, klasor oge sayilari."""
    context = get_context(request)
    config = mail_intake.load_config(context.settings)
    source = mail_source(context, config)
    result = source.probe()
    return {"result": result}


@router.get("/mail/folders")
def list_mail_folders(request: Request) -> dict[str, Any]:
    """Klasor agaci; arayuzdeki onay kutulu secici bunu cizer."""
    context = get_context(request)
    config = mail_intake.load_config(context.settings)
    source = mail_source(context, config)
    return {"folders": [folder.to_dict() for folder in source.folders()]}


@router.post("/mail/scan")
def scan_mail(request: Request, payload: dict[str, Any] = Body(default_factory=dict)):
    """Elle tarama. Ozet: created / appended / skipped_* / scanned / errors."""
    context = get_context(request)
    config = mail_intake.load_config(context.settings)
    if not config.enabled:
        return error_response("mail_disabled", "Önce Ayarlar → E-posta'dan taramayı açın.")
    if not config.has_addresses:
        return error_response(
            "mail_no_address",
            "Adres tanımlı değil: Kimden, Kime ya da CC listelerinden en az birine adres yazın.",
        )
    source = mail_source(context, config)
    with context.db_lock:
        summary = mail_intake.scan(context.connection(), source, config)
    return {"summary": summary, "board": _board_payload(task_utils.build_board(context))}


@router.post("/tasks/{task_id}/open-mail")
def open_task_mail(request: Request, task_id: int) -> dict[str, Any]:
    """Gorevin kaynagi olan e-postayi Outlook'ta acar."""
    context = get_context(request)
    task = repository.require_task(context.connection(), task_id)
    if task["source"] != repository.TASK_SOURCE_MAIL or not task["mail_entry_id"]:
        raise MailError("mail_not_found", "Bu görev bir e-postadan gelmedi.", status=404)
    source = mail_source(context)
    source.open_message(task["mail_entry_id"], task["mail_store_id"])
    return {"ok": True}


# --- Teams (derin baglanti) ---------------------------------------------
#
# Graph API yok, IT izni yok: mesaj `https://teams.microsoft.com/l/chat/...`
# baglantisiyla Teams'in yazma kutusuna konur, **Gonder'e kullanici basar**.
# Bu yuzden `sent_messages` satirlari "gonderildi" degil "acildi" demektir.
# Hedef yalnizca kisilerdir; kanala yazma yolu kaldirildi.


@router.get("/contacts")
def search_contacts(request: Request, q: str = "", limit: int = 20) -> dict[str, Any]:
    """Adres defteri: kisi kutusundaki otomatik tamamlama bunu kullanir."""
    context = get_context(request)
    capped = max(1, min(int(limit or repository.CONTACT_SEARCH_LIMIT), 50))
    return {"contacts": repository.list_contacts(context.connection(), q, capped)}


@router.post("/contacts/import-gal")
def import_address_book(request: Request) -> dict[str, Any]:
    """Outlook kurum rehberini (Genel Adres Listesi) adres defterine ceker.

    Yalnizca Windows'ta is gorur: kaynak COM'a baglanamiyorsa uc
    `feature_unavailable` doner. Elle girilen kisiler korunur; ayni adres
    rehberde de geciyorsa adi ve turu tazelenir.
    """
    context = get_context(request)
    source = mail_source(context)
    started = time.monotonic()
    entries = source.address_book()
    with context.db_lock:
        result = repository.import_contacts(context.connection(), entries)
    result["ms"] = int((time.monotonic() - started) * 1000)
    stamp = repository.now_iso()
    context.settings.set("teams.gal_synced_at", stamp)
    result["synced_at"] = stamp
    return {"result": result}


@router.put("/contacts/{email}")
def write_contact(
    request: Request, email: str, payload: dict[str, Any] = Body(default_factory=dict)
):
    """Defterdeki kisiyi duzenler (Ayarlar -> Teams kartindaki liste)."""
    context = get_context(request)
    with context.db_lock:
        contact = repository.update_contact(context.connection(), email, payload)
    return {"contact": contact}


@router.delete("/contacts/{email}")
def drop_contact(request: Request, email: str) -> dict[str, Any]:
    """Kisiyi defterden ve butun kayitlardan siler."""
    context = get_context(request)
    with context.db_lock:
        return repository.delete_contact(context.connection(), email)


@router.get("/issues/{key}/contacts")
def read_issue_contacts(request: Request, key: str) -> dict[str, Any]:
    context = get_context(request)
    return {"contacts": repository.list_issue_contacts(context.connection(), key)}


@router.put("/issues/{key}/contacts")
def write_issue_contacts(
    request: Request, key: str, payload: dict[str, Any] = Body(default_factory=dict)
):
    """Tam liste yazilir; ayni cagri adres defterini de gunceller."""
    context = get_context(request)
    entries = payload.get("contacts")
    if not isinstance(entries, list):
        return error_response("invalid_contacts", "contacts bir liste olmalı.")
    with context.db_lock:
        contacts = repository.set_issue_contacts(context.connection(), key, entries)
    return {"contacts": contacts}


@router.get("/templates")
def list_message_templates(request: Request) -> dict[str, Any]:
    context = get_context(request)
    return {
        "templates": repository.list_templates(context.connection()),
        "placeholders": [{"token": token, "label": label} for token, label in teams.PLACEHOLDERS],
        "topic_format": context.settings.get("teams.topic_format", teams.DEFAULT_TOPIC_FORMAT),
    }


@router.post("/templates")
def create_message_template(request: Request, payload: dict[str, Any] = Body(default_factory=dict)):
    context = get_context(request)
    with context.db_lock:
        template = repository.create_template(
            context.connection(),
            payload.get("name"),
            payload.get("body"),
            is_default=bool(payload.get("is_default")),
        )
    return {"template": template}


@router.put("/templates/{template_id}")
def write_message_template(
    request: Request, template_id: int, payload: dict[str, Any] = Body(default_factory=dict)
):
    context = get_context(request)
    with context.db_lock:
        template = repository.update_template(context.connection(), template_id, payload)
    return {"template": template}


@router.delete("/templates/{template_id}")
def drop_message_template(request: Request, template_id: int) -> dict[str, Any]:
    context = get_context(request)
    with context.db_lock:
        return repository.delete_template(context.connection(), template_id)


@router.post("/issues/{key}/message-preview")
def preview_message(
    request: Request, key: str, payload: dict[str, Any] = Body(default_factory=dict)
):
    """Yer tutuculari cozulmus mesaj; hicbir sey kaydedilmez.

    Cekmecedeki metin kutusu sablon secilince bununla dolar: `teams-link`
    her cagrisinda "acildi" satiri yazdigi icin onizleme icin kullanilamaz.
    """
    context = get_context(request)
    conn = context.connection()
    body = _template_body(conn, payload)
    if isinstance(body, JSONResponse):
        return body
    return {"message": _render_issue_message(context, conn, key, body)}


@router.post("/issues/{key}/teams-link")
def build_teams_link(
    request: Request, key: str, payload: dict[str, Any] = Body(default_factory=dict)
):
    """Teams baglantisini kurar ve "acildi" kaydini duser.

    Tarayici yolu: arayuz donen `https` adresini `window.open` ile acar.
    `teams-open` calisamadiginda yedek olarak da kullanilir.
    """
    context = get_context(request)
    prepared = _prepare_teams_message(context, key, payload)
    if isinstance(prepared, JSONResponse):
        return prepared

    link = teams.build_chat_link(prepared["emails"], prepared["message"], prepared["topic"])
    _record_open(context, prepared)
    return _link_payload(prepared, link)


@router.post("/issues/{key}/teams-open")
def open_teams_chat(
    request: Request, key: str, payload: dict[str, Any] = Body(default_factory=dict)
):
    """Sohbeti SUNUCUDAN acar: `msteams:` adresi isletim sistemine verilir.

    Tarayicidan acmak geride bos bir sekme birakiyordu. Acilis basarisizsa
    (`opened: false`) yanit yine `https` adresini tasir; arayuz o zaman eski
    yolla `window.open` yapar. "Acildi" kaydi her iki durumda da bir kez
    yazilir.
    """
    context = get_context(request)
    prepared = _prepare_teams_message(context, key, payload)
    if isinstance(prepared, JSONResponse):
        return prepared

    link = teams.build_chat_link(prepared["emails"], prepared["message"], prepared["topic"])
    app_link = teams.build_app_link(prepared["emails"], prepared["message"], prepared["topic"])
    _record_open(context, prepared)

    result = _link_payload(prepared, link)
    result["app_url"] = app_link["url"]
    result["opened"] = desktop.open_url(app_link["url"])
    return result


def _prepare_teams_message(
    context: AppContext, key: str, payload: dict[str, Any]
) -> Any:
    """Iki ucun ortak hazirligi: kayit, govde, kisiler, konu adi."""
    conn = context.connection()
    clean_key = str(key).strip().upper()
    if not repository.issue_is_known(conn, clean_key):
        raise RepositoryError("issue_not_found", "Kayıt bulunamadı.", status=404)

    body = _template_body(conn, payload)
    if isinstance(body, JSONResponse):
        return body

    contacts = repository.list_issue_contacts(conn, clean_key)
    if not contacts:
        return error_response("no_contacts", "Önce bu kayda bir Teams kişisi ekleyin.")

    emails = [item["email"] for item in contacts]
    return {
        "key": clean_key,
        "message": _render_issue_message(context, conn, clean_key, body),
        "emails": emails,
        "target": ", ".join(emails),
        "topic": _render_issue_message(
            context,
            conn,
            clean_key,
            context.settings.get("teams.topic_format", teams.DEFAULT_TOPIC_FORMAT)
            or teams.DEFAULT_TOPIC_FORMAT,
        ),
    }


def _record_open(context: AppContext, prepared: dict[str, Any]) -> None:
    """Kayit notu tam metni tutar, kirpilmis halini degil."""
    with context.db_lock:
        repository.record_sent_message(
            context.connection(),
            prepared["key"],
            teams.TARGET_PEOPLE,
            prepared["target"],
            prepared["message"],
        )


def _link_payload(prepared: dict[str, Any], link: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "kind": teams.TARGET_PEOPLE,
        "url": link["url"],
        "message": link["message"],
        "truncated": link["truncated"],
        "target": prepared["target"],
    }
    if link["truncated"]:
        # Adres uzunlugu yuzunden kirpildi: tam metin panodan yapistirilir.
        result["clipboard"] = prepared["message"]
    return result


@router.get("/issues/{key}/messages")
def read_sent_messages(request: Request, key: str, limit: int = 20) -> dict[str, Any]:
    context = get_context(request)
    capped = max(1, min(int(limit or 20), 100))
    return {"messages": repository.list_sent_messages(context.connection(), key, capped)}


def _template_body(conn: Any, payload: dict[str, Any]) -> Any:
    """Govde: elle yazilan metin, secilen sablon, yoksa varsayilan sablon."""
    body = payload.get("body")
    if body is not None and str(body).strip():
        return str(body)
    if payload.get("template_id") is not None:
        return repository.require_template(conn, payload["template_id"])["body"]
    template = repository.default_template(conn)
    if template is None:
        return error_response(
            "template_missing", "Önce Ayarlar → Teams altından bir şablon tanımlayın."
        )
    return template["body"]


def _render_issue_message(context: AppContext, conn: Any, key: str, body: str) -> str:
    """Sablonu kaydin degerleriyle doldurur (Jira alanlari + yerel alanlar)."""
    clean_key = str(key).strip().upper()
    record = repository.get_issue(conn, clean_key) or {
        "key": clean_key,
        "raw": {"key": clean_key, "fields": {}},
    }
    definitions = repository.local_fields_by_id(conn)
    stored = repository.local_values_for(conn, [clean_key]).get(clean_key, {})
    local_values = {
        field_id: field_utils.format_local_value(
            definitions[field_id]["type"], entry.get("value")
        )
        for field_id, entry in stored.items()
        if field_id in definitions
    }
    base_url = (context.settings.get("jira.base_url", "") or "").rstrip("/")
    return teams.render_template(
        body,
        record,
        local_values,
        base_url,
        schemas=repository.field_schemas(conn),
    )


# --- Teams aramalari (yerel onbellek) -----------------------------------
#
# Kaynak, Teams'in kendi makinedeki IndexedDB onbellegidir: kullanicinin
# "Aramalar -> Gecmis" ekraninda zaten gorunen KENDI kayitlari. Ag cagrisi
# yok, Graph API yok, baska kullanicinin verisi yok. Windows disinda uclar
# `feature_unavailable` doner.


def calls_source(context: AppContext, config: Any = None) -> Any:
    """Yapilandirilmis arama kaynagi (uretimde onbellek, testte sahte kaynak)."""
    return context.calls_factory(getattr(config, "cache_path", "") or "")


@router.post("/calls/scan")
def scan_calls(request: Request) -> dict[str, Any]:
    """Onbellegi tarar.

    Ozet yalnizca sayilari tasimaz: `source` verinin gecici kopyadan mi canli
    klasorden mi geldigini, `copied` / `skipped` / `skipped_kinds` kopyalamanin
    ne kadarinin tuttugunu, `warning` en yeni aramalarin eksik olabilecegini,
    `latest_call_at` ise okunan en yeni aramanin zamanini soyler.
    """
    context = get_context(request)
    config = calls_intake.load_config(context.settings)
    source = calls_source(context, config)
    started = time.monotonic()
    with context.db_lock:
        result = calls_intake.scan(
            context.connection(),
            source,
            my_mri_setting=context.settings.get("calls.my_mri", "") or "",
        )
    result["ms"] = int((time.monotonic() - started) * 1000)
    stamp = repository.now_iso()
    context.settings.set("calls.scanned_at", stamp)
    result["scanned_at"] = stamp
    return result


def _calls_payload(
    context: AppContext,
    days: int,
    q: str = "",
    direction: str = "",
    state: str = "",
    kind: str = "",
    with_stats: bool = True,
) -> dict[str, Any]:
    """Ekranin tek yanitta ihtiyaci olan her sey. YALNIZCA SQLite okur.

    Onbellek burada ACILMAZ: pencere degistirmek ya da arama kutusuna yazmak
    Teams'in IndexedDB'sine dokunmamali (saniyeler suruyor).
    """
    conn = context.connection()
    # Pencere suzgeci SQL'de: butun tabloyu cekip Python'da elemek yerine.
    since = calls_intake.iso_text(calls_intake.since_of(days))
    rows = repository.list_calls(conn, since=since)
    names = calls_intake.names_from_rows(rows)
    picked = calls_intake.select(
        rows, days=days, q=q, direction=direction, state=state, kind=kind, names=names
    )
    cards = [calls_intake.view(row, names) for row in picked]
    payload: dict[str, Any] = {
        "calls": cards,
        "people": calls_intake.people_totals(picked, names),
        "count": len(picked),
        "total": repository.call_count(conn),
        "days": calls_intake._as_days(days),
        "windows": list(calls_intake.WINDOW_DAYS),
        "scanned_at": context.settings.get("calls.scanned_at", "") or "",
        "supported": calls_supported(),
        # Rozet buradan hesaplanir; "Eslesmeyenler" ucuna istek atilmaz.
        "unmatched": sum(
            1
            for card in cards
            if card["kind"] == calls_intake.KIND_GROUP and card["thread_kind"] != "group_chat"
        ),
    }
    if with_stats:
        payload["stats"] = calls_intake.build_stats(rows, days=days, names=names)
    return payload


@router.get("/calls/view")
def read_calls_view(
    request: Request,
    days: int = calls_intake.DEFAULT_DAYS,
    q: str = "",
    direction: str = "",
    state: str = "",
    kind: str = "",
) -> dict[str, Any]:
    """Liste + kisiler + istatistik tek istekte (ekranin kullandigi uc)."""
    return _calls_payload(get_context(request), days, q, direction, state, kind)


@router.get("/calls")
def read_calls(
    request: Request,
    days: int = calls_intake.DEFAULT_DAYS,
    q: str = "",
    direction: str = "",
    state: str = "",
    kind: str = "",
) -> dict[str, Any]:
    """Pencere + suzgeclerle arama listesi ve ayni kumeden kisi dokumu."""
    payload = _calls_payload(
        get_context(request), days, q, direction, state, kind, with_stats=False
    )
    return payload


@router.get("/calls/stats")
def read_call_stats(request: Request, days: int = calls_intake.DEFAULT_DAYS) -> dict[str, Any]:
    """Istatistik seridi: top 5, uc dilim, aradim/arandim, is gunu ortalamasi."""
    context = get_context(request)
    since = calls_intake.iso_text(calls_intake.since_of(days))
    rows = repository.list_calls(context.connection(), since=since)
    return calls_intake.build_stats(rows, days=days)


@router.get("/calls/export.xlsx")
def export_calls(
    request: Request,
    days: int = calls_intake.DEFAULT_DAYS,
    q: str = "",
    direction: str = "",
    state: str = "",
    kind: str = "",
):
    context = get_context(request)
    payload = export.build_calls_workbook(
        context, days=days, q=q, direction=direction, state=state, kind=kind
    )
    return Response(
        content=payload,
        media_type=export.MEDIA_TYPE,
        headers={"Content-Disposition": export.content_disposition(export.CALLS_NAME)},
    )


@router.get("/calls/unmatched")
def read_unmatched_calls(
    request: Request, days: int = calls_intake.DEFAULT_DAYS
) -> dict[str, Any]:
    """Teshis: eslesmemis cok kisili aramalar ve neden eslesmedikleri.

    Takvimi TAZE okur (onbellek acilir), cunku asil soru "yeniden tarasam
    duzelir mi". Windows disinda `feature_unavailable` doner.
    """
    context = get_context(request)
    config = calls_intake.load_config(context.settings)
    source = calls_source(context, config)
    bundle = source.read()
    calendar = bundle[1] if len(bundle) > 1 else []
    return calls_intake.diagnose_unmatched(
        repository.list_calls(context.connection()), calendar, days=days
    )


@router.get("/calls/attendance-diagnose")
def read_attendance_diagnosis(
    request: Request, days: int = calls_intake.DEFAULT_DAYS
) -> dict[str, Any]:
    """Teshis: toplanti sohbetlerindeki her katilim mesaji ne oldu, neden?

    Onbellegi TAZE okur (saniyeler surer): yalnizca dugmeye basinca cagrilir.
    """
    context = get_context(request)
    config = calls_intake.load_config(context.settings)
    source = calls_source(context, config)
    bundle = tuple(source.read())
    calls, calendar, names, threads, attended = (*bundle, [], [], [], [])[:5]
    found = calls_intake.source_diagnostics(source)
    marker = calls_intake.my_mri(
        setting=context.settings.get("calls.my_mri", "") or "",
        discovered=found.get("my_mri"),
    )
    return calls_intake.diagnose_attendance(
        attended,
        marker,
        calendar,
        names,
        known_ids=repository.history_call_ids(context.connection()),
        days=days,
    )


@router.get("/calls/person/{counterpart_id}")
def read_call_person(
    request: Request, counterpart_id: str, days: int = calls_intake.DEFAULT_DAYS
) -> dict[str, Any]:
    """Kisi cekmecesi: ozet, o kisiyle butun gorusmeler, ortak grup/toplantilar."""
    context = get_context(request)
    since = calls_intake.iso_text(calls_intake.since_of(days))
    rows = repository.list_calls(context.connection(), since=since)
    return calls_intake.person_view(rows, counterpart_id, days=days)


# --- yerel alanlar ------------------------------------------------------


@router.get("/local-fields")
def list_local_fields(request: Request) -> dict[str, Any]:
    context = get_context(request)
    return {
        "fields": repository.list_local_fields(context.connection()),
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
            context.connection(),
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
        return error_response("invalid_order", "ids bir liste olmalı.")
    with context.db_lock:
        fields = repository.reorder_local_fields(context.connection(), ids)
    return {"fields": fields}


@router.get("/local-fields/{field_id}")
def read_local_field(request: Request, field_id: int) -> dict[str, Any]:
    context = get_context(request)
    return {"field": repository.require_local_field(context.connection(), field_id)}


@router.put("/local-fields/{field_id}")
def write_local_field(
    request: Request, field_id: int, payload: dict[str, Any] = Body(default_factory=dict)
):
    context = get_context(request)
    with context.db_lock:
        field = repository.update_local_field(context.connection(), field_id, payload)
    return {"field": field}


@router.delete("/local-fields/{field_id}")
def drop_local_field(request: Request, field_id: int) -> dict[str, Any]:
    context = get_context(request)
    with context.db_lock:
        return repository.delete_local_field(context.connection(), field_id)


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
        return repository.set_local_value(context.connection(), key, field_id, payload.get("value"))


@router.get("/issues/{key}/local/{field_id}/history")
def read_local_history(request: Request, key: str, field_id: int) -> dict[str, Any]:
    context = get_context(request)
    conn = context.connection()
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
        removed = repository.clear_local_history(context.connection(), key, field_id)
    return {"ok": True, "removed": removed}


@router.delete("/issues/{key}/local/{field_id}/history/{history_id}")
def drop_local_history_entry(
    request: Request, key: str, field_id: int, history_id: int
) -> dict[str, Any]:
    context = get_context(request)
    with context.db_lock:
        repository.delete_local_history_entry(context.connection(), key, field_id, history_id)
        remaining = repository.list_local_history(context.connection(), key, field_id)
    return {"ok": True, "changes": len(remaining)}


# --- Guncelle -----------------------------------------------------------


@router.post("/refresh")
def start_refresh(request: Request, payload: dict[str, Any] = Body(default_factory=dict)):
    context = get_context(request)
    config = context.settings.jira_config()
    if not config.base_url:
        return error_response("config_missing", "Önce Ayarlar ekranından Jira adresini girin.")

    group_id = payload.get("group_id")
    if group_id is not None:
        try:
            group_id = int(group_id)
        except (TypeError, ValueError):
            return error_response("invalid_group", "group_id sayı olmalı.")

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
#
# Grid satirlarini kuran yardimcilar app/grid.py icinde; Excel disa aktarimi
# da ayni yerden besleniyor.


def _detail_cell(
    field_id: str, schemas: dict[str, dict[str, Any]], raw: dict[str, Any]
) -> dict[str, Any]:
    value = field_utils.issue_value(raw, field_id)
    text = field_utils.format_field_value(schemas.get(field_id), value)
    return {
        "field": field_id,
        "name": repository.field_name(schemas, field_id),
        "text": text,
        "raw": grid.jsonable(value),
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
