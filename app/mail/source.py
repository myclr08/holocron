"""Kaynak-bagimsiz posta modeli.

Burada COM yok: mesaj nesnesi, klasor agaci ve `MailSource` sozlesmesi
duruyor. Outlook sarmalayicisi (`outlook.py`) ve testlerin bellek ici kaynagi
(`fake.py`) ayni sozlesmeyi uygular, boylece is mantigi (`intake.py`)
Windows'a hic bakmadan sinanabilir.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from fnmatch import fnmatch
from typing import Any, Iterable, Protocol, runtime_checkable

# Kurum rehberi (Genel Adres Listesi) girisinin turu; tanim `app/teams.py`
# icinde, adres defterinin yanindadir.
from ..teams import CONTACT_KINDS, CONTACT_LIST, CONTACT_PERSON  # noqa: F401

# Yalnizca bunlarla baslayan ogeler gorev uretir.
MAIL_CLASS_PREFIX = "IPM.Note"

# Takvim daveti, gorev, teslim raporu, "ofiste degilim" sablonu: hicbiri gorev degil.
SKIPPED_CLASS_PREFIXES: tuple[str, ...] = (
    "IPM.Schedule",
    "IPM.Appointment",
    "IPM.Task",
    "REPORT.",
    "IPM.Note.Rules.OofTemplate",
    "IPM.Note.Microsoft.Scheduled",
)

# "RE:", "FW:", Turkce Outlook'un "YNT:" ve "ILT:" onekleri konu basliginda
# tekrar tekrar birikir; konusma kimligi yoksa konudan uretirken atilir.
_REPLY_PREFIX = re.compile(
    r"^\s*(?:re|fw|fwd|ynt|yan|ilt|i̇lt|iletilen|cevap|wg|aw)\s*(?:\[\d+\])?\s*:\s*",
    re.IGNORECASE,
)
_SPACES = re.compile(r"\s+")
_BLANK_LINES = re.compile(r"\n{3,}")

BODY_LIMIT = 4000


class MailError(Exception):
    """Kullaniciya gosterilecek posta hatasi (arayuz kodu okur)."""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def is_supported() -> bool:
    """Outlook COM yalnizca Windows'ta var; arayuz karti buna gore kapanir."""
    return sys.platform == "win32"


def unavailable() -> MailError:
    return MailError(
        "feature_unavailable",
        "Bu özellik yalnız Windows'ta Outlook ile çalışır.",
    )


@dataclass
class MailMessage:
    """Tek bir posta; kaynagi ne olursa olsun ayni bicimde gelir.

    `message_id` internet ileti kimligidir (PR_INTERNET_MESSAGE_ID); Exchange
    ici bazi ogelerde bos gelebildigi icin o zaman EntryID'ye duser.
    """

    message_id: str
    conversation_id: str = ""
    entry_id: str = ""
    store_id: str = ""
    subject: str = ""
    sender_smtp: str = ""
    to_smtp: list[str] = field(default_factory=list)
    cc_smtp: list[str] = field(default_factory=list)
    received_at: datetime | None = None
    body_text: str = ""
    message_class: str = MAIL_CLASS_PREFIX
    folder_path: str = ""

    @property
    def recipients(self) -> list[str]:
        return [*self.to_smtp, *self.cc_smtp]

    def conversation(self) -> str:
        """Konusma kimligi; yoksa konudan uretilir."""
        marker = (self.conversation_id or "").strip()
        if marker:
            return marker
        topic = normalize_topic(self.subject)
        return f"topic:{topic}" if topic else f"message:{self.message_id}"


@dataclass
class MailFolder:
    """Klasor agacinin bir dugumu; arayuzdeki secici bunu cizer."""

    name: str
    path: str
    count: int = 0
    children: list["MailFolder"] = field(default_factory=list)
    # Sanal dugumler (ornegin "Arama Klasörleri" basligi) taranamaz: arayuz
    # onay kutusunu kapatir, yoksa kullanici cozulemeyecek bir yol secerdi.
    selectable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": self.path,
            "count": self.count,
            "selectable": self.selectable,
            "children": [child.to_dict() for child in self.children],
        }


@dataclass
class GalEntry:
    """Kurum rehberinden tek bir giris.

    `kind` dagitim listelerini kisilerden ayirir: ikisine de yazilabilir ama
    bir listeye yazmak bambaska bir sey, arayuz rozetle soyler.
    """

    email: str
    name: str = ""
    kind: str = CONTACT_PERSON


@runtime_checkable
class MailSource(Protocol):
    """Posta kaynagi sozlesmesi."""

    def probe(self) -> dict[str, Any]:
        """Baglantiyi sinar: surum, hesap adi, klasor sayilari."""

    def folders(self) -> list[MailFolder]:
        """Klasor agaci."""

    def messages(self, folder_path: str, since: datetime) -> Iterable[MailMessage]:
        """`since`den yeni mesajlar (yeniden eskiye dolasilir)."""

    def open_message(self, entry_id: str, store_id: str = "") -> bool:
        """Mesaji Outlook'ta acar."""

    def address_book(self) -> list[GalEntry]:
        """Kurum rehberi (Genel Adres Listesi) girisleri."""


# --- saf yardimcilar -----------------------------------------------------


def is_mail_class(message_class: Any) -> bool:
    """Gorev uretebilecek oge mi? Takvim ve sistem ogeleri elenir."""
    text = str(message_class or "").strip()
    if not text:
        # Sinifi okunamayan oge gorev uretmez: takvim davetini yanlislikla
        # gorev yapmaktansa atlamak yeglenir.
        return False
    upper = text.upper()
    for prefix in SKIPPED_CLASS_PREFIXES:
        if upper.startswith(prefix.upper()):
            return False
    return upper.startswith(MAIL_CLASS_PREFIX.upper())


def normalize_topic(subject: Any) -> str:
    """Konudan konusma anahtari: onekler atilir, bosluklar tek boslugua iner."""
    text = str(subject or "")
    while True:
        stripped = _REPLY_PREFIX.sub("", text, count=1)
        if stripped == text:
            break
        text = stripped
    return _SPACES.sub(" ", text).strip().casefold()


def clean_address(value: Any) -> str:
    """Adres normalizasyonu: kose parantez ve bosluk atilir, harf kucultulur."""
    text = str(value or "").strip().strip("<>").strip()
    if text.upper().startswith("SMTP:"):
        text = text[5:].strip()
    return text.casefold()


def parse_addresses(value: Any) -> list[str]:
    """Ayarlardaki virgullu adres listesi -> temiz liste (sira korunur)."""
    if isinstance(value, (list, tuple)):
        parts: list[str] = [str(item) for item in value]
    else:
        parts = re.split(r"[,;\n]+", str(value or ""))
    seen: set[str] = set()
    result: list[str] = []
    for part in parts:
        address = clean_address(part)
        if address and address not in seen:
            seen.add(address)
            result.append(address)
    return result


def address_matches(address: Any, patterns: Iterable[str]) -> bool:
    """Tek adres listeye uyuyor mu? `*@example.com` jokeri desteklenir."""
    target = clean_address(address)
    if not target:
        return False
    for pattern in patterns:
        marker = clean_address(pattern)
        if not marker:
            continue
        if marker == target or fnmatch(target, marker):
            return True
    return False


def clean_body(text: Any, limit: int = BODY_LIMIT) -> str:
    """Duz metin govde: satir sonlari normalize, uzunsa kesilir."""
    body = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    body = "\n".join(line.rstrip() for line in body.split("\n"))
    body = _BLANK_LINES.sub("\n\n", body).strip()
    if limit and limit > 0 and len(body) > limit:
        body = body[:limit].rstrip() + "…"
    return body


def as_aware(moment: datetime | None) -> datetime | None:
    """Saat dilimsiz damgayi yerel saate baglar; karsilastirma patlamasin."""
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment.astimezone()
    return moment


def stamp_text(moment: datetime | None) -> str:
    """'GG.AA.YYYY SS:dd' -- kartin 'Alındı' satiri, yerel saatle.

    Outlook zaten yerel saat verir (cevrim etkisizdir); UTC tasiyan bir kaynak
    geldiginde ise kullanici kendi saatini gorur.
    """
    aware = as_aware(moment)
    if aware is None:
        return ""
    return aware.astimezone().strftime("%d.%m.%Y %H:%M")


def iso_text(moment: datetime | None) -> str:
    if moment is None:
        return ""
    return as_aware(moment).isoformat()  # type: ignore[union-attr]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
