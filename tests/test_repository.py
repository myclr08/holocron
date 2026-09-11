"""Veri katmani: anahtar ayristirma, gruplar, uyelikler, kayit yazma."""

from __future__ import annotations

import pytest

from app import repository as repo
from tests.fake_jira import issue

DEMO_JQL = "project = DEMO AND status = Acik"


# --- anahtar ayristirici ------------------------------------------------


def test_parse_reads_plain_keys_in_order():
    assert repo.parse_issue_keys("DEMO-2 DEMO-1") == ["DEMO-2", "DEMO-1"]


def test_parse_upper_cases_and_drops_duplicates():
    assert repo.parse_issue_keys("demo-1, DEMO-1\nDemo-1") == ["DEMO-1"]


def test_parse_reads_keys_from_browse_urls():
    text = "https://jira.example.com/browse/DEMO-7?filter=1 ve https://jira.example.com/browse/ORN-9"
    assert repo.parse_issue_keys(text) == ["DEMO-7", "ORN-9"]


def test_parse_ignores_dates_and_plain_numbers():
    assert repo.parse_issue_keys("2026-09-11 tarihinde 12-34 oldu") == []


def test_parse_handles_underscore_and_digit_projects():
    assert repo.parse_issue_keys("A_B2-15") == ["A_B2-15"]


def test_parse_empty_input():
    assert repo.parse_issue_keys("") == []
    assert repo.parse_issue_keys(None) == []


def test_parse_report_separates_invalid_tokens():
    keys, invalid = repo.parse_keys_report("DEMO-1 salatalik DEMO-2")
    assert keys == ["DEMO-1", "DEMO-2"]
    assert invalid == ["salatalik"]


# --- gruplar ------------------------------------------------------------


def test_create_group_defaults(conn):
    group = repo.create_group(conn, "Takip", repo.KIND_MANUAL)
    assert group["kind"] == "manual"
    assert group["color"] == "blue"
    assert group["columns"] == []
    assert group["position"] == 0
    assert group["count"] == 0
    # Sutun secilmemisse genel varsayilan gecerli olur.
    assert repo.group_columns(conn, group) == list(repo.FALLBACK_COLUMNS)


def test_filter_group_requires_jql(conn):
    with pytest.raises(repo.RepositoryError) as excinfo:
        repo.create_group(conn, "Filtre", repo.KIND_FILTER)
    assert excinfo.value.code == "invalid_jql"


def test_manual_group_drops_jql(conn):
    group = repo.create_group(conn, "Takip", repo.KIND_MANUAL, jql=DEMO_JQL)
    assert group["jql"] == ""


def test_group_name_cannot_be_blank(conn):
    with pytest.raises(repo.RepositoryError) as excinfo:
        repo.create_group(conn, "   ", repo.KIND_MANUAL)
    assert excinfo.value.code == "invalid_name"


def test_color_must_be_in_palette(conn):
    with pytest.raises(repo.RepositoryError) as excinfo:
        repo.create_group(conn, "Takip", repo.KIND_MANUAL, color="pembe")
    assert excinfo.value.code == "invalid_color"
    for color in repo.GROUP_COLORS:
        assert repo.create_group(conn, color, repo.KIND_MANUAL, color=color)["color"] == color


def test_positions_increase_and_list_is_ordered(conn):
    first = repo.create_group(conn, "Bir", repo.KIND_MANUAL)
    second = repo.create_group(conn, "Iki", repo.KIND_MANUAL)
    assert [g["id"] for g in repo.list_groups(conn)] == [first["id"], second["id"]]

    repo.reorder_groups(conn, [second["id"], first["id"]])
    assert [g["name"] for g in repo.list_groups(conn)] == ["Iki", "Bir"]


def test_reorder_keeps_unlisted_groups(conn):
    first = repo.create_group(conn, "Bir", repo.KIND_MANUAL)
    second = repo.create_group(conn, "Iki", repo.KIND_MANUAL)
    third = repo.create_group(conn, "Uc", repo.KIND_MANUAL)
    repo.reorder_groups(conn, [third["id"]])
    assert [g["id"] for g in repo.list_groups(conn)] == [third["id"], first["id"], second["id"]]


def test_update_group_changes_fields(conn):
    group = repo.create_group(conn, "Takip", repo.KIND_MANUAL)
    updated = repo.update_group(
        conn,
        group["id"],
        {"name": "Yeni ad", "color": "red", "columns": ["issuekey", "summary"],
         "sort": {"field": "summary", "dir": "desc"}},
    )
    assert updated["name"] == "Yeni ad"
    assert updated["color"] == "red"
    assert updated["columns"] == ["issuekey", "summary"]
    assert updated["sort"] == {"field": "summary", "dir": "desc"}


def test_update_group_to_filter_requires_jql(conn):
    group = repo.create_group(conn, "Takip", repo.KIND_MANUAL)
    with pytest.raises(repo.RepositoryError):
        repo.update_group(conn, group["id"], {"kind": repo.KIND_FILTER})
    updated = repo.update_group(conn, group["id"], {"kind": repo.KIND_FILTER, "jql": DEMO_JQL})
    assert updated["kind"] == "filter" and updated["jql"] == DEMO_JQL


def test_invalid_sort_direction_is_rejected(conn):
    group = repo.create_group(conn, "Takip", repo.KIND_MANUAL)
    with pytest.raises(repo.RepositoryError) as excinfo:
        repo.update_group(conn, group["id"], {"sort": {"field": "summary", "dir": "yukari"}})
    assert excinfo.value.code == "invalid_sort"


def test_missing_group_raises_not_found(conn):
    with pytest.raises(repo.RepositoryError) as excinfo:
        repo.require_group(conn, 404)
    assert excinfo.value.status == 404


def test_delete_group_removes_items(conn):
    group = repo.create_group(conn, "Takip", repo.KIND_MANUAL)
    repo.add_items(conn, group["id"], ["DEMO-1"])
    repo.delete_group(conn, group["id"])
    assert repo.list_groups(conn) == []
    assert conn.execute("SELECT COUNT(*) AS c FROM group_items").fetchone()["c"] == 0


# --- uyelikler ----------------------------------------------------------


def test_add_items_reports_added_and_already(conn):
    group = repo.create_group(conn, "Takip", repo.KIND_MANUAL)
    first = repo.add_items(conn, group["id"], ["DEMO-1", "DEMO-2"])
    assert first == {"added": ["DEMO-1", "DEMO-2"], "already": []}

    second = repo.add_items(conn, group["id"], ["demo-2", "DEMO-3"])
    assert second == {"added": ["DEMO-3"], "already": ["DEMO-2"]}
    assert repo.list_item_keys(conn, group["id"]) == ["DEMO-1", "DEMO-2", "DEMO-3"]


def test_manual_group_items_are_not_pinned(conn):
    group = repo.create_group(conn, "Takip", repo.KIND_MANUAL)
    repo.add_items(conn, group["id"], ["DEMO-1"])
    assert repo.list_items(conn, group["id"])[0]["pinned"] is False


def test_hand_added_item_in_filter_group_is_pinned(conn):
    group = repo.create_group(conn, "Filtre", repo.KIND_FILTER, jql=DEMO_JQL)
    repo.add_items(conn, group["id"], ["DEMO-9"])
    assert repo.list_items(conn, group["id"])[0]["pinned"] is True


def test_toggle_pin_flips_state(conn):
    group = repo.create_group(conn, "Takip", repo.KIND_MANUAL)
    repo.add_items(conn, group["id"], ["DEMO-1"])
    assert repo.toggle_pin(conn, group["id"], "demo-1") is True
    assert repo.toggle_pin(conn, group["id"], "DEMO-1") is False


def test_remove_and_pin_need_existing_item(conn):
    group = repo.create_group(conn, "Takip", repo.KIND_MANUAL)
    with pytest.raises(repo.RepositoryError) as excinfo:
        repo.remove_item(conn, group["id"], "DEMO-1")
    assert excinfo.value.status == 404
    with pytest.raises(repo.RepositoryError):
        repo.toggle_pin(conn, group["id"], "DEMO-1")


def test_replace_filter_members_keeps_pinned(conn):
    group = repo.create_group(conn, "Filtre", repo.KIND_FILTER, jql=DEMO_JQL)
    repo.add_items(conn, group["id"], ["DEMO-1", "DEMO-2"])  # filtre grubunda ikisi de iglenir
    repo.toggle_pin(conn, group["id"], "DEMO-2")  # DEMO-2 artik igsiz

    result = repo.replace_filter_members(conn, group["id"], ["DEMO-3"])
    keys = repo.list_item_keys(conn, group["id"])

    assert result == {"added": 1, "removed": 1, "pinned_kept": 1}
    assert set(keys) == {"DEMO-1", "DEMO-3"}  # iglenmis DEMO-1 JQL'de olmasa da kaldi


def test_replace_filter_members_is_idempotent(conn):
    group = repo.create_group(conn, "Filtre", repo.KIND_FILTER, jql=DEMO_JQL)
    repo.replace_filter_members(conn, group["id"], ["DEMO-1", "DEMO-2"])
    again = repo.replace_filter_members(conn, group["id"], ["DEMO-1", "DEMO-2"])
    assert again["added"] == 0 and again["removed"] == 0
    assert repo.list_item_keys(conn, group["id"]) == ["DEMO-1", "DEMO-2"]


# --- kayitlar -----------------------------------------------------------


def test_upsert_marks_new_then_unchanged_then_updated(conn):
    first = repo.upsert_issues(conn, [issue("DEMO-1", "Ilk ozet")])
    assert first.new == ["DEMO-1"] and first.fetched == 1

    same = repo.upsert_issues(conn, [issue("DEMO-1", "Ilk ozet")])
    assert same.unchanged == ["DEMO-1"] and same.changed == {}

    changed = repo.upsert_issues(conn, [issue("DEMO-1", "Yeni ozet")])
    assert changed.updated == ["DEMO-1"]
    assert changed.changed == {"DEMO-1": ["summary"]}


def test_upsert_stores_raw_and_id(conn):
    repo.upsert_issues(conn, [issue("demo-1", "Ozet")])
    record = repo.get_issue(conn, "DEMO-1")
    assert record is not None
    assert record["raw"]["fields"]["summary"] == "Ozet"
    assert record["fetched_at"]
    # Anahtar her zaman buyuk harfle saklanir.
    assert repo.get_issue(conn, "demo-1")["key"] == "DEMO-1"


def test_get_issues_reads_many(conn):
    repo.upsert_issues(conn, [issue("DEMO-1"), issue("DEMO-2")])
    found = repo.get_issues(conn, ["DEMO-1", "demo-2", "DEMO-3"])
    assert set(found) == {"DEMO-1", "DEMO-2"}


def test_purge_orphan_issues_keeps_group_members(conn):
    group = repo.create_group(conn, "Takip", repo.KIND_MANUAL)
    repo.add_items(conn, group["id"], ["DEMO-1"])
    repo.upsert_issues(conn, [issue("DEMO-1"), issue("DEMO-2")])

    assert repo.purge_orphan_issues(conn) == 1
    assert repo.issue_count(conn) == 1
    assert repo.get_issue(conn, "DEMO-1") is not None


# --- sutunlar ve alan katalogu ------------------------------------------


def test_default_columns_fall_back_then_persist(conn):
    assert repo.default_columns(conn) == list(repo.FALLBACK_COLUMNS)
    saved = repo.set_default_columns(conn, ["issuekey", "summary", "summary", " "])
    assert saved == ["issuekey", "summary"]
    assert repo.default_columns(conn) == ["issuekey", "summary"]


def test_default_columns_cannot_be_empty(conn):
    with pytest.raises(repo.RepositoryError):
        repo.set_default_columns(conn, [])


def test_store_fields_replaces_catalog_and_adds_virtual(conn):
    repo.store_fields(conn, [{"id": "summary", "name": "Ozet", "schema": {"type": "string"}}])
    ids = [item["id"] for item in repo.list_fields(conn)]
    assert ids[0] == "issuekey"  # sanal alan katalogun basinda
    assert "summary" in ids

    schemas = repo.field_schemas(conn)
    assert schemas["summary"]["schema"]["type"] == "string"
    assert schemas["issuekey"]["name"] == "Anahtar"


def test_catalog_key_field_overrides_virtual(conn):
    """Jira katalogu 'issuekey' kimligiyle Key alanini veriyorsa tekrar cikmaz."""
    repo.store_fields(conn, [{"id": "issuekey", "name": "Key", "schema": {"type": "string"}}])
    ids = [item["id"] for item in repo.list_fields(conn)]
    assert ids.count("issuekey") == 1
