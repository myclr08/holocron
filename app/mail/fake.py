"""Bellek ici posta kaynagi.

`tests/` altinda degil `app/mail/` icinde duruyor: API testleri de ayni sahte
kaynagi baglayabilsin, Windows olmayan makinede uctan uca akis sinanabilsin
diye. Uretimde kimse bunu kullanmaz, ama tasima maliyeti birkac satirdir ve
"sahte kaynak nerede" sorusunu ortadan kaldirir.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from .source import (
    CONTACT_LIST,
    GalEntry,
    MailError,
    MailFolder,
    MailMessage,
    clean_body,
)

INBOX = "Gelen Kutusu"

# Sahte kurum rehberi; testler kendi listesini verebilir.
DEFAULT_GAL: tuple[GalEntry, ...] = (
    GalEntry(email="ayse.yilmaz@example.com", name="Ayşe Yılmaz"),
    GalEntry(email="mehmet.demir@example.com", name="Mehmet Demir"),
    GalEntry(email="tedarik@example.com", name="Tedarik Ekibi", kind=CONTACT_LIST),
)


def message(
    message_id: str,
    subject: str = "Konu",
    sender: str = "kimse@example.com",
    to: Iterable[str] = (),
    cc: Iterable[str] = (),
    received_at: datetime | None = None,
    body: str = "",
    conversation_id: str = "",
    message_class: str = "IPM.Note",
    folder_path: str = INBOX,
    entry_id: str = "",
    store_id: str = "STORE",
) -> MailMessage:
    """Test verisi uretmeyi kisaltan yardimci."""
    return MailMessage(
        message_id=message_id,
        conversation_id=conversation_id,
        entry_id=entry_id or f"ENTRY-{message_id}",
        store_id=store_id,
        subject=subject,
        sender_smtp=sender,
        to_smtp=list(to),
        cc_smtp=list(cc),
        received_at=received_at or datetime.now(timezone.utc),
        body_text=body,
        message_class=message_class,
        folder_path=folder_path,
    )


class FakeMailSource:
    """`MailSource` sozlesmesinin bellek ici karsiligi."""

    def __init__(
        self,
        messages: Iterable[MailMessage] = (),
        failing_folders: Iterable[str] = (),
        account: str = "ornek@example.com",
        version: str = "16.0 (sahte)",
        address_book: Iterable[GalEntry] | None = None,
    ) -> None:
        self.items: list[MailMessage] = list(messages)
        self.failing_folders = {str(name).casefold() for name in failing_folders}
        self.account = account
        self.version = version
        self.opened: list[tuple[str, str]] = []
        self.probes = 0
        self.gal: list[GalEntry] = list(DEFAULT_GAL if address_book is None else address_book)
        self.gal_reads = 0

    # --- sozlesme -----------------------------------------------------

    def probe(self) -> dict[str, Any]:
        self.probes += 1
        return {
            "ok": True,
            "version": self.version,
            "account": self.account,
            "folders": [folder.to_dict() for folder in self.folders()],
        }

    def folders(self) -> list[MailFolder]:
        counts: dict[str, int] = {}
        for item in self.items:
            counts[item.folder_path] = counts.get(item.folder_path, 0) + 1
        names = sorted(counts) or [INBOX]
        return [MailFolder(name=name.split("\\")[-1], path=name, count=counts.get(name, 0)) for name in names]

    def messages(self, folder_path: str, since: datetime) -> list[MailMessage]:
        marker = str(folder_path or INBOX).casefold()
        if marker in self.failing_folders:
            raise MailError("folder_not_found", f"'{folder_path}' klasörü bulunamadı.")
        picked = [
            item
            for item in self.items
            if item.folder_path.casefold() == marker and _aware(item.received_at) >= since
        ]
        # Outlook yeniden eskiye dolasir; sahte kaynak da ayni sirayi verir.
        return sorted(picked, key=lambda item: _aware(item.received_at), reverse=True)

    def open_message(self, entry_id: str, store_id: str = "") -> bool:
        self.opened.append((str(entry_id or ""), str(store_id or "")))
        if not entry_id:
            raise MailError("mail_not_found", "Bu görevin e-posta bağı yok.")
        return True

    def address_book(self) -> list[GalEntry]:
        self.gal_reads += 1
        return list(self.gal)

    # --- kolaylik -----------------------------------------------------

    def add(self, item: MailMessage) -> MailMessage:
        self.items.append(item)
        return item

    def seed(self, count: int, **kwargs: Any) -> list[MailMessage]:
        base = datetime.now(timezone.utc)
        created = []
        for index in range(count):
            created.append(
                self.add(
                    message(
                        f"<seed-{index}@example.com>",
                        received_at=base - timedelta(minutes=index),
                        body=clean_body(f"Gövde {index}"),
                        **kwargs,
                    )
                )
            )
        return created


def _aware(moment: datetime | None) -> datetime:
    if moment is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
