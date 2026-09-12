"""Toplanti sohbetindeki katilim mesajlarinin cozumu (saf XML/metin isi).

`call-history` yalnizca **baslattigin ya da sana gelen** aramalari tutuyor:
takvimden katildigin planli toplantilar orada HIC gecmiyor. Gercek katilim
kaydi toplanti sohbetinin kendisinde duruyor -- Teams her toplanti bitiminde
sohbete bir sistem mesaji yaziyor:

    <partlist alt="" type="ended" callId="...">
      <part identity="8:orgid:..."><name>...</name><duration>1863</duration></part>
      ...
    </partlist>

`type="started"` mesaji yalnizca "basladi" der; sureler `ended` mesajindadir.
`duration` kisi basina **saniyedir**: "kabul edip gitmedigim toplantilar"
sorunu da burada cozulur, cunku katilmayan kisi listede hic yoktur.

Burada ne IndexedDB var ne Windows: girdi ham mesaj sozlugu, cikti
`MeetingAttendance`. `<name>` degeri **saklanmaz**; adlar `profiles`
store'undan cozulur.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable
from xml.etree import ElementTree

from .source import as_int, clean_text, parse_utc

# Toplanti sohbetlerinin kimlik oneki (grup sohbetleri `19:<32 hex>@thread.v2`).
MEETING_THREAD_PREFIX = "19:meeting_"

# `messageType` alani: klasik istemcide "Event/Call", yeni istemcide baska
# yazimlar da gorulebiliyor; ikisini de yakalamak icin gevsek bakilir.
CALL_EVENT_HINTS: tuple[str, ...] = ("event/call", "call")

# Yalnizca biten toplanti sureleri tasir.
PARTLIST_ENDED = "ended"
PARTLIST_STARTED = "started"

# Bozuk XML icin yedek yol.
_PARTLIST_TAG = re.compile(r"<partlist\b[^>]*>", re.IGNORECASE)
_ATTR_TYPE = re.compile(r'\btype\s*=\s*"([^"]*)"', re.IGNORECASE)
_ATTR_CALL_ID = re.compile(r'\bcallid\s*=\s*"([^"]*)"', re.IGNORECASE)
_PART_BLOCK = re.compile(
    r'<part\b[^>]*\bidentity\s*=\s*"([^"]*)"[^>]*>(.*?)</part\s*>', re.IGNORECASE | re.DOTALL
)
_DURATION = re.compile(r"<duration\b[^>]*>\s*(\d+)\s*</duration\s*>", re.IGNORECASE)


@dataclass
class MeetingPart:
    """Toplantida bir kisi ve kac saniye kaldigi."""

    mri: str
    seconds: int = 0


@dataclass
class MeetingAttendance:
    """Bir toplantinin bitis kaydi: kim, ne kadar kaldi."""

    thread_id: str = ""
    call_id: str = ""
    ended_at: Any = ""
    parts: list[MeetingPart] = field(default_factory=list)

    def part_for(self, mri: Any) -> MeetingPart | None:
        marker = clean_text(mri)
        if not marker:
            return None
        return next((part for part in self.parts if part.mri == marker), None)

    @property
    def longest(self) -> int:
        return max((part.seconds for part in self.parts), default=0)


# --- thread suzgeci ------------------------------------------------------


def is_meeting_thread(thread_id: Any) -> bool:
    """`19:meeting_...@thread.v2` -> planli toplanti sohbeti."""
    return clean_text(thread_id).casefold().startswith(MEETING_THREAD_PREFIX)


def is_call_event(message_type: Any) -> bool:
    """Mesaj bir arama/toplanti sistem mesaji mi?"""
    marker = clean_text(message_type).casefold()
    return any(hint in marker for hint in CALL_EVENT_HINTS)


def has_partlist(content: Any) -> bool:
    return "<partlist" in clean_text(content).casefold()


# --- XML cozumu ----------------------------------------------------------


def parse_partlist(content: Any) -> tuple[str, str, list[MeetingPart]]:
    """`<partlist>` -> (tur, callId, katilimcilar).

    Once gercek XML denenir; kirik icerik (kacirilmamis `&`, yarim etiket)
    icin duzenli ifade yedegi devreye girer. `<name>` degeri hicbir yolda
    okunmaz: ad `profiles` store'undan cozulur.
    """
    text = clean_text(content)
    if not has_partlist(text):
        return "", "", []

    element = _parse_xml(text)
    if element is not None:
        parts = [
            MeetingPart(
                mri=clean_text(node.get("identity")),
                seconds=max(0, as_int(node.findtext("duration")) or 0),
            )
            for node in element.iter("part")
            if clean_text(node.get("identity"))
        ]
        return (
            clean_text(element.get("type")).casefold(),
            clean_text(element.get("callId") or element.get("callid")),
            parts,
        )
    return _parse_with_regex(text)


def _parse_xml(text: str) -> ElementTree.Element | None:
    """Once oldugu gibi, sonra bir kok icine sarilarak cozmeyi dener."""
    for candidate in (text, f"<holocron>{text}</holocron>"):
        try:
            root = ElementTree.fromstring(candidate)
        except ElementTree.ParseError:
            continue
        if root.tag.casefold() == "partlist":
            return root
        found = root.find(".//partlist")
        if found is not None:
            return found
    return None


def _parse_with_regex(text: str) -> tuple[str, str, list[MeetingPart]]:
    """Bozuk XML yedegi: etiketleri metin olarak okur."""
    head = _PARTLIST_TAG.search(text)
    if head is None:
        return "", "", []
    opening = head.group(0)
    kind = (_ATTR_TYPE.search(opening).group(1) if _ATTR_TYPE.search(opening) else "").casefold()
    call_id = _ATTR_CALL_ID.search(opening).group(1) if _ATTR_CALL_ID.search(opening) else ""
    parts: list[MeetingPart] = []
    for identity, body in _PART_BLOCK.findall(text):
        marker = clean_text(identity)
        if not marker:
            continue
        found = _DURATION.search(body)
        parts.append(MeetingPart(mri=marker, seconds=int(found.group(1)) if found else 0))
    return clean_text(kind), clean_text(call_id), parts


# --- mesaj -> katilim kaydi ----------------------------------------------


def attendance_of(message: Any, thread_id: Any = "") -> MeetingAttendance | None:
    """Tek mesaj -> katilim kaydi. Yalnizca `type="ended"` isler.

    `started` mesaji sure tasimaz; islenirse her toplanti iki kez sayilirdi.
    """
    if not isinstance(message, dict):
        return None
    content = message.get("content")
    if not (is_call_event(message.get("messageType")) or has_partlist(content)):
        return None

    kind, call_id, parts = parse_partlist(content)
    if kind != PARTLIST_ENDED or not parts:
        return None

    ended = (
        message.get("originalArrivalTime")
        or message.get("clientArrivalTime")
        or message.get("composetime")
        or ""
    )
    return MeetingAttendance(
        thread_id=clean_text(thread_id) or clean_text(message.get("conversationId")),
        call_id=clean_text(call_id) or clean_text(message.get("callId")),
        ended_at=ended,
        parts=parts,
    )


def attendance_from_record(value: Any) -> list[MeetingAttendance]:
    """`replychains` kaydi -> icindeki butun katilim kayitlari.

    Kayit `{conversationId, messageMap: {"<mri>_<id>": {...}}}` bicimindedir.
    Toplanti sohbeti olmayan kayitlar mesaj haritasi hic acilmadan elenir.
    """
    if not isinstance(value, dict):
        return []
    thread_id = clean_text(value.get("conversationId") or value.get("id"))
    if not is_meeting_thread(thread_id):
        return []
    messages = value.get("messageMap")
    if not isinstance(messages, dict):
        return []

    found: list[MeetingAttendance] = []
    for message in messages.values():
        record = attendance_of(message, thread_id)
        if record is not None:
            found.append(record)
    return found


def within(records: Iterable[MeetingAttendance], since: Any) -> list[MeetingAttendance]:
    """Pencere disindaki katilim kayitlarini eler."""
    limit = parse_utc(since)
    if limit is None:
        return list(records)
    picked: list[MeetingAttendance] = []
    for record in records:
        moment = parse_utc(record.ended_at)
        if moment is None or moment >= limit:
            picked.append(record)
    return picked
