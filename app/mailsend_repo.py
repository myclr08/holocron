"""E-posta sablonlari, grup bagi ve gonderim kayitlari (tum SQL).

Ayri bir modulde duruyor: `app/repository.py` zaten iki bin satir ve bu tablolar
(`mail_templates`, `mail_sends`, `groups.mail_template_id`) yalnizca "grup
kayitlarini e-postala" akisi tarafindan kullaniliyor.

`mail_sends` gercek bir gonderim kaydidir -- Teams'teki "acildi" satirindan farki
budur: Outlook'tan `Send()` cagrildiginda posta gercekten gider. Kip `display`
ise satir yine yazilir ama `mode` sutunu pencerenin acildigini soyler.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Sequence

from .mail.send import MODE_DISPLAY, MODES, clean_mode
from .repository import RepositoryError, now_iso

TEMPLATE_NAME_LIMIT = 60
TEMPLATE_SUBJECT_LIMIT = 250
TEMPLATE_BODY_LIMIT = 8000
TEMPLATE_ADDRESS_LIMIT = 2000

SENDS_LIMIT = 50


def _template_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "to_addresses": row["to_addresses"] or "",
        "cc_addresses": row["cc_addresses"] or "",
        "subject": row["subject"] or "",
        "body": row["body"] or "",
        "attach_excel": bool(row["attach_excel"]),
        "inline_table": bool(row["inline_table"]),
        "position": row["position"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


# --- dogrulama ----------------------------------------------------------


def clean_name(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise RepositoryError("invalid_name", "Şablon adı boş olamaz.")
    if len(text) > TEMPLATE_NAME_LIMIT:
        raise RepositoryError("invalid_name", f"Şablon adı en fazla {TEMPLATE_NAME_LIMIT} karakter.")
    return text


def clean_subject(value: Any) -> str:
    text = " ".join(str(value or "").split())
    if len(text) > TEMPLATE_SUBJECT_LIMIT:
        raise RepositoryError(
            "invalid_subject", f"Konu en fazla {TEMPLATE_SUBJECT_LIMIT} karakter."
        )
    return text


def clean_body(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    if len(text) > TEMPLATE_BODY_LIMIT:
        raise RepositoryError("invalid_body", f"Gövde en fazla {TEMPLATE_BODY_LIMIT} karakter.")
    return text


def clean_addresses(value: Any) -> str:
    text = str(value or "").strip()
    if len(text) > TEMPLATE_ADDRESS_LIMIT:
        raise RepositoryError("invalid_addresses", "Adres listesi çok uzun.")
    return text


def _as_flag(value: Any, default: bool = True) -> int:
    if value is None:
        return 1 if default else 0
    if isinstance(value, bool):
        return 1 if value else 0
    return 1 if str(value).strip().lower() in ("1", "true", "yes", "on", "evet") else 0


# --- sablonlar ----------------------------------------------------------


def list_templates(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM mail_templates ORDER BY position, id").fetchall()
    return [_template_dict(row) for row in rows]


def get_template(conn: sqlite3.Connection, template_id: Any) -> dict[str, Any] | None:
    try:
        wanted = int(template_id)
    except (TypeError, ValueError):
        return None
    row = conn.execute("SELECT * FROM mail_templates WHERE id = ?", (wanted,)).fetchone()
    return _template_dict(row) if row else None


def require_template(conn: sqlite3.Connection, template_id: Any) -> dict[str, Any]:
    template = get_template(conn, template_id)
    if template is None:
        raise RepositoryError("mail_template_not_found", "E-posta şablonu bulunamadı.", status=404)
    return template


def create_template(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    data = payload or {}
    position = conn.execute(
        "SELECT COALESCE(MAX(position), -1) + 1 AS p FROM mail_templates"
    ).fetchone()["p"]
    stamp = now_iso()
    with conn:
        cursor = conn.execute(
            "INSERT INTO mail_templates "
            "(name, to_addresses, cc_addresses, subject, body, attach_excel, inline_table, "
            " position, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                clean_name(data.get("name")),
                clean_addresses(data.get("to_addresses")),
                clean_addresses(data.get("cc_addresses")),
                clean_subject(data.get("subject")),
                clean_body(data.get("body")),
                _as_flag(data.get("attach_excel")),
                _as_flag(data.get("inline_table")),
                int(position),
                stamp,
                stamp,
            ),
        )
    return require_template(conn, int(cursor.lastrowid))  # type: ignore[arg-type]


_UPDATE_FIELDS: dict[str, Any] = {
    "name": clean_name,
    "to_addresses": clean_addresses,
    "cc_addresses": clean_addresses,
    "subject": clean_subject,
    "body": clean_body,
}


def update_template(
    conn: sqlite3.Connection, template_id: Any, payload: dict[str, Any]
) -> dict[str, Any]:
    template = require_template(conn, template_id)
    data = payload or {}
    updates: dict[str, Any] = {}
    for column, cleaner in _UPDATE_FIELDS.items():
        if column in data:
            updates[column] = cleaner(data[column])
    for column in ("attach_excel", "inline_table"):
        if column in data:
            updates[column] = _as_flag(data[column])
    if "position" in data and data["position"] is not None:
        try:
            updates["position"] = int(data["position"])
        except (TypeError, ValueError) as exc:
            raise RepositoryError("invalid_position", "position sayı olmalı.") from exc

    if updates:
        updates["updated_at"] = now_iso()
        assignments = ", ".join(f"{column} = ?" for column in updates)
        with conn:
            conn.execute(
                f"UPDATE mail_templates SET {assignments} WHERE id = ?",
                (*updates.values(), template["id"]),
            )
    return require_template(conn, template["id"])


def delete_template(conn: sqlite3.Connection, template_id: Any) -> dict[str, Any]:
    template = require_template(conn, template_id)
    with conn:
        conn.execute("DELETE FROM mail_templates WHERE id = ?", (template["id"],))
        # Bu sablonu varsayilan yapan gruplar bagsiz kalir, silinmez.
        conn.execute(
            "UPDATE groups SET mail_template_id = NULL WHERE mail_template_id = ?",
            (template["id"],),
        )
    return {"ok": True}


def reorder_templates(conn: sqlite3.Connection, ids: Sequence[Any]) -> list[dict[str, Any]]:
    """Verilen sira yazilir; listede gecmeyen sablonlar sonda kalir."""
    known = {template["id"] for template in list_templates(conn)}
    ordered: list[int] = []
    for value in ids or []:
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise RepositoryError("invalid_order", "Sıra listesi sayı olmalı.") from exc
        if number in known and number not in ordered:
            ordered.append(number)
    with conn:
        for position, template_id in enumerate(ordered):
            conn.execute(
                "UPDATE mail_templates SET position = ? WHERE id = ?", (position, template_id)
            )
        for position, template_id in enumerate(sorted(known - set(ordered)), start=len(ordered)):
            conn.execute(
                "UPDATE mail_templates SET position = ? WHERE id = ?", (position, template_id)
            )
    return list_templates(conn)


# --- grubun varsayilan sablonu ------------------------------------------


def group_template_id(conn: sqlite3.Connection, group_id: Any) -> int | None:
    row = conn.execute(
        "SELECT mail_template_id FROM groups WHERE id = ?", (int(group_id),)
    ).fetchone()
    if row is None or row["mail_template_id"] is None:
        return None
    return int(row["mail_template_id"])


def set_group_template(
    conn: sqlite3.Connection, group_id: Any, template_id: Any
) -> dict[str, Any]:
    """`None` bagi kaldirir; bilinmeyen sablon 404 verir."""
    wanted: int | None = None
    if template_id is not None and str(template_id).strip() != "":
        wanted = int(require_template(conn, template_id)["id"])
    with conn:
        conn.execute(
            "UPDATE groups SET mail_template_id = ? WHERE id = ?", (wanted, int(group_id))
        )
    return {"ok": True, "group_id": int(group_id), "template_id": wanted}


def template_for_group(
    conn: sqlite3.Connection, group_id: Any, template_id: Any = None
) -> dict[str, Any] | None:
    """Istenen sablon, yoksa grubun varsayilani, o da yoksa listenin ilki."""
    if template_id is not None and str(template_id).strip() != "":
        return require_template(conn, template_id)
    bound = group_template_id(conn, group_id)
    if bound is not None:
        found = get_template(conn, bound)
        if found is not None:
            return found
    templates = list_templates(conn)
    return templates[0] if templates else None


# --- gonderim kayitlari -------------------------------------------------


def record_send(
    conn: sqlite3.Connection,
    group_id: Any,
    template_id: Any,
    to_text: str,
    cc_text: str,
    subject: str,
    issue_count: int,
    file_name: str,
    mode: str = MODE_DISPLAY,
) -> dict[str, Any]:
    stamp = now_iso()
    chosen = clean_mode(mode)
    with conn:
        cursor = conn.execute(
            "INSERT INTO mail_sends "
            "(group_id, template_id, to_text, cc_text, subject, issue_count, file_name, "
            " mode, sent_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                int(group_id),
                int(template_id) if template_id is not None else None,
                str(to_text or ""),
                str(cc_text or ""),
                str(subject or ""),
                int(issue_count or 0),
                str(file_name or ""),
                chosen,
                stamp,
            ),
        )
    return _send_dict_from(int(cursor.lastrowid), group_id, template_id, to_text, cc_text,  # type: ignore[arg-type]
                           subject, issue_count, file_name, chosen, stamp)


def _send_dict_from(
    send_id: int,
    group_id: Any,
    template_id: Any,
    to_text: str,
    cc_text: str,
    subject: str,
    issue_count: int,
    file_name: str,
    mode: str,
    stamp: str,
) -> dict[str, Any]:
    return {
        "id": send_id,
        "group_id": int(group_id),
        "template_id": int(template_id) if template_id is not None else None,
        "to_text": str(to_text or ""),
        "cc_text": str(cc_text or ""),
        "subject": str(subject or ""),
        "issue_count": int(issue_count or 0),
        "file_name": str(file_name or ""),
        "mode": mode if mode in MODES else MODE_DISPLAY,
        "sent_at": stamp,
        "to_count": len([part for part in str(to_text or "").split(";") if part.strip()]),
    }


def _send_dict(row: sqlite3.Row) -> dict[str, Any]:
    return _send_dict_from(
        row["id"],
        row["group_id"],
        row["template_id"],
        row["to_text"] or "",
        row["cc_text"] or "",
        row["subject"] or "",
        row["issue_count"] or 0,
        row["file_name"] or "",
        row["mode"] or MODE_DISPLAY,
        row["sent_at"],
    )


def list_sends(
    conn: sqlite3.Connection, group_id: Any, limit: int = SENDS_LIMIT
) -> list[dict[str, Any]]:
    """Gonderilenler: yeniden eskiye, en fazla `limit` satir."""
    rows = conn.execute(
        "SELECT * FROM mail_sends WHERE group_id = ? ORDER BY id DESC LIMIT ?",
        (int(group_id), max(1, min(int(limit or SENDS_LIMIT), 200))),
    ).fetchall()
    return [_send_dict(row) for row in rows]
