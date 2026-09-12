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
_ELEMENT_EVENT_TYPE = re.compile(
    r"<calleventtype\b[^>]*>\s*([^<]*)\s*</calleventtype\s*>", re.IGNORECASE
)
_ELEMENT_ENDED = re.compile(r"<ended\b", re.IGNORECASE)
_ELEMENT_ICAL = re.compile(r"<icaluid\b[^>]*>\s*([^<]*)\s*</icaluid\s*>", re.IGNORECASE)


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
    # `meetingdetails` altindan: takvim eslemesinin en kesin yolu.
    ical_uid: str = ""
    start_time: Any = ""
    end_time: Any = ""
    meeting_type: str = ""

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
#
# Iki bicim var. Eskisi turu OZNITELIKTE tasiyor:
#
#     <partlist alt="" type="ended" callId="..."><part identity="..."> ...
#
# Gercek onbellekte gorulen yenisi ise oznitelik yerine ELEMAN kullaniyor:
#
#     <partlist alt="..."><calleventtype>ended</calleventtype>
#       <callid>...</callid><ended>...</ended>
#       <meetingdetails><icaluid>...</icaluid><starttime>...</starttime>...</meetingdetails>
#       <part identity="..."><name>...</name><duration>1863</duration></part>
#
# Ikisi de okunur. `name` / `displayname` / `organizerupn` degerleri hicbir
# yolda saklanmaz: adlar `profiles` store'undan cozulur.

ENDED_MARKERS: tuple[str, ...] = ("ended", "endedcall", "callended")
STARTED_MARKERS: tuple[str, ...] = ("started", "startedcall", "callstarted")


@dataclass
class Partlist:
    """`<partlist>` blokunun cozumlenmis hali."""

    kind: str = ""
    call_id: str = ""
    parts: list[MeetingPart] = field(default_factory=list)
    ical_uid: str = ""
    start_time: str = ""
    end_time: str = ""
    meeting_type: str = ""
    ended_at: str = ""

    @property
    def has_duration(self) -> bool:
        return any(part.seconds > 0 for part in self.parts)


def _tag(element: Any) -> str:
    return str(getattr(element, "tag", "")).rsplit("}", 1)[-1].casefold()


def _child(element: Any, name: str) -> Any:
    """Etiket adiyla ilk cocuk (buyuk/kucuk harf ve ad alani gozetilmez)."""
    for node in element.iter():
        if node is not element and _tag(node) == name:
            return node
    return None


def _child_text(element: Any, name: str) -> str:
    node = _child(element, name)
    return clean_text(node.text) if node is not None else ""


def _attr(element: Any, name: str) -> str:
    for key, value in (getattr(element, "attrib", {}) or {}).items():
        if str(key).casefold() == name:
            return clean_text(value)
    return ""


def _event_kind(element: Any, attribute_type: str) -> str:
    """Bitis mi baslangic mi? Sirayla `calleventtype`, oznitelik, eleman.

    `calleventtype` varsa o soyler; yoksa eski `type` ozniteligi; o da yoksa
    `<ended>` / `<started>` elemaninin varligina bakilir.
    """
    marker = _child_text(element, "calleventtype").casefold()
    if marker:
        if any(hint in marker for hint in ENDED_MARKERS):
            return PARTLIST_ENDED
        if any(hint in marker for hint in STARTED_MARKERS):
            return PARTLIST_STARTED
    if attribute_type:
        return attribute_type
    if _child(element, "started") is not None and _child(element, "ended") is None:
        return PARTLIST_STARTED
    if _child(element, "ended") is not None:
        return PARTLIST_ENDED
    return ""


def _parts_of(element: Any) -> list[MeetingPart]:
    """`<part>` elemanlari: kimlik oznitelikte ya da alt elemanda."""
    parts: list[MeetingPart] = []
    for node in element.iter():
        if _tag(node) != "part":
            continue
        identity = _attr(node, "identity") or _child_text(node, "identity")
        if not identity:
            continue
        parts.append(
            MeetingPart(mri=identity, seconds=max(0, as_int(_child_text(node, "duration")) or 0))
        )
    return parts


def _details_of(element: Any) -> tuple[str, str, str, str]:
    """`<meetingdetails>`: (icaluid, starttime, endtime, meetingtype)."""
    details = _child(element, "meetingdetails")
    if details is None:
        details = element
    return (
        _child_text(details, "icaluid"),
        _child_text(details, "starttime"),
        _child_text(details, "endtime"),
        _child_text(details, "meetingtype"),
    )


def parse_partlist(content: Any) -> Partlist:
    """`<partlist>` -> cozumlenmis blok (iki bicim de okunur)."""
    text = clean_text(content)
    if not has_partlist(text):
        return Partlist()

    element = _parse_xml(text)
    if element is None:
        return _parse_with_regex(text)

    ical_uid, start_time, end_time, meeting_type = _details_of(element)
    return Partlist(
        kind=_event_kind(element, _attr(element, "type").casefold()),
        call_id=_attr(element, "callid") or _child_text(element, "callid"),
        parts=_parts_of(element),
        ical_uid=ical_uid,
        start_time=start_time,
        end_time=end_time,
        meeting_type=meeting_type,
        ended_at=_child_text(element, "ended"),
    )


def _parse_xml(text: str) -> ElementTree.Element | None:
    """Once oldugu gibi, sonra bir kok icine sarilarak cozmeyi dener."""
    for candidate in (text, f"<holocron>{text}</holocron>"):
        try:
            root = ElementTree.fromstring(candidate)
        except ElementTree.ParseError:
            continue
        if _tag(root) == "partlist":
            return root
        for node in root.iter():
            if _tag(node) == "partlist":
                return node
    return None


def _parse_with_regex(text: str) -> Partlist:
    """Bozuk XML yedegi: etiketleri metin olarak okur."""
    head = _PARTLIST_TAG.search(text)
    if head is None:
        return Partlist()
    opening = head.group(0)
    found_type = _ATTR_TYPE.search(opening)
    found_call = _ATTR_CALL_ID.search(opening)
    kind = clean_text(found_type.group(1)).casefold() if found_type else ""
    if not kind:
        marker = _ELEMENT_EVENT_TYPE.search(text)
        if marker:
            kind = clean_text(marker.group(1)).casefold()
        elif _ELEMENT_ENDED.search(text):
            kind = PARTLIST_ENDED

    parts: list[MeetingPart] = []
    for identity, body in _PART_BLOCK.findall(text):
        marker = clean_text(identity)
        if not marker:
            continue
        found = _DURATION.search(body)
        parts.append(MeetingPart(mri=marker, seconds=int(found.group(1)) if found else 0))

    ical = _ELEMENT_ICAL.search(text)
    return Partlist(
        kind=kind,
        call_id=clean_text(found_call.group(1)) if found_call else "",
        parts=parts,
        ical_uid=clean_text(ical.group(1)) if ical else "",
    )


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

    block = parse_partlist(content)
    if not block.parts:
        return None
    if block.kind == PARTLIST_STARTED:
        return None
    if block.kind != PARTLIST_ENDED and not block.has_duration:
        # Turu yazmayan bloklarda sure varsa toplanti bitmistir; yoksa bu
        # yalnizca "basladi" mesajidir ve katilim bilgisi tasimaz.
        return None

    ended = (
        block.end_time
        or block.ended_at
        or message.get("originalArrivalTime")
        or message.get("clientArrivalTime")
        or message.get("composetime")
        or ""
    )
    return MeetingAttendance(
        thread_id=clean_text(thread_id) or clean_text(message.get("conversationId")),
        call_id=block.call_id or clean_text(message.get("callId")),
        ended_at=ended,
        parts=block.parts,
        ical_uid=block.ical_uid,
        start_time=block.start_time,
        end_time=block.end_time,
        meeting_type=block.meeting_type,
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


def sender_mri(value: Any) -> str:
    """`replychains` kaydindan kullanicinin KENDI kimligi.

    `isSentByCurrentUser` isaretli bir mesajin `creator` alani kullanicinin
    MRI'idir. (`call-history.userParticipantId` bu is icin yanlisti: o,
    arama basina katilimci kimligi.)
    """
    if not isinstance(value, dict):
        return ""
    messages = value.get("messageMap")
    if not isinstance(messages, dict):
        return ""
    for message in messages.values():
        if not isinstance(message, dict) or not message.get("isSentByCurrentUser"):
            continue
        marker = clean_text(message.get("creator") or message.get("from"))
        if marker:
            return marker
    return ""


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
