"""Posta -> gorev is mantigi. COM yok, saf Python.

Iki soru burada cevaplanir:

1. **Bu posta beni ilgilendiriyor mu?** Uc ayri liste vardir -- Kimden, Kime,
   CC -- ve her biri yalnizca kendi alanina bakar: gonderen `from` listesinde
   VEYA alicilardan biri `to` listesinde VEYA CC'dekilerden biri `cc`
   listesinde. `*@example.com` jokeri desteklenir. Ucu de bossa hicbir posta
   eslesmez.
2. **Bu konusmadan daha once gorev uretildi mi?** Otorite `mail_conversations`
   tablosudur: satir varsa bir daha gorev uretilmez. Gorev silinmis olsa bile
   satir durur, yoksa silinen gorev bir sonraki taramada geri gelirdi.

Ayni konusmadan tek gorev cikar; sonraki mesajlar yalnizca kaydedilir ve acik
gorevin sayacini gunceller.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable

from .. import fields as field_utils, repository
from .source import (
    BODY_LIMIT,
    MailError,
    MailMessage,
    MailSource,
    address_matches,
    as_aware,
    clean_body,
    is_mail_class,
    iso_text,
    parse_addresses,
    stamp_text,
    utc_now,
)

DEFAULT_DAYS = 30
DEFAULT_FOLDERS: tuple[str, ...] = ("Gelen Kutusu",)
NO_SUBJECT = "(konusuz)"


@dataclass(frozen=True)
class MailConfig:
    """Ayarlardan tureyen tarama yapilandirmasi.

    Uc adres listesi birbirinden bagimsizdir: `from_addresses` yalnizca
    gonderene, `to_addresses` yalnizca alicilara, `cc_addresses` yalnizca
    CC'ye bakar.
    """

    enabled: bool = False
    from_addresses: tuple[str, ...] = ()
    to_addresses: tuple[str, ...] = ()
    cc_addresses: tuple[str, ...] = ()
    folders: tuple[str, ...] = DEFAULT_FOLDERS
    days: int = DEFAULT_DAYS
    body_limit: int = BODY_LIMIT
    scan_on_refresh: bool = True

    @property
    def has_addresses(self) -> bool:
        return bool(self.from_addresses or self.to_addresses or self.cc_addresses)

    @property
    def ready(self) -> bool:
        """Tarama anlamli mi? Uc liste de bossa hicbir sey eslesmez."""
        return self.enabled and self.has_addresses

    def since(self, now: datetime | None = None) -> datetime:
        return (now or utc_now()) - timedelta(days=max(1, int(self.days or DEFAULT_DAYS)))


@dataclass
class ScanSummary:
    """Tarama sonucu; arayuz balonu ve Guncelle ozeti bunu okur."""

    created: int = 0
    appended: int = 0
    skipped_calendar: int = 0
    skipped_seen: int = 0
    scanned: int = 0
    errors: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "created": self.created,
            "appended": self.appended,
            "skipped_calendar": self.skipped_calendar,
            "skipped_seen": self.skipped_seen,
            "scanned": self.scanned,
            "errors": [dict(item) for item in self.errors],
        }


def load_config(settings: Any) -> MailConfig:
    """`settings` tablosundan yapilandirma; bozuk deger varsayilana duser."""
    enabled = str(settings.get("mail.enabled", "0") or "0") == "1"
    folders = tuple(parse_folders(settings.get("mail.folders", "")))
    return MailConfig(
        enabled=enabled,
        from_addresses=tuple(parse_addresses(settings.get("mail.from_addresses", ""))),
        to_addresses=tuple(parse_addresses(settings.get("mail.to_addresses", ""))),
        cc_addresses=tuple(parse_addresses(settings.get("mail.cc_addresses", ""))),
        folders=folders or DEFAULT_FOLDERS,
        days=_as_int(settings.get("mail.days", DEFAULT_DAYS), DEFAULT_DAYS, low=1, high=365),
        body_limit=_as_int(
            settings.get("mail.body_limit", BODY_LIMIT), BODY_LIMIT, low=200, high=100000
        ),
        scan_on_refresh=str(settings.get("mail.scan_on_refresh", "1") or "1") == "1",
    )


def parse_folders(value: Any) -> list[str]:
    """JSON liste ya da satir/virgul ayrilmis metin -> klasor yollari."""
    if isinstance(value, (list, tuple)):
        raw: Iterable[Any] = value
    else:
        text = str(value or "").strip()
        if not text:
            return []
        if text.startswith("["):
            import json

            try:
                parsed = json.loads(text)
            except ValueError:
                parsed = []
            raw = parsed if isinstance(parsed, list) else []
        else:
            raw = text.replace(";", ",").split(",")
    seen: set[str] = set()
    result: list[str] = []
    for item in raw:
        name = str(item or "").strip().strip("\\")
        marker = name.casefold()
        if name and marker not in seen:
            seen.add(marker)
            result.append(name)
    return result


def _as_int(value: Any, fallback: int, low: int, high: int) -> int:
    try:
        number = int(str(value).strip())
    except (TypeError, ValueError):
        return fallback
    return max(low, min(number, high))


# --- eslestirme ----------------------------------------------------------


def message_matches(message: MailMessage, config: MailConfig) -> bool:
    """Uc yoldan biri tutuyorsa posta bizi ilgilendirir.

    Yollar ayridir: `to` listesindeki bir adres yalnizca Kime alaninda,
    `cc` listesindeki yalnizca CC alaninda aranir. Boylece "bana gelenler"
    ile "benim gonderdiklerim" ayri ayri secilebilir.
    """
    if address_matches(message.sender_smtp, config.from_addresses):
        return True
    if any(address_matches(person, config.to_addresses) for person in message.to_smtp):
        return True
    return any(address_matches(person, config.cc_addresses) for person in message.cc_smtp)


def collect(
    source: MailSource,
    config: MailConfig,
    now: datetime | None = None,
    summary: ScanSummary | None = None,
) -> list[MailMessage]:
    """Klasorleri dolasir, eslesenleri eskiden yeniye siralar.

    Bir klasorun patlamasi digerlerini durdurmaz; hata ozetin `errors`
    listesine duser.
    """
    report = summary if summary is not None else ScanSummary()
    since = config.since(now)
    picked: dict[str, MailMessage] = {}

    for folder in config.folders:
        try:
            batch = list(source.messages(folder, since))
        except MailError as exc:
            report.errors.append({"folder": folder, "code": exc.code, "message": exc.message})
            continue
        except Exception as exc:  # pragma: no cover - beklenmedik COM hatasi
            report.errors.append(
                {"folder": folder, "code": "folder_failed", "message": str(exc) or "bilinmeyen hata"}
            )
            continue

        for message in batch:
            report.scanned += 1
            if not is_mail_class(message.message_class):
                report.skipped_calendar += 1
                continue
            moment = as_aware(message.received_at)
            if moment is None or moment < since:
                continue
            if not message_matches(message, config):
                continue
            key = message.message_id or message.entry_id
            if not key or key in picked:
                continue
            picked[key] = message

    return sorted(picked.values(), key=lambda item: (as_aware(item.received_at), item.message_id))


# --- gorev uretimi -------------------------------------------------------


def task_title(subject: Any) -> str:
    """Konu oldugu gibi (yalnizca `strip`), gorev adi sinirina kirpilir."""
    text = str(subject or "").strip()
    if not text:
        return NO_SUBJECT
    return field_utils.truncate(text, repository.TASK_TITLE_LIMIT)


def task_note(message: MailMessage) -> str:
    """Kartin altindaki kaynak satiri."""
    sender = message.sender_smtp or "bilinmiyor"
    moment = stamp_text(as_aware(message.received_at))
    return f"Kimden: {sender} · Alındı: {moment}" if moment else f"Kimden: {sender}"


def ingest_message(conn: Any, message: MailMessage, config: MailConfig) -> str:
    """Tek mesaji isler; 'created', 'appended' ya da 'seen' dondurur."""
    message_id = message.message_id or message.entry_id
    if repository.mail_message_seen(conn, message_id):
        return "seen"

    conversation_id = message.conversation()
    stamp = iso_text(message.received_at)
    known = repository.get_mail_conversation(conn, conversation_id)

    if known is None:
        task = repository.create_mail_task(
            conn,
            title=task_title(message.subject),
            description=clean_body(message.body_text, config.body_limit),
            note=task_note(message),
            conversation_id=conversation_id,
            sender=message.sender_smtp,
            received_at=stamp,
            entry_id=message.entry_id,
            store_id=message.store_id,
        )
        repository.upsert_mail_conversation(
            conn, conversation_id, task_id=task["id"], state=repository.MAIL_ACTIVE, seen_at=stamp
        )
        repository.record_mail_message(conn, message, task_id=task["id"])
        return "created"

    repository.touch_mail_conversation(conn, conversation_id, seen_at=stamp)
    task_id = known["task_id"]
    task = repository.get_task(conn, task_id) if task_id else None
    # Gorev silinmis ya da bitmisse mesaj yalnizca kaydedilir; sayac bozulmaz.
    if task is not None and task["status"] != repository.TASK_DONE:
        repository.bump_mail_task(conn, task["id"], received_at=stamp)
        repository.record_mail_message(conn, message, task_id=task["id"])
    else:
        repository.record_mail_message(conn, message, task_id=None)
    return "appended"


def scan(
    conn: Any,
    source: MailSource,
    config: MailConfig,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Tam tarama: klasorleri dolas, eslesenleri sirayla isle, ozet dondur."""
    summary = ScanSummary()
    if not config.ready:
        return summary.to_dict()

    for message in collect(source, config, now=now, summary=summary):
        outcome = ingest_message(conn, message, config)
        if outcome == "created":
            summary.created += 1
        elif outcome == "appended":
            summary.appended += 1
        else:
            summary.skipped_seen += 1
    return summary.to_dict()
