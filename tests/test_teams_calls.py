"""Asama 9: Teams arama gecmisi (yerel onbellekten tablo ve istatistik).

Test edilen sorular:

* Bu arama ne kadar surdu? (`durationInMs` yoksa iki yedek yol)
* Ne turden? (birebir / toplanti / grup -- takvim eslesmesi +/- 10 dk)
* Karsi taraf kim ve adi nereden cozuldu?
* Ayni onbellek iki kez taranirsa kopya olusuyor mu? (olusmamali)
* Istatistik: hafta sonu is gununden dusuluyor mu, kacirilan aramalar sureye
  giriyor mu (girmemeli), yuzdeler ve ilk bes dogru mu?
* Windows olmayan makinede uc ne diyor? (`feature_unavailable`)

Gercek IndexedDB hicbir testte acilmaz: `app/teamscalls/fake.py` bellek ici
kaynaktir, ham kayit cozumu ise saf fonksiyonlarla sinanir.
"""

from __future__ import annotations

import io
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from openpyxl import load_workbook

from app import db, repository as repo
from app.teamscalls import CallsError, default_source, intake, teams_cache
from app.teamscalls import source as source_module
from app.teamscalls import attendance
from app.teamscalls.fake import (
    ME,
    PERSON_ONE,
    PERSON_TWO,
    FakeCallSource,
    attended,
    call,
    event,
)
from app.teamscalls.source import (
    DIRECTION_IN,
    DIRECTION_OUT,
    STATE_ACCEPTED,
    STATE_DECLINED,
    STATE_MISSED,
    TYPE_MULTI_PARTY,
    TYPE_TWO_PARTY,
    CalendarRecord,
    CallRecord,
    ThreadRecord,
)

# Cuma 12:00 UTC: pencere hesabi makinenin gunune bagli kalmasin.
NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)

# Gercek bir onbellek sondasindan alinan adlar ve kimlik bicimleri.
CALL_DB = "Teams:call-history-manager:react-web-client:kiraci:kullanici:tr-tr"
CALENDAR_DB = "Teams:calendar:react-web-client:kiraci:kullanici:tr-tr"
PROFILE_DB = "Teams:profiles:react-web-client:kiraci:kullanici:tr-tr"
THREAD_DB = "Teams:conversation-manager:react-web-client:kiraci:kullanici:tr-tr"

MEETING_THREAD = "19:meeting_NGY3ZjkwZDAtMTIzNC00@thread.v2"
GROUP_THREAD = "19:abcdef0123456789abcdef0123456789@thread.v2"


def moment(days: float = 0, hours: float = 0) -> datetime:
    return NOW - timedelta(days=days, hours=hours)


def local_text(when: datetime) -> str:
    """Takvim store'unun yazdigi bicim: yerel saat, saat dilimi eki yok."""
    return when.astimezone().strftime("%Y-%m-%d %H:%M:%S")


# --- sure ----------------------------------------------------------------


def test_duration_uses_the_reported_milliseconds():
    record = call("a", moment(1), minutes=10, duration_ms=612000)
    assert intake.duration_of(record) == 612000


def test_duration_falls_back_to_connect_time():
    started = moment(1)
    record = CallRecord(
        call_id="a",
        start_time=started.isoformat(),
        connect_time=(started + timedelta(minutes=2)).isoformat(),
        end_time=(started + timedelta(minutes=12)).isoformat(),
        duration_ms=None,
    )
    # Baglanti ikinci dakikada kuruldu: gorusme on dakika.
    assert intake.duration_of(record) == 10 * 60000


def test_duration_falls_back_to_start_time_when_there_is_no_connect():
    started = moment(1)
    record = CallRecord(
        call_id="a",
        start_time=started.isoformat(),
        end_time=(started + timedelta(minutes=7)).isoformat(),
    )
    assert intake.duration_of(record) == 7 * 60000


def test_duration_is_zero_when_nothing_can_be_computed():
    assert intake.duration_of(CallRecord(call_id="a")) == 0


def test_a_missed_call_has_no_duration():
    record = CallRecord(call_id="a", start_time=moment(1).isoformat(), state=STATE_MISSED)
    assert intake.duration_of(record) == 0


@pytest.mark.parametrize(
    "ms,text",
    [(0, "—"), (38000, "38 sn"), (5 * 60000, "5 dk"), (72 * 60000, "1 sa 12 dk")],
)
def test_duration_text_is_turkish_and_short(ms, text):
    assert intake.duration_text(ms) == text


# --- tur ucleme -----------------------------------------------------------


def test_two_party_call_is_one_to_one():
    rows = intake.normalize([call("a", moment(1))])
    assert rows[0]["kind"] == intake.KIND_ONE_TO_ONE


def test_multi_party_without_a_meeting_is_a_group_call():
    rows = intake.normalize([call("a", moment(1), call_type=TYPE_MULTI_PARTY)])
    assert rows[0]["kind"] == intake.KIND_GROUP


def test_multi_party_on_a_calendar_slot_is_a_meeting():
    started = moment(1)
    rows = intake.normalize(
        [call("a", started, call_type=TYPE_MULTI_PARTY)],
        [event("Haftalık durum", local_text(started))],
    )
    assert rows[0]["kind"] == intake.KIND_MEETING
    assert rows[0]["meeting_subject"] == "Haftalık durum"
    assert rows[0]["meeting_organizer"] == "Örnek Kişi"
    assert rows[0]["my_response"] == "Accepted"


def test_a_meeting_nine_minutes_off_still_matches():
    started = moment(1)
    rows = intake.normalize(
        [call("a", started, call_type=TYPE_MULTI_PARTY)],
        [event("Gecikmeli toplantı", local_text(started - timedelta(minutes=9)))],
    )
    assert rows[0]["kind"] == intake.KIND_MEETING


def test_a_meeting_eleven_minutes_off_does_not_match():
    started = moment(1)
    rows = intake.normalize(
        [call("a", started, call_type=TYPE_MULTI_PARTY)],
        [event("Başka toplantı", local_text(started - timedelta(minutes=11)))],
    )
    assert rows[0]["kind"] == intake.KIND_GROUP
    assert rows[0]["meeting_subject"] == ""


def test_a_recurring_master_never_matches():
    started = moment(1)
    rows = intake.normalize(
        [call("a", started, call_type=TYPE_MULTI_PARTY)],
        [event("Seri", local_text(started), event_type="RecurringMaster")],
    )
    assert rows[0]["kind"] == intake.KIND_GROUP


def test_an_out_of_office_entry_never_matches():
    started = moment(1)
    rows = intake.normalize(
        [call("a", started, call_type=TYPE_MULTI_PARTY)],
        [event("İzin", local_text(started), show_as="Oof")],
    )
    assert rows[0]["kind"] == intake.KIND_GROUP


def test_the_closest_calendar_entry_wins():
    started = moment(1)
    rows = intake.normalize(
        [call("a", started, call_type=TYPE_MULTI_PARTY)],
        [
            event("Uzak", local_text(started - timedelta(minutes=8))),
            event("Yakın", local_text(started + timedelta(minutes=1))),
        ],
    )
    assert rows[0]["meeting_subject"] == "Yakın"


# --- karsi taraf ve ad cozumu --------------------------------------------


def test_the_counterpart_of_an_outgoing_call_is_the_target():
    rows = intake.normalize(
        [call("a", moment(1), direction=DIRECTION_OUT, other=PERSON_ONE)],
        names={PERSON_ONE: "Örnek Kişi"},
    )
    assert rows[0]["counterpart_id"] == PERSON_ONE
    assert rows[0]["counterpart_name"] == "Örnek Kişi"


def test_the_counterpart_of_an_incoming_call_is_the_originator():
    rows = intake.normalize(
        [call("a", moment(1), direction=DIRECTION_IN, other=PERSON_TWO)],
        names={PERSON_TWO: "İkinci Örnek"},
    )
    assert rows[0]["counterpart_id"] == PERSON_TWO
    assert rows[0]["counterpart_name"] == "İkinci Örnek"


def test_an_unresolved_name_falls_back_to_the_record_but_never_to_the_id():
    rows = intake.normalize([call("a", moment(1), other=PERSON_ONE, other_name="Kayıttaki Ad")])
    assert rows[0]["counterpart_name"] == "Kayıttaki Ad"

    # Hicbir yerden ad cikmadiysa alan BOS kalir: ham kimlik ad yerine gecmez.
    bare = intake.normalize([call("b", moment(1), other=PERSON_ONE)])
    assert bare[0]["counterpart_name"] == ""
    assert PERSON_ONE not in intake.display_party(bare[0])


def test_a_call_without_an_id_is_skipped():
    assert intake.normalize([CallRecord(call_id="")]) == []


def test_missing_fields_do_not_break_the_row():
    rows = intake.normalize([CallRecord(call_id="a")])
    assert rows[0]["started_at"] == ""
    assert rows[0]["duration_ms"] == 0
    assert rows[0]["kind"] == intake.KIND_GROUP
    assert rows[0]["counterpart_name"] == ""
    assert intake.display_party(rows[0]) == intake.GROUP_TITLE


def test_labels_are_turkish():
    rows = intake.normalize(
        [call("a", moment(1), direction=DIRECTION_IN, state=STATE_MISSED)]
    )
    card = intake.view(rows[0])
    assert card["kind_label"] == "Birebir"
    assert card["direction_label"] == "Arandım"
    assert card["state_label"] == "Kaçırılan"
    assert intake.KIND_LABELS[intake.KIND_MEETING] == "Toplantı"
    assert intake.KIND_LABELS[intake.KIND_GROUP] == "Grup araması"
    assert intake.DIRECTION_LABELS[DIRECTION_OUT] == "Aradım"
    assert intake.STATE_LABELS[STATE_DECLINED] == "Reddedilen"


# --- gorunen taraf (ham kimlik ekrana cikmaz) ----------------------------


def test_a_one_to_one_call_shows_the_person():
    rows = intake.normalize(
        [call("a", moment(1), other=PERSON_ONE)], names={PERSON_ONE: "Örnek Kişi"}
    )
    assert intake.display_party(rows[0]) == "Örnek Kişi"


def test_an_unknown_person_shows_the_tail_of_the_id_not_the_id():
    rows = intake.normalize([call("a", moment(1), other=PERSON_ONE)])
    label = intake.display_party(rows[0])
    assert label == "Bilinmeyen kişi (000001)"
    assert "8:orgid:" not in label


def test_an_unknown_person_without_an_id_is_still_readable():
    assert intake.person_label("", "") == intake.UNKNOWN_PERSON


def test_a_meeting_shows_its_subject_not_a_counterpart():
    started = moment(1)
    rows = intake.normalize(
        [call("a", started, call_type=TYPE_MULTI_PARTY, participants=[PERSON_ONE])],
        [event("Bütçe toplantısı", local_text(started))],
    )
    assert intake.display_party(rows[0]) == "Bütçe toplantısı"


def test_a_group_call_shows_the_participants_by_name():
    rows = intake.normalize(
        [call("a", moment(1), call_type=TYPE_MULTI_PARTY, participants=[PERSON_ONE, PERSON_TWO])],
        names={PERSON_ONE: "Örnek Kişi", PERSON_TWO: "İkinci Örnek"},
    )
    assert intake.display_party(rows[0], {PERSON_ONE: "Örnek Kişi", PERSON_TWO: "İkinci Örnek"}) == (
        "Örnek Kişi, İkinci Örnek"
    )


def test_a_crowded_group_call_stops_at_three_names():
    people = [f"8:orgid:kisi-{index}" for index in range(6)]
    names = {person: f"Kişi {index}" for index, person in enumerate(people)}
    rows = intake.normalize(
        [call("a", moment(1), call_type=TYPE_MULTI_PARTY, participants=people)], names=names
    )
    assert intake.display_party(rows[0], names) == "Kişi 0, Kişi 1, Kişi 2 +3"


def test_a_group_call_without_participants_says_what_it_is():
    rows = intake.normalize([call("a", moment(1), call_type=TYPE_MULTI_PARTY)])
    assert intake.display_party(rows[0]) == "Grup araması"


def test_a_group_call_never_shows_a_raw_id():
    rows = intake.normalize(
        [call("a", moment(1), call_type=TYPE_MULTI_PARTY, participants=[PERSON_ONE, PERSON_TWO])]
    )
    label = intake.display_party(rows[0])
    assert "8:orgid:" not in label
    assert label == "Bilinmeyen kişi (000001), Bilinmeyen kişi (000002)"


def test_the_name_map_is_rebuilt_from_the_stored_rows():
    rows = intake.normalize(
        [
            call("bir", moment(1), other=PERSON_ONE),
            call("grup", moment(2), call_type=TYPE_MULTI_PARTY, participants=[PERSON_ONE]),
        ],
        names={PERSON_ONE: "Örnek Kişi"},
    )
    known = intake.names_from_rows(rows)
    assert known == {PERSON_ONE: "Örnek Kişi"}
    # Birebir aramada cozulen ad grup aramasinin basliginda da cikar.
    assert intake.display_party(rows[1], known) == "Örnek Kişi"


def test_the_view_carries_the_display_party_and_the_participant_names():
    rows = intake.normalize(
        [call("a", moment(1), call_type=TYPE_MULTI_PARTY, participants=[PERSON_ONE])],
        names={PERSON_ONE: "Örnek Kişi"},
    )
    card = intake.view(rows[0], {PERSON_ONE: "Örnek Kişi"})
    assert card["title"] == "Örnek Kişi"
    assert card["participant_names"] == ["Örnek Kişi"]


def test_the_search_also_looks_at_participant_names():
    rows = intake.normalize(
        [
            call("grup", moment(1), call_type=TYPE_MULTI_PARTY, participants=[PERSON_TWO]),
            call("baska", moment(2), call_type=TYPE_MULTI_PARTY),
        ],
        names={PERSON_TWO: "İkinci Örnek"},
    )
    known = {PERSON_TWO: "İkinci Örnek"}
    picked = intake.select(rows, q="ikinci", now=NOW, names=known)
    assert [row["call_id"] for row in picked] == ["grup"]


def test_the_person_drawer_titles_shared_meetings_the_same_way():
    started = moment(1)
    rows = intake.normalize(
        [
            call("bir", moment(2), other=PERSON_ONE),
            call("iki", moment(3), other=PERSON_TWO),
            call(
                "grup",
                started,
                call_type=TYPE_MULTI_PARTY,
                participants=[PERSON_ONE, PERSON_TWO],
            ),
        ],
        names={PERSON_ONE: "Örnek Kişi", PERSON_TWO: "İkinci Örnek"},
    )
    data = intake.person_view(rows, PERSON_ONE, days=30, now=NOW)
    assert data["group_calls"][0]["title"] == "Örnek Kişi, İkinci Örnek"
    assert "8:orgid:" not in data["group_calls"][0]["title"]


# --- yalnizca gereken dort veritabani acilir ------------------------------
#
# Sonda 112 veritabanini dolasmak 596 saniye surdu. Tarama ad suzgecini
# `database_ids` uzerinde uygular: digerleri hic ACILMAZ.


class FakeRecord:
    def __init__(self, value, key=None):
        self.value = value
        self.key = key


class FakeStore:
    def __init__(self, name, records=()):
        self.name = name
        self.records = list(records)
        self.reads = 0

    def iterate_records(self):
        self.reads += 1
        return iter(self.records)


class FakeDatabase:
    def __init__(self, stores):
        self.stores = list(stores)

    def __iter__(self):
        return iter(self.stores)


class FakeId:
    def __init__(self, name, number):
        self.name = name
        self.dbid_no = number


class FakeWrapper:
    """ccl `WrappedIndexDB` yerine gecen en kucuk taklit."""

    made: list["FakeWrapper"] = []

    def __init__(self, leveldb, blob=None):
        self.leveldb = leveldb
        self.opened: list[str] = []
        self.closed = False
        FakeWrapper.made.append(self)
        self.databases = {
            1: (CALL_DB, FakeDatabase([
                FakeStore("call-history", [FakeRecord({
                    "callId": "a",
                    "startTime": "2026-09-11T08:30:00Z",
                    "endTime": "2026-09-11T08:40:00Z",
                    "callDirection": "outgoing",
                    "callState": "accepted",
                    "callType": "multiParty",
                    "participantList": [{"id": PERSON_ONE}],
                    "groupChatThreadId": GROUP_THREAD,
                })]),
                FakeStore("call-history-settings", [FakeRecord({"x": 1})]),
            ])),
            2: (CALENDAR_DB, FakeDatabase([
                FakeStore("calendar", [FakeRecord({
                    "startTime": datetime(2026, 9, 11, 11, 30),
                    "subject": "Toplantı",
                })]),
            ])),
            3: (PROFILE_DB, FakeDatabase([
                FakeStore("profiles", [FakeRecord({"mri": PERSON_ONE, "displayName": "Örnek Kişi"})]),
            ])),
            4: (THREAD_DB, FakeDatabase([
                FakeStore("conversations", [FakeRecord(
                    {"id": GROUP_THREAD, "threadProperties": {"topic": "Proje ekibi"}}
                )]),
            ])),
            5: ("Teams:call-history-sync-state-manager:react-web-client:k:u:tr-tr",
                FakeDatabase([FakeStore("call-history", [FakeRecord({"callId": "olmaz"})])])),
            6: ("Teams:messages:react-web-client:k:u:tr-tr",
                FakeDatabase([FakeStore("messages", [FakeRecord({"x": 1})])])),
        }

    @property
    def database_ids(self):
        return [FakeId(name, number) for number, (name, _) in self.databases.items()]

    def __getitem__(self, number):
        name, database = self.databases[number]
        self.opened.append(name)
        return database

    def close(self):
        self.closed = True


@pytest.fixture
def fake_reader(monkeypatch):
    FakeWrapper.made = []
    module = type("FakeCcl", (), {"WrappedIndexDB": FakeWrapper})
    monkeypatch.setattr(teams_cache, "load_reader", lambda: module)
    return FakeWrapper


def test_only_the_four_needed_databases_are_opened(fake_reader, tmp_path):
    source = teams_cache.TeamsCacheSource(cache_path=str(tmp_path), copy_first=False)
    bundle = source._read_all(tmp_path, None)
    opened = fake_reader.made[0].opened
    assert opened == [CALL_DB, CALENDAR_DB, PROFILE_DB, THREAD_DB]
    assert bundle.databases == 4
    assert fake_reader.made[0].closed is True


def test_the_read_collects_all_four_kinds(fake_reader, tmp_path):
    source = teams_cache.TeamsCacheSource(cache_path=str(tmp_path), copy_first=False)
    bundle = source._read_all(tmp_path, None)
    assert [item.call_id for item in bundle.calls] == ["a"]
    assert [item.subject for item in bundle.calendar] == ["Toplantı"]
    assert bundle.names == {PERSON_ONE: "Örnek Kişi"}
    assert [item.topic for item in bundle.threads] == ["Proje ekibi"]


def test_the_scan_reports_how_long_the_read_took(fake_reader, tmp_path, conn):
    cache = tmp_path / "leveldb"
    cache.mkdir()
    source = teams_cache.TeamsCacheSource(cache_path=str(cache), copy_first=False)
    result = intake.scan(conn, source)
    assert result["databases"] == 4
    assert result["read_ms"] >= 0
    assert result["source"] == "live"
    # Grup sohbetinin adi sohbet kaydindan geldi.
    assert repo.list_calls(conn)[0]["topic"] == "Proje ekibi"


def test_a_sibling_store_with_the_same_name_is_never_read(fake_reader, tmp_path):
    source = teams_cache.TeamsCacheSource(cache_path=str(tmp_path), copy_first=False)
    bundle = source._read_all(tmp_path, None)
    # `call-history-sync-state-manager` icinde de "call-history" store'u var.
    assert [item.call_id for item in bundle.calls] == ["a"]


def test_the_other_stores_of_a_wanted_database_are_left_alone(fake_reader, tmp_path):
    source = teams_cache.TeamsCacheSource(cache_path=str(tmp_path), copy_first=False)
    source._read_all(tmp_path, None)
    stores = {store.name: store for store in fake_reader.made[0].databases[1][1].stores}
    assert stores["call-history"].reads == 1
    assert stores["call-history-settings"].reads == 0


# --- kopyalama: kilitli dosya, eksik kopya, canli yedek -------------------
#
# Saha bulgusu: Teams acikken en yeni aramalar kopyaya girmiyordu. Sebep,
# henuz `.ldb`ye sikistirilmamis yazma gunlugunun (`*.log`) kilitli kalmasi.
# Bu bolum o kararlari koruyor.


@pytest.mark.parametrize(
    "name,kind",
    [
        ("000123.log", "log"),
        ("000045.ldb", "ldb"),
        ("000045.sst", "ldb"),
        ("MANIFEST-000002", "manifest"),
        ("CURRENT", "current"),
        ("LOCK", "lock"),
        ("bir-sey.txt", "other"),
    ],
)
def test_leveldb_files_are_classified(name, kind):
    assert teams_cache.file_kind(name) == kind


@pytest.mark.parametrize("name", ["000123.log", "MANIFEST-000002", "CURRENT"])
def test_these_files_make_a_copy_incomplete(name):
    assert teams_cache.is_critical(name) is True


@pytest.mark.parametrize("name", ["000045.ldb", "LOCK", "bir-sey.txt"])
def test_these_files_do_not(name):
    assert teams_cache.is_critical(name) is False


def report_with(*names: str) -> "teams_cache.CopyReport":
    return teams_cache.CopyReport(
        target=Path("kopya"), copied=3, skipped=len(names), skipped_names=list(names)
    )


def test_a_skipped_write_log_marks_the_copy_incomplete():
    assert report_with("000123.log").is_incomplete is True
    assert report_with("LOCK").is_incomplete is False
    assert report_with().is_incomplete is False


def test_the_skipped_files_are_summarised_by_kind():
    report = report_with("000123.log", "000124.log", "LOCK")
    assert report.kinds() == {"log": 2, "lock": 1}
    assert report.critical == ["000123.log", "000124.log"]


def test_a_locked_file_is_skipped_named_and_counted(tmp_path, monkeypatch):
    source = tmp_path / "leveldb"
    source.mkdir()
    (source / "000123.log").write_bytes(b"yeni veri")
    (source / "000045.ldb").write_bytes(b"eski veri")

    def stubborn(src, dst):
        if Path(src).name.endswith(".log"):
            raise PermissionError("kilitli")
        Path(dst).write_bytes(Path(src).read_bytes())

    def refuse(src, dst):
        raise PermissionError("paylaşımlı okuma da olmadı")

    monkeypatch.setattr(teams_cache, "copy_stream", stubborn)
    monkeypatch.setattr(teams_cache, "copy_shared", refuse)

    report = teams_cache.copy_tree(source, tmp_path / "kopya")
    assert report.copied == 1
    assert report.skipped == 1
    assert report.skipped_names == ["000123.log"]
    assert report.kinds() == {"log": 1}
    assert report.is_incomplete is True


def test_the_shared_read_rescues_a_file_that_normal_open_cannot(tmp_path, monkeypatch):
    source = tmp_path / "leveldb"
    source.mkdir()
    (source / "000123.log").write_bytes(b"yeni veri")

    def locked(src, dst):
        raise PermissionError("kilitli")

    def shared(src, dst):
        Path(dst).write_bytes(Path(src).read_bytes())

    monkeypatch.setattr(teams_cache, "copy_stream", locked)
    monkeypatch.setattr(teams_cache, "copy_shared", shared)

    report = teams_cache.copy_tree(source, tmp_path / "kopya")
    assert report.copied == 1
    assert report.skipped == 0
    assert (tmp_path / "kopya" / "000123.log").read_bytes() == b"yeni veri"


def test_the_shared_read_is_windows_only(monkeypatch):
    monkeypatch.setattr(teams_cache.sys, "platform", "linux")
    with pytest.raises(OSError):
        teams_cache.copy_shared("a", "b")


def reading_source(tmp_path, monkeypatch, report, failing: set[str] = frozenset()):
    """Kopyalamayi ve okumayi sahteleyen bir kaynak + okunan yollar listesi."""
    cache = tmp_path / "leveldb"
    cache.mkdir()
    source = teams_cache.TeamsCacheSource(cache_path=str(cache))
    monkeypatch.setattr(teams_cache, "copy_tree", lambda src, dst: report)
    seen: list[str] = []

    def fake_read_all(leveldb, blob):
        name = "live" if Path(leveldb) == cache else "copy"
        seen.append(name)
        if name in failing:
            raise RuntimeError("okunamadı")
        return ([], [], {})

    monkeypatch.setattr(source, "_read_all", fake_read_all)
    return source, seen, cache


def test_a_complete_copy_is_read_from_the_copy(tmp_path, monkeypatch):
    report = report_with("LOCK")
    report.target = tmp_path / "kopya"
    source, seen, _ = reading_source(tmp_path, monkeypatch, report)
    source.read()
    assert seen == ["copy"]
    assert source.diagnostics()["source"] == "copy"
    assert source.diagnostics()["warning"] == ""
    assert source.diagnostics()["skipped_kinds"] == {"lock": 1}


def test_an_incomplete_copy_falls_back_to_the_live_folder(tmp_path, monkeypatch):
    report = report_with("000123.log")
    report.target = tmp_path / "kopya"
    source, seen, _ = reading_source(tmp_path, monkeypatch, report)
    source.read()
    # Once canli klasor: en yeni aramalar kopyada yok.
    assert seen == ["live"]
    assert source.diagnostics()["source"] == "live"
    assert source.diagnostics()["warning"] == ""


def test_when_the_live_read_fails_too_the_copy_is_used_with_a_warning(tmp_path, monkeypatch):
    report = report_with("000123.log", "MANIFEST-000002")
    report.target = tmp_path / "kopya"
    source, seen, _ = reading_source(tmp_path, monkeypatch, report, failing={"live"})
    source.read()
    assert seen == ["live", "copy"]
    info = source.diagnostics()
    assert info["source"] == "copy"
    assert "kopyalanamadı" in info["warning"]
    assert "Teams'i kapatıp" in info["warning"]
    assert info["skipped_kinds"] == {"log": 1, "manifest": 1}


def test_a_broken_complete_copy_still_tries_the_live_folder(tmp_path, monkeypatch):
    report = report_with()
    report.target = tmp_path / "kopya"
    source, seen, _ = reading_source(tmp_path, monkeypatch, report, failing={"copy"})
    source.read()
    assert seen == ["copy", "live"]
    assert source.diagnostics()["source"] == "live"


def test_when_nothing_can_be_read_the_user_is_told(tmp_path, monkeypatch):
    report = report_with("000123.log")
    report.target = tmp_path / "kopya"
    source, _, _ = reading_source(tmp_path, monkeypatch, report, failing={"live", "copy"})
    with pytest.raises(CallsError) as caught:
        source.read()
    assert caught.value.code == "calls_read_failed"


# --- tekrarlayan toplantilar (seri kaydi) ---------------------------------
#
# Saha: sabah daily'leri eslesmiyordu. Sebep, `RecurringMaster` kayitlarinin
# tamamen atilmasiydi; tekrarlayan toplanti takvimde cogu zaman YALNIZCA seri
# kaydi olarak duruyor, olusumlar ayri kayit degil.


def master(subject: str, start=None, cid: str = MEETING_THREAD, cancelled: bool = False):
    record = event(
        subject,
        local_text(start or moment(200)),  # seri kaydinin tarihi: serinin ilk gunu
        event_type="RecurringMaster",
        cid=cid,
    )
    record.is_cancelled = cancelled
    return record


def test_a_recurring_master_matches_by_thread_id():
    """Saat tutmasa da kimlik tutuyorsa tekrarlayan toplanti eslesir."""
    rows = intake.normalize(
        [call("a", moment(1), call_type=TYPE_MULTI_PARTY, thread_id=MEETING_THREAD)],
        [master("Sabah daily")],
    )
    assert rows[0]["kind"] == intake.KIND_MEETING
    assert rows[0]["meeting_subject"] == "Sabah daily"


def test_a_recurring_master_still_never_matches_by_time():
    """Seri kaydinin tarihi serinin ilk gunudur; saat yakinligi anlamsiz."""
    started = moment(1)
    rows = intake.normalize(
        [call("a", started, call_type=TYPE_MULTI_PARTY)],
        [master("Sabah daily", start=started, cid="")],
    )
    assert rows[0]["kind"] == intake.KIND_GROUP


def test_a_cancelled_master_still_matches_by_thread_id():
    """Iptal edilmis seriye ait GECMIS aramalar var; kimlik yine tutar."""
    rows = intake.normalize(
        [call("a", moment(1), call_type=TYPE_MULTI_PARTY, thread_id=MEETING_THREAD)],
        [master("İptal edilmiş seri", cancelled=True)],
    )
    assert rows[0]["kind"] == intake.KIND_MEETING
    assert rows[0]["meeting_subject"] == "İptal edilmiş seri"


def test_a_cancelled_occurrence_still_stays_out_of_the_time_match():
    started = moment(1)
    cancelled = event("İptal", local_text(started))
    cancelled.is_cancelled = True
    rows = intake.normalize([call("a", started, call_type=TYPE_MULTI_PARTY)], [cancelled])
    assert rows[0]["kind"] == intake.KIND_GROUP


def test_the_group_chat_thread_is_compared_with_the_cid_too():
    rows = intake.normalize(
        [call("a", moment(1), call_type=TYPE_MULTI_PARTY, group_thread_id=MEETING_THREAD)],
        [master("Ekip toplantısı")],
    )
    assert rows[0]["kind"] == intake.KIND_MEETING
    assert rows[0]["meeting_subject"] == "Ekip toplantısı"


def test_the_scan_counts_the_recurring_matches(conn):
    source = FakeCallSource(
        calls=[
            call("seri", moment(1), call_type=TYPE_MULTI_PARTY, thread_id=MEETING_THREAD),
            call("tek", moment(2), call_type=TYPE_MULTI_PARTY, thread_id=GROUP_THREAD),
        ],
        calendar=[
            master("Sabah daily"),
            event("Tek seferlik", local_text(moment(2)), cid=GROUP_THREAD),
        ],
    )
    result = intake.scan(conn, source)
    assert result["meetings_matched"] == 2
    assert result["recurring_matched"] == 1


def test_the_event_type_is_not_stored_on_the_row(conn):
    """Seri sayaci gecici bir alandir; tabloda sutunu yok."""
    intake.scan(
        conn,
        FakeCallSource(
            calls=[call("a", moment(1), call_type=TYPE_MULTI_PARTY, thread_id=MEETING_THREAD)],
            calendar=[master("Sabah daily")],
        ),
    )
    assert "matched_event_type" not in repo.get_call(conn, "a")


# --- teshis: eslesmeyenler dokumu ----------------------------------------


def unmatched_rows():
    return intake.normalize(
        [
            # Kimligi var, takvimde karsiligi yok.
            call("kimlikli", moment(1), call_type=TYPE_MULTI_PARTY, thread_id=MEETING_THREAD),
            # Hic kimlik tasimiyor.
            call("kimliksiz", moment(2), call_type=TYPE_MULTI_PARTY, participants=[PERSON_ONE]),
            # Birebir arama teshise hic girmez.
            call("birebir", moment(1), other=PERSON_ONE),
        ],
        names={PERSON_ONE: "Örnek Kişi"},
    )


def test_the_diagnosis_only_covers_unmatched_group_calls():
    data = intake.diagnose_unmatched(unmatched_rows(), [], days=30, now=NOW)
    assert [item["call_id"] for item in data["calls"]] == ["kimlikli", "kimliksiz"]
    assert data["summary"]["unmatched"] == 2


def test_a_call_without_a_thread_id_says_so():
    data = intake.diagnose_unmatched(unmatched_rows(), [], days=30, now=NOW)
    entry = next(item for item in data["calls"] if item["call_id"] == "kimliksiz")
    assert entry["reason"] == "no_thread_id"
    assert entry["participants"] == ["Örnek Kişi"]
    assert data["summary"]["no_thread_id"] == 1


def test_a_call_whose_core_is_missing_from_the_calendar_says_so():
    data = intake.diagnose_unmatched(unmatched_rows(), [], days=30, now=NOW)
    entry = next(item for item in data["calls"] if item["call_id"] == "kimlikli")
    assert entry["reason"] == "no_calendar_with_core"
    assert entry["thread_core"] == intake.thread_core(MEETING_THREAD)
    assert data["summary"]["core_not_in_calendar"] == 1


def test_a_near_miss_reports_the_gap_in_minutes():
    rows = intake.normalize(
        [call("a", moment(1), call_type=TYPE_MULTI_PARTY, thread_id=MEETING_THREAD)]
    )
    near = event("Yakın toplantı", local_text(moment(1) + timedelta(minutes=42)))
    data = intake.diagnose_unmatched(rows, [near], days=30, now=NOW)
    entry = data["calls"][0]
    assert entry["reason"].startswith("only_time_gap:")
    assert entry["reason"].endswith("42")
    assert entry["candidates"][0]["subject"] == "Yakın toplantı"
    assert entry["candidates"][0]["gap_minutes"] == 42


def test_the_diagnosis_says_which_calls_a_rescan_would_fix():
    rows = intake.normalize(
        [call("a", moment(1), call_type=TYPE_MULTI_PARTY, thread_id=MEETING_THREAD)]
    )
    data = intake.diagnose_unmatched(rows, [master("Sabah daily")], days=30, now=NOW)
    entry = data["calls"][0]
    assert entry["reason"] == "matches_now"
    assert entry["would_match"]["subject"] == "Sabah daily"
    assert data["summary"]["matched_after_fix"] == 1


def test_the_candidate_list_stops_at_three():
    rows = intake.normalize(
        [call("a", moment(1), call_type=TYPE_MULTI_PARTY, thread_id=GROUP_THREAD)]
    )
    events = [
        event(f"Toplantı {index}", local_text(moment(1) + timedelta(minutes=20 + index)))
        for index in range(6)
    ]
    data = intake.diagnose_unmatched(rows, events, days=30, now=NOW)
    assert len(data["calls"][0]["candidates"]) == 3
    # En yakindan uzaga siralanir.
    gaps = [item["gap_minutes"] for item in data["calls"][0]["candidates"]]
    assert gaps == sorted(gaps)


def test_the_diagnosis_endpoint_answers(api_client, fake_calls):
    fake_calls.calls = [
        call("kimliksiz", moment(1), call_type=TYPE_MULTI_PARTY, participants=[PERSON_ONE]),
        call("birebir", moment(2), other=PERSON_ONE),
    ]
    api_client.post("/api/calls/scan")
    data = api_client.get("/api/calls/unmatched?days=90").json()
    assert data["summary"]["unmatched"] == 1
    assert data["summary"]["no_thread_id"] == 1
    assert data["calls"][0]["reason"] == "no_thread_id"
    assert data["calls"][0]["duration_text"]


def test_the_diagnosis_endpoint_needs_the_cache(api_client, context, monkeypatch):
    from app import teamscalls

    monkeypatch.setattr("app.teamscalls.is_supported", lambda: False)
    context.calls_factory = teamscalls.default_source
    response = api_client.get("/api/calls/unmatched")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "feature_unavailable"


# --- takvim saat dilimi ---------------------------------------------------
#
# Saha: takvim adaylari uc saat geride gorunuyordu. ccl `datetime` nesnesini
# saat dilimsiz veriyor ama degeri UTC; biz yerel saat saniyorduk.


def test_a_naive_calendar_datetime_is_read_as_utc():
    naive = datetime(2026, 9, 11, 7, 0)  # ccl boyle verir: saat dilimsiz UTC
    moment = source_module.parse_local(naive)
    assert moment == datetime(2026, 9, 11, 7, 0, tzinfo=timezone.utc)
    # Yerel saat sanilsaydi an UTC ofseti kadar kayardi.
    assert moment.timestamp() == naive.replace(tzinfo=timezone.utc).timestamp()


def test_a_calendar_epoch_is_read_as_utc():
    assert source_module.parse_local(1789000000000) == datetime.fromtimestamp(
        1789000000, tz=timezone.utc
    )


def test_a_calendar_text_is_still_local():
    """Disa aktarimlardan gelen metin bicimi yerel saattir."""
    moment = source_module.parse_local("2026-09-11 10:00:00")
    assert moment == datetime(2026, 9, 11, 10, 0).astimezone()


def test_a_meeting_at_ten_matches_a_call_at_ten():
    """Gercek hata buydu: 10:00 toplantisi 07:00 gorunup hic eslesmiyordu."""
    started = datetime(2026, 9, 11, 10, 0).astimezone()
    utc_naive = started.astimezone(timezone.utc).replace(tzinfo=None)
    meeting = CalendarRecord(start_time=utc_naive, subject="Hayat Daily")
    rows = intake.normalize(
        [call("a", started, call_type=TYPE_MULTI_PARTY)], [meeting]
    )
    assert rows[0]["kind"] == intake.KIND_MEETING
    assert rows[0]["meeting_subject"] == "Hayat Daily"


def test_the_candidate_hour_is_reported_in_real_time():
    started = datetime(2026, 9, 11, 10, 0).astimezone()
    meeting = CalendarRecord(
        start_time=started.astimezone(timezone.utc).replace(tzinfo=None),
        subject="SurfacePlus",
    )
    view = intake.event_view(meeting)
    assert view["start_time"] == started.astimezone(timezone.utc).isoformat()


# --- grup sohbeti aramalari (takvimde karsiligi beklenmez) ---------------

CHAT_THREAD = "19:67f9abee1234567890abcdef12345678@thread.v2"


@pytest.mark.parametrize(
    "thread_id,kind",
    [
        (MEETING_THREAD, "meeting"),
        (CHAT_THREAD, "group_chat"),
        ("19:kisa@thread.v2", "other"),
        ("", ""),
    ],
)
def test_a_thread_id_is_classified(thread_id, kind):
    assert intake.core_kind(thread_id) == kind


def test_a_meeting_thread_wins_over_a_chat_thread():
    row = {"thread_id": MEETING_THREAD, "group_thread_id": CHAT_THREAD}
    assert intake.thread_kind(row) == "meeting"


def test_a_group_chat_call_is_not_counted_as_a_problem():
    rows = intake.normalize(
        [
            call("sohbet", moment(1), call_type=TYPE_MULTI_PARTY, group_thread_id=CHAT_THREAD),
            call("toplanti", moment(2), call_type=TYPE_MULTI_PARTY, thread_id=MEETING_THREAD),
        ]
    )
    data = intake.diagnose_unmatched(rows, [], days=30, now=NOW)
    chat = next(item for item in data["calls"] if item["call_id"] == "sohbet")
    assert chat["reason"] == "group_chat_thread"
    assert chat["thread_kind"] == "group_chat"

    assert data["summary"]["unmatched"] == 2
    assert data["summary"]["group_chat"] == 1
    # Bakilmasi gereken yalnizca toplanti cekirdekli olan.
    assert data["summary"]["suspicious"] == 1
    assert data["summary"]["core_not_in_calendar"] == 1


def test_the_row_carries_its_thread_kind_to_the_screen():
    rows = intake.normalize(
        [call("a", moment(1), call_type=TYPE_MULTI_PARTY, group_thread_id=CHAT_THREAD)]
    )
    assert intake.view(rows[0])["thread_kind"] == "group_chat"


def test_the_badge_ignores_group_chat_calls(api_client, fake_calls):
    fake_calls.calls = [
        call("sohbet", moment(1), call_type=TYPE_MULTI_PARTY, group_thread_id=CHAT_THREAD),
        call("kimliksiz", moment(2), call_type=TYPE_MULTI_PARTY),
    ]
    api_client.post("/api/calls/scan")
    kinds = [item["thread_kind"] for item in api_client.get("/api/calls?days=90").json()["calls"]]
    assert sorted(kinds) == ["", "group_chat"]

    summary = api_client.get("/api/calls/unmatched?days=90").json()["summary"]
    assert summary["group_chat"] == 1
    assert summary["suspicious"] == 1


# --- toplanti sohbetinden katilim ----------------------------------------
#
# Saha: takvimden katilinan planli toplantilar `call-history`de HIC yok.
# Katilimin kendisi toplanti sohbetindeki `<partlist type="ended">`
# mesajinda: kim, kac saniye kaldi.

MY_GUID = "0f8fad5b-d9cb-469f-a165-70867728950e"
MY_MRI = "8:orgid:" + MY_GUID
TENANT_GUID = "11111111-1111-1111-1111-111111111111"

ENDED_XML = (
    '<partlist alt="" type="ended" callId="cagri-1">'
    '<part identity="{me}"><name>Ben</name><duration>1863</duration></part>'
    '<part identity="{other}"><name>Örnek Kişi</name><duration>1800</duration></part>'
    "</partlist>"
).format(me=MY_MRI, other=PERSON_ONE)

STARTED_XML = (
    '<partlist alt="" type="started" callId="cagri-1">'
    f'<part identity="{MY_MRI}"><name>Ben</name></part>'
    "</partlist>"
)


def chain(content: str, thread: str = "19:meeting_ABC123456789@thread.v2") -> dict:
    """`replychains` kaydinin gercekteki bicimi."""
    return {
        "conversationId": thread,
        "messageMap": {
            f"{MY_MRI}_1": {
                "messageType": "Event/Call",
                "content": content,
                "originalArrivalTime": 1789000000000,
                "isSentByCurrentUser": False,
            }
        },
    }


def test_an_ended_partlist_becomes_attendance():
    record = attendance.attendance_of(
        {"messageType": "Event/Call", "content": ENDED_XML, "originalArrivalTime": 1789000000000},
        "19:meeting_ABC123456789@thread.v2",
    )
    assert record.call_id == "cagri-1"
    assert [(part.mri, part.seconds) for part in record.parts] == [
        (MY_MRI, 1863),
        (PERSON_ONE, 1800),
    ]


def test_a_started_partlist_creates_no_row():
    """`started` sure tasimaz; islenirse her toplanti iki kez sayilirdi."""
    record = attendance.attendance_of({"messageType": "Event/Call", "content": STARTED_XML})
    assert record.kind == "started"
    plan = intake.plan_attendance([record], MY_MRI)
    assert plan.rows == []
    assert plan.decisions[0]["decision"] == "skipped:started"


def test_a_broken_partlist_falls_back_to_the_regex():
    broken = ENDED_XML.replace("<name>Ben</name>", "<name>A & B</name>")
    block = attendance.parse_partlist(broken)
    assert block.kind == "ended"
    assert block.call_id == "cagri-1"
    assert [part.seconds for part in block.parts] == [1863, 1800]


def test_a_message_without_a_partlist_is_not_attendance():
    assert attendance.attendance_of({"messageType": "Text", "content": "merhaba"}) is None
    assert attendance.attendance_of({"messageType": "Event/Call", "content": ""}) is None


def test_only_meeting_threads_are_opened():
    """270 bin kayit var: toplanti olmayan sohbetin mesaj haritasi acilmaz."""
    assert attendance.attendance_from_record(chain(ENDED_XML))
    assert attendance.attendance_from_record(chain(ENDED_XML, thread=CHAT_THREAD)) == []
    assert attendance.is_meeting_thread("19:meeting_x@thread.v2") is True
    assert attendance.is_meeting_thread(CHAT_THREAD) is False


def test_the_name_inside_the_xml_is_never_kept():
    record = attendance.attendance_from_record(chain(ENDED_XML))[0]
    assert "Ben" not in json.dumps([part.__dict__ for part in record.parts], ensure_ascii=False)


# --- kendi kimligim -------------------------------------------------------


DB_WITH_USER = f"Teams:calendar:react-web-client:{TENANT_GUID}:{MY_GUID}:tr-tr"
DB_WITH_MRI = f"Teams:anonymoususersmanager:react-web-client:{TENANT_GUID}:{MY_MRI}:tr-tr"


def test_my_mri_comes_from_the_database_name():
    """Ad `Teams:<rol>:react-web-client:<kiraci>:<kullanici>:<dil>` bicimindedir."""
    assert source_module.mri_from_database_name(DB_WITH_USER) == MY_MRI


def test_some_database_names_carry_the_mri_outright():
    assert source_module.mri_from_database_name(DB_WITH_MRI) == MY_MRI


def test_a_database_name_without_an_identity_gives_nothing():
    assert source_module.mri_from_database_name("Teams:messages:react-web-client:a:b:tr") == ""


def test_my_mri_comes_from_my_own_message():
    """`isSentByCurrentUser` isaretli mesajin `creator` alani benim."""
    record = {
        "conversationId": "19:meeting_x@thread.v2",
        "messageMap": {
            "a": {"creator": PERSON_ONE, "isSentByCurrentUser": False},
            "b": {"creator": MY_MRI, "isSentByCurrentUser": True},
        },
    }
    assert attendance.sender_mri(record) == MY_MRI
    assert attendance.sender_mri({"messageMap": {}}) == ""


def test_the_setting_wins_over_the_discovered_identity():
    assert intake.my_mri(setting=MY_GUID, discovered=PERSON_ONE) == MY_MRI
    assert intake.my_mri(discovered=MY_MRI) == MY_MRI
    assert intake.my_mri() == ""


def test_the_call_history_participant_id_is_not_used_any_more():
    """Sahada hicbir katilimci listesinde bulunamadi: yanlis kaynakti."""
    record = teams_cache.call_from_value({"callId": "a", "userParticipantId": MY_GUID})
    assert not hasattr(record, "user_participant_id")


def test_without_an_identity_no_attendance_row_is_made():
    record = fake_attended()
    assert intake.normalize_attendance([record], "") == []


# --- katilim -> satir -----------------------------------------------------


def fake_attended(seconds: int = 1863, mine: bool = True, call_id: str = "cagri-1"):
    parts = [(PERSON_ONE, 1800)]
    if mine:
        parts.insert(0, (MY_MRI, seconds))
    return attended(
        "19:meeting_ABC123456789@thread.v2",
        NOW - timedelta(days=1),
        parts=parts,
        call_id=call_id,
    )


def test_attendance_becomes_a_meeting_row():
    rows = intake.normalize_attendance(
        [fake_attended()], MY_MRI, names={PERSON_ONE: "Örnek Kişi"}
    )
    assert len(rows) == 1
    row = rows[0]
    assert row["kind"] == intake.KIND_MEETING
    assert row["source"] == "chat"
    assert row["duration_ms"] == 1863 * 1000
    assert row["call_id"] == "cagri-1"
    assert intake.duration_text(row["duration_ms"]) == "31 dk"
    # Baslangic, bitisten kendi surem kadar oncesi.
    from app.teamscalls.source import parse_utc

    span = parse_utc(row["ended_at"]) - parse_utc(row["started_at"])
    assert span == timedelta(seconds=1863)


def test_the_duration_is_my_own_not_the_meetings():
    rows = intake.normalize_attendance([fake_attended(seconds=600)], MY_MRI)
    assert rows[0]["duration_ms"] == 600 * 1000


def test_no_duration_means_no_row():
    """"En uzun part" yedegi KALDIRILDI: katilmadigim toplantiyi listeliyordu."""
    plan = intake.plan_attendance([fake_attended(seconds=0)], MY_MRI)
    assert plan.rows == []
    assert plan.decisions[0]["decision"] == "skipped:no_duration"


def test_a_meeting_i_did_not_attend_makes_no_row():
    """"Kabul edip gitmedigim" toplanti: katilimci listesinde yokum."""
    assert intake.normalize_attendance([fake_attended(mine=False)], MY_MRI) == []


def test_the_participants_carry_their_seconds():
    rows = intake.normalize_attendance([fake_attended()], MY_MRI, names={PERSON_ONE: "Örnek Kişi"})
    people = json.loads(rows[0]["participants_json"])
    assert people[1] == {"id": PERSON_ONE, "name": "Örnek Kişi", "seconds": 1800}


def test_the_calendar_subject_is_attached_by_thread():
    rows = intake.normalize_attendance(
        [fake_attended()],
        MY_MRI,
        events=[event("Sabah daily", local_text(moment(5)),
                      event_type="RecurringMaster", cid="19:meeting_ABC123456789@thread.v2")],
    )
    assert rows[0]["meeting_subject"] == "Sabah daily"


def test_a_call_without_an_id_gets_one_from_the_thread_and_time():
    rows = intake.normalize_attendance([fake_attended(call_id="")], MY_MRI)
    assert rows[0]["call_id"].startswith("19:meeting_ABC123456789@thread.v2:")


def test_a_call_already_in_the_history_is_not_added_twice():
    rows = intake.normalize_attendance(
        [fake_attended()], MY_MRI, known_ids={"cagri-1"}
    )
    assert rows == []


def test_the_scan_merges_both_sources(conn):
    source = FakeCallSource(
        calls=[call("gecmis", moment(2))],
        attendance=[fake_attended()],
        my_mri=MY_MRI,
    )
    result = intake.scan(conn, source)
    assert result["from_chat"] == 1
    assert result["my_mri_known"] is True
    sources = {row["call_id"]: row["source"] for row in repo.list_calls(conn)}
    assert sources == {"gecmis": "history", "cagri-1": "chat"}


def test_a_history_call_beats_the_chat_record(conn):
    """Ayni `callId` gecmiste varsa sohbet kaydi eklenmez."""
    history = call("cagri-1", moment(1), call_type=TYPE_MULTI_PARTY)
    result = intake.scan(
        conn,
        FakeCallSource(calls=[history], attendance=[fake_attended()], my_mri=MY_MRI),
    )
    assert result["from_chat"] == 0
    assert repo.get_call(conn, "cagri-1")["source"] == "history"


def test_a_chat_record_survives_a_second_scan(conn):
    source = FakeCallSource(attendance=[fake_attended()])
    intake.scan(conn, source, my_mri_setting=MY_MRI)
    intake.scan(conn, source, my_mri_setting=MY_MRI)
    assert repo.call_count(conn) == 1


def test_chat_meetings_count_in_the_statistics():
    rows = intake.normalize_attendance([fake_attended()], MY_MRI)
    stats = intake.build_stats(rows, days=30, now=NOW)
    assert stats["total_ms"] == 1863 * 1000
    slices = {item["kind"]: item for item in stats["split"]}
    assert slices[intake.KIND_MEETING]["count"] == 1
    # Yon kavrami yok: aradim/arandim sayimina girmez.
    assert stats["direction"]["outgoing"]["count"] == 0
    assert stats["direction"]["incoming"]["count"] == 0


def test_the_row_says_where_it_came_from():
    rows = intake.normalize_attendance([fake_attended()], MY_MRI)
    card = intake.view(rows[0])
    assert card["source"] == "chat"
    assert card["source_label"] == "Sohbetten"


def test_the_api_shows_chat_meetings(api_client, fake_calls):
    fake_calls.calls = []
    fake_calls.attendance = [fake_attended()]
    api_client.put("/api/settings", json={"calls.my_mri": MY_MRI})
    result = api_client.post("/api/calls/scan").json()
    assert result["from_chat"] == 1

    data = api_client.get("/api/calls?days=90").json()
    assert data["calls"][0]["source"] == "chat"
    assert data["calls"][0]["source_label"] == "Sohbetten"


def test_attendance_older_than_the_window_is_dropped():
    old = attended(
        "19:meeting_ABC123456789@thread.v2",
        NOW - timedelta(days=200),
        parts=[(MY_MRI, 600)],
    )
    kept = attendance.within([old], intake.since_of(90, NOW))
    assert kept == []


# --- yeni partlist bicimi (gercek onbellek) ------------------------------
#
# Sahadaki 9.035 `Event/Call` mesajinin HICBIRINDE `type` ozniteligi yok:
# olay turu `<calleventtype>`, toplanti bilgisi `<meetingdetails>` altinda.

NEW_XML = (
    '<partlist alt="Toplantı sona erdi">'
    "<calleventtype>ended</calleventtype>"
    "<callid>cagri-yeni</callid>"
    "<ended>2026-09-11T09:31:03Z</ended>"
    "<meetingdetails>"
    "<icaluid>ICAL-42</icaluid>"
    "<starttime>2026-09-11T09:00:00Z</starttime>"
    "<endtime>2026-09-11T09:31:03Z</endtime>"
    "<meetingtype>Scheduled</meetingtype>"
    "<organizerupn>ornek@example.com</organizerupn>"
    "</meetingdetails>"
    '<part identity="{me}"><name>Ben</name><duration>1863</duration></part>'
    "<part><identity>{other}</identity><displayname>Örnek Kişi</displayname>"
    "<duration>1800</duration></part>"
    "</partlist>"
).format(me=MY_MRI, other=PERSON_ONE)

NEW_STARTED_XML = (
    '<partlist alt="Toplantı başladı">'
    "<calleventtype>started</calleventtype>"
    f'<part identity="{MY_MRI}"><name>Ben</name></part>'
    "</partlist>"
)


def test_the_new_format_is_read_without_a_type_attribute():
    block = attendance.parse_partlist(NEW_XML)
    assert block.kind == "ended"
    assert block.call_id == "cagri-yeni"
    assert block.ical_uid == "ICAL-42"
    assert block.meeting_type == "Scheduled"
    assert [(part.mri, part.seconds) for part in block.parts] == [
        (MY_MRI, 1863),
        (PERSON_ONE, 1800),
    ]


def test_the_identity_can_be_a_child_element():
    assert attendance.parse_partlist(NEW_XML).parts[1].mri == PERSON_ONE


def test_the_new_started_message_is_ignored():
    record = attendance.attendance_of({"messageType": "Event/Call", "content": NEW_STARTED_XML})
    assert record.kind == "started"
    assert intake.normalize_attendance([record], MY_MRI) == []


def test_a_block_without_a_type_but_with_durations_counts_as_ended():
    """Turu yazmayan bloklarda sure varsa toplanti bitmistir."""
    bare = NEW_XML.replace("<calleventtype>ended</calleventtype>", "").replace(
        "<ended>2026-09-11T09:31:03Z</ended>", ""
    )
    record = attendance.attendance_of({"messageType": "Event/Call", "content": bare})
    assert record is not None
    assert record.part_for(MY_MRI).seconds == 1863
    assert intake.normalize_attendance([record], MY_MRI)[0]["duration_ms"] == 1863 * 1000


def test_a_block_without_a_type_and_without_durations_is_skipped():
    bare = (
        '<partlist alt="x"><callid>c</callid>'
        f'<part identity="{MY_MRI}"><name>Ben</name></part></partlist>'
    )
    record = attendance.attendance_of({"messageType": "Event/Call", "content": bare})
    assert intake.normalize_attendance([record], MY_MRI) == []


def test_the_old_format_still_works():
    block = attendance.parse_partlist(ENDED_XML)
    assert block.kind == "ended"
    assert block.call_id == "cagri-1"
    assert len(block.parts) == 2


def test_the_meeting_time_comes_from_the_details():
    record = attendance.attendance_of(
        {"messageType": "Event/Call", "content": NEW_XML}, "19:meeting_x@thread.v2"
    )
    assert record.ended_at == "2026-09-11T09:31:03Z"
    assert record.start_time == "2026-09-11T09:00:00Z"


def test_the_names_inside_the_new_format_are_not_kept():
    record = attendance.attendance_of({"messageType": "Event/Call", "content": NEW_XML})
    dumped = json.dumps([part.__dict__ for part in record.parts], ensure_ascii=False)
    for secret in ("Ben", "Örnek Kişi", "ornek@example.com"):
        assert secret not in dumped, secret


# --- icaluid ile takvim eslemesi -----------------------------------------


def test_the_calendar_is_matched_by_ical_uid():
    """En kesin yol: `meetingdetails.icaluid` <-> takvim `iCalUid`."""
    meeting = event("Bütçe toplantısı", local_text(moment(9)))
    meeting.ical_uid = "ICAL-42"
    record = attendance.attendance_of(
        {"messageType": "Event/Call", "content": NEW_XML}, "19:meeting_bilinmeyen@thread.v2"
    )
    rows = intake.normalize_attendance([record], MY_MRI, events=[meeting])
    assert rows[0]["meeting_subject"] == "Bütçe toplantısı"


def test_the_ical_uid_beats_the_thread_id():
    by_thread = event("Yanlış toplantı", local_text(moment(1)), cid="19:meeting_x@thread.v2")
    by_ical = event("Doğru toplantı", local_text(moment(9)))
    by_ical.ical_uid = "ICAL-42"
    record = attendance.attendance_of(
        {"messageType": "Event/Call", "content": NEW_XML}, "19:meeting_x@thread.v2"
    )
    rows = intake.normalize_attendance([record], MY_MRI, events=[by_thread, by_ical])
    assert rows[0]["meeting_subject"] == "Doğru toplantı"


def test_the_calendar_record_carries_its_ical_uid():
    record = teams_cache.calendar_from_value(
        {"startTime": datetime(2026, 9, 11, 7, 0), "iCalUid": "ICAL-42", "subject": "Toplantı"}
    )
    assert record.ical_uid == "ICAL-42"


# --- hiz: ekran yalnizca SQLite okur -------------------------------------


def test_the_screen_endpoints_never_touch_the_cache(api_client, fake_calls):
    """Pencere degistirmek ya da arama yazmak onbellegi ACMAMALI."""
    seed_api(api_client, fake_calls)
    before = fake_calls.reads

    api_client.get("/api/calls/view?days=7")
    api_client.get("/api/calls/view?days=90&q=örnek")
    api_client.get("/api/calls?days=30")
    api_client.get("/api/calls/stats?days=30")
    api_client.get(f"/api/calls/person/{PERSON_ONE}?days=30")
    assert fake_calls.reads == before

    # Teshis uclari bilerek okur; yalnizca dugmeye basinca cagrilir.
    api_client.get("/api/calls/unmatched?days=30")
    assert fake_calls.reads == before + 1
    api_client.get("/api/calls/attendance-diagnose?days=30")
    assert fake_calls.reads == before + 2


def test_the_view_endpoint_answers_the_whole_screen(api_client, fake_calls):
    seed_api(api_client, fake_calls)
    data = api_client.get("/api/calls/view?days=90").json()
    for key in ("calls", "people", "stats", "count", "unmatched", "windows", "scanned_at"):
        assert key in data, key
    assert data["stats"]["total_ms"] > 0
    # Rozet sayisi da burada: ayri istek gerekmez.
    assert data["unmatched"] == sum(
        1
        for card in data["calls"]
        if card["kind"] == "group_call" and card["thread_kind"] != "group_chat"
    )


def test_the_list_does_not_carry_the_raw_record(api_client, fake_calls):
    """`raw_json` yalnizca cekmecede gerekir; listede okunmaz bile."""
    seed_api(api_client, fake_calls)
    data = api_client.get("/api/calls/view?days=90").json()
    assert "raw_json" not in data["calls"][0]


def test_the_window_is_filtered_in_sql(conn):
    intake.scan(
        conn,
        FakeCallSource(calls=[call("eski", moment(200)), call("yeni", moment(1))]),
    )
    since = intake.iso_text(intake.since_of(30, NOW))
    assert [row["call_id"] for row in repo.list_calls(conn, since=since)] == ["yeni"]
    assert len(repo.list_calls(conn)) == 2


def test_the_calls_table_is_indexed_by_time(conn):
    indexes = {row["name"] for row in conn.execute("PRAGMA index_list(teams_calls)")}
    assert "idx_teams_calls_started" in indexes
    plan = conn.execute(
        "EXPLAIN QUERY PLAN SELECT call_id FROM teams_calls WHERE started_at >= ? "
        "ORDER BY started_at DESC",
        ("2026-01-01",),
    ).fetchall()
    assert any("idx_teams_calls_started" in str(row["detail"]) for row in plan)


def test_a_thousand_rows_render_in_well_under_a_second(api_client, fake_calls):
    """Saha: pencere degisimi cok yavasti. Hedef: 1.000 kayitta < 300 ms."""
    import time

    fake_calls.calls = [
        call(f"c{index}", moment(index % 80, hours=index % 12), minutes=index % 30 + 1)
        for index in range(1000)
    ]
    api_client.post("/api/calls/scan")

    started = time.perf_counter()
    data = api_client.get("/api/calls/view?days=90").json()
    elapsed = (time.perf_counter() - started) * 1000
    assert data["count"] > 0
    assert elapsed < 300, f"{elapsed:.0f} ms"

    started = time.perf_counter()
    api_client.get("/api/calls/view?days=7&q=örnek")
    assert (time.perf_counter() - started) * 1000 < 300


# --- katilim: sert kurallar ve teshis ------------------------------------


def ended_message(seconds: int = 1863, identity: str = MY_MRI, call_id: str = "cagri-1",
                  kind: str = "ended", message_type: str = "Event/Call") -> dict:
    content = (
        f'<partlist alt="x"><calleventtype>{kind}</calleventtype>'
        f"<callid>{call_id}</callid>"
        f'<part identity="{identity}"><name>Ben</name><duration>{seconds}</duration></part>'
        f'<part identity="{PERSON_ONE}"><duration>1800</duration></part>'
        "</partlist>"
    )
    return {
        "messageType": message_type,
        "content": content,
        "originalArrivalTime": int((NOW - timedelta(days=1)).timestamp() * 1000),
    }


def record_of(**kwargs):
    return attendance.attendance_of(ended_message(**kwargs), "19:meeting_ABC123456789@thread.v2")


def test_a_partlist_inside_another_message_type_still_counts():
    """`messageType` her zaman Event/Call degil (Media_CallRecording gorulur)."""
    record = record_of(message_type="RichText/Media_CallRecording")
    assert record is not None
    assert intake.normalize_attendance([record], MY_MRI)[0]["duration_ms"] == 1863 * 1000


def test_my_identity_matches_without_the_orgid_prefix():
    """Sahada `part identity` bazen `8:<guid>` geliyor; kayit dusuyordu."""
    record = record_of(identity="8:" + MY_GUID)
    part, how = record.match_for(MY_MRI)
    assert how == "guid"
    assert intake.normalize_attendance([record], MY_MRI)[0]["duration_ms"] == 1863 * 1000


def test_my_identity_matches_in_any_case():
    record = record_of(identity=MY_MRI.upper())
    assert record.match_for(MY_MRI)[1] == "exact"


def test_several_sessions_of_one_meeting_are_summed():
    """Cok oturumlu toplanti: ayni `callid`, sureler toplanir, tek satir."""
    plan = intake.plan_attendance(
        [record_of(seconds=600), record_of(seconds=900), record_of(seconds=300)], MY_MRI
    )
    assert len(plan.rows) == 1
    assert plan.rows[0]["duration_ms"] == 1800 * 1000
    assert [note["decision"] for note in plan.decisions] == [
        "created",
        "merged:cagri-1",
        "merged:cagri-1",
    ]


def test_without_a_call_id_the_key_is_the_ical_and_the_day():
    record = attendance.attendance_of(
        {
            "messageType": "Event/Call",
            "content": (
                '<partlist><calleventtype>ended</calleventtype>'
                "<meetingdetails><icaluid>ICAL-7</icaluid></meetingdetails>"
                f'<part identity="{MY_MRI}"><duration>60</duration></part></partlist>'
            ),
            "originalArrivalTime": int((NOW - timedelta(days=1)).timestamp() * 1000),
        },
        "19:meeting_x@thread.v2",
    )
    key = intake.attendance_key(record)
    assert key.startswith("ICAL-7:")
    assert intake.normalize_attendance([record], MY_MRI)[0]["call_id"] == key


def test_every_decision_code_is_reported():
    records = [
        record_of(call_id="olusan"),
        record_of(call_id="baskasi", identity=PERSON_TWO),
        record_of(call_id="suresiz", seconds=0),
        record_of(call_id="baslangic", kind="started"),
        record_of(call_id="gecmiste"),
    ]
    plan = intake.plan_attendance(records, MY_MRI, known_ids={"gecmiste"})
    assert [note["decision"] for note in plan.decisions] == [
        "created",
        "skipped:no_me",
        "skipped:no_duration",
        "skipped:started",
        "deduped:history",
    ]
    assert len(plan.rows) == 1


def test_the_diagnosis_groups_and_counts(api_client, fake_calls):
    fake_calls.calls = []
    fake_calls.attendance = [
        record_of(call_id="olusan"),
        record_of(call_id="baskasi", identity=PERSON_TWO),
        record_of(call_id="suresiz", seconds=0),
    ]
    fake_calls.my_mri = MY_MRI
    api_client.post("/api/calls/scan")

    data = api_client.get("/api/calls/attendance-diagnose?days=90").json()
    assert data["messages"] == 3
    assert data["my_mri_known"] is True
    assert data["summary"]["created"] == 1
    assert data["summary"]["skipped"] == 2
    first = data["meetings"][0]
    for key in ("thread_core", "ended_at", "event_kind", "part_count", "me_present",
                "my_seconds", "decision"):
        assert key in first, key


def test_the_diagnosis_names_the_meeting_when_the_calendar_knows_it(api_client, fake_calls):
    fake_calls.calls = []
    fake_calls.attendance = [record_of()]
    fake_calls.calendar = [
        event("Sabah daily", local_text(moment(9)), cid="19:meeting_ABC123456789@thread.v2")
    ]
    fake_calls.my_mri = MY_MRI
    data = api_client.get("/api/calls/attendance-diagnose?days=90").json()
    assert data["meetings"][0]["subject"] == "Sabah daily"


# --- tekillestirme --------------------------------------------------------


def test_the_same_cache_scanned_twice_creates_no_duplicates(conn):
    source = FakeCallSource(calls=[call("a", moment(1)), call("b", moment(2))])
    first = intake.scan(conn, source)
    assert (first["scanned"], first["new"], first["updated"]) == (2, 2, 0)

    second = intake.scan(conn, source)
    assert (second["scanned"], second["new"], second["updated"]) == (2, 0, 2)
    assert repo.call_count(conn) == 2


def test_a_changed_call_is_updated_in_place(conn):
    intake.scan(conn, FakeCallSource(calls=[call("a", moment(1), minutes=5)]))
    intake.scan(conn, FakeCallSource(calls=[call("a", moment(1), minutes=9)]))
    rows = repo.list_calls(conn)
    assert len(rows) == 1
    assert rows[0]["duration_ms"] == 9 * 60000


def test_the_scan_reports_how_many_meetings_matched(conn):
    started = moment(1)
    source = FakeCallSource(
        calls=[
            call("a", started, call_type=TYPE_MULTI_PARTY),
            call("b", moment(2), call_type=TYPE_MULTI_PARTY, other="kisi-b"),
        ],
        calendar=[event("Toplantı", local_text(started))],
    )
    assert intake.scan(conn, source)["meetings_matched"] == 1


# --- istatistik -----------------------------------------------------------


def seeded_rows() -> list[dict]:
    """Kucuk ama her dilimi kapsayan bir kume."""
    return intake.normalize(
        [
            call("bir", moment(1), minutes=10, other=PERSON_ONE),
            call("iki", moment(2), minutes=20, other=PERSON_ONE, direction=DIRECTION_IN),
            call("uc", moment(3), minutes=5, other=PERSON_TWO),
            call("dort", moment(1, hours=2), minutes=30, call_type=TYPE_MULTI_PARTY),
            call("bes", moment(4), minutes=99, state=STATE_MISSED, other=PERSON_ONE),
            call("alti", moment(4), minutes=99, state=STATE_DECLINED, other=PERSON_TWO),
        ],
        names={PERSON_ONE: "Örnek Kişi", PERSON_TWO: "İkinci Örnek"},
    )


def test_missed_and_declined_calls_never_count_as_contact_time():
    stats = intake.build_stats(seeded_rows(), days=30, now=NOW)
    # 10 + 20 + 5 + 30 dakika; kacirilan ve reddedilen aramalar sureye girmez.
    assert stats["total_ms"] == 65 * 60000
    assert stats["total_text"] == "1 sa 5 dk"
    assert stats["calls"] == 6
    assert stats["connected"] == 4
    assert stats["direction"]["missed"] == 1
    assert stats["direction"]["declined"] == 1


def test_the_three_slices_add_up_to_a_hundred_percent():
    stats = intake.build_stats(seeded_rows(), days=30, now=NOW)
    slices = {item["kind"]: item for item in stats["split"]}
    assert slices[intake.KIND_GROUP]["ms"] == 30 * 60000
    assert slices[intake.KIND_ONE_TO_ONE]["ms"] == 35 * 60000
    assert slices[intake.KIND_MEETING]["ms"] == 0
    assert round(sum(item["percent"] for item in stats["split"])) == 100
    assert slices[intake.KIND_GROUP]["hours"] == 0.5


def test_direction_totals_split_the_connected_calls():
    stats = intake.build_stats(seeded_rows(), days=30, now=NOW)
    assert stats["direction"]["outgoing"]["count"] == 3
    assert stats["direction"]["incoming"]["count"] == 1
    assert stats["direction"]["incoming"]["ms"] == 20 * 60000
    assert stats["direction"]["outgoing"]["label"] == "Aradım"


def test_the_top_list_holds_only_one_to_one_calls_and_stops_at_five():
    rows = intake.normalize(
        [call(f"c{index}", moment(index + 1), minutes=index + 1, other=f"kisi-{index}") for index in range(8)]
        + [call("grup", moment(1), minutes=120, call_type=TYPE_MULTI_PARTY)]
    )
    stats = intake.build_stats(rows, days=30, now=NOW)
    assert len(stats["top"]) == 5
    # En uzun birebir gorusme basta; grup aramasi listeye hic girmez.
    assert stats["top"][0]["ms"] == 8 * 60000
    assert all(person["counterpart_id"].startswith("kisi-") for person in stats["top"])


def test_weekend_days_are_dropped_from_the_workday_average():
    stats = intake.build_stats(seeded_rows(), days=7, now=NOW)
    # 4 Eylul Cuma - 11 Eylul Cuma: sekiz gunun ikisi hafta sonu.
    assert stats["workday"]["days"] == 6
    assert stats["workday"]["average_ms"] == stats["total_ms"] // 6


def test_the_busiest_day_is_reported():
    stats = intake.build_stats(seeded_rows(), days=30, now=NOW)
    busiest = stats["workday"]["busiest"]
    # 10 Eylul: 10 dakikalik arama + 30 dakikalik grup aramasi.
    assert busiest["date"] == moment(1).astimezone().date().isoformat()
    assert busiest["ms"] == 40 * 60000


def test_calls_outside_the_window_are_ignored():
    rows = intake.normalize([call("eski", moment(45)), call("yeni", moment(1))])
    stats = intake.build_stats(rows, days=30, now=NOW)
    assert stats["calls"] == 1


def test_workdays_between_counts_only_monday_to_friday():
    from datetime import date

    assert intake.workdays_between(date(2026, 9, 7), date(2026, 9, 13)) == 5
    assert intake.workdays_between(date(2026, 9, 12), date(2026, 9, 13)) == 0


# --- suzgecler ------------------------------------------------------------


def test_the_search_box_looks_at_the_name_and_the_meeting_subject():
    started = moment(1)
    rows = intake.normalize(
        [
            call("a", started, other=PERSON_ONE),
            call("b", moment(2), call_type=TYPE_MULTI_PARTY, other="kisi-b"),
        ],
        [event("Bütçe toplantısı", local_text(started - timedelta(days=1)), organizer="Toplantı Sahibi")],
        names={PERSON_ONE: "Örnek Kişi"},
    )
    assert [row["call_id"] for row in intake.select(rows, q="örnek", now=NOW)] == ["a"]
    assert [row["call_id"] for row in intake.select(rows, q="bütçe", now=NOW)] == ["b"]


def test_filters_narrow_by_direction_state_and_kind():
    rows = seeded_rows()
    assert len(intake.select(rows, direction=DIRECTION_IN, now=NOW)) == 1
    assert len(intake.select(rows, state=STATE_MISSED, now=NOW)) == 1
    assert len(intake.select(rows, kind=intake.KIND_GROUP, now=NOW)) == 1


def test_the_list_is_newest_first():
    picked = intake.select(seeded_rows(), now=NOW)
    # "bir" en yeni kayit; "dort" ayni gunun iki saat oncesi.
    assert [row["call_id"] for row in picked][:2] == ["bir", "dort"]


# --- kisi gorunumu --------------------------------------------------------


def test_the_person_view_gathers_calls_and_shared_meetings():
    rows = intake.normalize(
        [
            call("bir", moment(1), minutes=10, other=PERSON_ONE),
            call("iki", moment(2), minutes=20, other=PERSON_ONE, direction=DIRECTION_IN),
            call("uc", moment(3), minutes=4, other=PERSON_ONE, state=STATE_MISSED),
            call("baska", moment(2), minutes=7, other=PERSON_TWO),
            call(
                "grup",
                moment(1, hours=3),
                minutes=45,
                call_type=TYPE_MULTI_PARTY,
                participants=[PERSON_ONE, PERSON_TWO],
            ),
        ],
        names={PERSON_ONE: "Örnek Kişi", PERSON_TWO: "İkinci Örnek"},
    )
    data = intake.person_view(rows, PERSON_ONE, days=30, now=NOW)
    assert data["person"]["name"] == "Örnek Kişi"
    assert [item["call_id"] for item in data["calls"]] == ["bir", "iki", "uc"]
    assert [item["call_id"] for item in data["group_calls"]] == ["grup"]

    summary = data["summary"]
    assert summary["count"] == 3
    assert summary["outgoing"] == 2
    assert summary["incoming"] == 1
    assert summary["missed"] == 1
    assert summary["total_ms"] == 30 * 60000
    assert summary["longest_ms"] == 20 * 60000
    assert summary["last_at"] == max(item["started_at"] for item in rows if item["call_id"] in ("bir", "grup"))


def test_an_unknown_person_gets_an_empty_but_valid_view():
    data = intake.person_view(seeded_rows(), "yok", days=30, now=NOW)
    assert data["calls"] == []
    assert data["summary"]["total_ms"] == 0


# --- ham kayit cozumu (gercek IndexedDB yapisi) ---------------------------
#
# Asagidaki adlar ve alanlar gercek bir onbellek sondasindan alindi:
# veritabani adlari `Teams:<ad>:react-web-client:<kiraci>:<kullanici>:<dil>`,
# takvim saatleri `datetime`, bazi dizgeler `bytes`, katilimcilar
# `participantList` altinda ve `displayName` hep `null`.

def test_the_four_databases_we_need_are_recognised():
    assert teams_cache.database_role(CALL_DB) == "call-history-manager"
    assert teams_cache.database_role(CALENDAR_DB) == "calendar"
    assert teams_cache.database_role(PROFILE_DB) == "profiles"
    assert teams_cache.database_role(THREAD_DB) == "conversation-manager"


@pytest.mark.parametrize(
    "name",
    [
        "Teams:call-history-sync-state-manager:react-web-client:k:u:tr-tr",
        "Teams:call-history-voicemail-manager:react-web-client:k:u:tr-tr",
        "Teams:replychain-calendar:react-web-client:k:u:tr-tr",
        "Teams:conversation-manager-extra:react-web-client:k:u:tr-tr",
        "Teams:messages:react-web-client:k:u:tr-tr",
    ],
)
def test_sibling_databases_are_excluded(name):
    """Alt dizge aramasi kardes veritabanlarini da aciyordu; tam segment sart."""
    assert teams_cache.database_role(name) == ""


def test_the_call_database_is_recognised_by_its_segment():
    assert teams_cache.is_call_database(CALL_DB) is True
    assert teams_cache.is_call_database(CALENDAR_DB) is False
    # Eski alt dizge eslemesi bunu da yakaliyordu; artik yakalamiyor.
    assert teams_cache.is_call_database("call-history-manager-db") is False


def test_every_wanted_database_has_exactly_one_store():
    assert teams_cache.ROLE_STORES == {
        "call-history-manager": "call-history",
        "calendar": "calendar",
        "profiles": "profiles",
        "conversation-manager": "conversations",
        # Toplanti sohbetleri: katilim ("kim kac dakika kaldi") burada.
        "replychain-manager": "replychains",
    }


def test_a_raw_call_record_becomes_a_call():
    record = teams_cache.call_from_value(
        {
            "callId": "17:abc",
            "startTime": "2026-09-11T08:30:00Z",
            "endTime": "2026-09-11T08:42:00Z",
            "connectTime": "2026-09-11T08:30:10Z",
            "durationInMs": 710000,
            "callDirection": "outgoing",
            "callState": "accepted",
            "callType": "twoParty",
            "isDeleted": False,
            "isCurrentUserPartOfCall": True,
            "originatorParticipant": {"displayName": "Ben", "id": ME},
            "targetParticipant": {"displayName": None, "id": PERSON_ONE},
            "participantList": [
                {"id": PERSON_ONE, "type": "user", "tenantId": "k", "displayName": None},
                {"id": PERSON_TWO, "type": "user", "tenantId": "k", "displayName": None},
            ],
            "threadId": MEETING_THREAD,
            "groupChatThreadId": "",
        }
    )
    assert record.call_id == "17:abc"
    assert record.duration_ms == 710000
    assert record.target_id == PERSON_ONE
    assert record.originator_name == "Ben"
    assert record.participants == [PERSON_ONE, PERSON_TWO]
    assert record.thread_id == MEETING_THREAD


def test_the_case_of_the_enum_fields_does_not_matter():
    """Gercek kayitlarda "incoming"/"Incoming" ve "twoParty" karisik geliyor."""
    lower = teams_cache.call_from_value(
        {"callId": "a", "callDirection": "incoming", "callState": "missed", "callType": "multiParty"}
    )
    assert lower.direction == "Incoming"
    assert lower.state == "Missed"
    assert lower.call_type == "MultiParty"

    upper = teams_cache.call_from_value(
        {"callId": "b", "callDirection": "OUTGOING", "callType": "TWOPARTY"}
    )
    assert upper.direction == "Outgoing"
    assert upper.call_type == "TwoParty"


def test_a_deleted_call_is_skipped():
    assert teams_cache.call_from_value({"callId": "a", "isDeleted": True}) is None
    assert teams_cache.call_from_value({"callId": "a", "isDeleted": False}) is not None


def test_a_bytes_display_name_is_decoded():
    """Teams bazi dizgeleri bytes yaziyor; ham `b'...'` ekrana cikmamali."""
    record = teams_cache.call_from_value(
        {
            "callId": "a",
            "originatorParticipant": {"id": ME, "displayName": "Örnek Kişi".encode("utf-8")},
        }
    )
    assert record.originator_name == "Örnek Kişi"

    latin = teams_cache.call_from_value(
        {"callId": "b", "originatorParticipant": {"id": ME, "displayName": b"\xdcmit"}}
    )
    assert latin.originator_name == "Ümit"


def test_the_arrival_time_stands_in_for_a_missing_start():
    record = teams_cache.call_from_value(
        {"callId": "a", "originalArrivalTime": 1789000000000}
    )
    assert intake.normalize([record])[0]["started_at"].startswith("2026-09-10")


def test_a_raw_record_without_an_id_is_dropped():
    assert teams_cache.call_from_value({"startTime": "2026-09-11T08:30:00Z"}) is None
    assert teams_cache.call_from_value("metin") is None


def test_a_raw_calendar_record_keeps_its_datetime():
    """Takvim saatleri `datetime` gelir; metne cevirmek eslemeyi bozuyordu."""
    started = datetime(2026, 9, 11, 11, 30)
    record = teams_cache.calendar_from_value(
        {
            "id": "event-1",
            "startTime": started,
            "endTime": datetime(2026, 9, 11, 12, 0),
            "subject": "Bütçe toplantısı".encode("utf-8"),
            "organizerName": "Örnek Kişi",
            "organizerAddress": "ornek@example.com",
            "myResponseType": "Accepted",
            "isOnlineMeeting": True,
            "isCancelled": False,
            "showAs": "Busy",
            "attendees": [
                {"name": "İkinci Örnek", "address": "ikinci@example.com",
                 "role": "Required", "status": {"response": "Accepted"}},
            ],
            "skypeTeamsDataObj": {"cid": MEETING_THREAD},
            "skypeTeamsMeetingUrl": "https://teams.microsoft.com/l/meetup-join/19%3ameeting_x",
        }
    )
    assert record.start_time == started
    assert record.subject == "Bütçe toplantısı"
    assert record.attendees == ["İkinci Örnek"]
    assert record.cid == MEETING_THREAD
    assert teams_cache.calendar_from_value({"subject": "saatsiz"}) is None


def test_profile_records_feed_the_name_map():
    assert teams_cache.names_from_value(
        {"mri": PERSON_ONE, "displayName": "Örnek Kişi", "email": "ornek@example.com"}
    ) == {PERSON_ONE: "Örnek Kişi"}
    # Kabuk icindeki kayit da cozulur.
    assert teams_cache.names_from_value(
        {"value": {"id": PERSON_TWO, "imDisplayName": "İkinci Örnek"}}
    ) == {PERSON_TWO: "İkinci Örnek"}
    assert teams_cache.names_from_value({"displayName": "adsız"}) == {}


def test_a_conversation_record_becomes_a_thread():
    thread = teams_cache.thread_from_value(
        {
            "id": GROUP_THREAD,
            "threadProperties": {"topic": "Proje ekibi".encode("utf-8")},
            "members": [{"id": PERSON_ONE}, {"id": PERSON_TWO}],
            "chatTitle": {"avatarUsersInfo": [{"displayName": "Örnek Kişi"}]},
        }
    )
    assert thread.thread_id == GROUP_THREAD
    assert thread.topic == "Proje ekibi"
    assert thread.members == [PERSON_ONE, PERSON_TWO]
    assert thread.member_names == ["Örnek Kişi"]
    # Kayit anahtari da kimlik olarak kabul edilir.
    assert teams_cache.thread_from_value({"threadProperties": {}}, GROUP_THREAD).thread_id == (
        GROUP_THREAD
    )


# --- toplanti eslemesi: once thread kimligi -------------------------------


def test_a_thread_id_matches_the_calendar_without_looking_at_the_clock():
    """Saatler tutmasa da `threadId` == `cid` ise eslesme kesindir."""
    rows = intake.normalize(
        [call("a", moment(1), call_type=TYPE_MULTI_PARTY, thread_id=MEETING_THREAD)],
        [event("Bütçe toplantısı", local_text(moment(9)), cid=MEETING_THREAD)],
    )
    assert rows[0]["kind"] == intake.KIND_MEETING
    assert rows[0]["meeting_subject"] == "Bütçe toplantısı"


def test_the_thread_id_beats_a_closer_meeting_in_time():
    started = moment(1)
    rows = intake.normalize(
        [call("a", started, call_type=TYPE_MULTI_PARTY, thread_id=MEETING_THREAD)],
        [
            event("Yakın ama başka", local_text(started)),
            event("Doğru toplantı", local_text(moment(20)), cid=MEETING_THREAD),
        ],
    )
    assert rows[0]["meeting_subject"] == "Doğru toplantı"


def test_the_meeting_url_also_carries_the_thread():
    """Baglantida kimlik URL kodlanmis gecer; govdesi aranir."""
    core = intake.thread_core(MEETING_THREAD)
    url = f"https://teams.microsoft.com/l/meetup-join/19%3ameeting_{core}%40thread.v2/0"
    rows = intake.normalize(
        [call("a", moment(1), call_type=TYPE_MULTI_PARTY, thread_id=MEETING_THREAD)],
        [event("Bağlantıdan eşleşen", local_text(moment(30)), meeting_url=url)],
    )
    assert rows[0]["kind"] == intake.KIND_MEETING


def test_a_short_thread_id_never_matches_by_substring():
    assert intake.thread_matches("19:x", event("A", local_text(moment(1)), cid="19:xyz")) is False


def test_a_group_chat_call_is_titled_from_the_conversation():
    rows = intake.normalize(
        [call("a", moment(1), call_type=TYPE_MULTI_PARTY, group_thread_id=GROUP_THREAD)],
        threads=[ThreadRecord(thread_id=GROUP_THREAD, topic="Proje ekibi")],
    )
    assert rows[0]["kind"] == intake.KIND_GROUP
    assert rows[0]["topic"] == "Proje ekibi"
    assert intake.display_party(rows[0]) == "Proje ekibi"


def test_the_conversation_members_stand_in_for_a_missing_participant_list():
    rows = intake.normalize(
        [call("a", moment(1), call_type=TYPE_MULTI_PARTY, group_thread_id=GROUP_THREAD)],
        names={PERSON_ONE: "Örnek Kişi"},
        threads=[ThreadRecord(thread_id=GROUP_THREAD, members=[PERSON_ONE, PERSON_TWO])],
    )
    assert intake.participants_of(rows[0]) == [PERSON_ONE, PERSON_TWO]
    assert intake.participant_labels(rows[0])[0] == "Örnek Kişi"


def test_participant_names_are_resolved_once_and_stored():
    """Ad tarama aninda profillerden cozulur; ekran sozluge muhtac kalmaz."""
    rows = intake.normalize(
        [call("a", moment(1), call_type=TYPE_MULTI_PARTY, participants=[PERSON_ONE])],
        names={PERSON_ONE: "Örnek Kişi"},
    )
    assert intake.participant_pairs(rows[0]) == [{"id": PERSON_ONE, "name": "Örnek Kişi"}]
    # Sozluk verilmese bile ad ekranda durur.
    assert intake.display_party(rows[0]) == "Örnek Kişi"


def test_the_invitees_are_kept_apart_from_the_participants():
    started = moment(1)
    rows = intake.normalize(
        [
            call(
                "a",
                started,
                call_type=TYPE_MULTI_PARTY,
                participants=[PERSON_ONE],
                thread_id=MEETING_THREAD,
            )
        ],
        [
            event(
                "Bütçe toplantısı",
                local_text(started),
                cid=MEETING_THREAD,
                attendees=["Örnek Kişi", "İkinci Örnek", "Üçüncü Kişi"],
            )
        ],
        names={PERSON_ONE: "Örnek Kişi"},
    )
    card = intake.view(rows[0])
    # Katilanlar arama kaydindan, davetliler takvimden gelir.
    assert card["participant_names"] == ["Örnek Kişi"]
    assert card["attendees"] == ["Örnek Kişi", "İkinci Örnek", "Üçüncü Kişi"]


def test_an_old_row_with_plain_participant_ids_still_reads():
    """0008 doneminde katilimcilar duz kimlik listesiydi."""
    row = {"kind": intake.KIND_GROUP, "participants_json": json.dumps([PERSON_ONE])}
    assert intake.participants_of(row) == [PERSON_ONE]
    assert intake.display_party(row, {PERSON_ONE: "Örnek Kişi"}) == "Örnek Kişi"


def test_the_reader_copies_before_it_reads(tmp_path):
    source = tmp_path / "leveldb"
    source.mkdir()
    (source / "000001.log").write_bytes(b"veri")
    report = teams_cache.copy_tree(source, tmp_path / "kopya")
    assert report.copied == 1
    assert (tmp_path / "kopya" / "000001.log").read_bytes() == b"veri"


def test_a_missing_cache_folder_is_reported_not_crashed(tmp_path):
    source = teams_cache.TeamsCacheSource(cache_path=str(tmp_path / "yok"))
    with pytest.raises(CallsError) as caught:
        source.read()
    assert caught.value.code == "calls_cache_missing"


def test_outside_windows_the_source_refuses(monkeypatch):
    monkeypatch.setattr("app.teamscalls.is_supported", lambda: False)
    with pytest.raises(CallsError) as caught:
        default_source()
    assert caught.value.code == "feature_unavailable"


# --- API ------------------------------------------------------------------


def seed_api(api_client, fake_calls, calendar=()):
    fake_calls.calls = [
        call("bir", moment(1), minutes=10, other=PERSON_ONE),
        call("iki", moment(2), minutes=20, other=PERSON_ONE, direction=DIRECTION_IN),
        call("uc", moment(3), minutes=5, other=PERSON_TWO, state=STATE_MISSED),
        call(
            "grup",
            moment(1, hours=1),
            minutes=30,
            call_type=TYPE_MULTI_PARTY,
            participants=[PERSON_ONE, PERSON_TWO],
        ),
    ]
    fake_calls.calendar = list(calendar)
    return api_client.post("/api/calls/scan").json()


def test_the_scan_endpoint_summarises_the_run(api_client, fake_calls):
    result = seed_api(api_client, fake_calls)
    assert result["scanned"] == 4
    assert result["new"] == 4
    assert result["updated"] == 0
    assert result["meetings_matched"] == 0
    assert result["ms"] >= 0
    assert result["scanned_at"]
    assert fake_calls.reads == 1
    # Verinin nereden geldigi ve en yeni kaydin zamani ozette durur.
    assert result["source"] == "copy"
    assert result["warning"] == ""
    assert result["copied"] == 0
    assert result["skipped"] == 0
    assert result["skipped_kinds"] == {}
    assert result["latest_call_at"].startswith("2026-09-1")

    again = api_client.post("/api/calls/scan").json()
    assert (again["new"], again["updated"]) == (0, 4)


def test_the_list_endpoint_carries_labels_and_people(api_client, fake_calls):
    seed_api(api_client, fake_calls)
    data = api_client.get("/api/calls?days=30").json()
    assert data["count"] == 4
    assert data["total"] == 4
    assert data["calls"][0]["duration_text"]
    assert data["calls"][0]["kind_label"] in ("Birebir", "Toplantı", "Grup araması")
    assert {person["name"] for person in data["people"]} == {"Örnek Kişi", "İkinci Örnek"}


def test_the_list_endpoint_filters(api_client, fake_calls):
    seed_api(api_client, fake_calls)
    assert api_client.get("/api/calls?days=30&direction=Incoming").json()["count"] == 1
    assert api_client.get("/api/calls?days=30&state=Missed").json()["count"] == 1
    assert api_client.get("/api/calls?days=30&kind=group_call").json()["count"] == 1
    # "İkinci Örnek" hem birebir aramada hem grup aramasinin katilimcisinda gecer.
    assert api_client.get("/api/calls?days=30&q=ikinci").json()["count"] == 2
    assert api_client.get("/api/calls?days=30&q=bilinmeyen").json()["count"] == 0


def test_the_stats_endpoint_answers_the_five_cards(api_client, fake_calls):
    seed_api(api_client, fake_calls)
    stats = api_client.get("/api/calls/stats?days=90").json()
    assert stats["total_ms"] == 60 * 60000
    assert len(stats["split"]) == 3
    assert stats["direction"]["missed"] == 1
    assert stats["workday"]["days"] > 0
    assert stats["top"][0]["name"] == "Örnek Kişi"


def test_the_person_endpoint_returns_the_drawer_payload(api_client, fake_calls):
    seed_api(api_client, fake_calls)
    data = api_client.get(f"/api/calls/person/{PERSON_ONE}?days=90").json()
    assert data["person"]["name"] == "Örnek Kişi"
    assert len(data["calls"]) == 2
    assert len(data["group_calls"]) == 1
    assert data["summary"]["total_text"] == "30 dk"


def test_the_export_can_be_read_back(api_client, fake_calls):
    started = moment(1, hours=1)
    seed_api(api_client, fake_calls, calendar=[event("Bütçe toplantısı", local_text(started))])
    response = api_client.get("/api/calls/export.xlsx?days=90")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats"
    )

    book = load_workbook(io.BytesIO(response.content))
    assert book.sheetnames == ["Aramalar", "Kişiler", "İstatistik"]

    sheet = book["Aramalar"]
    headers = [cell.value for cell in sheet[1]]
    assert headers[:6] == ["Tarih", "Yön", "Karşı taraf", "Tür", "Durum", "Süre"]
    # Katilanlar (arama kaydi) ve davetliler (takvim) ayri sutunlar.
    assert headers[-2:] == ["Katılanlar", "Davetliler"]
    assert sheet.max_row == 5
    kinds = {sheet.cell(row=line, column=4).value for line in range(2, 6)}
    assert "Toplantı" in kinds

    people = book["Kişiler"]
    assert [cell.value for cell in people[1]][:3] == ["Kişi", "Görüşme", "Süre"]
    assert people.max_row >= 2

    stats = book["İstatistik"]
    labels = {stats.cell(row=line, column=1).value for line in range(1, stats.max_row + 1)}
    assert "Toplam temas" in labels
    assert "İş günü başına" in labels


def test_the_endpoints_say_feature_unavailable_without_windows(api_client, context, monkeypatch):
    from app import teamscalls

    monkeypatch.setattr("app.teamscalls.is_supported", lambda: False)
    context.calls_factory = teamscalls.default_source
    response = api_client.post("/api/calls/scan")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "feature_unavailable"


def test_the_scan_reports_a_locked_cache(api_client, fake_calls):
    fake_calls.report = {
        "copied": 41,
        "skipped": 2,
        "skipped_kinds": {"log": 1, "lock": 1},
        "source": "copy",
        "warning": "Teams açıkken 1 dosya kopyalanamadı; en yeni aramalar eksik "
        "olabilir. Teams'i kapatıp tekrar deneyin.",
    }
    result = seed_api(api_client, fake_calls)
    assert result["copied"] == 41
    assert result["skipped"] == 2
    assert result["skipped_kinds"] == {"log": 1, "lock": 1}
    assert "kopyalanamadı" in result["warning"]


def test_a_source_error_reaches_the_user(api_client, fake_calls):
    fake_calls.error = CallsError("calls_read_failed", "Teams önbelleği okunamadı.")
    response = api_client.post("/api/calls/scan")
    assert response.status_code == 400
    assert response.json()["error"]["message"] == "Teams önbelleği okunamadı."


def test_the_settings_expose_the_cache_path_and_the_refresh_switch(api_client):
    settings = api_client.get("/api/settings").json()["settings"]
    assert settings["calls.cache_path"] == ""
    assert settings["calls.scan_on_refresh"] == "0"
    assert "calls_supported" in settings

    saved = api_client.put(
        "/api/settings",
        json={"calls.cache_path": "D:\\kopya\\leveldb", "calls.scan_on_refresh": True},
    ).json()["settings"]
    assert saved["calls.cache_path"] == "D:\\kopya\\leveldb"
    assert saved["calls.scan_on_refresh"] == "1"


def test_the_config_reads_the_settings(store):
    store.set("calls.cache_path", "D:\\kopya")
    store.set("calls.scan_on_refresh", "1")
    config = intake.load_config(store)
    assert config.cache_path == "D:\\kopya"
    assert config.scan_on_refresh is True


def test_the_migration_creates_the_calls_table(conn):
    assert "teams_calls" in db.table_names(conn)
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(teams_calls)")}
    assert {
        "call_id",
        "started_at",
        "ended_at",
        "connected_at",
        "duration_ms",
        "direction",
        "state",
        "kind",
        "counterpart_id",
        "counterpart_name",
        "forwarded",
        "meeting_subject",
        "meeting_organizer",
        "my_response",
        "participants_json",
        "raw_json",
        "seen_at",
    } <= columns


def test_the_raw_record_is_kept_for_later(conn):
    intake.scan(conn, FakeCallSource(calls=[call("a", moment(1))]))
    row = repo.get_call(conn, "a")
    assert json.loads(row["raw_json"])["callId"] == "a"
