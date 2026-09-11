"""Grup, sutun, grid ve Guncelle uclari: fastapi.testclient uzerinden."""

from __future__ import annotations

from app import repository as repo
from app.settings_store import MODE_SERVER
from tests.fake_jira import issue

BASE = "https://jira.example.com"

CATALOG = [
    {"id": "summary", "name": "Ozet", "schema": {"type": "string"}},
    {"id": "status", "name": "Durum", "schema": {"type": "status"}},
    {"id": "assignee", "name": "Atanan", "schema": {"type": "user"}},
    {"id": "storypoints", "name": "Puan", "custom": True, "schema": {"type": "number"}},
]


def seed_catalog(conn):
    repo.store_fields(conn, CATALOG)


def make_group(client, name="Takip", kind="manual", **extra):
    payload = {"name": name, "kind": kind}
    payload.update(extra)
    response = client.post("/api/groups", json=payload)
    assert response.status_code == 200, response.text
    return response.json()["group"]


def demo_issue(key, summary, status="Acik", assignee="Demo Kullanici", points=None):
    fields = {
        "status": {"name": status},
        "assignee": {"displayName": assignee},
    }
    if points is not None:
        fields["storypoints"] = points
    return issue(key, summary, **fields)


# --- grup CRUD ----------------------------------------------------------


def test_group_lifecycle(api_client):
    listed = api_client.get("/api/groups").json()
    assert listed["groups"] == []
    assert listed["colors"] == list(repo.GROUP_COLORS)
    assert listed["default_columns"][0] == "issuekey"

    group = make_group(api_client, "Filom", color="purple")
    assert group["color"] == "purple"
    assert group["count"] == 0

    read = api_client.get(f"/api/groups/{group['id']}").json()
    assert read["group"]["name"] == "Filom"
    assert read["columns"] == list(repo.FALLBACK_COLUMNS)

    updated = api_client.put(f"/api/groups/{group['id']}", json={"name": "Yeni ad"}).json()
    assert updated["group"]["name"] == "Yeni ad"

    assert api_client.delete(f"/api/groups/{group['id']}").json() == {"ok": True}
    assert api_client.get("/api/groups").json()["groups"] == []


def test_filter_group_needs_jql(api_client):
    response = api_client.post("/api/groups", json={"name": "Filtre", "kind": "filter"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_jql"


def test_unknown_group_returns_404_in_common_shape(api_client):
    response = api_client.get("/api/groups/999")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "group_not_found"


def test_reorder_endpoint(api_client):
    first = make_group(api_client, "Bir")
    second = make_group(api_client, "Iki")
    response = api_client.post("/api/groups/reorder", json={"ids": [second["id"], first["id"]]})
    assert [g["name"] for g in response.json()["groups"]] == ["Iki", "Bir"]


def test_reorder_rejects_non_list(api_client):
    response = api_client.post("/api/groups/reorder", json={"ids": "hepsi"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_order"


# --- kayit ekleme -------------------------------------------------------


def test_add_items_reports_added_already_invalid(api_client):
    group = make_group(api_client)
    text = "DEMO-1\nhttps://jira.example.com/browse/DEMO-2\ndemo-1\nsalatalik"
    result = api_client.post(f"/api/groups/{group['id']}/items", json={"text": text}).json()

    assert result["added"] == ["DEMO-1", "DEMO-2"]
    assert result["already"] == []
    assert result["invalid"] == ["salatalik"]
    assert result["group"]["count"] == 2

    again = api_client.post(f"/api/groups/{group['id']}/items", json={"text": "DEMO-1"}).json()
    assert again["already"] == ["DEMO-1"] and again["added"] == []


def test_remove_and_pin_item(api_client):
    group = make_group(api_client)
    api_client.post(f"/api/groups/{group['id']}/items", json={"text": "DEMO-1 DEMO-2"})

    pinned = api_client.post(f"/api/groups/{group['id']}/items/demo-1/pin").json()
    assert pinned == {"key": "DEMO-1", "pinned": True}
    assert api_client.post(f"/api/groups/{group['id']}/items/DEMO-1/pin").json()["pinned"] is False

    dropped = api_client.delete(f"/api/groups/{group['id']}/items/DEMO-2").json()
    assert dropped["ok"] is True and dropped["group"]["count"] == 1

    missing = api_client.delete(f"/api/groups/{group['id']}/items/DEMO-9")
    assert missing.status_code == 404


# --- grid ---------------------------------------------------------------


def prepare_grid(api_client, conn, records=None):
    seed_catalog(conn)
    api_client.put("/api/settings", json={"jira.base_url": BASE, "jira.mode": MODE_SERVER})
    group = make_group(api_client, "Filom")
    api_client.put(
        f"/api/groups/{group['id']}",
        json={"columns": ["issuekey", "summary", "status", "storypoints"]},
    )
    records = records or [
        demo_issue("DEMO-1", "İstanbul ofisi kurulumu", status="Acik", points=5),
        demo_issue("DEMO-2", "Ankara deposu", status="Kapali", points=13),
    ]
    keys = " ".join(record["key"] for record in records)
    api_client.post(f"/api/groups/{group['id']}/items", json={"text": keys})
    repo.upsert_issues(conn, records)
    return group


def test_grid_returns_formatted_cells_and_urls(api_client, conn):
    group = prepare_grid(api_client, conn)
    data = api_client.get(f"/api/groups/{group['id']}/issues").json()

    assert [column["name"] for column in data["columns"]] == ["Anahtar", "Ozet", "Durum", "Puan"]
    assert data["total"] == 2 and data["shown"] == 2

    first = data["rows"][0]
    assert first["key"] == "DEMO-1"
    assert first["url"] == f"{BASE}/browse/DEMO-1"
    texts = {cell["field"]: cell["text"] for cell in first["cells"]}
    assert texts == {
        "issuekey": "DEMO-1",
        "summary": "İstanbul ofisi kurulumu",
        "status": "Acik",
        "storypoints": "5",
    }
    # Ham deger de gider: Excel ve detay tarafi icin.
    raw = {cell["field"]: cell["raw"] for cell in first["cells"]}
    assert raw["status"] == {"name": "Acik"}


def test_grid_row_without_stored_issue_is_marked_missing(api_client, conn):
    group = prepare_grid(api_client, conn)
    api_client.post(f"/api/groups/{group['id']}/items", json={"text": "DEMO-99"})

    rows = api_client.get(f"/api/groups/{group['id']}/issues").json()["rows"]
    missing = [row for row in rows if row["key"] == "DEMO-99"][0]
    assert missing["missing"] is True
    assert [cell["text"] for cell in missing["cells"] if cell["field"] == "summary"] == [""]
    # Anahtar sutunu yine dolu: kullanici neyin eksik oldugunu gorur.
    assert [cell["text"] for cell in missing["cells"] if cell["field"] == "issuekey"] == ["DEMO-99"]


def test_grid_search_is_case_insensitive_for_turkish(api_client, conn):
    group = prepare_grid(api_client, conn)
    path = f"/api/groups/{group['id']}/issues"

    for needle in ("istanbul", "İSTANBUL", "Istanbul", "ıstanbul"):
        data = api_client.get(path, params={"q": needle}).json()
        assert [row["key"] for row in data["rows"]] == ["DEMO-1"], needle
        assert data["total"] == 2 and data["shown"] == 1

    assert api_client.get(path, params={"q": "ankara"}).json()["rows"][0]["key"] == "DEMO-2"
    assert api_client.get(path, params={"q": "bulunmayan"}).json()["rows"] == []


def test_grid_search_covers_key_and_all_selected_columns(api_client, conn):
    group = prepare_grid(api_client, conn)
    path = f"/api/groups/{group['id']}/issues"
    assert [r["key"] for r in api_client.get(path, params={"q": "demo-2"}).json()["rows"]] == ["DEMO-2"]
    assert [r["key"] for r in api_client.get(path, params={"q": "kapali"}).json()["rows"]] == ["DEMO-2"]


def test_grid_sorting_by_column(api_client, conn):
    group = prepare_grid(api_client, conn)
    path = f"/api/groups/{group['id']}/issues"

    ascending = api_client.get(path, params={"sort": "summary", "dir": "asc"}).json()
    assert [row["key"] for row in ascending["rows"]] == ["DEMO-2", "DEMO-1"]
    assert ascending["sort"] == {"field": "summary", "dir": "asc"}

    descending = api_client.get(path, params={"sort": "summary", "dir": "desc"}).json()
    assert [row["key"] for row in descending["rows"]] == ["DEMO-1", "DEMO-2"]

    # Sayisal alan metin gibi degil, sayi gibi siralanir (5 < 13).
    numeric = api_client.get(path, params={"sort": "storypoints", "dir": "asc"}).json()
    assert [row["key"] for row in numeric["rows"]] == ["DEMO-1", "DEMO-2"]


def test_group_sort_is_used_when_query_has_none(api_client, conn):
    group = prepare_grid(api_client, conn)
    api_client.put(f"/api/groups/{group['id']}", json={"sort": {"field": "summary", "dir": "asc"}})
    rows = api_client.get(f"/api/groups/{group['id']}/issues").json()["rows"]
    assert [row["key"] for row in rows] == ["DEMO-2", "DEMO-1"]


def test_long_text_is_trimmed_in_grid_but_full_in_detail(api_client, conn):
    long_summary = "u" * 500
    group = prepare_grid(api_client, conn, [demo_issue("DEMO-1", long_summary)])

    cell = api_client.get(f"/api/groups/{group['id']}/issues").json()["rows"][0]["cells"][1]
    assert len(cell["text"]) == 200 and cell["text"].endswith("…")
    assert cell["raw"] == long_summary

    detail = api_client.get("/api/issues/DEMO-1").json()
    summary = [item for item in detail["fields"] if item["field"] == "summary"][0]
    assert summary["text"] == long_summary


# --- detay --------------------------------------------------------------


def test_issue_detail_lists_named_fields(api_client, conn):
    prepare_grid(api_client, conn)
    detail = api_client.get("/api/issues/demo-1").json()

    assert detail["key"] == "DEMO-1"
    assert detail["url"] == f"{BASE}/browse/DEMO-1"
    by_id = {item["field"]: item for item in detail["fields"]}
    assert by_id["issuekey"]["text"] == "DEMO-1"
    assert by_id["assignee"]["name"] == "Atanan"
    assert by_id["assignee"]["text"] == "Demo Kullanici"
    assert by_id["status"]["text"] == "Acik"


def test_unknown_issue_detail_is_404(api_client):
    response = api_client.get("/api/issues/DEMO-404")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "issue_not_found"


# --- sutunlar -----------------------------------------------------------


def test_default_columns_round_trip(api_client, conn):
    assert api_client.get("/api/settings/columns").json()["columns"][0] == "issuekey"

    saved = api_client.put("/api/settings/columns", json={"columns": ["issuekey", "status"]})
    assert saved.json()["columns"] == ["issuekey", "status"]

    group = make_group(api_client)
    assert api_client.get(f"/api/groups/{group['id']}").json()["columns"] == ["issuekey", "status"]


def test_default_columns_reject_bad_payload(api_client):
    assert api_client.put("/api/settings/columns", json={"columns": "hepsi"}).status_code == 400
    assert api_client.put("/api/settings/columns", json={"columns": []}).status_code == 400


def test_fields_endpoint_includes_virtual_key_field(api_client, conn):
    seed_catalog(conn)
    fields = api_client.get("/api/fields").json()["fields"]
    by_id = {item["id"]: item for item in fields}
    assert by_id["issuekey"]["virtual"] is True
    assert by_id["summary"]["virtual"] is False
    assert by_id["storypoints"]["custom"] is True


# --- Guncelle uclari ----------------------------------------------------


def test_refresh_without_base_url_is_refused(api_client):
    response = api_client.post("/api/refresh", json={})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "config_missing"


def test_refresh_runs_and_reports_status(api_client, conn, fake_jira):
    api_client.put("/api/settings", json={"jira.base_url": BASE, "jira.secret": "pat"})
    fake_jira.json("GET", "/rest/api/2/field", CATALOG)
    from tests.fake_jira import key_search

    fake_jira.add("POST", "/rest/api/2/search", key_search({"DEMO-1": demo_issue("DEMO-1", "Ozet")}))

    group = make_group(api_client)
    api_client.post(f"/api/groups/{group['id']}/items", json={"text": "DEMO-1"})

    started = api_client.post("/api/refresh", json={})
    assert started.status_code == 200
    assert started.json()["status"]["state"] in ("running", "done")

    api_client.app.state.context.refresh.join(5)
    status = api_client.get("/api/refresh/status").json()["status"]
    assert status["state"] == "done"
    assert status["summary"]["new"] == 1
    assert status["stage"] == "Tamamlandi"


def test_refresh_status_is_idle_before_any_run(api_client):
    status = api_client.get("/api/refresh/status").json()["status"]
    assert status["state"] == "idle"
    assert status["running"] is False


def test_cancel_without_running_job(api_client):
    response = api_client.post("/api/refresh/cancel").json()
    assert response["cancelled"] is False


def test_refresh_unknown_group_is_404(api_client):
    api_client.put("/api/settings", json={"jira.base_url": BASE, "jira.secret": "pat"})
    response = api_client.post("/api/refresh", json={"group_id": 4040})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "group_not_found"


# --- arayuz kancalari ---------------------------------------------------


def test_main_page_carries_stage_two_hooks(api_client):
    html = api_client.get("/").text
    for marker in ('id="group-list"', 'id="grid"', 'id="drawer"', 'id="progress"', "Yeni Filo"):
        assert marker in html, marker

    css = api_client.get("/static/css/app.css").text
    assert "--saber-blue" in css and "--saber-purple" in css
    assert api_client.get("/static/js/app.js").status_code == 200
