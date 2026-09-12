"""Arama gecmisi is mantigi. IndexedDB yok, COM yok, saf Python.

Uc soru burada cevaplanir:

1. **Bu arama ne kadar surdu?** `durationInMs` her kayitta yok; yoksa
   `endTime - connectTime`, o da yoksa `endTime - startTime` kullanilir.
   Kacirilmis aramada sure sifirdir (baglanti hic kurulmadi).
2. **Bu arama ne turden?** `TwoParty` birebir gorusmedir. `MultiParty` ise
   takvimde ayni saate denk gelen bir kayit varsa **toplanti**, yoksa
   **grup aramasi** sayilir; eslesme penceresi +/- 10 dakikadir.
3. **Karsi taraf kim?** Gelen aramada arayan (`originatorParticipant`),
   giden aramada aranan (`targetParticipant`). Ad, profil store'larindan
   cozulur; cozulemezse kaydin kendi `displayName` alani, o da yoksa kimlik
   yazilir (bos satir kullaniciya hicbir sey anlatmaz).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field as dataclass_field
from datetime import date, datetime, timedelta
from typing import Any, Iterable, Sequence

from .. import fields as field_utils, repository
from .source import (
    DIRECTION_IN,
    DIRECTION_OUT,
    EVENT_RECURRING_MASTER,
    SHOW_AS_OOF,
    STATE_ACCEPTED,
    STATE_DECLINED,
    STATE_MISSED,
    TYPE_MULTI_PARTY,
    TYPE_TWO_PARTY,
    CalendarRecord,
    CallRecord,
    CallSource,
    ThreadRecord,
    as_int,
    canonical_direction,
    canonical_state,
    canonical_type,
    clean_text,
    empty_diagnostics,
    iso_text,
    jsonable,
    parse_local,
    parse_utc,
    utc_now,
)

# Arama turleri (veritabanindaki `kind` sutunu).
KIND_ONE_TO_ONE = "one_to_one"
KIND_MEETING = "meeting"
KIND_GROUP = "group_call"
KINDS: tuple[str, ...] = (KIND_ONE_TO_ONE, KIND_MEETING, KIND_GROUP)

KIND_LABELS: dict[str, str] = {
    KIND_ONE_TO_ONE: "Birebir",
    KIND_MEETING: "Toplantı",
    KIND_GROUP: "Grup araması",
}

DIRECTION_LABELS: dict[str, str] = {
    DIRECTION_OUT: "Aradım",
    DIRECTION_IN: "Arandım",
}

STATE_LABELS: dict[str, str] = {
    STATE_ACCEPTED: "Kabul",
    STATE_MISSED: "Kaçırılan",
    STATE_DECLINED: "Reddedilen",
}

# Toplanti eslemesi: aramanin baslangici takvim kaydina bu kadar yakinsa tutar.
# (Thread kimligi tutuyorsa saat hic bakilmaz; bu yalnizca YEDEK yoldur.)
MEETING_TOLERANCE_MINUTES = 10
# Bu kadar kisa bir "thread kimligi" ile alt dizge eslemesi yapilmaz.
MIN_THREAD_LENGTH = 10

DEFAULT_DAYS = 30
WINDOW_DAYS: tuple[int, ...] = (7, 30, 90)
TOP_PEOPLE = 5

# Adi cozulemeyen kisi. Ham kimlik EKRANA CIKMAZ: kullaniciya hicbir sey
# anlatmayan `8:orgid:0000...` yerine kimligin son alti karakteri yazilir,
# boylece iki bilinmeyen kisi yine birbirinden ayirt edilebilir.
UNKNOWN_PERSON = "Bilinmeyen kişi"
# Kimligin kuyrugundan kac karakter gosterilir.
ID_TAIL = 6

# Karsi tarafi olmayan aramalarin basligi.
GROUP_TITLE = "Grup araması"
MEETING_TITLE = "Toplantı"
# Grup basliginda en fazla kac ad yazilir; kalani "+N" olur.
MAX_PARTY_NAMES = 3


# --- yapilandirma --------------------------------------------------------


@dataclass(frozen=True)
class CallsConfig:
    """Ayarlardan tureyen yapilandirma."""

    cache_path: str = ""
    scan_on_refresh: bool = False


def load_config(settings: Any) -> CallsConfig:
    return CallsConfig(
        cache_path=clean_text(settings.get("calls.cache_path", "")),
        scan_on_refresh=str(settings.get("calls.scan_on_refresh", "0") or "0") == "1",
    )


# --- sure ----------------------------------------------------------------


def duration_of(call: CallRecord) -> int:
    """Aramanin suresi (ms). Uc yedek sirayla denenir, negatif sonuc sifirdir."""
    direct = as_int(call.duration_ms)
    if direct is not None and direct > 0:
        return direct

    end = parse_utc(call.end_time)
    if end is not None:
        for candidate in (call.connect_time, call.start_time):
            begin = parse_utc(candidate)
            if begin is None:
                continue
            seconds = (end - begin).total_seconds()
            if seconds > 0:
                return int(seconds * 1000)
    return max(0, direct or 0)


def duration_text(ms: Any) -> str:
    """'1 sa 12 dk', '5 dk', '38 sn', '—'."""
    total = as_int(ms) or 0
    if total <= 0:
        return "—"
    seconds = total // 1000
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours} sa {minutes} dk"
    if minutes:
        return f"{minutes} dk"
    return f"{secs} sn"


# --- kisi cozumu ---------------------------------------------------------


def resolve_name(person_id: str, fallback: str, names: dict[str, str] | None) -> str:
    """Kimlik -> ad; once profil sozlugu, sonra kaydin kendi adi.

    Cozulemezse BOS doner: ham kimlik ad yerine gecmez. Gosterim karari
    `person_label` ile tek yerden verilir.
    """
    marker = clean_text(person_id)
    known = clean_text((names or {}).get(marker, "")) if marker else ""
    name = known or clean_text(fallback)
    return "" if name == marker else name


def person_label(person_id: Any, name: Any = "", names: dict[str, str] | None = None) -> str:
    """Bir kisinin ekranda gorunen adi. Ham kimlik asla dondurulmez."""
    marker = clean_text(person_id)
    resolved = resolve_name(marker, name, names)
    if resolved:
        return resolved
    tail = marker[-ID_TAIL:] if marker else ""
    return f"{UNKNOWN_PERSON} ({tail})" if tail else UNKNOWN_PERSON


def names_from_rows(rows: Iterable[dict[str, Any]]) -> dict[str, str]:
    """Kayitli satirlardan kimlik -> ad sozlugu.

    Tarama anindaki profil sozlugu saklanmiyor; ama birebir aramalarda ad
    zaten cozulmus halde satirda duruyor. Bir kisiyle bir kez birebir
    gorustuyseniz adi grup aramasi basliginda da cikar.
    """
    known: dict[str, str] = {}
    for row in rows:
        marker = clean_text(row.get("counterpart_id"))
        name = clean_text(row.get("counterpart_name"))
        if marker and name and marker not in known:
            known[marker] = name
    return known


def counterpart_of(call: CallRecord, names: dict[str, str] | None) -> tuple[str, str]:
    """Karsi taraf: gelen aramada arayan, giden aramada aranan."""
    if clean_text(call.direction) == DIRECTION_OUT:
        person_id, fallback = call.target_id, call.target_name
    else:
        person_id, fallback = call.originator_id, call.originator_name
    return clean_text(person_id), resolve_name(person_id, fallback, names)


# --- takvim eslemesi -----------------------------------------------------


def usable_events(calendar: Iterable[CalendarRecord]) -> list[CalendarRecord]:
    """**Zaman** eslemesine girebilecek takvim kayitlari.

    Yineleyen serinin sablonu (`RecurringMaster`) buraya GIRMEZ: tarihi
    serinin ilk gunudur, bugunku aramayla saat karsilastirmak anlamsiz olur.
    Iptal edilmis kayit ve "ofiste degilim" de girmez.
    """
    picked: list[CalendarRecord] = []
    for event in calendar or ():
        if clean_text(event.event_type) == EVENT_RECURRING_MASTER:
            continue
        if clean_text(event.show_as) == SHOW_AS_OOF:
            continue
        if getattr(event, "is_cancelled", False):
            continue
        if parse_local(event.start_time) is None:
            continue
        picked.append(event)
    return picked


def identity_events(calendar: Iterable[CalendarRecord]) -> list[CalendarRecord]:
    """**Kimlik** eslemesine girebilecek kayitlar (kimligi olan her sey).

    Tekrarlayan toplantilar (sabah daily'leri) takvimde cogu zaman yalnizca
    seri kaydi olarak durur; olusumlar ayri kayit olmayabilir. Kimlik
    eslemesinde saat rol oynamadigi icin seri kaydi da, iptal edilmis seri de
    adaydir: gecmis aramalar iptal edilmis bir seriye ait olabilir.
    """
    picked: list[CalendarRecord] = []
    for event in calendar or ():
        if clean_text(event.show_as) == SHOW_AS_OOF:
            continue
        if not clean_text(getattr(event, "cid", "")) and not clean_text(
            getattr(event, "meeting_url", "")
        ):
            continue
        picked.append(event)
    return picked


@dataclass
class EventPools:
    """Iki ayri havuz: kimlikle eslesenler ve saatle eslesenler."""

    identity: list[CalendarRecord] = dataclass_field(default_factory=list)
    time: list[CalendarRecord] = dataclass_field(default_factory=list)

    def __iter__(self):
        """Eski kod bunu duz bir takvim listesi gibi gezebilsin."""
        return iter(self.time)


def event_pools(calendar: Iterable[CalendarRecord]) -> EventPools:
    """Takvimi bir kez suzup iki havuza ayirir (her arama icin tekrar etmesin)."""
    if isinstance(calendar, EventPools):
        return calendar
    records = list(calendar or ())
    return EventPools(identity=identity_events(records), time=usable_events(records))


def thread_core(thread_id: Any) -> str:
    """`19:meeting_ABC123@thread.v2` -> `ABC123`.

    Toplanti baglantisinda thread kimligi URL kodlanmis gecer (`%3a`, `%40`),
    duz alt dizge aramasi tutmaz; govdesi ise oldugu gibi durur.
    """
    text = clean_text(thread_id)
    if ":" in text:
        text = text.split(":", 1)[1]
    if "@" in text:
        text = text.split("@", 1)[0]
    if text.startswith("meeting_"):
        text = text[len("meeting_"):]
    return text


def thread_matches(thread_id: Any, event: CalendarRecord) -> bool:
    """Arama ile takvim kaydi ayni toplantiya mi ait? (kesin eslesme)"""
    marker = clean_text(thread_id)
    if len(marker) < MIN_THREAD_LENGTH:
        return False
    cid = clean_text(getattr(event, "cid", ""))
    if cid and (cid == marker or marker in cid or cid in marker):
        return True
    core = thread_core(marker)
    if not core or len(core) < MIN_THREAD_LENGTH:
        return False
    url = clean_text(getattr(event, "meeting_url", ""))
    return bool(url and core in url)


def call_threads(call: CallRecord) -> list[str]:
    """Aramanin tasidigi sohbet kimlikleri (toplanti ve grup sohbeti)."""
    markers = [clean_text(call.thread_id), clean_text(call.group_thread_id)]
    return [marker for marker in markers if marker]


def match_by_thread(
    call: CallRecord, events: Iterable[CalendarRecord]
) -> CalendarRecord | None:
    """Kimlikle kesin eslesme: `threadId` ya da `groupChatThreadId` <-> `cid`."""
    markers = call_threads(call)
    if not markers:
        return None
    for event in events:
        if any(thread_matches(marker, event) for marker in markers):
            return event
    return None


def match_event(
    call: CallRecord,
    events: Any,
    tolerance_minutes: int = MEETING_TOLERANCE_MINUTES,
) -> CalendarRecord | None:
    """Aramanin takvim kaydi.

    Once **kesin** yol denenir: aramanin `threadId` ya da `groupChatThreadId`
    degeri takvim kaydinin `skypeTeamsDataObj.cid` alanina (ya da toplanti
    baglantisinin govdesine) denk geliyorsa saat hic hesaba katilmaz --
    tekrarlayan toplantilar yalnizca boyle eslesir. Kimlik tutmuyorsa
    baslangic saatine en yakin kayit alinir; pencere disi eslesmez.
    """
    pools = event_pools(events)
    exact = match_by_thread(call, pools.identity)
    if exact is not None:
        return exact

    started = parse_utc(call.start_time)
    if started is None:
        return None
    window = timedelta(minutes=max(0, int(tolerance_minutes)))
    best: CalendarRecord | None = None
    best_gap: timedelta | None = None
    for event in pools.time:
        moment = parse_local(event.start_time)
        if moment is None:
            continue
        gap = abs(moment - started)
        if gap > window:
            continue
        if best_gap is None or gap < best_gap:
            best, best_gap = event, gap
    return best


def thread_index(threads: Iterable[ThreadRecord]) -> dict[str, ThreadRecord]:
    """Sohbet kimligi -> sohbet kaydi."""
    known: dict[str, ThreadRecord] = {}
    for thread in threads or ():
        marker = clean_text(thread.thread_id)
        if marker:
            known.setdefault(marker, thread)
    return known


def thread_of(call: CallRecord, threads: dict[str, ThreadRecord]) -> ThreadRecord | None:
    """Aramanin bagli oldugu sohbet (once grup sohbeti, sonra toplanti)."""
    for marker in (call.group_thread_id, call.thread_id):
        thread = threads.get(clean_text(marker))
        if thread is not None:
            return thread
    return None


# --- normalize -----------------------------------------------------------


def normalize_call(
    call: CallRecord,
    events: Any = (),
    names: dict[str, str] | None = None,
    seen_at: str | None = None,
    threads: dict[str, ThreadRecord] | None = None,
) -> dict[str, Any] | None:
    """Tek arama -> veritabani satiri. Kimliksiz kayit atlanir."""
    call_id = clean_text(call.call_id)
    if not call_id:
        return None

    call_type = canonical_type(call.call_type)
    event = match_event(call, events) if call_type != TYPE_TWO_PARTY else None
    if call_type == TYPE_TWO_PARTY:
        kind = KIND_ONE_TO_ONE
    elif event is not None:
        kind = KIND_MEETING
    else:
        kind = KIND_GROUP

    thread = thread_of(call, threads or {})
    counterpart_id, counterpart_name = counterpart_of(call, names)

    # Katilimcilar: aramanin kendi `participantList` alani; bos kalirsa
    # sohbetin uyeleri yedege gecer. Adlar profil sozlugunden cozulur ve
    # ad SATIRDA saklanir; boylece ekranda ham kimlik hic gorunmez.
    people = [clean_text(item) for item in (call.participants or []) if clean_text(item)]
    if not people and thread is not None:
        people = [clean_text(item) for item in thread.members if clean_text(item)]
    participants = [
        {"id": person, "name": resolve_name(person, "", names)} for person in people
    ]

    subject = clean_text(event.subject) if event is not None else clean_text(call.subject)
    topic = clean_text(thread.topic) if thread is not None else ""
    if not topic and thread is not None and thread.member_names:
        # Basligi olmayan grup sohbetinde Teams de avatar adlarini yaziyor.
        topic = ", ".join(thread.member_names[:MAX_PARTY_NAMES])
    attendees = list(event.attendees) if event is not None else []

    return {
        "call_id": call_id,
        "started_at": iso_text(parse_utc(call.start_time)),
        "ended_at": iso_text(parse_utc(call.end_time)),
        "connected_at": iso_text(parse_utc(call.connect_time)),
        "duration_ms": duration_of(call),
        "direction": canonical_direction(call.direction),
        "state": canonical_state(call.state),
        "kind": kind,
        "counterpart_id": counterpart_id,
        "counterpart_name": counterpart_name,
        "forwarded": clean_text(call.forwarded),
        "meeting_subject": subject,
        "meeting_organizer": clean_text(event.organizer_name) if event is not None else "",
        "my_response": clean_text(event.my_response) if event is not None else "",
        "thread_id": clean_text(call.thread_id),
        "group_thread_id": clean_text(call.group_thread_id),
        "topic": topic,
        "participants_json": json.dumps(participants, ensure_ascii=False),
        "attendees_json": json.dumps(attendees, ensure_ascii=False),
        "raw_json": json.dumps(jsonable(call.raw or {}), ensure_ascii=False, default=str),
        "seen_at": seen_at or repository.now_iso(),
        # Saklanmaz (tabloda sutunu yok): yalnizca tarama ozetindeki
        # "kac tekrarlayan toplanti eslesti" sayimi icin tasinir.
        "matched_event_type": clean_text(event.event_type) if event is not None else "",
    }


def normalize(
    calls: Iterable[CallRecord],
    calendar: Iterable[CalendarRecord] = (),
    names: dict[str, str] | None = None,
    seen_at: str | None = None,
    threads: Iterable[ThreadRecord] = (),
) -> list[dict[str, Any]]:
    """Ham kayitlar -> veritabani satirlari (eskiden yeniye)."""
    events = event_pools(calendar)
    known_threads = thread_index(threads)
    stamp = seen_at or repository.now_iso()
    rows: list[dict[str, Any]] = []
    for call in calls or ():
        row = normalize_call(call, events, names, stamp, known_threads)
        if row is not None:
            rows.append(row)
    rows.sort(key=lambda item: (item["started_at"], item["call_id"]))
    return rows


# --- ekran gorunumu ------------------------------------------------------


def _load_list(value: Any) -> list[Any]:
    """JSON metni ya da hazir liste -> liste; bozuk deger bos listeye duser."""
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def participant_pairs(row: dict[str, Any]) -> list[dict[str, str]]:
    """Katilimcilar: `{"id", "name"}` ciftleri.

    Eski satirlar duz kimlik listesi tasiyordu; ikisi de okunur.
    """
    pairs: list[dict[str, str]] = []
    for item in _load_list(row.get("participants_json")):
        if isinstance(item, dict):
            person = clean_text(item.get("id"))
            name = clean_text(item.get("name"))
        else:
            person, name = clean_text(item), ""
        if person or name:
            pairs.append({"id": person, "name": name})
    return pairs


def participants_of(row: dict[str, Any]) -> list[str]:
    """Katilimci kimlikleri (kisi eslemesi bunun uzerinden yapilir)."""
    return [pair["id"] for pair in participant_pairs(row) if pair["id"]]


def attendees_of(row: dict[str, Any]) -> list[str]:
    """Takvim davetlileri (katilanlar degil: davet edilenler)."""
    return [clean_text(item) for item in _load_list(row.get("attendees_json")) if clean_text(item)]


def participant_labels(record: dict[str, Any], names: dict[str, str] | None = None) -> list[str]:
    """Katilimcilarin gorunen adlari (ham kimlik cikmaz).

    Once tarama aninda profillerden cozulup satira yazilan ad, sonra ekran
    anindaki ad sozlugu, en sonunda "Bilinmeyen kişi (son alti hane)".
    """
    return [person_label(pair["id"], pair["name"], names) for pair in participant_pairs(record)]


def display_party(record: dict[str, Any], names: dict[str, str] | None = None) -> str:
    """Satirin "karsi taraf" sutununda gorunen metin. Tek dogru kaynak budur.

    Cok kisili aramada "karsi taraf" diye bir sey yoktur: toplantida konu,
    grup aramasinda katilimci adlari (en fazla uc ad, kalani "+N") yazilir.
    Katilimci da yoksa arama turunun kendisi yazilir. Birebir gorusmede
    karsi tarafin adi; adi cozulemediyse "Bilinmeyen kişi (son alti hane)".
    """
    kind = clean_text(record.get("kind"))
    if kind == KIND_ONE_TO_ONE:
        return person_label(record.get("counterpart_id"), record.get("counterpart_name"), names)

    if kind == KIND_MEETING:
        subject = clean_text(record.get("meeting_subject"))
        if subject:
            return subject

    # Grup sohbetinin kendi adi varsa katilimci dokumunden daha anlamlidir.
    topic = clean_text(record.get("topic"))
    if topic:
        return topic

    people = participant_labels(record, names)
    if people:
        shown = ", ".join(people[:MAX_PARTY_NAMES])
        rest = len(people) - MAX_PARTY_NAMES
        return f"{shown} +{rest}" if rest > 0 else shown
    return MEETING_TITLE if kind == KIND_MEETING else GROUP_TITLE


def view(row: dict[str, Any], names: dict[str, str] | None = None) -> dict[str, Any]:
    """Satirin arayuze giden hali: etiketler, okunur sure, gorunen taraf."""
    card = dict(row)
    card["participants"] = participants_of(row)
    card["participant_names"] = participant_labels(row, names)
    # Katilanlar (arama kaydi) ile davetliler (takvim) ayri sutunlardir.
    card["attendees"] = attendees_of(row)
    card.pop("participants_json", None)
    card.pop("attendees_json", None)
    card.pop("raw_json", None)
    card["kind_label"] = KIND_LABELS.get(row.get("kind", ""), "")
    card["direction_label"] = DIRECTION_LABELS.get(row.get("direction", ""), "")
    card["state_label"] = STATE_LABELS.get(row.get("state", ""), clean_text(row.get("state")))
    card["duration_text"] = duration_text(row.get("duration_ms"))
    card["title"] = display_party(row, names)
    # Ham kimlik ekrana cikmasin: birebirde cozulemeyen ad da etiketlenir.
    card["counterpart_label"] = (
        person_label(row.get("counterpart_id"), row.get("counterpart_name"), names)
        if clean_text(row.get("counterpart_id"))
        else ""
    )
    card["connected"] = is_connected(row)
    return card


def is_connected(row: dict[str, Any]) -> bool:
    """Gercekten gorusuldu mu? Kacirilan ve reddedilen arama temas degildir."""
    return clean_text(row.get("state")) == STATE_ACCEPTED


def matches(row: dict[str, Any], needle: str, names: dict[str, str] | None = None) -> bool:
    """Arama kutusu: gorunen taraf, katilimci adlari, konu, organizator, etiketler.

    Grup aramasinda ekranda katilimci adlari yaziyorsa arama da onlarda
    calismali; yoksa "gorunen ama bulunamayan" satirlar olurdu.
    """
    if not needle:
        return True
    haystack = " ".join(
        str(part or "")
        for part in (
            display_party(row, names),
            *participant_labels(row, names),
            *attendees_of(row),
            row.get("topic"),
            row.get("counterpart_name"),
            row.get("counterpart_id"),
            row.get("meeting_subject"),
            row.get("meeting_organizer"),
            KIND_LABELS.get(row.get("kind", ""), ""),
            DIRECTION_LABELS.get(row.get("direction", ""), ""),
            STATE_LABELS.get(row.get("state", ""), ""),
        )
    )
    return needle in field_utils.fold(haystack)


def since_of(days: Any = DEFAULT_DAYS, now: datetime | None = None) -> datetime:
    """Pencerenin baslangici; gun sayisi 1..3650 arasina sikistirilir."""
    try:
        number = int(str(days).strip())
    except (TypeError, ValueError):
        number = DEFAULT_DAYS
    number = max(1, min(number, 3650))
    return (now or utc_now()) - timedelta(days=number)


def in_window(row: dict[str, Any], since: datetime) -> bool:
    moment = parse_utc(row.get("started_at"))
    return moment is not None and moment >= since


def select(
    rows: Iterable[dict[str, Any]],
    days: Any = DEFAULT_DAYS,
    q: str = "",
    direction: str = "",
    state: str = "",
    kind: str = "",
    now: datetime | None = None,
    names: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Pencere + suzgecler; yeniden eskiye siralanmis satirlar."""
    rows = list(rows)
    known = names_from_rows(rows) if names is None else names
    since = since_of(days, now)
    needle = field_utils.fold((q or "").strip())
    wanted_direction = clean_text(direction)
    wanted_state = clean_text(state)
    wanted_kind = clean_text(kind)

    picked: list[dict[str, Any]] = []
    for row in rows:
        if not in_window(row, since):
            continue
        if wanted_direction and clean_text(row.get("direction")) != wanted_direction:
            continue
        if wanted_state and clean_text(row.get("state")) != wanted_state:
            continue
        if wanted_kind and clean_text(row.get("kind")) != wanted_kind:
            continue
        if not matches(row, needle, known):
            continue
        picked.append(row)
    picked.sort(key=lambda item: (str(item.get("started_at") or ""), str(item.get("call_id") or "")), reverse=True)
    return picked


# --- istatistik ----------------------------------------------------------


def local_day(row: dict[str, Any]) -> date | None:
    """Aramanin YEREL gunu; gun dokumu makinenin saatine gore yapilir."""
    moment = parse_utc(row.get("started_at"))
    if moment is None:
        return None
    return moment.astimezone().date()


def workdays_between(first: date, last: date) -> int:
    """Iki tarih arasindaki is gunu (Pzt-Cum) sayisi; tatil dusulmez."""
    if last < first:
        return 0
    count = 0
    cursor = first
    while cursor <= last:
        if cursor.weekday() < 5:
            count += 1
        cursor += timedelta(days=1)
    return count


def people_totals(
    rows: Iterable[dict[str, Any]], names: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    """Kisi dokumu: yalnizca birebir aramalar, suresine gore siralanmis.

    Kacirilan ve reddedilen aramalar sureye girmez ama sayilir; "iki kez
    aradim, ikisinde de ulasamadim" bilgisi kaybolmasin.
    """
    totals: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("kind") != KIND_ONE_TO_ONE:
            continue
        key = clean_text(row.get("counterpart_id")) or clean_text(row.get("counterpart_name"))
        if not key:
            continue
        entry = totals.setdefault(
            key,
            {
                "counterpart_id": clean_text(row.get("counterpart_id")),
                "name": person_label(row.get("counterpart_id"), row.get("counterpart_name"), names),
                "ms": 0,
                "count": 0,
                "outgoing": 0,
                "incoming": 0,
                "missed": 0,
            },
        )
        entry["count"] += 1
        if clean_text(row.get("direction")) == DIRECTION_OUT:
            entry["outgoing"] += 1
        else:
            entry["incoming"] += 1
        if clean_text(row.get("state")) in (STATE_MISSED, STATE_DECLINED):
            entry["missed"] += 1
        if is_connected(row):
            entry["ms"] += as_int(row.get("duration_ms")) or 0

    people = list(totals.values())
    for entry in people:
        entry["duration_text"] = duration_text(entry["ms"])
    people.sort(key=lambda item: (-item["ms"], -item["count"], field_utils.fold(item["name"])))
    return people


def build_stats(
    rows: Iterable[dict[str, Any]],
    days: Any = DEFAULT_DAYS,
    now: datetime | None = None,
    names: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Istatistik seridi: top 5, uc dilim, aradim/arandim, is gunu ortalamasi."""
    rows = list(rows)
    known = names_from_rows(rows) if names is None else names
    moment = now or utc_now()
    since = since_of(days, moment)
    window = [row for row in rows if in_window(row, since)]
    connected = [row for row in window if is_connected(row)]
    total_ms = sum(as_int(row.get("duration_ms")) or 0 for row in connected)

    split: list[dict[str, Any]] = []
    for kind in (KIND_MEETING, KIND_GROUP, KIND_ONE_TO_ONE):
        part = [row for row in connected if row.get("kind") == kind]
        ms = sum(as_int(row.get("duration_ms")) or 0 for row in part)
        split.append(
            {
                "kind": kind,
                "label": KIND_LABELS[kind],
                "ms": ms,
                "hours": round(ms / 3600000, 1),
                "percent": round(ms * 100 / total_ms, 1) if total_ms else 0.0,
                "count": len(part),
                "duration_text": duration_text(ms),
            }
        )

    directions: dict[str, Any] = {}
    for name, marker in (("outgoing", DIRECTION_OUT), ("incoming", DIRECTION_IN)):
        part = [row for row in connected if clean_text(row.get("direction")) == marker]
        ms = sum(as_int(row.get("duration_ms")) or 0 for row in part)
        directions[name] = {
            "label": DIRECTION_LABELS[marker],
            "count": len(part),
            "ms": ms,
            "duration_text": duration_text(ms),
        }
    directions["missed"] = sum(
        1 for row in window if clean_text(row.get("state")) == STATE_MISSED
    )
    directions["declined"] = sum(
        1 for row in window if clean_text(row.get("state")) == STATE_DECLINED
    )

    daily: dict[date, dict[str, Any]] = {}
    for row in connected:
        day = local_day(row)
        if day is None:
            continue
        entry = daily.setdefault(day, {"ms": 0, "count": 0})
        entry["ms"] += as_int(row.get("duration_ms")) or 0
        entry["count"] += 1

    first = since.astimezone().date()
    last = moment.astimezone().date()
    workdays = workdays_between(first, last)
    busiest = max(daily.items(), key=lambda item: (item[1]["ms"], item[0]), default=None)

    return {
        "days": _as_days(days),
        "since": iso_text(since),
        "until": iso_text(moment),
        "calls": len(window),
        "connected": len(connected),
        "total_ms": total_ms,
        "total_text": duration_text(total_ms),
        "top": people_totals(connected, known)[:TOP_PEOPLE],
        "split": split,
        "direction": directions,
        "workday": {
            "days": workdays,
            "average_ms": int(total_ms / workdays) if workdays else 0,
            "average_text": duration_text(int(total_ms / workdays) if workdays else 0),
            "busiest": (
                {
                    "date": busiest[0].isoformat(),
                    "ms": busiest[1]["ms"],
                    "count": busiest[1]["count"],
                    "duration_text": duration_text(busiest[1]["ms"]),
                }
                if busiest
                else None
            ),
        },
    }


def _as_days(days: Any) -> int:
    try:
        number = int(str(days).strip())
    except (TypeError, ValueError):
        return DEFAULT_DAYS
    return max(1, min(number, 3650))


# --- kisi cekmecesi ------------------------------------------------------


def _order(row: dict[str, Any]) -> tuple[str, str]:
    """Siralama anahtari: once zaman, esitlikte kimlik (kararli siralama)."""
    return (str(row.get("started_at") or ""), str(row.get("call_id") or ""))


def person_view(
    rows: Iterable[dict[str, Any]],
    counterpart_id: str,
    days: Any = DEFAULT_DAYS,
    now: datetime | None = None,
    names: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Bir kisiyle butun gorusmeler + katildigi grup/toplantilar."""
    rows = list(rows)
    known = names_from_rows(rows) if names is None else names
    marker = clean_text(counterpart_id)
    since = since_of(days, now)
    window = [row for row in rows if in_window(row, since)]

    personal = [
        row
        for row in window
        if row.get("kind") == KIND_ONE_TO_ONE and clean_text(row.get("counterpart_id")) == marker
    ]
    shared = [
        row
        for row in window
        if row.get("kind") != KIND_ONE_TO_ONE
        and (clean_text(row.get("counterpart_id")) == marker or marker in participants_of(row))
    ]

    connected = [row for row in personal if is_connected(row)]
    total_ms = sum(as_int(row.get("duration_ms")) or 0 for row in connected)
    longest = max((as_int(row.get("duration_ms")) or 0 for row in connected), default=0)
    last_at = max((str(row.get("started_at") or "") for row in personal + shared), default="")
    name = next(
        (
            clean_text(row.get("counterpart_name"))
            for row in personal
            if clean_text(row.get("counterpart_name"))
        ),
        "",
    )

    return {
        "person": {"id": marker, "name": person_label(marker, name, known)},
        "summary": {
            "total_ms": total_ms,
            "total_text": duration_text(total_ms),
            "count": len(personal),
            "outgoing": sum(
                1 for row in personal if clean_text(row.get("direction")) == DIRECTION_OUT
            ),
            "incoming": sum(
                1 for row in personal if clean_text(row.get("direction")) == DIRECTION_IN
            ),
            "missed": sum(
                1
                for row in personal
                if clean_text(row.get("state")) in (STATE_MISSED, STATE_DECLINED)
            ),
            "longest_ms": longest,
            "longest_text": duration_text(longest),
            "last_at": last_at,
            "group_count": len(shared),
        },
        "calls": [view(row, known) for row in sorted(personal, key=_order, reverse=True)],
        "group_calls": [view(row, known) for row in sorted(shared, key=_order, reverse=True)],
    }


# --- teshis: neden eslesmedi? --------------------------------------------
#
# Tekrarlayan toplantilar uzun sure "grup aramasi" olarak kaldi. Bu bolum
# kullanicinin ekranindan tek tikla toplanabilen bir dokum uretir: hangi
# arama hangi sebeple eslesmedi, takvimde en yakin adaylar neydi.

# Bir arama icin en fazla kac takvim adayi gosterilir.
MAX_CANDIDATES = 3
# Aday sayilabilmek icin en fazla bu kadar uzakta olabilir.
CANDIDATE_WINDOW_HOURS = 24

REASON_NO_THREAD = "no_thread_id"
REASON_NO_CORE = "no_calendar_with_core"
REASON_TIME_GAP = "only_time_gap"
REASON_MATCHES_NOW = "matches_now"


def row_threads(row: dict[str, Any]) -> list[str]:
    markers = [clean_text(row.get("thread_id")), clean_text(row.get("group_thread_id"))]
    return [marker for marker in markers if marker]


def event_view(event: CalendarRecord, gap_minutes: int | None = None) -> dict[str, Any]:
    """Takvim adayinin teshis dokumu (kimlik govdeleriyle)."""
    url = clean_text(getattr(event, "meeting_url", ""))
    moment = parse_local(event.start_time)
    return {
        "subject": clean_text(event.subject),
        "event_type": clean_text(event.event_type),
        "start_time": iso_text(moment),
        "cid_core": thread_core(getattr(event, "cid", "")),
        "url_core": url[-40:] if url else "",
        "is_cancelled": bool(getattr(event, "is_cancelled", False)),
        "gap_minutes": gap_minutes,
    }


def candidates_for(
    row: dict[str, Any], events: Sequence[CalendarRecord], limit: int = MAX_CANDIDATES
) -> list[dict[str, Any]]:
    """Aramaya zaman olarak en yakin takvim kayitlari (neden eslesmedigi icin)."""
    started = parse_utc(row.get("started_at"))
    if started is None:
        return []
    window = timedelta(hours=CANDIDATE_WINDOW_HOURS)
    near: list[tuple[timedelta, CalendarRecord]] = []
    for event in events:
        moment = parse_local(event.start_time)
        if moment is None:
            continue
        gap = abs(moment - started)
        if gap <= window:
            near.append((gap, event))
    near.sort(key=lambda item: item[0])
    return [
        event_view(event, int(gap.total_seconds() // 60)) for gap, event in near[:limit]
    ]


def diagnose_unmatched(
    rows: Iterable[dict[str, Any]],
    calendar: Iterable[CalendarRecord] = (),
    days: Any = DEFAULT_DAYS,
    now: datetime | None = None,
    names: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Eslesmemis cok kisili aramalarin dokumu + neden eslesmedikleri.

    `calendar` TAZE okunan takvimdir: bir satirin bugun eslesip eslesmeyecegi
    ("yeniden tarasam duzelir mi") ancak boyle anlasilir.
    """
    stored = list(rows)
    known = names_from_rows(stored) if names is None else names
    since = since_of(days, now)
    pools = event_pools(calendar)

    entries: list[dict[str, Any]] = []
    counts = {REASON_NO_THREAD: 0, REASON_NO_CORE: 0, REASON_MATCHES_NOW: 0}
    for row in stored:
        if row.get("kind") != KIND_GROUP or not in_window(row, since):
            continue
        markers = row_threads(row)
        fresh = next(
            (
                event
                for event in pools.identity
                if any(thread_matches(marker, event) for marker in markers)
            ),
            None,
        )
        candidates = candidates_for(row, pools.time or pools.identity)

        if not markers:
            reason = REASON_NO_THREAD
            counts[REASON_NO_THREAD] += 1
        elif fresh is not None:
            reason = REASON_MATCHES_NOW
            counts[REASON_MATCHES_NOW] += 1
        elif candidates and candidates[0]["gap_minutes"] is not None:
            reason = f"{REASON_TIME_GAP}:{candidates[0]['gap_minutes']}"
            counts[REASON_NO_CORE] += 1
        else:
            reason = REASON_NO_CORE
            counts[REASON_NO_CORE] += 1

        entries.append(
            {
                "call_id": row.get("call_id", ""),
                "started_at": row.get("started_at", ""),
                "duration_text": duration_text(row.get("duration_ms")),
                "thread_id": clean_text(row.get("thread_id")),
                "group_thread_id": clean_text(row.get("group_thread_id")),
                "thread_core": thread_core(markers[0]) if markers else "",
                "participants": participant_labels(row, known),
                "title": display_party(row, known),
                "reason": reason,
                "candidates": candidates,
                "would_match": {"subject": clean_text(fresh.subject)} if fresh is not None else None,
            }
        )

    entries.sort(key=lambda item: str(item.get("started_at") or ""), reverse=True)
    return {
        "days": _as_days(days),
        "calendar_events": len(pools.identity) + len(pools.time),
        "summary": {
            "unmatched": len(entries),
            "no_thread_id": counts[REASON_NO_THREAD],
            "core_not_in_calendar": counts[REASON_NO_CORE],
            "matched_after_fix": counts[REASON_MATCHES_NOW],
        },
        "calls": entries,
    }


# --- tarama --------------------------------------------------------------


def source_diagnostics(source: Any) -> dict[str, Any]:
    """Kaynagin teshis bilgisi (kopyalanan/atlanan dosya, kaynak, uyari).

    Sozlesme bunu zorunlu kilmaz: veremeyen kaynak bos degerlerle gecer.
    """
    base = empty_diagnostics()
    reader = getattr(source, "diagnostics", None)
    info = reader() if callable(reader) else None
    if isinstance(info, dict):
        for key in base:
            if key in info and info[key] is not None:
                base[key] = info[key]
    return base


def scan(conn: Any, source: CallSource, seen_at: str | None = None) -> dict[str, Any]:
    """Kaynagi okur, normalize eder, `call_id` ile tekillestirerek yazar.

    Ozet yalnizca sayilari degil, verinin NEREDEN geldigini de tasir: en yeni
    aramalar eksikse kullanicinin bunu balonda gormesi gerekir.
    """
    bundle = source.read()
    # Eski uc'lu donus de kabul edilir: sohbetler bos gecer.
    calls, calendar, names, threads = (*bundle, [])[:4] if len(bundle) == 3 else bundle
    rows = normalize(calls, calendar, names, seen_at, threads)
    report = repository.import_calls(conn, rows)
    report["meetings_matched"] = sum(1 for row in rows if row["kind"] == KIND_MEETING)
    # Tekrarlayan toplantilar yalnizca kimlik eslemesiyle yakalanir; kac
    # tanesinin seri kaydindan geldigi ayrica sayilir.
    report["recurring_matched"] = sum(
        1
        for row in rows
        if row["kind"] == KIND_MEETING
        and clean_text(row.get("matched_event_type")) == EVENT_RECURRING_MASTER
    )
    report["latest_call_at"] = max(
        (row["started_at"] for row in rows if row["started_at"]), default=""
    )
    report.update(source_diagnostics(source))
    return report
