"""Yapi sondasinin gizlilik suzgeci ve kopyalama dayanikliligi.

Sondanin sozu tek cumlelik: cikti baskasina yapistirilabilir olmali. Bu
dosya o sozu koruyor -- bir dizge degerinin rapora sizmasi buradaki
testlerden birini kirmadan mumkun olmamali.
"""

from __future__ import annotations

import datetime as dt
import importlib.util
import sys
import zipfile
from collections import Counter
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(name: str, relative: str):
    """tools/ bir paket degil; dosyayi dogrudan yukluyoruz.

    Modul once `sys.modules`e yazilir: `@dataclass` sinifin modulunu oradan
    okuyor, kayitsiz modulde cozumleme `None` dondurup patliyor.
    """
    path = ROOT / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


probe = _load("teams_probe", "tools/teams_probe/probe.py")
builder = _load("build_portable_probe", "tools/build_portable.py")


# --- vendor ---------------------------------------------------------------


def test_vendored_reader_imports_without_pip():
    """Kurum vekili pip'i engellese de okuyucu yerinde durmali."""
    module = probe.load_reader()
    assert hasattr(module, "WrappedIndexDB")
    assert hasattr(module, "IndexedDb")


def test_vendored_leveldb_and_deserializers_are_there():
    probe.load_reader()
    from ccl_chromium_reader.serialization_formats import (  # noqa: PLC0415
        ccl_blink_value_deserializer,
        ccl_v8_value_deserializer,
    )
    from ccl_chromium_reader.storage_formats import ccl_leveldb  # noqa: PLC0415

    assert hasattr(ccl_leveldb, "RawLevelDb")
    assert hasattr(ccl_v8_value_deserializer, "Deserializer")
    assert hasattr(ccl_blink_value_deserializer, "BlinkV8Deserializer")


def test_vendor_carries_its_licences():
    vendor = ROOT / "app" / "vendor"
    assert (vendor / "ccl_chromium_reader" / "LICENSE").is_file()
    assert (vendor / "ccl_simplesnappy" / "LICENSE").is_file()
    readme = (vendor / "README.md").read_text(encoding="utf-8")
    assert "MIT" in readme


# --- gizlilik: hicbir dizge degeri ciktiya girmez -------------------------

SECRET = "Ahmet Yılmaz <ahmet@örnek.com> gizli mesaj"


@pytest.mark.parametrize(
    "name",
    ["content", "text", "body", "displayName", "mri", "email", "imdisplayname", "subject"],
)
def test_sensitive_fields_never_show_their_value(name):
    stats = probe.summarize_records([{name: SECRET}])
    line = stats[name].detail_text()
    assert SECRET not in line
    assert "Ahmet" not in line and "ahmet" not in line
    assert "str" in line and "uzunluk" in line


def test_redact_value_reduces_every_string_to_a_length():
    assert probe.redact_value(SECRET) == f"str(len={len(SECRET)})"
    assert SECRET not in probe.redact_value(SECRET)
    assert probe.redact_value(b"0123") == "bytes(len=4)"
    assert probe.redact_value([1, 2, 3]) == "list(n=3)"
    assert probe.redact_value({"a": 1}) == "dict(n=1)"
    assert probe.redact_value(None) == "null"
    assert probe.redact_value(7) == "int(7)"
    assert probe.redact_value(True) == "bool(True)"


def test_no_string_value_survives_a_full_render():
    """Uctan uca: rapor metninde hicbir dizge degeri gecmemeli."""
    record = {
        "callId": "CALL-SECRET-0001",
        "originatorParticipant": {"displayName": SECRET, "mri": "8:orgid:gizli-kimlik"},
        "subject": "Bütçe toplantısı",
        "durationInMs": 61000,
    }
    store = probe.StoreReport(name="call-history", records=1)
    store.stats = probe.summarize_records([record])
    report = probe.DatabaseReport(name="call-history-manager", stores=[store])
    text = probe.render(Path("x.leveldb"), None, None, [report], probe.Counter(), 0.4)

    for leak in ("CALL-SECRET-0001", SECRET, "Ahmet", "gizli-kimlik", "Bütçe"):
        assert leak not in text, leak
    # Alan ADLARI ise gorunmeli: sondanin butun isi bu.
    for wanted in ("callId", "originatorParticipant.displayName", "durationInMs"):
        assert wanted in text, wanted


def test_string_statistics_are_lengths_only():
    stats = probe.summarize_records([{"body": "abc"}, {"body": "abcdefg"}])
    line = stats["body"].detail_text()
    assert "uzunluk ort 5 / maks 7" in line
    assert "abc" not in line


# --- alan yollari ---------------------------------------------------------


def test_nested_paths_are_dotted():
    stats = probe.summarize_records([{"a": {"b": {"c": 1}}}])
    assert "a" in stats and "a.b" in stats and "a.b.c" in stats


def test_paths_stop_at_depth_four():
    stats = probe.summarize_records([{"a": {"b": {"c": {"d": {"e": 1}}}}}])
    assert "a.b.c.d" in stats
    assert not any(path.count(".") >= 4 for path in stats), sorted(stats)


def test_list_items_collapse_into_one_path():
    stats = probe.summarize_records([{"attendees": [{"id": "x"}, {"id": "y"}]}])
    assert "attendees[]" in stats
    assert "attendees[].id" in stats
    assert stats["attendees[].id"].seen == 2
    assert "attendees[0]" not in stats


def test_sample_limit_is_honoured():
    records = [{"n": index} for index in range(500)]
    stats = probe.summarize_records(records, limit=200)
    assert stats["n"].seen == 200


def test_non_dictionary_records_land_under_a_root_entry():
    stats = probe.summarize_records(["duz metin", 5])
    assert "(kok)" in stats
    assert "duz metin" not in stats["(kok)"].detail_text()


# --- tarih tespiti --------------------------------------------------------


def test_thirteen_digit_epoch_is_read_as_a_date():
    moment = probe.epoch_ms_to_moment(1_757_000_000_000)
    assert moment is not None and moment.year == 2025


def test_ten_digit_epoch_is_not_a_date():
    assert probe.epoch_ms_to_moment(1_757_000_000) is None
    assert probe.epoch_ms_to_moment(True) is None
    assert probe.epoch_ms_to_moment("1757000000000") is None


def test_date_looking_names_are_recognised():
    for name in ("startTime", "createdAt", "arrivalTime", "composetime", "ts", "endDate"):
        assert probe.name_is_datish(name), name
    assert not probe.name_is_datish("subject")


def test_iso_strings_only_count_when_the_name_promises_a_date():
    assert probe.parse_moment("startTime", "2026-09-12T08:30:00Z") is not None
    # Ad tarih vaat etmiyorsa metin metindir: tarihe cevrilmez, basilmaz.
    assert probe.parse_moment("subject", "2026-09-12T08:30:00Z") is None


def test_min_and_max_dates_are_reported():
    stats = probe.summarize_records(
        [
            {"startTime": "2026-01-05T10:00:00Z"},
            {"startTime": "2026-03-09T12:00:00Z"},
            {"startTime": "2026-02-01T09:00:00Z"},
        ]
    )
    line = stats["startTime"].detail_text()
    assert "2026-01-05" in line and "2026-03-09" in line
    assert "2026-02-01" not in line


def test_real_datetime_values_are_dates_too():
    moment = dt.datetime(2026, 5, 4, 3, 2, 1, tzinfo=dt.timezone.utc)
    stats = probe.summarize_records([{"whatever": moment}])
    assert stats["whatever"].is_dated


# --- kopyalama ------------------------------------------------------------


def test_copy_tree_counts_what_it_copied(tmp_path):
    source = tmp_path / "src"
    (source / "deep").mkdir(parents=True)
    (source / "one.ldb").write_bytes(b"a")
    (source / "deep" / "two.log").write_bytes(b"b")

    report = probe.copy_tree(source, tmp_path / "dst")
    assert report.copied == 2
    assert report.skipped == 0
    assert (tmp_path / "dst" / "deep" / "two.log").is_file()


def test_a_locked_file_is_skipped_and_counted(tmp_path, monkeypatch):
    """Teams acikken LOCK dosyasi kopyalanamaz; sonda yine de calismali."""
    source = tmp_path / "src"
    source.mkdir()
    (source / "good.ldb").write_bytes(b"a")
    (source / "LOCK").write_bytes(b"b")

    real_copy = probe.shutil.copy2

    def picky(src, dst, **kwargs):
        if Path(src).name == "LOCK":
            raise PermissionError(13, "kilitli")
        return real_copy(src, dst, **kwargs)

    monkeypatch.setattr(probe.shutil, "copy2", picky)
    report = probe.copy_tree(source, tmp_path / "dst")

    assert report.copied == 1
    assert report.skipped == 1
    assert report.reasons["PermissionError"] == 1
    assert (tmp_path / "dst" / "good.ldb").is_file()


def test_copying_over_an_old_run_starts_clean(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "new.ldb").write_bytes(b"a")
    target = tmp_path / "dst"
    target.mkdir()
    (target / "stale.ldb").write_bytes(b"eski")

    probe.copy_tree(source, target)
    assert not (target / "stale.ldb").exists()


# --- yollar ---------------------------------------------------------------


def test_default_path_is_built_from_local_appdata():
    path = probe.default_cache_path({"LOCALAPPDATA": r"C:\Users\x\AppData\Local"})
    parts = [part.casefold() for part in path.parts]
    assert "msteams_8wekyb3d8bbwe" in parts
    assert path.name == "https_teams.microsoft.com_0.indexeddb.leveldb"


def test_blob_folder_sits_next_to_the_leveldb():
    leveldb = Path("/tmp/https_teams.microsoft.com_0.indexeddb.leveldb")
    assert probe.blob_dir_for(leveldb).name == "https_teams.microsoft.com_0.indexeddb.blob"


def test_missing_folder_is_reported_not_crashed(tmp_path, capsys):
    assert probe.run(path=tmp_path / "yok") == 2
    assert "bulunamadi" in capsys.readouterr().err


# --- aday ve hedef bolumleri ---------------------------------------------


@pytest.mark.parametrize(
    "name", ["conversation", "messages", "threadStore", "call-history", "people", "contacts", "replychain"]
)
def test_candidate_stores_are_spotted(name):
    assert probe.is_candidate(name)


def test_unrelated_stores_are_not_candidates():
    assert not probe.is_candidate("settings")


def test_replychain_calendar_is_not_a_target_database():
    assert probe.is_target_database("calendar")
    assert probe.is_target_database("call-history-manager")
    assert not probe.is_target_database("replychain-calendar")


# --- paket ---------------------------------------------------------------


def test_probe_zip_carries_the_script_the_readme_and_the_vendor(tmp_path):
    output = builder.build_probe(tmp_path / "dist")
    assert output.name == "holocron-teams-probe.zip"

    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()

    assert all(name.startswith("holocron-teams-probe/") for name in names)
    roots = {name.split("/")[1] for name in names}
    assert roots == {"probe.py", "README.md", "vendor"}
    assert any(name.endswith("vendor/ccl_chromium_reader/ccl_chromium_indexeddb.py") for name in names)
    assert any(name.endswith("vendor/ccl_simplesnappy/ccl_simplesnappy.py") for name in names)
    assert any(name.endswith("vendor/ccl_chromium_reader/LICENSE") for name in names)
    assert not any("__pycache__" in name or name.endswith(".pyc") for name in names)


def test_the_probe_never_enters_the_application_packages():
    for variant in (builder.VARIANT_FULL, builder.VARIANT_LITE):
        entries = builder.manifest("windows", variant)
        assert not any("probe" in entry for entry in entries)
        assert "tools" not in entries
    assert "tools" in builder.EXCLUDED_FROM_PACKAGE


def test_probe_dry_run_names_its_own_zip(capsys):
    assert builder.main(["--probe", "--dry-run"]) == 0
    output = capsys.readouterr().out
    assert "holocron-teams-probe.zip" in output
    assert "vendor/" in output


# --- sonda v2/v3: toplanti katilimi (--meetings) -------------------------
#
# Bu mod `replychains` store'unu olcer. Gizlilik kurali aynen gecerli:
# ad, mesaj metni, sure DEGERI ve kimlik ciktiya girmez.

MEETING_XML = (
    '<partlist alt="Toplantı sona erdi">'
    "<calleventtype>ended</calleventtype>"
    "<callid>cagri-9</callid>"
    "<meetingdetails><icaluid>ICAL-42</icaluid><meetingtype>Scheduled</meetingtype>"
    "<organizerupn>gizli@example.com</organizerupn></meetingdetails>"
    '<part identity="8:orgid:benim"><name>Gizli Ad</name><duration>1863</duration></part>'
    '<part identity="8:orgid:baska"><name>Başka Ad</name><duration>1800</duration></part>'
    "</partlist>"
)

USER_GUID = "0f8fad5b-d9cb-469f-a165-70867728950e"


def meeting_report():
    report = probe.ChainReport("toplanti sohbetleri (19:meeting_)")
    probe.scan_message(
        report,
        {
            "messageType": "Event/Call",
            "content": MEETING_XML,
            "originalArrivalTime": 1789000000000,
        },
        "8:orgid:benim",
    )
    return report


def test_the_meeting_probe_counts_without_printing_values():
    report = meeting_report()
    assert report.partlists == 1
    assert report.parts_max == 2
    assert report.with_duration == 1
    assert report.mine == 1
    assert report.event_types == Counter({"ended": 1})
    assert report.meeting_types == Counter({"Scheduled": 1})


@pytest.mark.parametrize(
    "secret",
    ["Gizli Ad", "Başka Ad", "gizli@example.com", "ICAL-42", "1863", "cagri-9", "8:orgid:benim"],
)
def test_no_meeting_value_survives_the_render(secret):
    text = probe.render_meetings(Path("x/leveldb"), [meeting_report()], "veritabani adi", 1.0)
    assert secret not in text


def test_the_render_keeps_the_whole_report():
    """Iskelet dongusu ciktinin geri kalanini yutmamali."""
    text = probe.render_meetings(Path("x/leveldb"), [meeting_report()], "veritabani adi", 1.0)
    assert "TOPLANTI SOHBETLERI" in text
    assert "calleventtype     : ended x1" in text
    assert "icerik iskeleti" in text
    assert text.rstrip().endswith("Son.")


def test_the_skeleton_shows_names_and_lengths_only():
    shape = probe.skeleton_of(MEETING_XML)
    assert shape[0] == "partlist(alt)"
    assert any(line.strip() == "part(identity)" for line in shape)
    assert any(line.strip().startswith("duration(len") for line in shape)
    for line in shape:
        assert "Gizli" not in line
        assert "1863" not in line


def test_a_broken_content_does_not_break_the_skeleton():
    assert probe.skeleton_of("<partlist><part identity=") == ["(cozulemedi)"]


def test_the_user_identity_comes_from_the_database_name():
    name = f"Teams:calendar:react-web-client:11111111-1111-1111-1111-111111111111:{USER_GUID}:tr-tr"
    assert probe.mri_from_database_name(name) == f"8:orgid:{USER_GUID}"
    assert probe.mri_from_database_name(f"Teams:x:y:z:8:orgid:{USER_GUID}:tr") == (
        f"8:orgid:{USER_GUID}"
    )
    assert probe.mri_from_database_name("Teams:messages:react-web-client:a:b:tr") == ""


def test_the_probe_tells_how_it_found_the_identity_not_what_it_is():
    text = probe.render_meetings(Path("x/leveldb"), [meeting_report()], "veritabani adi", 1.0)
    assert "Kendi kimligim    : veritabani adi" in text
    assert USER_GUID not in text


@pytest.mark.parametrize(
    "identity,path",
    [
        ("8:orgid:benim", "tam"),
        ("8:ORGID:BENIM", "tam"),
        ("8:benim", "guid"),
        ("8:orgid:baskasi", "yok"),
    ],
)
def test_the_identity_path_is_reported(identity, path):
    assert probe.identity_path([identity], "8:orgid:benim") == path


def test_the_meeting_report_says_how_the_identity_matched():
    report = probe.ChainReport("t")
    probe.scan_message(
        report,
        {"messageType": "Event/Call", "content": MEETING_XML.replace("8:orgid:benim", "8:benim")},
        "8:orgid:benim",
    )
    assert report.mine == 1
    assert report.mine_guid == 1
    assert report.mine_exact == 0
    text = probe.render_meetings(Path("x"), [report], "veritabani adi", 1.0)
    assert "kendim gecen      : 1 (tam 0, guid 1)" in text
