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
from dataclasses import dataclass
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
    as_int,
    clean_text,
    empty_diagnostics,
    iso_text,
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
MEETING_TOLERANCE_MINUTES = 10

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
    """Yineleyen serinin sablonu ve 'ofiste degilim' kaydi eslesmeye girmez."""
    picked: list[CalendarRecord] = []
    for event in calendar or ():
        if clean_text(event.event_type) == EVENT_RECURRING_MASTER:
            continue
        if clean_text(event.show_as) == SHOW_AS_OOF:
            continue
        if parse_local(event.start_time) is None:
            continue
        picked.append(event)
    return picked


def match_event(
    call: CallRecord,
    events: Sequence[CalendarRecord],
    tolerance_minutes: int = MEETING_TOLERANCE_MINUTES,
) -> CalendarRecord | None:
    """Aramanin baslangicina en yakin takvim kaydi (pencere disi eslesmez)."""
    started = parse_utc(call.start_time)
    if started is None:
        return None
    window = timedelta(minutes=max(0, int(tolerance_minutes)))
    best: CalendarRecord | None = None
    best_gap: timedelta | None = None
    for event in events:
        moment = parse_local(event.start_time)
        if moment is None:
            continue
        gap = abs(moment - started)
        if gap > window:
            continue
        if best_gap is None or gap < best_gap:
            best, best_gap = event, gap
    return best


# --- normalize -----------------------------------------------------------


def normalize_call(
    call: CallRecord,
    events: Sequence[CalendarRecord] = (),
    names: dict[str, str] | None = None,
    seen_at: str | None = None,
) -> dict[str, Any] | None:
    """Tek arama -> veritabani satiri. Kimliksiz kayit atlanir."""
    call_id = clean_text(call.call_id)
    if not call_id:
        return None

    call_type = clean_text(call.call_type)
    event = match_event(call, events) if call_type != TYPE_TWO_PARTY else None
    if call_type == TYPE_TWO_PARTY:
        kind = KIND_ONE_TO_ONE
    elif event is not None:
        kind = KIND_MEETING
    else:
        kind = KIND_GROUP

    counterpart_id, counterpart_name = counterpart_of(call, names)
    participants = [clean_text(item) for item in (call.participants or []) if clean_text(item)]
    subject = clean_text(event.subject) if event is not None else clean_text(call.subject)

    return {
        "call_id": call_id,
        "started_at": iso_text(parse_utc(call.start_time)),
        "ended_at": iso_text(parse_utc(call.end_time)),
        "connected_at": iso_text(parse_utc(call.connect_time)),
        "duration_ms": duration_of(call),
        "direction": clean_text(call.direction),
        "state": clean_text(call.state),
        "kind": kind,
        "counterpart_id": counterpart_id,
        "counterpart_name": counterpart_name,
        "forwarded": clean_text(call.forwarded),
        "meeting_subject": subject,
        "meeting_organizer": clean_text(event.organizer_name) if event is not None else "",
        "my_response": clean_text(event.my_response) if event is not None else "",
        "participants_json": json.dumps(participants, ensure_ascii=False),
        "raw_json": json.dumps(call.raw or {}, ensure_ascii=False, default=str),
        "seen_at": seen_at or repository.now_iso(),
    }


def normalize(
    calls: Iterable[CallRecord],
    calendar: Iterable[CalendarRecord] = (),
    names: dict[str, str] | None = None,
    seen_at: str | None = None,
) -> list[dict[str, Any]]:
    """Ham kayitlar -> veritabani satirlari (eskiden yeniye)."""
    events = usable_events(calendar)
    stamp = seen_at or repository.now_iso()
    rows: list[dict[str, Any]] = []
    for call in calls or ():
        row = normalize_call(call, events, names, stamp)
        if row is not None:
            rows.append(row)
    rows.sort(key=lambda item: (item["started_at"], item["call_id"]))
    return rows


# --- ekran gorunumu ------------------------------------------------------


def participants_of(row: dict[str, Any]) -> list[str]:
    """`participants_json` -> liste; bozuk deger bos listeye duser."""
    raw = row.get("participants_json")
    if isinstance(raw, list):
        return [clean_text(item) for item in raw if clean_text(item)]
    try:
        parsed = json.loads(str(raw or "[]"))
    except (TypeError, ValueError):
        return []
    return [clean_text(item) for item in parsed if clean_text(item)] if isinstance(parsed, list) else []


def participant_labels(record: dict[str, Any], names: dict[str, str] | None = None) -> list[str]:
    """Katilimci kimlikleri -> gorunen adlar (ham kimlik cikmaz)."""
    return [person_label(person, "", names) for person in participants_of(record)]


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
    card.pop("participants_json", None)
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
    calls, calendar, names = source.read()
    rows = normalize(calls, calendar, names, seen_at)
    report = repository.import_calls(conn, rows)
    report["meetings_matched"] = sum(1 for row in rows if row["kind"] == KIND_MEETING)
    report["latest_call_at"] = max(
        (row["started_at"] for row in rows if row["started_at"]), default=""
    )
    report.update(source_diagnostics(source))
    return report
