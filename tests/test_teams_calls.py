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
from app.teamscalls.fake import (
    ME,
    PERSON_ONE,
    PERSON_TWO,
    FakeCallSource,
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
)

# Cuma 12:00 UTC: pencere hesabi makinenin gunune bagli kalmasin.
NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)


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


# --- ham kayit cozumu (IndexedDB yapisi) ----------------------------------


def test_the_call_database_is_recognised_by_name():
    assert teams_cache.is_call_database("call-history-manager-db") is True
    assert teams_cache.is_call_database("replychain-calendar") is False


def test_the_calendar_database_skips_the_reply_chain():
    assert teams_cache.is_calendar_database("calendar-db") is True
    assert teams_cache.is_calendar_database("replychain-calendar-db") is False


def test_name_stores_are_recognised():
    for name in ("profiles", "internal-data", "capiv3-contacts"):
        assert teams_cache.is_name_store(name) is True
    assert teams_cache.is_name_store("call-history") is False


def test_a_raw_call_record_becomes_a_call():
    record = teams_cache.call_from_value(
        {
            "callId": "17:abc",
            "startTime": "2026-09-11T08:30:00Z",
            "endTime": "2026-09-11T08:42:00Z",
            "connectTime": "2026-09-11T08:30:10Z",
            "durationInMs": 710000,
            "callDirection": "Outgoing",
            "callState": "Accepted",
            "callType": "TwoParty",
            "originatorParticipant": {"displayName": "Ben", "id": ME},
            "targetParticipant": {"id": PERSON_ONE},
            "forwardedTargetType": "",
            "participants": [{"id": PERSON_ONE}, PERSON_TWO],
        }
    )
    assert record.call_id == "17:abc"
    assert record.duration_ms == 710000
    assert record.target_id == PERSON_ONE
    assert record.originator_name == "Ben"
    assert record.participants == [PERSON_ONE, PERSON_TWO]


def test_a_raw_record_without_an_id_is_dropped():
    assert teams_cache.call_from_value({"startTime": "2026-09-11T08:30:00Z"}) is None
    assert teams_cache.call_from_value("metin") is None


def test_a_raw_calendar_record_becomes_an_event():
    record = teams_cache.calendar_from_value(
        {
            "id": "event-1",
            "startTime": "2026-09-11 11:30:00",
            "endTime": "2026-09-11 12:00:00",
            "subject": "Bütçe toplantısı",
            "organizerName": "Örnek Kişi",
            "myResponseType": "Accepted",
            "isOnlineMeeting": True,
            "eventType": "SingleInstance",
            "showAs": "Busy",
            "attendees": [{"displayName": "İkinci Örnek"}],
        }
    )
    assert record.subject == "Bütçe toplantısı"
    assert record.organizer_name == "Örnek Kişi"
    assert record.attendees == ["İkinci Örnek"]
    assert teams_cache.calendar_from_value({"subject": "saatsiz"}) is None


def test_profile_records_feed_the_name_map():
    assert teams_cache.names_from_value({"mri": PERSON_ONE, "displayName": "Örnek Kişi"}) == {
        PERSON_ONE: "Örnek Kişi"
    }
    # Kabuk icindeki kayit da cozulur.
    assert teams_cache.names_from_value(
        {"value": {"id": PERSON_TWO, "imDisplayName": "İkinci Örnek"}}
    ) == {PERSON_TWO: "İkinci Örnek"}
    assert teams_cache.names_from_value({"displayName": "adsız"}) == {}


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
    assert [cell.value for cell in sheet[1]][:6] == [
        "Tarih",
        "Yön",
        "Karşı taraf",
        "Tür",
        "Durum",
        "Süre",
    ]
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
