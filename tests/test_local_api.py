"""Yerel alan uclari ve grid tumlesigi: fastapi.testclient uzerinden."""

from __future__ import annotations

from app import repository as repo
from app.settings_store import MODE_SERVER
from tests.fake_jira import issue, key_search

BASE = "https://jira.example.com"

CATALOG = [
    {"id": "summary", "name": "Ozet", "schema": {"type": "string"}},
    {"id": "status", "name": "Durum", "schema": {"type": "status"}},
]

DURUMLAR = ["bekliyor", "musteriye soruldu", "bitti"]


def setup_group(api_client, conn, keys=("DEMO-1", "DEMO-2")):
    repo.store_fields(conn, CATALOG)
    api_client.put("/api/settings", json={"jira.base_url": BASE, "jira.mode": MODE_SERVER})
    group = api_client.post("/api/groups", json={"name": "Filom", "kind": "manual"}).json()["group"]
    api_client.post(f"/api/groups/{group['id']}/items", json={"text": " ".join(keys)})
    repo.upsert_issues(conn, [issue(key, f"Ozet {key}") for key in keys])
    return group


def add_field(api_client, name="Musteri durumu", type="select", options=None, history=True):
    payload = {"name": name, "type": type, "track_history": history}
    if type == "select":
        payload["options"] = options if options is not None else DURUMLAR
    response = api_client.post("/api/local-fields", json=payload)
    assert response.status_code == 200, response.text
    return response.json()["field"]


def choose(api_client, group, columns):
    assert api_client.put(f"/api/groups/{group['id']}", json={"columns": columns}).status_code == 200


def cells(row):
    return {cell["field"]: cell for cell in row["cells"]}


# --- alan uclari --------------------------------------------------------


def test_local_field_crud(api_client):
    empty = api_client.get("/api/local-fields").json()
    assert empty["fields"] == []
    assert [item["id"] for item in empty["types"]] == ["text", "number", "date", "bool", "select"]

    field = add_field(api_client, "Not", type="text", history=False)
    assert field["column_id"] == f"local:{field['id']}"

    read = api_client.get(f"/api/local-fields/{field['id']}").json()["field"]
    assert read["name"] == "Not"

    updated = api_client.put(
        f"/api/local-fields/{field['id']}", json={"name": "Ic not", "track_history": True}
    ).json()["field"]
    assert updated["name"] == "Ic not" and updated["track_history"] is True

    assert api_client.delete(f"/api/local-fields/{field['id']}").json()["ok"] is True
    assert api_client.get("/api/local-fields").json()["fields"] == []


def test_duplicate_name_is_refused_with_common_error_shape(api_client):
    add_field(api_client, "Durum")
    response = api_client.post(
        "/api/local-fields", json={"name": "durum", "type": "text"}
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "duplicate_name"


def test_unknown_local_field_is_404(api_client):
    response = api_client.get("/api/local-fields/999")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "local_field_not_found"


def test_reorder_endpoint(api_client):
    first = add_field(api_client, "Bir", type="text", history=False)
    second = add_field(api_client, "Iki", type="text", history=False)
    response = api_client.post(
        "/api/local-fields/reorder", json={"ids": [second["id"], first["id"]]}
    )
    assert [item["name"] for item in response.json()["fields"]] == ["Iki", "Bir"]

    bad = api_client.post("/api/local-fields/reorder", json={"ids": "hepsi"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_order"


def test_type_change_is_refused(api_client):
    field = add_field(api_client, "Not", type="text", history=False)
    response = api_client.put(f"/api/local-fields/{field['id']}", json={"type": "number"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "type_immutable"


# --- deger yazma --------------------------------------------------------


def test_write_value_and_read_it_back(api_client, conn):
    group = setup_group(api_client, conn)
    field = add_field(api_client)
    choose(api_client, group, ["issuekey", "summary", field["column_id"]])

    written = api_client.put(
        f"/api/issues/demo-1/local/{field['id']}", json={"value": "BEKLIYOR"}
    ).json()
    assert written["key"] == "DEMO-1"
    assert written["value"] == "bekliyor"
    assert written["text"] == "bekliyor"
    assert written["changes"] == 1

    grid = api_client.get(f"/api/groups/{group['id']}/issues").json()
    row = [item for item in grid["rows"] if item["key"] == "DEMO-1"][0]
    cell = cells(row)[field["column_id"]]
    assert cell["text"] == "bekliyor"
    assert cell["raw"] == "bekliyor"
    assert cell["editable"] is True
    assert cell["changes"] == 1


def test_invalid_value_returns_invalid_value_code(api_client, conn):
    setup_group(api_client, conn)
    field = add_field(api_client, "Puan", type="number", history=False)
    response = api_client.put(f"/api/issues/DEMO-1/local/{field['id']}", json={"value": "salatalik"})
    assert response.status_code == 400
    body = response.json()["error"]
    assert body["code"] == "invalid_value"
    assert "Sayi bekleniyor" in body["message"]


def test_value_for_unknown_issue_is_404(api_client, conn):
    setup_group(api_client, conn)
    field = add_field(api_client, "Not", type="text", history=False)
    response = api_client.put(f"/api/issues/DEMO-404/local/{field['id']}", json={"value": "x"})
    assert response.status_code == 404


def test_each_type_has_an_editable_cell(api_client, conn):
    group = setup_group(api_client, conn)
    fields = {
        "text": add_field(api_client, "Not", type="text", history=False),
        "number": add_field(api_client, "Puan", type="number", history=False),
        "date": add_field(api_client, "Vade", type="date", history=False),
        "bool": add_field(api_client, "Acil", type="bool", history=False),
        "select": add_field(api_client, "Durum", type="select", history=False),
    }
    choose(api_client, group, ["issuekey"] + [item["column_id"] for item in fields.values()])

    values = {
        "text": "kisa not",
        "number": "3,5",
        "date": "11.09.2026",
        "bool": "evet",
        "select": "bitti",
    }
    for kind, value in values.items():
        api_client.put(f"/api/issues/DEMO-1/local/{fields[kind]['id']}", json={"value": value})

    row = [
        item
        for item in api_client.get(f"/api/groups/{group['id']}/issues").json()["rows"]
        if item["key"] == "DEMO-1"
    ][0]
    texts = {field: cell["text"] for field, cell in cells(row).items()}
    assert texts[fields["text"]["column_id"]] == "kisa not"
    assert texts[fields["number"]["column_id"]] == "3.5"
    assert texts[fields["date"]["column_id"]] == "11.09.2026"
    assert texts[fields["bool"]["column_id"]] == "Evet"
    assert texts[fields["select"]["column_id"]] == "bitti"


# --- gecmis uclari ------------------------------------------------------


def test_history_endpoint_lists_timeline(api_client, conn):
    setup_group(api_client, conn)
    field = add_field(api_client)
    api_client.put(f"/api/issues/DEMO-1/local/{field['id']}", json={"value": "bekliyor"})
    api_client.put(f"/api/issues/DEMO-1/local/{field['id']}", json={"value": "musteriye soruldu"})

    data = api_client.get(f"/api/issues/DEMO-1/local/{field['id']}/history").json()
    assert data["key"] == "DEMO-1"
    assert data["changes"] == 2
    assert [entry["new_text"] for entry in data["entries"]] == ["musteriye soruldu", "bekliyor"]


def test_history_is_empty_when_tracking_is_off(api_client, conn):
    setup_group(api_client, conn)
    field = add_field(api_client, "Not", type="text", history=False)
    api_client.put(f"/api/issues/DEMO-1/local/{field['id']}", json={"value": "bir"})
    api_client.put(f"/api/issues/DEMO-1/local/{field['id']}", json={"value": "iki"})
    data = api_client.get(f"/api/issues/DEMO-1/local/{field['id']}/history").json()
    assert data["entries"] == [] and data["track_history"] is False


def test_single_history_row_and_full_clear(api_client, conn):
    setup_group(api_client, conn)
    field = add_field(api_client)
    api_client.put(f"/api/issues/DEMO-1/local/{field['id']}", json={"value": "bekliyor"})
    api_client.put(f"/api/issues/DEMO-1/local/{field['id']}", json={"value": "bitti"})
    entries = api_client.get(f"/api/issues/DEMO-1/local/{field['id']}/history").json()["entries"]

    dropped = api_client.delete(
        f"/api/issues/DEMO-1/local/{field['id']}/history/{entries[0]['id']}"
    ).json()
    assert dropped["changes"] == 1

    missing = api_client.delete(
        f"/api/issues/DEMO-1/local/{field['id']}/history/{entries[0]['id']}"
    )
    assert missing.status_code == 404

    cleared = api_client.delete(f"/api/issues/DEMO-1/local/{field['id']}/history").json()
    assert cleared == {"ok": True, "removed": 1}
    assert api_client.get(f"/api/issues/DEMO-1/local/{field['id']}/history").json()["entries"] == []


# --- turetilmis sutunlar ------------------------------------------------


def test_derived_columns_are_returned_like_normal_cells(api_client, conn):
    group = setup_group(api_client, conn)
    field = add_field(api_client)
    base = field["column_id"]
    choose(api_client, group, ["issuekey", base, f"{base}:changed_at", f"{base}:changes"])

    api_client.put(f"/api/issues/DEMO-1/local/{field['id']}", json={"value": "bekliyor"})
    api_client.put(f"/api/issues/DEMO-1/local/{field['id']}", json={"value": "bitti"})

    data = api_client.get(f"/api/groups/{group['id']}/issues").json()
    assert [column["name"] for column in data["columns"]] == [
        "Anahtar",
        "Musteri durumu",
        "Musteri durumu (son degisim)",
        "Musteri durumu (kac kez degisti)",
    ]
    assert data["columns"][1]["editable"] is True
    assert data["columns"][2]["editable"] is False

    row = [item for item in data["rows"] if item["key"] == "DEMO-1"][0]
    by_field = cells(row)
    assert by_field[f"{base}:changes"]["text"] == "2"
    assert by_field[f"{base}:changed_at"]["text"]  # bicimlenmis tarih-saat
    assert "editable" not in by_field[f"{base}:changes"]

    untouched = cells([item for item in data["rows"] if item["key"] == "DEMO-2"][0])
    assert untouched[f"{base}:changes"]["text"] == "0"
    assert untouched[f"{base}:changed_at"]["text"] == ""


def test_sorting_by_local_and_derived_columns(api_client, conn):
    group = setup_group(api_client, conn)
    field = add_field(api_client, "Puan", type="number", history=True)
    base = field["column_id"]
    choose(api_client, group, ["issuekey", base, f"{base}:changes"])

    api_client.put(f"/api/issues/DEMO-1/local/{field['id']}", json={"value": "13"})
    api_client.put(f"/api/issues/DEMO-2/local/{field['id']}", json={"value": "5"})
    api_client.put(f"/api/issues/DEMO-2/local/{field['id']}", json={"value": "7"})

    path = f"/api/groups/{group['id']}/issues"
    # Sayi sutunu metin gibi degil sayi gibi siralanir (7 < 13).
    numeric = api_client.get(path, params={"sort": base, "dir": "asc"}).json()
    assert [row["key"] for row in numeric["rows"]] == ["DEMO-2", "DEMO-1"]

    # Degisim sayisi: DEMO-2 iki kez, DEMO-1 bir kez.
    counted = api_client.get(path, params={"sort": f"{base}:changes", "dir": "desc"}).json()
    assert [row["key"] for row in counted["rows"]] == ["DEMO-2", "DEMO-1"]


def test_empty_local_values_sort_last(api_client, conn):
    group = setup_group(api_client, conn)
    field = add_field(api_client, "Not", type="text", history=False)
    choose(api_client, group, ["issuekey", field["column_id"]])
    api_client.put(f"/api/issues/DEMO-2/local/{field['id']}", json={"value": "abc"})

    rows = api_client.get(
        f"/api/groups/{group['id']}/issues", params={"sort": field["column_id"], "dir": "asc"}
    ).json()["rows"]
    assert [row["key"] for row in rows] == ["DEMO-2", "DEMO-1"]


# --- arama --------------------------------------------------------------


def test_search_covers_local_values(api_client, conn):
    group = setup_group(api_client, conn)
    field = add_field(api_client, "Not", type="text", history=False)
    choose(api_client, group, ["issuekey", "summary", field["column_id"]])
    api_client.put(
        f"/api/issues/DEMO-2/local/{field['id']}", json={"value": "İstanbul ziyareti"}
    )

    path = f"/api/groups/{group['id']}/issues"
    for needle in ("istanbul", "İSTANBUL", "ıstanbul"):
        found = api_client.get(path, params={"q": needle}).json()
        assert [row["key"] for row in found["rows"]] == ["DEMO-2"], needle
        assert found["total"] == 2 and found["shown"] == 1


def test_search_finds_local_value_even_when_column_is_not_selected(api_client, conn):
    group = setup_group(api_client, conn)
    field = add_field(api_client, "Not", type="text", history=False)
    choose(api_client, group, ["issuekey", "summary"])  # yerel sutun secili degil
    api_client.put(f"/api/issues/DEMO-2/local/{field['id']}", json={"value": "gizli not"})

    rows = api_client.get(f"/api/groups/{group['id']}/issues", params={"q": "gizli"}).json()["rows"]
    assert [row["key"] for row in rows] == ["DEMO-2"]


def test_bool_is_searchable_by_its_turkish_label(api_client, conn):
    group = setup_group(api_client, conn)
    field = add_field(api_client, "Acil", type="bool", history=False)
    choose(api_client, group, ["issuekey", field["column_id"]])
    api_client.put(f"/api/issues/DEMO-1/local/{field['id']}", json={"value": True})

    rows = api_client.get(f"/api/groups/{group['id']}/issues", params={"q": "evet"}).json()["rows"]
    assert [row["key"] for row in rows] == ["DEMO-1"]


# --- sutun secici ve detay ----------------------------------------------


def test_fields_endpoint_groups_local_and_derived(api_client, conn):
    repo.store_fields(conn, CATALOG)
    field = add_field(api_client, "Durum")
    listed = api_client.get("/api/fields").json()["fields"]
    by_id = {item["id"]: item for item in listed}

    assert by_id[field["column_id"]]["kind"] == "local"
    derived = by_id[f"{field['column_id']}:changes"]
    assert derived["kind"] == "derived"
    assert derived["parent"] == field["column_id"]
    # Yerel alanlar Jira alanlarindan sonra, turetilmis hemen kendi alaninin altinda.
    ids = [item["id"] for item in listed]
    assert ids.index(field["column_id"]) < ids.index(derived["id"])
    assert ids.index("summary") < ids.index(field["column_id"])


def test_issue_detail_carries_local_section_with_history(api_client, conn):
    setup_group(api_client, conn)
    field = add_field(api_client)
    api_client.put(f"/api/issues/DEMO-1/local/{field['id']}", json={"value": "bekliyor"})
    api_client.put(f"/api/issues/DEMO-1/local/{field['id']}", json={"value": "bitti"})

    detail = api_client.get("/api/issues/DEMO-1").json()
    assert len(detail["local"]) == 1
    entry = detail["local"][0]
    # Arayuzdeki duzenleyici alan kimligini "id" adiyla okur.
    assert entry["id"] == field["id"] == entry["field_id"]
    assert entry["name"] == "Musteri durumu"
    assert entry["type"] == "select"
    assert entry["options"] == DURUMLAR
    assert entry["text"] == "bitti"
    assert entry["changes"] == 2
    assert [item["new_text"] for item in entry["history"]] == ["bitti", "bekliyor"]

    other = api_client.get("/api/issues/DEMO-2").json()["local"][0]
    assert other["empty"] is True and other["history"] == []


# --- Guncelle yerel degerlere dokunmaz ----------------------------------


def test_refresh_keeps_local_values_and_history(api_client, conn, fake_jira):
    group = setup_group(api_client, conn, keys=("DEMO-1",))
    api_client.put("/api/settings", json={"jira.base_url": BASE, "jira.secret": "pat"})
    field = add_field(api_client, "Not", type="text", history=True)
    choose(api_client, group, ["issuekey", "summary", field["column_id"]])
    api_client.put(f"/api/issues/DEMO-1/local/{field['id']}", json={"value": "elle yazildi"})

    fake_jira.json("GET", "/rest/api/2/field", CATALOG)
    fake_jira.add(
        "POST",
        "/rest/api/2/search",
        key_search({"DEMO-1": issue("DEMO-1", "Jira'dan gelen yeni ozet")}),
    )
    api_client.post("/api/refresh", json={})
    api_client.app.state.context.refresh.join(5)
    assert api_client.get("/api/refresh/status").json()["status"]["state"] == "done"

    row = api_client.get(f"/api/groups/{group['id']}/issues").json()["rows"][0]
    by_field = cells(row)
    assert by_field["summary"]["text"] == "Jira'dan gelen yeni ozet"
    assert by_field[field["column_id"]]["text"] == "elle yazildi"
    assert by_field[field["column_id"]]["changes"] == 1


# --- arayuz kancalari ---------------------------------------------------


def test_main_page_carries_stage_three_hooks(api_client):
    html = api_client.get("/").text
    for marker in ('id="local-fields"', 'id="history-popover"', 'id="history-clear"'):
        assert marker in html, marker

    css = api_client.get("/static/css/app.css").text
    assert ".local-cell" in css and ".history-line" in css
    script = api_client.get("/static/js/app.js").text
    assert "localFieldsModal" in script and "openHistory" in script


def test_detail_of_unfetched_group_member_still_carries_local_fields(api_client, conn):
    """Grid'de duzenlenebilen bir satir cekmecede de duzenlenebilmeli."""
    group = setup_group(api_client, conn)
    api_client.post(f"/api/groups/{group['id']}/items", json={"text": "DEMO-9"})
    field = add_field(api_client, "Not", type="text", history=False)
    api_client.put(f"/api/issues/DEMO-9/local/{field['id']}", json={"value": "henuz cekilmedi"})

    detail = api_client.get("/api/issues/DEMO-9").json()
    assert detail["key"] == "DEMO-9"
    assert detail["missing"] is True
    assert detail["fetched_at"] is None
    assert detail["local"][0]["text"] == "henuz cekilmedi"
    # Anahtar sutunu yine dolu, Jira alanlari bos.
    by_id = {item["field"]: item for item in detail["fields"]}
    assert by_id["issuekey"]["text"] == "DEMO-9"

    # Hicbir grupta gecmeyen anahtar hala 404.
    assert api_client.get("/api/issues/DEMO-404").status_code == 404
