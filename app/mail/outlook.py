"""Klasik Outlook (COM) sarmalayicisi.

`win32com` yalnizca Windows'ta ve yalnizca ihtiyac aninda ice aktarilir; bu
yuzden dosyanin kendisi her yerde okunabilir ve COM'a dokunmayan yardimcilari
(SMTP cozumleme, sinif suzgeci, oge -> mesaj cevrimi) sahte nesnelerle
sinanabilir. Sifre saklanmaz: acik Outlook oturumu kullanilir.

Bilinmesi gerekenler:

* **X500 tuzagi.** Exchange ici adresler `/O=.../CN=...` gelir. Gonderen icin
  `GetExchangeUser().PrimarySmtpAddress`, olmazsa PR_SENDER_SMTP_ADDRESS;
  alicilar icin `AddressEntry.GetExchangeUser()`, olmazsa PR_SMTP_ADDRESS,
  o da olmazsa ham `Address`.
* **`Restrict` kullanilmaz.** Tarih bicimi makinenin yerel ayarina bagli
  (`MM/DD/YYYY` vs `DD.MM.YYYY`); Turkce Windows'ta sessizce bos sonuc
  doner. Bunun yerine `Sort("[ReceivedTime]", True)` ile yeniden eskiye
  dolasilir ve pencerenin disina cikilinca durulur.
* **Is parcacigi.** Guncelle arka planda dondugu icin her oturumda
  `pythoncom.CoInitialize()` / `CoUninitialize()` cagrilir.
"""

from __future__ import annotations

import sys
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from .source import (
    BODY_LIMIT,
    MailError,
    MailFolder,
    MailMessage,
    clean_address,
    clean_body,
    is_mail_class,
    unavailable,
)

# MAPI proptag'leri: adi degil numarasi tasinir, yanlis yazilirsa COM patlar.
PR_INTERNET_MESSAGE_ID = "http://schemas.microsoft.com/mapi/proptag/0x1035001F"
PR_SENDER_SMTP_ADDRESS = "http://schemas.microsoft.com/mapi/proptag/0x5D01001F"
PR_SMTP_ADDRESS = "http://schemas.microsoft.com/mapi/proptag/0x39FE001F"

OL_FOLDER_INBOX = 6

RECIPIENT_TO = 1
RECIPIENT_CC = 2
RECIPIENT_BCC = 3

# Ayarda "Gelen Kutusu" (ya da Ingilizce kurulumda "Inbox") yazarsa varsayilan
# klasor kullanilir; boylece kullanicinin store adini bilmesi gerekmez.
DEFAULT_FOLDER_NAMES = {"", "gelen kutusu", "inbox"}

PATH_SEPARATOR = "\\"


# --- COM'a dokunmayan yardimcilar ---------------------------------------


def property_value(item: Any, tag: str) -> str:
    """`PropertyAccessor.GetProperty`; alan yoksa bos metin.

    COM tarafi eksik ozellik icin istisna atar, bu normaldir: X500 adresli
    eski ogelerde SMTP alani hic bulunmaz.
    """
    accessor = getattr(item, "PropertyAccessor", None)
    if accessor is None:
        return ""
    try:
        return str(accessor.GetProperty(tag) or "")
    except Exception:
        return ""


def exchange_smtp(entry: Any) -> str:
    """`AddressEntry` -> birincil SMTP adresi (Exchange kullanicisi ise)."""
    if entry is None:
        return ""
    getter = getattr(entry, "GetExchangeUser", None)
    if callable(getter):
        try:
            user = getter()
        except Exception:
            user = None
        if user is not None:
            address = clean_address(getattr(user, "PrimarySmtpAddress", ""))
            if address:
                return address
    return ""


def sender_address(item: Any) -> str:
    """Gonderenin SMTP adresi; X500 gelirse Exchange/proptag yoluna duser."""
    kind = str(getattr(item, "SenderEmailType", "") or "").upper()
    raw = clean_address(getattr(item, "SenderEmailAddress", ""))

    if kind == "EX" or raw.startswith("/o="):
        address = exchange_smtp(getattr(item, "Sender", None))
        if address:
            return address
        address = clean_address(property_value(item, PR_SENDER_SMTP_ADDRESS))
        if address:
            return address
    if raw and not raw.startswith("/o="):
        return raw
    # Son care: gorunen adres alani (bazen dogrudan SMTP tasir).
    return clean_address(property_value(item, PR_SENDER_SMTP_ADDRESS)) or ""


def recipient_address(recipient: Any) -> str:
    """Tek alicinin SMTP adresi; uc kademeli cozumleme."""
    address = exchange_smtp(getattr(recipient, "AddressEntry", None))
    if address:
        return address
    address = clean_address(property_value(recipient, PR_SMTP_ADDRESS))
    if address:
        return address
    raw = clean_address(getattr(recipient, "Address", ""))
    return "" if raw.startswith("/o=") else raw


def recipient_addresses(item: Any) -> tuple[list[str], list[str]]:
    """(Kime, CC). Tek bir alici patlarsa tum posta dusmez, o alici atlanir."""
    to_list: list[str] = []
    cc_list: list[str] = []
    try:
        recipients = list(getattr(item, "Recipients", []) or [])
    except Exception:
        return to_list, cc_list

    for recipient in recipients:
        try:
            kind = int(getattr(recipient, "Type", RECIPIENT_TO) or RECIPIENT_TO)
            address = recipient_address(recipient)
        except Exception:
            continue
        if not address:
            continue
        if kind == RECIPIENT_CC:
            cc_list.append(address)
        elif kind == RECIPIENT_BCC:
            continue
        else:
            to_list.append(address)
    return to_list, cc_list


def is_meeting_item(item: Any) -> bool:
    """Toplanti daveti/yaniti: `MeetingStatus` dolu olan oge takvimdir."""
    status = getattr(item, "MeetingStatus", None)
    if status is None:
        return False
    try:
        return int(status) != 0
    except (TypeError, ValueError):
        return bool(status)


def as_datetime(value: Any) -> datetime | None:
    """COM tarihini saat dilimli `datetime`a cevirir."""
    if value is None:
        return None
    # pywintypes.datetime `datetime` alt sinifidir ve saat dilimi tasir.
    if isinstance(value, datetime):
        return value if value.tzinfo else value.astimezone()
    stamp = getattr(value, "timestamp", None)
    if not callable(stamp):
        return None
    try:
        return datetime.fromtimestamp(stamp(), tz=timezone.utc)
    except Exception:
        return None


def message_from_item(
    item: Any,
    folder_path: str = "",
    store_id: str = "",
    body_limit: int = BODY_LIMIT,
) -> MailMessage | None:
    """COM ogesini kaynak-bagimsiz mesaja cevirir; takvim ogesinde None."""
    message_class = str(getattr(item, "MessageClass", "") or "")
    if not is_mail_class(message_class) or is_meeting_item(item):
        return None

    entry_id = str(getattr(item, "EntryID", "") or "")
    message_id = property_value(item, PR_INTERNET_MESSAGE_ID).strip() or entry_id
    to_list, cc_list = recipient_addresses(item)

    conversation_id = str(getattr(item, "ConversationID", "") or "").strip()
    if not conversation_id:
        conversation_id = ""  # MailMessage.conversation() konudan uretir

    return MailMessage(
        message_id=message_id,
        conversation_id=conversation_id,
        entry_id=entry_id,
        store_id=store_id,
        subject=str(getattr(item, "Subject", "") or "").strip(),
        sender_smtp=sender_address(item),
        to_smtp=to_list,
        cc_smtp=cc_list,
        received_at=as_datetime(getattr(item, "ReceivedTime", None)),
        body_text=clean_body(getattr(item, "Body", ""), body_limit),
        message_class=message_class,
        folder_path=folder_path,
    )


def split_path(folder_path: str) -> list[str]:
    return [part for part in str(folder_path or "").split(PATH_SEPARATOR) if part.strip()]


def is_default_inbox(folder_path: str) -> bool:
    return str(folder_path or "").strip().casefold() in DEFAULT_FOLDER_NAMES


def _child_folder(container: Any, name: str) -> Any:
    """Bir klasor koleksiyonunda ada gore (buyuk/kucuk harf ayirmadan) arar."""
    marker = str(name or "").strip().casefold()
    if not marker:
        return None
    try:
        children = list(container)
    except Exception:
        return None
    for child in children:
        if str(getattr(child, "Name", "") or "").strip().casefold() == marker:
            return child
    return None


# --- COM sarmalayicisi ---------------------------------------------------


class OutlookSource:
    """Calisan Outlook oturumuna baglanan `MailSource`."""

    def __init__(self, body_limit: int = BODY_LIMIT) -> None:
        if sys.platform != "win32":
            raise unavailable()
        self.body_limit = body_limit

    # --- oturum -------------------------------------------------------

    @contextmanager
    def _namespace(self) -> Iterator[Any]:
        """COM oturumu; arka plan is parcaciginda CoInitialize sarttir."""
        try:
            import pythoncom  # type: ignore
            import win32com.client  # type: ignore
        except ImportError as exc:  # pragma: no cover - Windows disi
            raise MailError(
                "pywin32_missing",
                "pywin32 bulunamadı; Outlook bağlantısı için gerekli.",
            ) from exc

        pythoncom.CoInitialize()
        try:
            try:
                application = win32com.client.Dispatch("Outlook.Application")
                namespace = application.GetNamespace("MAPI")
            except Exception as exc:
                raise MailError(
                    "outlook_unavailable",
                    "Outlook'a bağlanılamadı. Outlook açık mı?",
                ) from exc
            yield namespace
        finally:
            pythoncom.CoUninitialize()

    # --- sozlesme -----------------------------------------------------

    def probe(self) -> dict[str, Any]:
        with self._namespace() as namespace:
            application = namespace.Application
            account = ""
            try:
                accounts = list(namespace.Accounts)
                if accounts:
                    account = str(getattr(accounts[0], "DisplayName", "") or "")
            except Exception:
                account = ""
            if not account:
                try:
                    account = str(namespace.CurrentUser.Name or "")
                except Exception:
                    account = ""
            return {
                "ok": True,
                "version": str(getattr(application, "Version", "") or ""),
                "account": account,
                "folders": [folder.to_dict() for folder in self._folders(namespace)],
            }

    def folders(self) -> list[MailFolder]:
        with self._namespace() as namespace:
            return self._folders(namespace)

    def messages(self, folder_path: str, since: datetime) -> list[MailMessage]:
        with self._namespace() as namespace:
            folder = self._resolve(namespace, folder_path)
            store_id = str(getattr(getattr(folder, "Store", None), "StoreID", "") or "")
            return list(self._walk(folder, folder_path, store_id, since))

    def open_message(self, entry_id: str, store_id: str = "") -> bool:
        if not entry_id:
            raise MailError("mail_not_found", "Bu görevin e-posta bağı yok.")
        with self._namespace() as namespace:
            try:
                item = (
                    namespace.GetItemFromID(entry_id, store_id)
                    if store_id
                    else namespace.GetItemFromID(entry_id)
                )
            except Exception as exc:
                raise MailError(
                    "mail_not_found",
                    "E-posta Outlook'ta bulunamadı; taşınmış ya da silinmiş olabilir.",
                ) from exc
            item.Display()
            return True

    # --- ic yardimcilar -----------------------------------------------

    def _folders(self, namespace: Any) -> list[MailFolder]:
        tree: list[MailFolder] = []
        try:
            stores = list(namespace.Folders)
        except Exception as exc:  # pragma: no cover - COM
            raise MailError("folders_failed", "Klasör ağacı okunamadı.") from exc
        for store in stores:
            node = self._folder_node(store, "")
            if node is not None:
                tree.append(node)
        return tree

    def _folder_node(self, folder: Any, parent_path: str, depth: int = 0) -> MailFolder | None:
        """Klasor agacini kurar; cok derin agaclarda dordunculerde durur."""
        name = str(getattr(folder, "Name", "") or "")
        if not name:
            return None
        path = f"{parent_path}{PATH_SEPARATOR}{name}" if parent_path else name
        try:
            count = int(folder.Items.Count)
        except Exception:
            count = 0
        node = MailFolder(name=name, path=path, count=count)
        if depth >= 4:
            return node
        try:
            children = list(folder.Folders)
        except Exception:
            children = []
        for child in children:
            sub = self._folder_node(child, path, depth + 1)
            if sub is not None:
                node.children.append(sub)
        return node

    def _resolve(self, namespace: Any, folder_path: str) -> Any:
        """Yol -> klasor. 'Gelen Kutusu' varsayilan gelen kutusudur."""
        if is_default_inbox(folder_path):
            try:
                return namespace.GetDefaultFolder(OL_FOLDER_INBOX)
            except Exception as exc:
                raise MailError("folder_not_found", "Gelen Kutusu bulunamadı.") from exc

        parts = split_path(folder_path)
        current: Any = None
        containers: Any = namespace.Folders
        for index, part in enumerate(parts):
            current = _child_folder(containers, part)
            if current is None:
                # Ust duzey adi store degil de gelen kutusu altindaki bir klasor
                # olabilir: "Alt\\Klasor" yazan kullaniciyi bos birakmayalim.
                if index == 0:
                    inbox = namespace.GetDefaultFolder(OL_FOLDER_INBOX)
                    current = _child_folder(inbox.Folders, part)
                if current is None:
                    raise MailError(
                        "folder_not_found", f"'{folder_path}' klasörü bulunamadı."
                    )
            containers = current.Folders
        if current is None:
            raise MailError("folder_not_found", f"'{folder_path}' klasörü bulunamadı.")
        return current

    def _walk(
        self, folder: Any, folder_path: str, store_id: str, since: datetime
    ) -> Iterator[MailMessage]:
        """Yeniden eskiye dolasir, pencerenin disina cikinca durur."""
        items = folder.Items
        try:
            items.Sort("[ReceivedTime]", True)
        except Exception:
            pass  # siralanamadiysa dogal sirada dolasilir, sadece yavastir

        for item in items:
            try:
                received = as_datetime(getattr(item, "ReceivedTime", None))
            except Exception:
                continue
            if received is not None and received < since:
                # Sirali listede ilk eski ogede durmak taramayi kisa tutar.
                break
            try:
                message = message_from_item(item, folder_path, store_id, self.body_limit)
            except Exception:
                continue
            if message is not None:
                yield message
