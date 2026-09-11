"""Kisisel kanban ("Gorevlerim") panosunun kurulmasi.

Burada SQL yok: veri `repository` uzerinden okunur. Ekran ucu
(`/api/tasks`) ve Excel disa aktarimi ayni satirlari kullanir, boylece
dosya ekranda gorunenin birebir karsiligidir.

Gunun tarihi disaridan verilebilir; "gecikmis / bugun / yakin" hesabi
makinenin saatine bagli kalmadan sinanabilsin diye.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from . import fields as field_utils, repository

# "Yakin" esigi: son tarihe kac gun kaldiginda karti uyarir.
SOON_DAYS = 3
# Biten kartlar panoyu doldurmasin: bu kadar gun once bitenler gizlenir.
OLD_DONE_DAYS = 30

DUE_OVERDUE = "overdue"
DUE_TODAY = "today"
DUE_SOON = "soon"
DUE_LATER = "later"
DUE_NONE = "none"

STATUS_CATEGORIES: tuple[str, ...] = ("new", "indeterminate", "done")


@dataclass
class Board:
    """Panonun bir istekteki hali."""

    columns: list[dict[str, Any]]
    tasks: list[dict[str, Any]]
    old_done_count: int
    summary: dict[str, int]
    base_url: str


def today_of(now: datetime | date | None = None) -> date:
    """Gunun tarihi; testler kendi gununu verebilir."""
    if isinstance(now, datetime):
        return now.date()
    if isinstance(now, date):
        return now
    return datetime.now().date()


def due_state(due_date: Any, now: datetime | date | None = None) -> str:
    """Son tarihin bugune gore durumu."""
    text = str(due_date or "").strip()
    if not text:
        return DUE_NONE
    moment = field_utils.parse_moment(text)
    if moment is None:
        return DUE_NONE
    days = (moment.date() - today_of(now)).days
    if days < 0:
        return DUE_OVERDUE
    if days == 0:
        return DUE_TODAY
    if days <= SOON_DAYS:
        return DUE_SOON
    return DUE_LATER


def is_old_done(task: dict[str, Any], now: datetime | date | None = None) -> bool:
    """Biten ve uzerinden OLD_DONE_DAYS gun gecen gorev."""
    if task["status"] != repository.TASK_DONE:
        return False
    moment = field_utils.parse_moment(task.get("done_at"))
    if moment is None:
        return False
    return (today_of(now) - moment.date()).days > OLD_DONE_DAYS


def issue_view(key: str, record: dict[str, Any] | None, base_url: str = "") -> dict[str, Any]:
    """Karta ilistirilen Jira ozeti; kayit henuz cekilmemisse fetched=False."""
    raw = (record or {}).get("raw") or {}
    values = raw.get("fields") or {}
    status = values.get("status") if isinstance(values.get("status"), dict) else {}
    category = (status or {}).get("statusCategory") or {}
    category_key = str(category.get("key") or "")
    return {
        "key": key,
        "summary": field_utils.plain_text(values.get("summary")),
        "status_text": field_utils.format_field_value({"type": "status"}, status or None),
        "status_category": category_key if category_key in STATUS_CATEGORIES else "",
        "fetched": record is not None,
        "url": f"{base_url}/browse/{key}" if base_url else "",
    }


def matches(task: dict[str, Any], needle: str) -> bool:
    """Arama: ad, aciklama, not, anahtar ve bagli kaydin ozeti."""
    if not needle:
        return True
    issue = task.get("issue") or {}
    haystack = " ".join(
        str(part or "")
        for part in (
            task["title"],
            task["description"],
            task["note"],
            task["issue_key"],
            issue.get("summary"),
            issue.get("status_text"),
        )
    )
    return needle in field_utils.fold(haystack)


def build_board(
    context: Any,
    q: str = "",
    include_old_done: bool = False,
    now: datetime | date | None = None,
    status: str | None = None,
) -> Board:
    """Gorevleri uc sutun halinde kurar; suzgec ve eski-done gizlemesi uygulanmis."""
    conn = context.connection()
    base_url = (context.settings.get("jira.base_url", "") or "").rstrip("/")

    raw_tasks = repository.list_tasks(conn)
    keys = [task["issue_key"] for task in raw_tasks if task["issue_key"]]
    stored = repository.get_issues(conn, keys) if keys else {}

    needle = field_utils.fold(q.strip()) if q else ""
    old_done_count = 0
    cards: list[dict[str, Any]] = []
    for task in raw_tasks:
        card = dict(task)
        card["due_state"] = due_state(task["due_date"], now)
        card["issue"] = (
            issue_view(task["issue_key"], stored.get(task["issue_key"]), base_url)
            if task["issue_key"]
            else None
        )
        if not matches(card, needle):
            continue
        if is_old_done(card, now):
            old_done_count += 1
            if not include_old_done:
                continue
        cards.append(card)

    if status is not None and status != "all":
        wanted = repository.clean_task_status(status)
        cards = [card for card in cards if card["status"] == wanted]

    columns = [
        {
            "status": name,
            "label": repository.TASK_STATUS_LABELS[name],
            "tasks": [card for card in cards if card["status"] == name],
        }
        for name in repository.TASK_STATUSES
    ]
    for column in columns:
        column["count"] = len(column["tasks"])

    return Board(
        columns=columns,
        tasks=cards,
        old_done_count=old_done_count,
        summary=summary_counts(conn, now),
        base_url=base_url,
    )


def summary_counts(conn: Any, now: datetime | date | None = None) -> dict[str, int]:
    """Kenar cubugu rozeti: acik gorev ve gecikmis gorev sayisi."""
    open_count = 0
    overdue = 0
    for task in repository.list_tasks(conn):
        if task["status"] == repository.TASK_DONE:
            continue
        open_count += 1
        if due_state(task["due_date"], now) == DUE_OVERDUE:
            overdue += 1
    return {"open": open_count, "overdue": overdue}
