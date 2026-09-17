"""Arama gecmisi is mantigi. IndexedDB yok, COM yok, saf Python.

Uc soru burada cevaplanir:

1. **Bu arama ne kadar surdu?** `durationInMs` her kayitta yok; yoksa
   `endTime - connectTime`, o da yoksa `endTime - startTime` kullanilir.
   Kacirilmis aramada sure sifirdir (baglanti hic kurulmadi).
2. **Bu arama birebir mi, grup mu?** `TwoParty` birebir, `MultiParty` grup
   aramasidir. Alan bos ya da taninmiyorsa katilimci sayisina bakilir:
   **kendim haric katilimci > 1 ise grup**, degilse birebir. (Toplanti diye
   bir tur YOKTUR: takvim eslemesi tumden kaldirildi.)
3. **Bu arama hangi grupla yapildi?** Grubun kimligi **katilimci kumesidir**
   (kendim haric, siralanmis kimlikler): ayni kisilerle yapilan butun grup
   aramalari tek satirda toplanir. Sohbet kimligi (`threadId`) kullanilmaz,
   grup adi da onbellekten ARANMAZ; etiket katilimci adlarindan turer
   ("Ali, Veli, Ayse +2").
4. **Karsi taraf kim?** Gelen aramada arayan (`originatorParticipant`),
   giden aramada aranan (`targetParticipant`). Ad, profil store'larindan
   cozulur; cozulemezse kaydin kendi `displayName` alani, o da yoksa kimlik
   yazilir (bos satir kullaniciya hicbir sey anlatmaz).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Iterable

from .. import fields as field_utils, repository
from .source import (
    DIRECTION_IN,
    DIRECTION_OUT,
    STATE_ACCEPTED,
    STATE_DECLINED,
    STATE_MISSED,
    TYPE_MULTI_PARTY,
    TYPE_TWO_PARTY,
    CallRecord,
    CallSource,
    as_int,
    as_mri,
    canonical_direction,
    canonical_state,
    canonical_type,
    clean_text,
    empty_diagnostics,
    iso_text,
    jsonable,
    parse_utc,
    utc_now,
)

# Arama turleri (veritabanindaki `kind` sutunu).
KIND_ONE_TO_ONE = "one_to_one"
KIND_GROUP = "group_call"
KINDS: tuple[str, ...] = (KIND_ONE_TO_ONE, KIND_GROUP)

KIND_LABELS: dict[str, str] = {
    KIND_ONE_TO_ONE: "Birebir",
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


def same_person(left: Any, right: Any) -> bool:
    """Iki kimlik ayni kisi mi? (`8:orgid:` oneki ve harf buyuklugu onemsiz)"""
    first = as_mri(clean_text(left)).casefold()
    second = as_mri(clean_text(right)).casefold()
    return bool(first) and first == second


# --- tur karari ----------------------------------------------------------


def kind_of(call: CallRecord, me: str = "") -> str:
    """Birebir mi, grup mu?

    Once Teams'in kendi alani: `TwoParty` birebir, `MultiParty` gruptur.
    Alan bos ya da taninmiyorsa (eski kayitlar, bozuk deger) katilimci
    sayisina bakilir: **kendim haric katilimci birden fazlaysa grup**.
    Kimligim bilinmiyorsa kimse elenemez; o zaman listenin KENDISI sayilir
    (birebir aramada iki kisi vardir: ben ve karsi taraf).
    """
    call_type = canonical_type(call.call_type)
    if call_type == TYPE_TWO_PARTY:
        return KIND_ONE_TO_ONE
    if call_type == TYPE_MULTI_PARTY:
        return KIND_GROUP
    people = [person for person in (clean_text(item) for item in (call.participants or [])) if person]
    if clean_text(me):
        others = [person for person in people if not same_person(person, me)]
        return KIND_GROUP if len(others) > 1 else KIND_ONE_TO_ONE
    return KIND_GROUP if len(people) > 2 else KIND_ONE_TO_ONE


# --- normalize -----------------------------------------------------------


def normalize_call(
    call: CallRecord,
    names: dict[str, str] | None = None,
    seen_at: str | None = None,
    me: str = "",
) -> dict[str, Any] | None:
    """Tek arama -> veritabani satiri. Kimliksiz kayit atlanir."""
    call_id = clean_text(call.call_id)
    if not call_id:
        return None

    counterpart_id, counterpart_name = counterpart_of(call, names)

    # Katilimcilar aramanin kendi `participantList` alanindan gelir. Adlar
    # profil sozlugunden cozulur ve ad SATIRDA saklanir; boylece ekranda ham
    # kimlik hic gorunmez. Grubun kimligi de bu kumeden turer.
    people = [clean_text(item) for item in (call.participants or []) if clean_text(item)]
    participants = [
        {"id": person, "name": resolve_name(person, "", names)} for person in people
    ]

    return {
        "call_id": call_id,
        "started_at": iso_text(parse_utc(call.start_time)),
        "ended_at": iso_text(parse_utc(call.end_time)),
        "connected_at": iso_text(parse_utc(call.connect_time)),
        "duration_ms": duration_of(call),
        "direction": canonical_direction(call.direction),
        "state": canonical_state(call.state),
        "kind": kind_of(call, me),
        "counterpart_id": counterpart_id,
        "counterpart_name": counterpart_name,
        "forwarded": clean_text(call.forwarded),
        "participants_json": json.dumps(participants, ensure_ascii=False),
        "raw_json": json.dumps(jsonable(call.raw or {}), ensure_ascii=False, default=str),
        "seen_at": seen_at or repository.now_iso(),
    }


def normalize(
    calls: Iterable[CallRecord],
    names: dict[str, str] | None = None,
    seen_at: str | None = None,
    me: str = "",
) -> list[dict[str, Any]]:
    """Ham kayitlar -> veritabani satirlari (eskiden yeniye)."""
    stamp = seen_at or repository.now_iso()
    rows: list[dict[str, Any]] = []
    for call in calls or ():
        row = normalize_call(call, names, stamp, me)
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


def other_pairs(row: dict[str, Any], me: str = "") -> list[dict[str, str]]:
    """Kendim haric katilimcilar.

    Grup istatistiginde "en cok gorusulen" ben olamam. Kimligim bilinmiyorsa
    (ayar bos, tarama bulamadi) kimse elenmez -- yanlis kisiyi elemektense
    hic elememek yeglenir.
    """
    return [pair for pair in participant_pairs(row) if not same_person(pair["id"], me)]


def participant_labels(
    record: dict[str, Any], names: dict[str, str] | None = None, me: str = ""
) -> list[str]:
    """Katilimcilarin gorunen adlari (ham kimlik cikmaz, ben listede yokum).

    Once tarama aninda profillerden cozulup satira yazilan ad, sonra ekran
    anindaki ad sozlugu, en sonunda "Bilinmeyen kişi (son alti hane)".
    """
    return [person_label(pair["id"], pair["name"], names) for pair in other_pairs(record, me)]


def party_text(labels: Iterable[str]) -> str:
    """'Ali, Veli, Ayşe +2': en fazla uc ad, kalani sayiyla."""
    people = list(labels)
    if not people:
        return ""
    shown = ", ".join(people[:MAX_PARTY_NAMES])
    rest = len(people) - MAX_PARTY_NAMES
    return f"{shown} +{rest}" if rest > 0 else shown


def display_party(
    record: dict[str, Any], names: dict[str, str] | None = None, me: str = ""
) -> str:
    """Satirin "karsi taraf" sutununda gorunen metin. Tek dogru kaynak budur.

    Grup aramasinda "karsi taraf" diye bir sey yoktur: katilimci adlari
    (en fazla uc ad, kalani "+N") yazilir; katilimci kaydedilmemisse
    "Grup araması". Birebir gorusmede karsi tarafin adi; adi cozulemediyse
    "Bilinmeyen kişi (son alti hane)".
    """
    if clean_text(record.get("kind")) == KIND_ONE_TO_ONE:
        return person_label(record.get("counterpart_id"), record.get("counterpart_name"), names)
    # Adlar alfabetik: ayni grup listede ve Gruplar sekmesinde AYNI etiketi
    # tasisin (katilimci listesinin sirasi kayittan kayda degisiyor).
    people = sorted(participant_labels(record, names, me), key=field_utils.fold)
    return party_text(people) or GROUP_TITLE


def view(
    row: dict[str, Any], names: dict[str, str] | None = None, me: str = ""
) -> dict[str, Any]:
    """Satirin arayuze giden hali: etiketler, okunur sure, gorunen taraf."""
    card = dict(row)
    card["participants"] = [pair["id"] for pair in other_pairs(row, me) if pair["id"]]
    card["participant_names"] = participant_labels(row, names, me)
    card.pop("participants_json", None)
    card.pop("raw_json", None)
    card["kind_label"] = KIND_LABELS.get(row.get("kind", ""), "")
    card["direction_label"] = DIRECTION_LABELS.get(row.get("direction", ""), "")
    card["state_label"] = STATE_LABELS.get(row.get("state", ""), clean_text(row.get("state")))
    card["duration_text"] = duration_text(row.get("duration_ms"))
    card["title"] = display_party(row, names, me)
    # Ham kimlik ekrana cikmasin: birebirde cozulemeyen ad da etiketlenir.
    card["counterpart_label"] = (
        person_label(row.get("counterpart_id"), row.get("counterpart_name"), names)
        if clean_text(row.get("counterpart_id"))
        else ""
    )
    card["connected"] = is_connected(row)
    # Gruplar sekmesinden listeye gecerken kullanilan anahtar.
    card["group_key"] = group_key(row, me)
    return card


def is_connected(row: dict[str, Any]) -> bool:
    """Gercekten gorusuldu mu? Kacirilan ve reddedilen arama temas degildir."""
    return clean_text(row.get("state")) == STATE_ACCEPTED


def matches(
    row: dict[str, Any], needle: str, names: dict[str, str] | None = None, me: str = ""
) -> bool:
    """Arama kutusu: gorunen taraf, katilimci adlari, sohbet adi, etiketler.

    Grup aramasinda ekranda katilimci adlari yaziyorsa arama da onlarda
    calismali; yoksa "gorunen ama bulunamayan" satirlar olurdu.
    """
    if not needle:
        return True
    haystack = " ".join(
        str(part or "")
        for part in (
            display_party(row, names, me),
            *participant_labels(row, names, me),
            row.get("counterpart_name"),
            row.get("counterpart_id"),
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
    group: str = "",
    now: datetime | None = None,
    names: dict[str, str] | None = None,
    me: str = "",
) -> list[dict[str, Any]]:
    """Pencere + suzgecler; yeniden eskiye siralanmis satirlar."""
    rows = list(rows)
    known = names_from_rows(rows) if names is None else names
    since = since_of(days, now)
    needle = field_utils.fold((q or "").strip())
    wanted_direction = clean_text(direction)
    wanted_state = clean_text(state)
    wanted_kind = clean_text(kind)
    wanted_group = clean_text(group)

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
        if wanted_group and group_key(row, me) != wanted_group:
            continue
        if not matches(row, needle, known, me):
            continue
        picked.append(row)
    picked.sort(key=lambda item: (str(item.get("started_at") or ""), str(item.get("call_id") or "")), reverse=True)
    return picked


# --- gruplar -------------------------------------------------------------
#
# Grup, KATILIMCI KUMESIDIR: ayni kisilerle yapilan butun aramalar tek
# satirda toplanir. Sohbet kimligi (`threadId` / `groupChatThreadId`)
# KULLANILMAZ: ayni ekip iki ayri sohbetten arayinca grup ikiye bolunuyordu.
# Grubun adi da onbellekten ARANMAZ; etiket katilimci adlarindan turer.

# Anahtardaki kimlikleri ayiran isaret.
GROUP_KEY_SEPARATOR = "|"


def group_key(row: dict[str, Any], me: str = "") -> str:
    """Grup aramasinin kimligi: kendim haric katilimcilarin siralanmis kumesi.

    Birebir aramada ve katilimcisi kaydedilmemis grup aramasinda bostur
    (kimligi olmayan satir Gruplar sekmesine girmez).
    """
    if clean_text(row.get("kind")) != KIND_GROUP:
        return ""
    people = sorted({pair["id"] for pair in other_pairs(row, me) if pair["id"]})
    return GROUP_KEY_SEPARATOR.join(people)


def group_totals(
    rows: Iterable[dict[str, Any]], names: dict[str, str] | None = None, me: str = ""
) -> list[dict[str, Any]]:
    """Gruplar sekmesi: her grup arama sayisi, toplam sure, son arama, katilimcilar.

    Grup = katilimci kumesi. Etiket katilimci adlarindan turer ("A, B, C +2");
    tam liste `participants` alaninda doner. Sure yalnizca gercekten
    gorusulen aramalardan toplanir (kacirilan arama temas degildir) ama sayim
    hepsini kapsar. Siralama sureye gore.
    """
    groups: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = group_key(row, me)
        if not key:
            continue
        entry = groups.setdefault(
            key,
            {
                "key": key,
                "count": 0,
                "ms": 0,
                "last_at": "",
                "people": {},
            },
        )
        entry["count"] += 1
        if is_connected(row):
            entry["ms"] += as_int(row.get("duration_ms")) or 0
        started = str(row.get("started_at") or "")
        if started > entry["last_at"]:
            entry["last_at"] = started
        for pair in other_pairs(row, me):
            if pair["id"] and pair["id"] not in entry["people"]:
                entry["people"][pair["id"]] = person_label(pair["id"], pair["name"], names)

    result: list[dict[str, Any]] = []
    for entry in groups.values():
        # Adlar alfabetik: ayni grup her zaman ayni etiketi tasisin (hangi
        # aramanin once geldigine gore degismesin).
        people = sorted(entry["people"].values(), key=field_utils.fold)
        result.append(
            {
                "key": entry["key"],
                # Etiket her zaman katilimci adlarindan turer.
                "name": party_text(people) or GROUP_TITLE,
                "count": entry["count"],
                "ms": entry["ms"],
                "duration_text": duration_text(entry["ms"]),
                "last_at": entry["last_at"],
                "participants": people,
                "participant_ids": list(entry["people"].keys()),
                "people_count": len(people),
            }
        )
    result.sort(key=lambda item: (-item["ms"], -item["count"], field_utils.fold(item["name"])))
    return result


# --- istatistik ----------------------------------------------------------


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


def group_people_totals(
    rows: Iterable[dict[str, Any]], names: dict[str, str] | None = None, me: str = ""
) -> list[dict[str, Any]]:
    """Grup aramalarina katilan kisiler, suresine gore.

    Bir grup aramasinin butun suresi katilan HER kisiye yazilir: soru "bu
    kisiyle ayni aramada ne kadar bulundum". Kendim listede yokum (kimligim
    biliniyorsa); katilimci kaydedilmemisse karsi taraf yedege gecer.
    """
    totals: dict[str, dict[str, Any]] = {}
    for row in rows:
        if row.get("kind") != KIND_GROUP:
            continue
        pairs = other_pairs(row, me)
        if not pairs:
            # Katilimci listesi bos: hic degilse aramayi baslatan/aranan taraf.
            marker = clean_text(row.get("counterpart_id"))
            if not marker or same_person(marker, me):
                continue
            pairs = [{"id": marker, "name": clean_text(row.get("counterpart_name"))}]
        ms = as_int(row.get("duration_ms")) or 0 if is_connected(row) else 0
        key = group_key(row, me)
        for pair in pairs:
            marker = pair["id"] or pair["name"]
            if not marker:
                continue
            entry = totals.setdefault(
                marker,
                {
                    "counterpart_id": pair["id"],
                    "name": person_label(pair["id"], pair["name"], names),
                    "ms": 0,
                    "count": 0,
                    "groups": set(),
                },
            )
            entry["count"] += 1
            entry["ms"] += ms
            if key:
                entry["groups"].add(key)

    people = []
    for entry in totals.values():
        entry["groups"] = len(entry["groups"])
        entry["duration_text"] = duration_text(entry["ms"])
        people.append(entry)
    people.sort(key=lambda item: (-item["ms"], -item["count"], field_utils.fold(item["name"])))
    return people


def combined_people_totals(
    rows: Iterable[dict[str, Any]], names: dict[str, str] | None = None, me: str = ""
) -> list[dict[str, Any]]:
    """Birebir + grup toplaminda en cok gorusulenler (ayni kisi tek satir)."""
    rows = list(rows)
    totals: dict[str, dict[str, Any]] = {}
    for source, field_name in (
        (people_totals(rows, names), "one_to_one_ms"),
        (group_people_totals(rows, names, me), "group_ms"),
    ):
        for person in source:
            key = person["counterpart_id"] or person["name"]
            entry = totals.setdefault(
                key,
                {
                    "counterpart_id": person["counterpart_id"],
                    "name": person["name"],
                    "ms": 0,
                    "count": 0,
                    "one_to_one_ms": 0,
                    "group_ms": 0,
                },
            )
            entry["ms"] += person["ms"]
            entry["count"] += person["count"]
            entry[field_name] += person["ms"]

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
    me: str = "",
) -> dict[str, Any]:
    """Istatistik seridi: dort kutu.

    1. birebir aramalarda en cok gorusulenler,
    2. grup aramalarinda en cok gorusulenler,
    3. ikisinin toplaminda en cok gorusulenler,
    4. birebir / grup dagilimi (adet ve sure).
    """
    rows = list(rows)
    known = names_from_rows(rows) if names is None else names
    moment = now or utc_now()
    since = since_of(days, moment)
    window = [row for row in rows if in_window(row, since)]
    connected = [row for row in window if is_connected(row)]
    total_ms = sum(as_int(row.get("duration_ms")) or 0 for row in connected)

    split: list[dict[str, Any]] = []
    for kind in (KIND_ONE_TO_ONE, KIND_GROUP):
        part = [row for row in window if row.get("kind") == kind]
        talked = [row for row in part if is_connected(row)]
        ms = sum(as_int(row.get("duration_ms")) or 0 for row in talked)
        split.append(
            {
                "kind": kind,
                "label": KIND_LABELS[kind],
                "ms": ms,
                "hours": round(ms / 3600000, 1),
                "percent": round(ms * 100 / total_ms, 1) if total_ms else 0.0,
                "count": len(part),
                "count_percent": round(len(part) * 100 / len(window), 1) if window else 0.0,
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

    return {
        "days": _as_days(days),
        "since": iso_text(since),
        "until": iso_text(moment),
        "calls": len(window),
        "connected": len(connected),
        "total_ms": total_ms,
        "total_text": duration_text(total_ms),
        "top": people_totals(window, known)[:TOP_PEOPLE],
        "group_top": group_people_totals(window, known, me)[:TOP_PEOPLE],
        "combined_top": combined_people_totals(window, known, me)[:TOP_PEOPLE],
        "split": split,
        "direction": directions,
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
    me: str = "",
) -> dict[str, Any]:
    """Bir kisiyle butun birebir gorusmeler + ortak grup aramalari."""
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
        if row.get("kind") == KIND_GROUP
        and (clean_text(row.get("counterpart_id")) == marker or marker in participants_of(row))
    ]

    connected = [row for row in personal if is_connected(row)]
    total_ms = sum(as_int(row.get("duration_ms")) or 0 for row in connected)
    longest = max((as_int(row.get("duration_ms")) or 0 for row in connected), default=0)
    group_ms = sum(
        as_int(row.get("duration_ms")) or 0 for row in shared if is_connected(row)
    )
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
            "group_ms": group_ms,
            "group_text": duration_text(group_ms),
        },
        "calls": [view(row, known, me) for row in sorted(personal, key=_order, reverse=True)],
        "group_calls": [view(row, known, me) for row in sorted(shared, key=_order, reverse=True)],
    }


# --- tarama --------------------------------------------------------------


def my_mri(setting: Any = "", discovered: Any = "") -> str:
    """Kullanicinin kendi kimligi (MRI).

    Sirayla: ayardan elle verilen deger, sonra kaynagin buldugu deger
    (veritabani adindaki kullanici GUID'i). Bulunamazsa bos doner; o zaman
    grup aramalarinda kimse elenmez (yanlis kisiyi elemektense hic elememek).
    """
    manual = as_mri(clean_text(setting))
    if manual:
        return manual
    return as_mri(clean_text(discovered))


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


def scan(
    conn: Any,
    source: CallSource,
    seen_at: str | None = None,
    my_mri_setting: str = "",
) -> dict[str, Any]:
    """Kaynagi okur, normalize eder, `call_id` ile tekillestirerek yazar.

    Ozet yalnizca sayilari degil, verinin NEREDEN geldigini de tasir: en yeni
    aramalar eksikse kullanicinin bunu balonda gormesi gerekir.
    """
    bundle = tuple(source.read())
    # Eski donusler de kabul edilir: eksik parcalar bos gecer.
    calls, names = (*bundle, [], {})[:2]
    found = source_diagnostics(source)
    marker = my_mri(setting=my_mri_setting, discovered=found.get("my_mri"))
    rows = normalize(calls, names, seen_at, marker)
    report = repository.import_calls(conn, rows)
    report["groups"] = sum(1 for row in rows if row["kind"] == KIND_GROUP)
    report["latest_call_at"] = max(
        (row["started_at"] for row in rows if row["started_at"]), default=""
    )
    report.update(found)
    # Teshis sozlugunde de `my_mri` var (kaynagin buldugu); gecerli olan
    # ayarla birlesmis olandir, bu yuzden EN SONDA yazilir.
    report["my_mri"] = marker
    report["my_mri_known"] = bool(marker)
    return report
