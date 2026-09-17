"""Teams arama gecmisi (yerel onbellekten tablo, gruplar ve istatistik).

Test edilen sorular:

* Bu arama ne kadar surdu? (`durationInMs` yoksa iki yedek yol)
* Ne turden? (birebir / grup -- toplanti diye bir tur YOK)
* Karsi taraf kim ve adi nereden cozuldu?
* Ayni onbellek iki kez taranirsa kopya olusuyor mu? (olusmamali)
* Gruplar: ayni sohbetin aramalari tek satirda toplaniyor mu, ad nereden
  geliyor, Excel'e ciktiginda ne yaziyor?
* Istatistik: serit dort kutu mu, kacirilan aramalar sureye giriyor mu
  (girmemeli), grup suresi katilan herkese yaziliyor mu?
* Windows olmayan makinede uc ne diyor? (`feature_unavailable`)

Gercek IndexedDB hicbir testte acilmaz: `app/teamscalls/fake.py` bellek ici
kaynaktir, ham kayit cozumu ise saf fonksiyonlarla sinanir.
"""

from __future__ import annotations

import io
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from openpyxl import load_workbook

from app import db, repository as repo
from app.teamscalls import CallsError, default_source, intake, teams_cache
from app.teamscalls import source as source_module
from app.teamscalls.fake import (
    ME,
    PERSON_ONE,
    PERSON_TWO,
    FakeCallSource,
    call,
)
from app.teamscalls.source import (
    DIRECTION_IN,
    DIRECTION_OUT,
    STATE_ACCEPTED,
    STATE_DECLINED,
    STATE_MISSED,
    TYPE_MULTI_PARTY,
    TYPE_TWO_PARTY,
    CallRecord,
)

# Cuma 12:00 UTC: pencere hesabi makinenin gunune bagli kalmasin.
NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)

# Gercek bir onbellek sondasindan alinan adlar ve kimlik bicimleri.
CALL_DB = "Teams:call-history-manager:react-web-client:kiraci:kullanici:tr-tr"
PROFILE_DB = "Teams:profiles:react-web-client:kiraci:kullanici:tr-tr"
THREAD_DB = "Teams:conversation-manager:react-web-client:kiraci:kullanici:tr-tr"

GROUP_THREAD = "19:abcdef0123456789abcdef0123456789@thread.v2"
PERSON_THREE = "8:orgid:00000000-0000-0000-0000-000000000003"


def moment(days: float = 0, hours: float = 0) -> datetime:
    return NOW - timedelta(days=days, hours=hours)


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


# --- tur karari: birebir mi, grup mu? -------------------------------------


def test_two_party_call_is_one_to_one():
    rows = intake.normalize([call("a", moment(1))])
    assert rows[0]["kind"] == intake.KIND_ONE_TO_ONE


def test_multi_party_call_is_a_group_call():
    rows = intake.normalize([call("a", moment(1), call_type=TYPE_MULTI_PARTY)])
    assert rows[0]["kind"] == intake.KIND_GROUP


def test_an_unknown_call_type_falls_back_to_the_participant_count():
    """Tur alani yazmiyorsa kural: kendim haric katilimci > 1 ise grup."""
    crowded = call(
        "grup", moment(1), call_type="", participants=[ME, PERSON_ONE, PERSON_TWO]
    )
    alone = call("birebir", moment(1), call_type="", participants=[ME, PERSON_ONE])
    rows = intake.normalize([crowded, alone], me=ME)
    kinds = {row["call_id"]: row["kind"] for row in rows}
    assert kinds == {"grup": intake.KIND_GROUP, "birebir": intake.KIND_ONE_TO_ONE}


def test_without_my_identity_the_fallback_still_counts_the_participants():
    """Kimligim bilinmiyorsa kimse elenmez; iki kisilik liste yine birebirdir."""
    rows = intake.normalize([call("a", moment(1), call_type="", participants=[ME, PERSON_ONE])])
    assert rows[0]["kind"] == intake.KIND_ONE_TO_ONE


def test_no_row_is_ever_a_meeting():
    """Toplanti turu tumden kalkti: uretilen hicbir satir 'meeting' olamaz."""
    rows = intake.normalize(
        [
            call("bir", moment(1)),
            call("grup", moment(2), call_type=TYPE_MULTI_PARTY, participants=[PERSON_ONE]),
        ]
    )
    assert {row["kind"] for row in rows} <= set(intake.KINDS)
    assert "meeting" not in {row["kind"] for row in rows}
    assert not hasattr(intake, "KIND_MEETING")


def test_the_row_carries_no_meeting_columns():
    row = intake.normalize([call("a", moment(1))])[0]
    for name in ("meeting_subject", "meeting_organizer", "my_response", "attendees_json", "source"):
        assert name not in row, name


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
    # Tur alani da katilimci da yok: bos kayit birebir sayilir.
    assert rows[0]["kind"] == intake.KIND_ONE_TO_ONE
    assert rows[0]["counterpart_name"] == ""
    assert intake.display_party(rows[0]) == intake.UNKNOWN_PERSON


def test_labels_are_turkish():
    rows = intake.normalize(
        [call("a", moment(1), direction=DIRECTION_IN, state=STATE_MISSED)]
    )
    card = intake.view(rows[0])
    assert card["kind_label"] == "Birebir"
    assert card["direction_label"] == "Arandım"
    assert card["state_label"] == "Kaçırılan"
    assert intake.KIND_LABELS[intake.KIND_GROUP] == "Grup araması"
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


def test_a_group_call_is_labelled_from_the_participants_not_the_cache():
    """Grup adi onbellekten ARANMAZ: etiket katilimci adlarindan turer."""
    rows = intake.normalize(
        [call("a", moment(1), call_type=TYPE_MULTI_PARTY, participants=[PERSON_ONE, PERSON_TWO])],
        names={PERSON_ONE: "Örnek Kişi", PERSON_TWO: "İkinci Örnek"},
    )
    # Adlar alfabetik siralanir: etiket kayittan kayda degismez.
    assert intake.display_party(rows[0]) == "İkinci Örnek, Örnek Kişi"


def test_a_group_call_shows_the_participants_by_name():
    rows = intake.normalize(
        [call("a", moment(1), call_type=TYPE_MULTI_PARTY, participants=[PERSON_ONE, PERSON_TWO])],
        names={PERSON_ONE: "Örnek Kişi", PERSON_TWO: "İkinci Örnek"},
    )
    assert intake.display_party(rows[0], {PERSON_ONE: "Örnek Kişi", PERSON_TWO: "İkinci Örnek"}) == (
        "İkinci Örnek, Örnek Kişi"
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


def test_the_person_drawer_titles_shared_group_calls_the_same_way():
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
    assert data["group_calls"][0]["title"] == "İkinci Örnek, Örnek Kişi"
    assert "8:orgid:" not in data["group_calls"][0]["title"]


# --- yalnizca gereken uc veritabani acilir --------------------------------
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


def test_only_the_two_needed_databases_are_opened(fake_reader, tmp_path):
    source = teams_cache.TeamsCacheSource(cache_path=str(tmp_path), copy_first=False)
    bundle = source._read_all(tmp_path, None)
    opened = fake_reader.made[0].opened
    assert opened == [CALL_DB, PROFILE_DB]
    assert bundle.databases == 2
    assert fake_reader.made[0].closed is True


def test_the_calendar_and_conversation_databases_are_never_opened_any_more(fake_reader, tmp_path):
    """Takvim eslemesi de sohbet adi kesfi de kalkti: roller listesinde yoklar."""
    for name in ("calendar", "conversation-manager", "replychain-manager"):
        assert teams_cache.database_role(f"Teams:{name}:react-web-client:k:u:tr-tr") == ""
        assert name not in teams_cache.ROLE_STORES
    source = teams_cache.TeamsCacheSource(cache_path=str(tmp_path), copy_first=False)
    source._read_all(tmp_path, None)
    assert THREAD_DB not in fake_reader.made[0].opened


def test_the_read_collects_both_kinds(fake_reader, tmp_path):
    source = teams_cache.TeamsCacheSource(cache_path=str(tmp_path), copy_first=False)
    bundle = source._read_all(tmp_path, None)
    assert [item.call_id for item in bundle.calls] == ["a"]
    assert bundle.names == {PERSON_ONE: "Örnek Kişi"}


def test_the_scan_reports_how_long_the_read_took(fake_reader, tmp_path, conn):
    cache = tmp_path / "leveldb"
    cache.mkdir()
    source = teams_cache.TeamsCacheSource(cache_path=str(cache), copy_first=False)
    result = intake.scan(conn, source)
    assert result["databases"] == 2
    assert result["read_ms"] >= 0
    assert result["source"] == "live"
    assert repo.list_calls(conn)[0]["call_id"] == "a"


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


# --- kendi kimligim -------------------------------------------------------
#
# Kimlik veritabani ADINDA duruyor; grup istatistiginde "ben" katilimci
# sayilmayayim diye gerekiyor.

MY_GUID = "0f8fad5b-d9cb-469f-a165-70867728950e"
MY_MRI = "8:orgid:" + MY_GUID
TENANT_GUID = "11111111-1111-1111-1111-111111111111"

DB_WITH_USER = f"Teams:calendar:react-web-client:{TENANT_GUID}:{MY_GUID}:tr-tr"
DB_WITH_MRI = f"Teams:anonymoususersmanager:react-web-client:{TENANT_GUID}:{MY_MRI}:tr-tr"


def test_my_mri_comes_from_the_database_name():
    """Ad `Teams:<rol>:react-web-client:<kiraci>:<kullanici>:<dil>` bicimindedir."""
    assert source_module.mri_from_database_name(DB_WITH_USER) == MY_MRI


def test_some_database_names_carry_the_mri_outright():
    assert source_module.mri_from_database_name(DB_WITH_MRI) == MY_MRI


def test_a_database_name_without_an_identity_gives_nothing():
    assert source_module.mri_from_database_name("Teams:messages:react-web-client:a:b:tr") == ""


def test_the_setting_wins_over_the_discovered_identity():
    assert intake.my_mri(setting=MY_GUID, discovered=PERSON_ONE) == MY_MRI
    assert intake.my_mri(discovered=MY_MRI) == MY_MRI
    assert intake.my_mri() == ""


def test_the_call_history_participant_id_is_not_used_any_more():
    """Sahada hicbir katilimci listesinde bulunamadi: yanlis kaynakti."""
    record = teams_cache.call_from_value({"callId": "a", "userParticipantId": MY_GUID})
    assert not hasattr(record, "user_participant_id")


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
    api_client.get("/api/calls/export.xlsx?days=30")
    assert fake_calls.reads == before

    # Onbellegi yalnizca tarama acar.
    api_client.post("/api/calls/scan")
    assert fake_calls.reads == before + 1


def test_the_view_endpoint_answers_the_whole_screen(api_client, fake_calls):
    seed_api(api_client, fake_calls)
    data = api_client.get("/api/calls/view?days=90").json()
    for key in ("calls", "people", "groups", "stats", "count", "windows", "scanned_at"):
        assert key in data, key
    assert data["stats"]["total_ms"] > 0
    # Toplantiya ait alanlar yanittan tumden cikti.
    assert "unmatched" not in data


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


# Paylasimli CI makineleri (Windows runner'lari) yerel makinenin yirmi kati
# yavas olabiliyor: mutlak sinir yalnizca "felaket freni"dir, asil olcu ORAN.
SLOW_RUNNER = bool(os.environ.get("CI")) or sys.platform == "win32"
VIEW_BUDGET_MS = 1500 if SLOW_RUNNER else 300

# On kat kayit, en fazla yirmi bes kat sure: dogrusal bir yol boyle gorunur.
# (Her istekte butun tabloyu yeniden kuran bir yol yuz kati asardi.)
MAX_GROWTH = 25


def measure(client, url: str, rounds: int = 5) -> float:
    """En hizli gecisin suresi (ms). Tek olcum paylasimli makinede gurultulu."""
    best = float("inf")
    for _ in range(rounds):
        started = time.perf_counter()
        client.get(url)
        best = min(best, (time.perf_counter() - started) * 1000)
    return best


def seeded_calls(count: int) -> list:
    return [
        call(f"c{index}", moment(index % 80, hours=index % 12), minutes=index % 30 + 1)
        for index in range(count)
    ]


def test_the_view_grows_linearly_with_the_table(api_client, fake_calls):
    """Saha: pencere degisimi cok yavasti.

    Test saate degil OLCEGE baglidir: on kat kayit yirmi bes kattan az sure
    demek "her istekte butun tabloyu yeniden okumuyoruz" demektir. Mutlak
    sinir yalnizca kaza freni; CI makinesinde bilerek gevsek.
    """
    fake_calls.calls = seeded_calls(100)
    api_client.post("/api/calls/scan")
    small = measure(api_client, "/api/calls/view?days=90")

    fake_calls.calls = seeded_calls(1000)
    api_client.post("/api/calls/scan")
    large = measure(api_client, "/api/calls/view?days=90")

    data = api_client.get("/api/calls/view?days=90").json()
    assert data["count"] > 100
    assert large < VIEW_BUDGET_MS, f"{large:.0f} ms"
    assert large / max(small, 0.5) < MAX_GROWTH, f"{small:.1f} ms -> {large:.1f} ms"


def test_the_search_stays_fast_on_a_full_table(api_client, fake_calls):
    fake_calls.calls = seeded_calls(1000)
    api_client.post("/api/calls/scan")
    assert measure(api_client, "/api/calls/view?days=7&q=örnek") < VIEW_BUDGET_MS
    assert measure(api_client, "/api/calls/stats?days=90") < VIEW_BUDGET_MS


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


def test_the_scan_reports_how_many_group_calls_there_are(conn):
    source = FakeCallSource(
        calls=[
            call("a", moment(1), call_type=TYPE_MULTI_PARTY, participants=[PERSON_ONE]),
            call("b", moment(2), other="kisi-b"),
        ]
    )
    result = intake.scan(conn, source)
    assert result["groups"] == 1
    assert "meetings_matched" not in result


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


def test_the_two_slices_add_up_to_a_hundred_percent():
    """Dordüncü kutu: birebir / grup payi, hem sure hem adet."""
    stats = intake.build_stats(seeded_rows(), days=30, now=NOW)
    slices = {item["kind"]: item for item in stats["split"]}
    assert set(slices) == {intake.KIND_ONE_TO_ONE, intake.KIND_GROUP}
    assert slices[intake.KIND_GROUP]["ms"] == 30 * 60000
    assert slices[intake.KIND_ONE_TO_ONE]["ms"] == 35 * 60000
    assert round(sum(item["percent"] for item in stats["split"])) == 100
    assert round(sum(item["count_percent"] for item in stats["split"])) == 100
    assert slices[intake.KIND_GROUP]["count"] == 1
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


def test_the_group_box_credits_every_participant_with_the_whole_call():
    """Ikinci kutu: grup aramasinin suresi katilan HERKESE yazilir."""
    stats = intake.build_stats(group_rows(), days=30, now=NOW, me=ME)
    people = {person["name"]: person for person in stats["group_top"]}
    # Uclunun iki aramasi 40 dk; ucuncu grup 5 dk.
    assert people["Örnek Kişi"]["ms"] == 45 * 60000
    assert people["İkinci Örnek"]["ms"] == 40 * 60000
    assert people["Üçüncü Örnek"]["ms"] == 5 * 60000
    assert people["Örnek Kişi"]["groups"] == 2
    # Kendim listede yokum.
    assert "Ben" not in people


def test_the_combined_box_sums_the_one_to_one_and_the_group_time():
    stats = intake.build_stats(group_rows(), days=30, now=NOW, me=ME)
    first = stats["combined_top"][0]
    assert first["name"] == "Örnek Kişi"
    # 9 dk birebir + 45 dk grup.
    assert first["ms"] == 54 * 60000
    assert first["one_to_one_ms"] == 9 * 60000
    assert first["group_ms"] == 45 * 60000


def test_the_boxes_stop_at_five_people():
    people = [f"8:orgid:kisi-{index}" for index in range(8)]
    rows = intake.normalize(
        [
            call(
                f"grup-{index}",
                moment(index + 1),
                minutes=index + 1,
                call_type=TYPE_MULTI_PARTY,
                participants=[ME, person, f"8:orgid:baska-{index}"],
            )
            for index, person in enumerate(people)
        ],
        me=ME,
    )
    stats = intake.build_stats(rows, days=30, now=NOW, me=ME)
    assert len(stats["group_top"]) == 5
    assert len(stats["combined_top"]) == 5


def test_calls_outside_the_window_are_ignored():
    rows = intake.normalize([call("eski", moment(45)), call("yeni", moment(1))])
    stats = intake.build_stats(rows, days=30, now=NOW)
    assert stats["calls"] == 1


def test_the_workday_statistics_are_gone():
    """Kullanici istedi: "Toplam Teams" ve "is gunu" kutulari kaldirildi."""
    stats = intake.build_stats(seeded_rows(), days=30, now=NOW)
    assert "workday" not in stats
    assert not hasattr(intake, "workdays_between")


# --- suzgecler ------------------------------------------------------------


def test_the_search_box_looks_at_the_name_and_the_participants():
    rows = intake.normalize(
        [
            call("a", moment(1), other=PERSON_ONE),
            call(
                "b",
                moment(2),
                call_type=TYPE_MULTI_PARTY,
                other="kisi-b",
                participants=[PERSON_TWO],
            ),
        ],
        names={PERSON_ONE: "Örnek Kişi", PERSON_TWO: "İkinci Örnek"},
    )
    assert [row["call_id"] for row in intake.select(rows, q="örnek kişi", now=NOW)] == ["a"]
    assert [row["call_id"] for row in intake.select(rows, q="ikinci", now=NOW)] == ["b"]


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
# bazi dizgeler `bytes`, katilimcilar `participantList` altinda ve
# `displayName` hep `null`.

def test_the_two_databases_we_need_are_recognised():
    assert teams_cache.database_role(CALL_DB) == "call-history-manager"
    assert teams_cache.database_role(PROFILE_DB) == "profiles"
    # Sohbet ve takvim veritabanlarinin artik rolu yok.
    assert teams_cache.database_role(THREAD_DB) == ""


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
    assert teams_cache.is_call_database(PROFILE_DB) is False
    # Eski alt dizge eslemesi bunu da yakaliyordu; artik yakalamiyor.
    assert teams_cache.is_call_database("call-history-manager-db") is False


def test_every_wanted_database_has_exactly_one_store():
    assert teams_cache.ROLE_STORES == {
        "call-history-manager": "call-history",
        "profiles": "profiles",
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
            "threadId": GROUP_THREAD,
            "groupChatThreadId": "",
        }
    )
    assert record.call_id == "17:abc"
    assert record.duration_ms == 710000
    assert record.target_id == PERSON_ONE
    assert record.originator_name == "Ben"
    assert record.participants == [PERSON_ONE, PERSON_TWO]
    # Sohbet kimligi tasinmaz; yalnizca ham kayitta durur.
    assert record.raw["threadId"] == GROUP_THREAD


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


def test_profile_records_feed_the_name_map():
    assert teams_cache.names_from_value(
        {"mri": PERSON_ONE, "displayName": "Örnek Kişi", "email": "ornek@example.com"}
    ) == {PERSON_ONE: "Örnek Kişi"}
    # Kabuk icindeki kayit da cozulur.
    assert teams_cache.names_from_value(
        {"value": {"id": PERSON_TWO, "imDisplayName": "İkinci Örnek"}}
    ) == {PERSON_TWO: "İkinci Örnek"}
    assert teams_cache.names_from_value({"displayName": "adsız"}) == {}


def test_the_conversation_reader_is_gone():
    """Grup adi onbellekten aranmiyor: sohbet okuyucusu tumden kalkti."""
    assert not hasattr(teams_cache, "thread_from_value")
    assert not hasattr(teams_cache, "calendar_from_value")
    # Ham kayittaki sohbet kimligi de tasinmiyor (yalnizca `raw` icinde durur).
    record = teams_cache.call_from_value({"callId": "a", "threadId": GROUP_THREAD})
    assert not hasattr(record, "thread_id")
    assert record.raw["threadId"] == GROUP_THREAD


# --- gruplar: kimlik katilimci kumesidir ---------------------------------


def group_rows() -> list[dict]:
    """Ayni uclunun iki aramasi, baska bir ikilinin bir aramasi."""
    return intake.normalize(
        [
            call(
                "grup-1",
                moment(1),
                minutes=30,
                call_type=TYPE_MULTI_PARTY,
                participants=[ME, PERSON_ONE, PERSON_TWO],
            ),
            call(
                "grup-2",
                moment(3),
                minutes=10,
                call_type=TYPE_MULTI_PARTY,
                # Ayni kisiler, baska sira: ayni grup.
                participants=[PERSON_TWO, ME, PERSON_ONE],
            ),
            call(
                "grup-3",
                moment(2),
                minutes=5,
                call_type=TYPE_MULTI_PARTY,
                participants=[ME, PERSON_ONE, PERSON_THREE],
            ),
            call("birebir", moment(1), minutes=9, other=PERSON_ONE),
        ],
        names={
            PERSON_ONE: "Örnek Kişi",
            PERSON_TWO: "İkinci Örnek",
            PERSON_THREE: "Üçüncü Örnek",
            ME: "Ben",
        },
        me=ME,
    )


def test_the_same_people_make_one_group_whatever_the_order():
    groups = intake.group_totals(group_rows(), me=ME)
    assert len(groups) == 2
    first = groups[0]
    assert first["count"] == 2
    assert first["ms"] == 40 * 60000
    assert first["duration_text"] == "40 dk"
    assert first["people_count"] == 2
    # Son arama en yeni olanindir.
    assert first["last_at"] == max(
        row["started_at"] for row in group_rows() if row["call_id"] in ("grup-1", "grup-2")
    )


def test_the_group_key_is_the_sorted_participant_set_without_me():
    row = [item for item in group_rows() if item["call_id"] == "grup-1"][0]
    assert intake.group_key(row, me=ME) == "|".join(sorted([PERSON_ONE, PERSON_TWO]))
    # Birebir aramanin grup kimligi yoktur.
    personal = [item for item in group_rows() if item["call_id"] == "birebir"][0]
    assert intake.group_key(personal, me=ME) == ""


def test_the_group_label_comes_from_the_participant_names():
    groups = intake.group_totals(group_rows(), me=ME)
    names = {group["name"] for group in groups}
    # Adlar alfabetik: etiket hangi aramanin once geldigine gore degismez.
    assert names == {"İkinci Örnek, Örnek Kişi", "Örnek Kişi, Üçüncü Örnek"}
    # Tam liste ayri alanda doner (cekmece ve ipucu onu gosterir).
    assert groups[0]["participants"] == ["İkinci Örnek", "Örnek Kişi"]


def test_a_crowded_group_label_stops_at_three_names():
    people = [f"8:orgid:kisi-{index}" for index in range(6)]
    names = {person: f"Kişi {index}" for index, person in enumerate(people)}
    rows = intake.normalize(
        [call("a", moment(1), minutes=5, call_type=TYPE_MULTI_PARTY, participants=people)],
        names=names,
    )
    group = intake.group_totals(rows, names)[0]
    assert group["name"].endswith("+3")
    assert len(group["participants"]) == 6


def test_a_group_call_without_participants_has_no_group():
    rows = intake.normalize([call("a", moment(1), call_type=TYPE_MULTI_PARTY)])
    assert intake.group_totals(rows) == []


def test_the_list_can_be_filtered_by_group():
    rows = group_rows()
    key = intake.group_key(
        [item for item in rows if item["call_id"] == "grup-1"][0], me=ME
    )
    picked = intake.select(rows, group=key, now=NOW, me=ME)
    assert {row["call_id"] for row in picked} == {"grup-1", "grup-2"}


def test_the_card_carries_the_group_key_to_the_screen():
    rows = group_rows()
    card = intake.view([item for item in rows if item["call_id"] == "grup-1"][0], me=ME)
    assert card["group_key"] == intake.group_key(
        [item for item in rows if item["call_id"] == "grup-1"][0], me=ME
    )
    # Kendim katilimci listesinde gorunmem.
    assert "Ben" not in card["participant_names"]


def test_participant_names_are_resolved_once_and_stored():
    """Ad tarama aninda profillerden cozulur; ekran sozluge muhtac kalmaz."""
    rows = intake.normalize(
        [call("a", moment(1), call_type=TYPE_MULTI_PARTY, participants=[PERSON_ONE])],
        names={PERSON_ONE: "Örnek Kişi"},
    )
    assert intake.participant_pairs(rows[0]) == [{"id": PERSON_ONE, "name": "Örnek Kişi"}]
    # Sozluk verilmese bile ad ekranda durur.
    assert intake.display_party(rows[0]) == "Örnek Kişi"


def test_the_card_has_no_invitees_any_more():
    """Davetliler takvimden geliyordu; takvim okumasi kalkti."""
    rows = intake.normalize(
        [call("a", moment(1), call_type=TYPE_MULTI_PARTY, participants=[PERSON_ONE])],
        names={PERSON_ONE: "Örnek Kişi"},
    )
    card = intake.view(rows[0])
    assert card["participant_names"] == ["Örnek Kişi"]
    assert "attendees" not in card


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


def seed_api(api_client, fake_calls):
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
    return api_client.post("/api/calls/scan").json()


def test_the_scan_endpoint_summarises_the_run(api_client, fake_calls):
    result = seed_api(api_client, fake_calls)
    assert result["scanned"] == 4
    assert result["new"] == 4
    assert result["updated"] == 0
    assert result["groups"] == 1
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
    assert data["calls"][0]["kind_label"] in ("Birebir", "Grup araması")
    assert {person["name"] for person in data["people"]} == {"Örnek Kişi", "İkinci Örnek"}


def test_the_list_endpoint_filters(api_client, fake_calls):
    seed_api(api_client, fake_calls)
    assert api_client.get("/api/calls?days=30&direction=Incoming").json()["count"] == 1
    assert api_client.get("/api/calls?days=30&state=Missed").json()["count"] == 1
    assert api_client.get("/api/calls?days=30&kind=group_call").json()["count"] == 1
    # "İkinci Örnek" hem birebir aramada hem grup aramasinin katilimcisinda gecer.
    assert api_client.get("/api/calls?days=30&q=ikinci").json()["count"] == 2
    assert api_client.get("/api/calls?days=30&q=bilinmeyen").json()["count"] == 0


def test_the_stats_endpoint_answers_the_four_boxes(api_client, fake_calls):
    seed_api(api_client, fake_calls)
    stats = api_client.get("/api/calls/stats?days=90").json()
    assert stats["total_ms"] == 60 * 60000
    # Dagilim iki dilim: birebir ve grup. Toplanti dilimi kalmadi.
    assert [item["kind"] for item in stats["split"]] == ["one_to_one", "group_call"]
    assert stats["direction"]["missed"] == 1
    assert stats["top"][0]["name"] == "Örnek Kişi"
    # Ust seridin dort kutusu: birebir, grup, toplam, dagilim.
    assert stats["group_top"][0]["duration_text"] == "30 dk"
    assert stats["combined_top"][0]["name"] == "Örnek Kişi"
    # Kaldirilan kutular yanitta da yok.
    assert "workday" not in stats


def test_the_view_endpoint_lists_the_groups_and_filters_by_one(api_client, fake_calls):
    seed_api(api_client, fake_calls)
    data = api_client.get("/api/calls/view?days=90").json()
    assert len(data["groups"]) == 1
    group = data["groups"][0]
    assert group["count"] == 1
    assert group["duration_text"] == "30 dk"
    assert sorted(group["participants"]) == ["Örnek Kişi", "İkinci Örnek"]

    picked = api_client.get(f"/api/calls/view?days=90&group={group['key']}").json()
    assert [card["call_id"] for card in picked["calls"]] == ["grup"]
    # Grup suzgeci acikken bile Gruplar sekmesi butun gruplari gosterir.
    assert len(picked["groups"]) == 1


def test_the_person_endpoint_returns_the_drawer_payload(api_client, fake_calls):
    seed_api(api_client, fake_calls)
    data = api_client.get(f"/api/calls/person/{PERSON_ONE}?days=90").json()
    assert data["person"]["name"] == "Örnek Kişi"
    assert len(data["calls"]) == 2
    assert len(data["group_calls"]) == 1
    assert data["summary"]["total_text"] == "30 dk"


def test_the_export_can_be_read_back(api_client, fake_calls):
    seed_api(api_client, fake_calls)
    response = api_client.get("/api/calls/export.xlsx?days=90")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(
        "application/vnd.openxmlformats"
    )

    book = load_workbook(io.BytesIO(response.content))
    assert book.sheetnames == ["Aramalar", "Kişiler", "Gruplar", "İstatistik"]

    sheet = book["Aramalar"]
    headers = [cell.value for cell in sheet[1]]
    assert headers[:6] == ["Tarih", "Yön", "Karşı taraf", "Tür", "Durum", "Süre"]
    assert headers[-1] == "Katılanlar"
    assert sheet.max_row == 5
    kinds = {sheet.cell(row=line, column=4).value for line in range(2, 6)}
    assert kinds == {"Birebir", "Grup araması"}
    assert "Toplantı" not in kinds

    people = book["Kişiler"]
    assert [cell.value for cell in people[1]][:3] == ["Kişi", "Görüşme", "Süre"]
    assert people.max_row >= 2

    groups = book["Gruplar"]
    assert [cell.value for cell in groups[1]] == [
        "Grup", "Arama", "Süre", "Süre (dk)", "Son arama", "Kişi", "Katılımcılar"
    ]
    assert groups.max_row == 2
    assert groups.cell(row=2, column=2).value == 1
    assert "Örnek Kişi" in groups.cell(row=2, column=7).value

    stats = book["İstatistik"]
    labels = {stats.cell(row=line, column=1).value for line in range(1, stats.max_row + 1)}
    assert "Toplam temas" in labels
    assert "Grupta en çok görüşülen" in labels
    assert "İş günü başına" not in labels


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
        "participants_json",
        "raw_json",
        "seen_at",
    } <= columns
    # Toplantiya ve sohbete ait sutunlar gocle dustu.
    assert columns.isdisjoint({
        "meeting_subject",
        "meeting_organizer",
        "my_response",
        "attendees_json",
        "source",
        "thread_id",
        "group_thread_id",
        "topic",
    })


def test_the_raw_record_is_kept_for_later(conn):
    intake.scan(conn, FakeCallSource(calls=[call("a", moment(1))]))
    row = repo.get_call(conn, "a")
    assert json.loads(row["raw_json"])["callId"] == "a"
