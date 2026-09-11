"""Yerel alanlarin veri katmani: tanim, deger yazma, gecmis, turetilmis sutunlar."""

from __future__ import annotations

import pytest

from app import repository as repo
from tests.fake_jira import issue

DURUMLAR = ["bekliyor", "musteriye soruldu", "bitti"]


def make_field(conn, name="Musteri durumu", type="select", options=None, track_history=True):
    return repo.create_local_field(
        conn,
        name=name,
        type=type,
        options=DURUMLAR if (options is None and type == "select") else options,
        track_history=track_history,
    )


def seed_issue(conn, key="DEMO-1", group_name="Takip"):
    group = repo.create_group(conn, group_name, repo.KIND_MANUAL)
    repo.add_items(conn, group["id"], [key])
    repo.upsert_issues(conn, [issue(key, "Ornek")])
    return group


# --- alan tanimi --------------------------------------------------------


def test_create_field_defaults(conn):
    field = make_field(conn, "Not", type="text", track_history=False)
    assert field["type"] == "text"
    assert field["type_label"] == "Metin"
    assert field["column_id"] == f"local:{field['id']}"
    assert field["track_history"] is False
    assert field["options"] == []
    assert field["position"] == 0
    assert field["created_at"]
    assert field["value_count"] == 0 and field["history_count"] == 0


def test_field_name_is_required_and_unique_case_folded(conn):
    make_field(conn, "Musteri Durumu")
    with pytest.raises(repo.RepositoryError) as excinfo:
        make_field(conn, "  ")
    assert excinfo.value.code == "invalid_name"

    for clash in ("musteri durumu", "MUSTERI DURUMU", "Musteri Durumu"):
        with pytest.raises(repo.RepositoryError) as excinfo:
            make_field(conn, clash)
        assert excinfo.value.code == "duplicate_name"


def test_turkish_dotless_i_counts_as_the_same_name(conn):
    make_field(conn, "Ilk temas", type="text")
    with pytest.raises(repo.RepositoryError) as excinfo:
        make_field(conn, "İLK TEMAS", type="text")
    assert excinfo.value.code == "duplicate_name"


def test_unknown_type_is_refused(conn):
    with pytest.raises(repo.RepositoryError) as excinfo:
        repo.create_local_field(conn, "Sihir", "buyu")
    assert excinfo.value.code == "invalid_type"


def test_select_field_requires_options(conn):
    with pytest.raises(repo.RepositoryError) as excinfo:
        repo.create_local_field(conn, "Durum", "select", options=[])
    assert excinfo.value.code == "invalid_options"


def test_type_cannot_change_but_name_options_history_can(conn):
    field = make_field(conn, "Durum")
    with pytest.raises(repo.RepositoryError) as excinfo:
        repo.update_local_field(conn, field["id"], {"type": "text"})
    assert excinfo.value.code == "type_immutable"

    updated = repo.update_local_field(
        conn,
        field["id"],
        {"name": "Musteri durumu", "options": ["a", "b"], "track_history": False},
    )
    assert updated["name"] == "Musteri durumu"
    assert updated["options"] == ["a", "b"]
    assert updated["track_history"] is False
    # Ayni tipi gondermek hata degil.
    assert repo.update_local_field(conn, field["id"], {"type": "select"})["type"] == "select"


def test_rename_to_its_own_name_is_allowed(conn):
    field = make_field(conn, "Durum")
    assert repo.update_local_field(conn, field["id"], {"name": "durum"})["name"] == "durum"


def test_missing_field_raises_404(conn):
    with pytest.raises(repo.RepositoryError) as excinfo:
        repo.require_local_field(conn, 404)
    assert excinfo.value.status == 404


def test_reorder_fields(conn):
    first = make_field(conn, "Bir", type="text")
    second = make_field(conn, "Iki", type="text")
    third = make_field(conn, "Uc", type="text")
    ordered = repo.reorder_local_fields(conn, [third["id"], first["id"]])
    assert [item["name"] for item in ordered] == ["Uc", "Bir", "Iki"]


def test_column_id_round_trip(conn):
    assert repo.parse_local_column("local:3") == (3, "")
    assert repo.parse_local_column("local:3:changes") == (3, "changes")
    assert repo.parse_local_column("local:3:changed_at") == (3, "changed_at")
    assert repo.parse_local_column("local:3:sihir") is None
    assert repo.parse_local_column("summary") is None
    assert repo.parse_local_column("local:abc") is None


# --- deger yazma --------------------------------------------------------


def test_value_is_normalized_on_write(conn):
    seed_issue(conn)
    field = make_field(conn, "Puan", type="number", track_history=False)
    result = repo.set_local_value(conn, "demo-1", field["id"], "3,50")
    assert result["value"] == "3.5"
    assert result["key"] == "DEMO-1"
    assert repo.get_local_value(conn, "DEMO-1", field["id"]) == "3.5"


def test_invalid_value_is_refused_with_turkish_message(conn):
    seed_issue(conn)
    field = make_field(conn)
    with pytest.raises(repo.RepositoryError) as excinfo:
        repo.set_local_value(conn, "DEMO-1", field["id"], "salatalik")
    assert excinfo.value.code == "invalid_value"
    assert "seçenekler arasında yok" in excinfo.value.message
    assert repo.get_local_value(conn, "DEMO-1", field["id"]) == ""


def test_blank_value_deletes_the_cell(conn):
    seed_issue(conn)
    field = make_field(conn, "Not", type="text", track_history=False)
    repo.set_local_value(conn, "DEMO-1", field["id"], "bir sey")
    repo.set_local_value(conn, "DEMO-1", field["id"], "")
    assert repo.get_local_value(conn, "DEMO-1", field["id"]) == ""
    assert conn.execute("SELECT COUNT(*) AS c FROM local_values").fetchone()["c"] == 0


def test_value_is_per_issue_not_per_group(conn):
    seed_issue(conn, "DEMO-1", "Birinci")
    other = repo.create_group(conn, "Ikinci", repo.KIND_MANUAL)
    repo.add_items(conn, other["id"], ["DEMO-1"])
    field = make_field(conn, "Not", type="text", track_history=False)
    repo.set_local_value(conn, "DEMO-1", field["id"], "tek deger")
    rows = conn.execute("SELECT COUNT(*) AS c FROM local_values").fetchone()["c"]
    assert rows == 1


def test_unknown_issue_is_refused(conn):
    field = make_field(conn, "Not", type="text", track_history=False)
    with pytest.raises(repo.RepositoryError) as excinfo:
        repo.set_local_value(conn, "DEMO-404", field["id"], "deger")
    assert excinfo.value.status == 404


def test_group_member_without_fetched_issue_can_be_annotated(conn):
    group = repo.create_group(conn, "Takip", repo.KIND_MANUAL)
    repo.add_items(conn, group["id"], ["DEMO-9"])  # henuz cekilmemis kayit
    field = make_field(conn, "Not", type="text", track_history=False)
    assert repo.set_local_value(conn, "DEMO-9", field["id"], "not")["value"] == "not"


def test_updated_at_is_stamped(conn):
    seed_issue(conn)
    field = make_field(conn, "Not", type="text", track_history=False)
    repo.set_local_value(conn, "DEMO-1", field["id"], "deger")
    row = conn.execute("SELECT updated_at FROM local_values").fetchone()
    assert row["updated_at"]


# --- gecmis -------------------------------------------------------------


def test_history_only_records_real_changes(conn):
    seed_issue(conn)
    field = make_field(conn)
    repo.set_local_value(conn, "DEMO-1", field["id"], "bekliyor")
    repo.set_local_value(conn, "DEMO-1", field["id"], "bekliyor")  # ayni deger
    repo.set_local_value(conn, "DEMO-1", field["id"], "BEKLIYOR")  # normalize sonrasi ayni
    entries = repo.list_local_history(conn, "DEMO-1", field["id"])
    assert len(entries) == 1
    assert entries[0]["old_value"] == "" and entries[0]["new_value"] == "bekliyor"


def test_history_is_newest_first_and_carries_display_text(conn):
    seed_issue(conn)
    field = make_field(conn)
    repo.set_local_value(conn, "DEMO-1", field["id"], "bekliyor")
    repo.set_local_value(conn, "DEMO-1", field["id"], "musteriye soruldu")
    entries = repo.list_local_history(conn, "DEMO-1", field["id"])
    assert [entry["new_value"] for entry in entries] == ["musteriye soruldu", "bekliyor"]
    assert entries[0]["old_text"] == "bekliyor"
    assert entries[0]["new_text"] == "musteriye soruldu"
    assert entries[0]["changed_at"]


def test_history_records_deletion_too(conn):
    seed_issue(conn)
    field = make_field(conn)
    repo.set_local_value(conn, "DEMO-1", field["id"], "bitti")
    repo.set_local_value(conn, "DEMO-1", field["id"], "")
    entries = repo.list_local_history(conn, "DEMO-1", field["id"])
    assert entries[0]["old_value"] == "bitti" and entries[0]["new_value"] == ""


def test_no_history_when_tracking_is_off(conn):
    seed_issue(conn)
    field = make_field(conn, "Not", type="text", track_history=False)
    repo.set_local_value(conn, "DEMO-1", field["id"], "ilk")
    repo.set_local_value(conn, "DEMO-1", field["id"], "ikinci")
    assert repo.list_local_history(conn, "DEMO-1", field["id"]) == []


def test_tracking_turned_on_later_starts_from_that_moment(conn):
    seed_issue(conn)
    field = make_field(conn, "Not", type="text", track_history=False)
    repo.set_local_value(conn, "DEMO-1", field["id"], "ilk")
    repo.update_local_field(conn, field["id"], {"track_history": True})
    repo.set_local_value(conn, "DEMO-1", field["id"], "ikinci")

    entries = repo.list_local_history(conn, "DEMO-1", field["id"])
    assert len(entries) == 1
    # Gecmisten once yazilan deger 'eski deger' olarak gorunur, satiri yoktur.
    assert entries[0]["old_value"] == "ilk" and entries[0]["new_value"] == "ikinci"


def test_tracking_turned_off_keeps_existing_history(conn):
    seed_issue(conn)
    field = make_field(conn)
    repo.set_local_value(conn, "DEMO-1", field["id"], "bekliyor")
    repo.update_local_field(conn, field["id"], {"track_history": False})
    assert len(repo.list_local_history(conn, "DEMO-1", field["id"])) == 1

    repo.set_local_value(conn, "DEMO-1", field["id"], "bitti")
    assert len(repo.list_local_history(conn, "DEMO-1", field["id"])) == 1


def test_single_history_row_can_be_deleted(conn):
    seed_issue(conn)
    field = make_field(conn)
    repo.set_local_value(conn, "DEMO-1", field["id"], "bekliyor")
    repo.set_local_value(conn, "DEMO-1", field["id"], "bitti")
    entries = repo.list_local_history(conn, "DEMO-1", field["id"])

    repo.delete_local_history_entry(conn, "DEMO-1", field["id"], entries[0]["id"])
    left = repo.list_local_history(conn, "DEMO-1", field["id"])
    assert [entry["new_value"] for entry in left] == ["bekliyor"]
    # Deger silinmez, yalnizca gecmis satiri gider.
    assert repo.get_local_value(conn, "DEMO-1", field["id"]) == "bitti"

    with pytest.raises(repo.RepositoryError) as excinfo:
        repo.delete_local_history_entry(conn, "DEMO-1", field["id"], entries[0]["id"])
    assert excinfo.value.status == 404


def test_whole_history_can_be_cleared(conn):
    seed_issue(conn)
    field = make_field(conn)
    repo.set_local_value(conn, "DEMO-1", field["id"], "bekliyor")
    repo.set_local_value(conn, "DEMO-1", field["id"], "bitti")
    assert repo.clear_local_history(conn, "DEMO-1", field["id"]) == 2
    assert repo.list_local_history(conn, "DEMO-1", field["id"]) == []
    assert repo.get_local_value(conn, "DEMO-1", field["id"]) == "bitti"


def test_history_is_per_cell(conn):
    seed_issue(conn, "DEMO-1")
    group = repo.list_groups(conn)[0]
    repo.add_items(conn, group["id"], ["DEMO-2"])
    field = make_field(conn)
    repo.set_local_value(conn, "DEMO-1", field["id"], "bekliyor")
    repo.set_local_value(conn, "DEMO-2", field["id"], "bitti")
    assert len(repo.list_local_history(conn, "DEMO-1", field["id"])) == 1
    assert len(repo.list_local_history(conn, "DEMO-2", field["id"])) == 1

    stats = repo.history_stats(conn, ["DEMO-1", "DEMO-2"])
    assert stats["DEMO-1"][field["id"]]["changes"] == 1
    assert stats["DEMO-2"][field["id"]]["changes"] == 1


# --- alan silme ---------------------------------------------------------


def test_delete_field_removes_values_history_and_columns(conn):
    seed_issue(conn)
    field = make_field(conn)
    repo.set_local_value(conn, "DEMO-1", field["id"], "bekliyor")
    repo.set_local_value(conn, "DEMO-1", field["id"], "bitti")

    group = repo.list_groups(conn)[0]
    columns = [
        "issuekey",
        field["column_id"],
        f"{field['column_id']}:changes",
        f"{field['column_id']}:changed_at",
    ]
    repo.update_group(conn, group["id"], {"columns": columns})
    repo.update_group(conn, group["id"], {"sort": {"field": field["column_id"], "dir": "asc"}})
    repo.set_default_columns(conn, ["issuekey", field["column_id"]])

    report = repo.delete_local_field(conn, field["id"])
    assert report["values"] == 1 and report["history"] == 2 and report["groups"] == 1

    assert repo.list_local_fields(conn) == []
    assert conn.execute("SELECT COUNT(*) AS c FROM local_values").fetchone()["c"] == 0
    assert conn.execute("SELECT COUNT(*) AS c FROM local_value_history").fetchone()["c"] == 0

    fresh = repo.require_group(conn, group["id"])
    assert fresh["columns"] == ["issuekey"]
    assert fresh["sort"] is None
    assert repo.default_columns(conn) == ["issuekey"]


def test_delete_field_does_not_touch_other_fields(conn):
    seed_issue(conn)
    kept = make_field(conn, "Kalan", type="text", track_history=False)
    gone = make_field(conn, "Giden", type="text", track_history=False)
    repo.set_local_value(conn, "DEMO-1", kept["id"], "kalsin")
    repo.set_local_value(conn, "DEMO-1", gone["id"], "gitsin")

    repo.delete_local_field(conn, gone["id"])
    assert repo.get_local_value(conn, "DEMO-1", kept["id"]) == "kalsin"


def test_group_usage_count(conn):
    field = make_field(conn, "Not", type="text", track_history=False)
    first = repo.create_group(conn, "Bir", repo.KIND_MANUAL)
    repo.create_group(conn, "Iki", repo.KIND_MANUAL)
    repo.update_group(conn, first["id"], {"columns": ["issuekey", field["column_id"]]})
    assert repo.get_local_field(conn, field["id"])["group_count"] == 1


# --- kayit yasam dongusu ------------------------------------------------


def test_value_survives_when_issue_leaves_the_group(conn):
    group = seed_issue(conn)
    field = make_field(conn, "Not", type="text")
    repo.set_local_value(conn, "DEMO-1", field["id"], "onemli not")

    repo.remove_item(conn, group["id"], "DEMO-1")
    assert repo.get_local_value(conn, "DEMO-1", field["id"]) == "onemli not"
    assert len(repo.list_local_history(conn, "DEMO-1", field["id"])) == 1

    # Kayit gruba geri gelirse not yerinde durur.
    repo.add_items(conn, group["id"], ["DEMO-1"])
    assert repo.get_local_value(conn, "DEMO-1", field["id"]) == "onemli not"


def test_purge_orphans_also_clears_local_data(conn):
    group = seed_issue(conn)
    field = make_field(conn, "Not", type="text")
    repo.set_local_value(conn, "DEMO-1", field["id"], "silinecek")
    repo.remove_item(conn, group["id"], "DEMO-1")

    repo.purge_orphan_issues(conn)
    assert repo.get_local_value(conn, "DEMO-1", field["id"]) == ""
    assert repo.list_local_history(conn, "DEMO-1", field["id"]) == []


def test_refresh_does_not_touch_local_values(conn):
    seed_issue(conn)
    field = make_field(conn, "Not", type="text")
    repo.set_local_value(conn, "DEMO-1", field["id"], "elle yazildi")

    # Jira'dan gelen yeni surum kaydin ustune yazilir.
    repo.upsert_issues(conn, [issue("DEMO-1", "Jira'dan gelen yeni ozet")])
    assert repo.get_local_value(conn, "DEMO-1", field["id"]) == "elle yazildi"
    assert len(repo.list_local_history(conn, "DEMO-1", field["id"])) == 1


# --- sema ve sutun katalogu ---------------------------------------------


def test_local_fields_appear_in_schemas_with_derived_columns(conn):
    field = make_field(conn, "Musteri durumu")
    schemas = repo.field_schemas(conn)
    base = field["column_id"]

    assert schemas[base]["name"] == "Musteri durumu"
    assert schemas[f"{base}:changed_at"]["name"] == "Musteri durumu (son değişim)"
    assert schemas[f"{base}:changed_at"]["schema"]["type"] == "datetime"
    assert schemas[f"{base}:changes"]["name"] == "Musteri durumu (kaç kez değişti)"
    assert schemas[f"{base}:changes"]["schema"]["type"] == "number"


def test_derived_columns_are_offered_only_when_history_is_on(conn):
    field = make_field(conn, "Not", type="text", track_history=False)
    ids = [item["id"] for item in repo.local_field_columns(conn)]
    assert ids == [field["column_id"]]

    repo.update_local_field(conn, field["id"], {"track_history": True})
    ids = [item["id"] for item in repo.local_field_columns(conn)]
    assert ids == [
        field["column_id"],
        f"{field['column_id']}:changed_at",
        f"{field['column_id']}:changes",
    ]


def test_field_catalog_lists_local_fields_after_jira_fields(conn):
    repo.store_fields(conn, [{"id": "summary", "name": "Ozet", "schema": {"type": "string"}}])
    field = make_field(conn, "Not", type="text", track_history=False)
    catalog = repo.list_fields(conn)
    kinds = [item["kind"] for item in catalog]
    assert kinds == ["virtual", "jira", "local"]
    assert catalog[-1]["id"] == field["column_id"]
    assert catalog[-1]["local"]["type"] == "text"


def test_derived_column_names_survive_history_being_turned_off(conn):
    """Sutun secili kalmissa adi cozulmeye devam etmeli."""
    field = make_field(conn, "Durum")
    repo.update_local_field(conn, field["id"], {"track_history": False})
    schemas = repo.field_schemas(conn)
    assert f"{field['column_id']}:changes" in schemas
    assert [item["id"] for item in repo.local_field_columns(conn)] == [field["column_id"]]
