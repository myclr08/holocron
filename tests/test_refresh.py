"""Guncelle orkestrasyonu: sahte Jira ile, gercek aga cikmadan."""

from __future__ import annotations

import threading

import pytest

from app import refresh as refresh_module
from app import repository as repo
from app.settings_store import MODE_CLOUD, MODE_SERVER
from tests.fake_jira import FakeResponse, issue, key_search

BASE = "https://jira.example.com"
SEARCH_PATH = "/rest/api/2/search"
CLOUD_SEARCH_PATH = "/rest/api/3/search/jql"
FIELD_PATH = "/rest/api/2/field"

CATALOG = [
    {"id": "summary", "name": "Ozet", "schema": {"type": "string"}},
    {"id": "status", "name": "Durum", "schema": {"type": "status"}},
]


def configure(context, mode: str = MODE_SERVER) -> None:
    context.settings.apply(
        {"jira.mode": mode, "jira.base_url": BASE, "jira.secret": "ornek-pat", "jira.email": "a@example.com"}
    )


def install(fake_jira, issues, jql_map, path: str = SEARCH_PATH, gate=None, gate_jql: str | None = None):
    """JQL -> anahtar listesi eslemesi kuran sahte arama ucu."""
    known = {item["key"].upper(): item for item in issues}
    lookup = key_search(known)

    def handler(call):
        jql = (call.json_body or {}).get("jql", "")
        if jql.upper().startswith("KEY IN "):
            return lookup(call)
        if gate is not None and (gate_jql is None or jql == gate_jql):
            gate.wait(5)
        if jql in jql_map:
            found = [known[key] for key in jql_map[jql]]
            return FakeResponse(
                body={"startAt": 0, "maxResults": 100, "total": len(found), "issues": found}
            )
        return FakeResponse(status=400, body={"errorMessages": ["JQL ayristirilamadi: " + jql]})

    fake_jira.add("POST", path, handler)
    fake_jira.json("GET", FIELD_PATH, CATALOG)
    return known


def run(context, group_id=None):
    return context.refresh.run_blocking(context, group_id)


# --- temel akis ---------------------------------------------------------


def test_manual_group_keys_are_fetched(context, fake_jira, conn):
    configure(context)
    install(fake_jira, [issue("DEMO-1", "Ilk"), issue("DEMO-2", "Ikinci")], {})
    group = repo.create_group(conn, "Takip", repo.KIND_MANUAL)
    repo.add_items(conn, group["id"], ["DEMO-1", "DEMO-2"])

    status = run(context)

    assert status["state"] == "done"
    assert status["summary"]["fetched"] == 2
    assert status["summary"]["new"] == 2
    assert repo.get_issue(conn, "DEMO-1")["raw"]["fields"]["summary"] == "Ilk"


def test_field_catalog_is_fetched_only_when_empty(context, fake_jira, conn):
    configure(context)
    install(fake_jira, [], {})
    repo.create_group(conn, "Bos", repo.KIND_MANUAL)

    run(context)
    assert repo.field_count(conn) == len(CATALOG)
    assert len(fake_jira.calls_to("GET", FIELD_PATH)) == 1

    run(context)
    # Katalog dolu: ikinci iste tekrar cekilmez.
    assert len(fake_jira.calls_to("GET", FIELD_PATH)) == 1


def test_filter_group_membership_follows_jql(context, fake_jira, conn):
    configure(context)
    jql = "project = DEMO"
    install(fake_jira, [issue("DEMO-1"), issue("DEMO-2"), issue("DEMO-3")], {jql: ["DEMO-1", "DEMO-2"]})
    group = repo.create_group(conn, "Filo", repo.KIND_FILTER, jql=jql)

    status = run(context)
    assert repo.list_item_keys(conn, group["id"]) == ["DEMO-1", "DEMO-2"]
    assert status["summary"]["filter_groups"][str(group["id"])]["added"] == 2

    # JQL sonucu degisti: DEMO-2 dustu, DEMO-3 geldi.
    install(fake_jira, [issue("DEMO-1"), issue("DEMO-2"), issue("DEMO-3")], {jql: ["DEMO-1", "DEMO-3"]})
    status = run(context)
    assert set(repo.list_item_keys(conn, group["id"])) == {"DEMO-1", "DEMO-3"}
    assert status["summary"]["filter_groups"][str(group["id"])] == {
        "name": "Filo",
        "added": 1,
        "removed": 1,
    }


def test_pinned_item_survives_filter_refresh(context, fake_jira, conn):
    configure(context)
    jql = "project = DEMO"
    install(fake_jira, [issue("DEMO-1"), issue("DEMO-9")], {jql: ["DEMO-1"]})
    group = repo.create_group(conn, "Filo", repo.KIND_FILTER, jql=jql)
    repo.add_items(conn, group["id"], ["DEMO-9"])  # elle eklenen kayit iglenir

    run(context)

    keys = repo.list_item_keys(conn, group["id"])
    assert set(keys) == {"DEMO-1", "DEMO-9"}
    # JQL'den gelmeyen iglenmis kayit da cekildi.
    assert repo.get_issue(conn, "DEMO-9") is not None


def test_unknown_key_lands_in_not_found(context, fake_jira, conn):
    configure(context)
    install(fake_jira, [issue("DEMO-1")], {})
    group = repo.create_group(conn, "Takip", repo.KIND_MANUAL)
    repo.add_items(conn, group["id"], ["DEMO-1", "YOK-404"])

    status = run(context)

    assert status["summary"]["not_found"] == ["YOK-404"]
    assert status["summary"]["fetched"] == 1
    # Uyelik silinmez: kullanici kararini kendisi verir.
    assert "YOK-404" in repo.list_item_keys(conn, group["id"])


def test_changed_fields_are_reported(context, fake_jira, conn):
    configure(context)
    install(fake_jira, [issue("DEMO-1", "Ilk ozet")], {})
    group = repo.create_group(conn, "Takip", repo.KIND_MANUAL)
    repo.add_items(conn, group["id"], ["DEMO-1"])
    run(context)

    install(fake_jira, [issue("DEMO-1", "Yeni ozet")], {})
    status = run(context)

    assert status["changed"] == {"DEMO-1": ["summary"]}
    assert status["summary"]["updated"] == 1
    assert status["summary"]["new"] == 0


def test_broken_jql_does_not_kill_the_job(context, fake_jira, conn):
    configure(context)
    good = "project = DEMO"
    install(fake_jira, [issue("DEMO-1")], {good: ["DEMO-1"]})
    broken = repo.create_group(conn, "Bozuk", repo.KIND_FILTER, jql="bu bir JQL degil")
    healthy = repo.create_group(conn, "Saglam", repo.KIND_FILTER, jql=good)

    status = run(context)

    assert status["state"] == "done"
    assert [item["group_id"] for item in status["errors"]] == [broken["id"]]
    assert status["errors"][0]["code"] == "bad_request"
    assert repo.list_item_keys(conn, healthy["id"]) == ["DEMO-1"]


def test_single_group_scope_touches_only_that_group(context, fake_jira, conn):
    configure(context)
    install(fake_jira, [issue("DEMO-1"), issue("DEMO-2")], {})
    first = repo.create_group(conn, "Bir", repo.KIND_MANUAL)
    second = repo.create_group(conn, "Iki", repo.KIND_MANUAL)
    repo.add_items(conn, first["id"], ["DEMO-1"])
    repo.add_items(conn, second["id"], ["DEMO-2"])

    status = run(context, first["id"])

    assert status["group_id"] == first["id"]
    assert repo.get_issue(conn, "DEMO-1") is not None
    assert repo.get_issue(conn, "DEMO-2") is None


def test_unknown_group_scope_is_rejected(context):
    configure(context)
    with pytest.raises(repo.RepositoryError) as excinfo:
        run(context, 4040)
    assert excinfo.value.status == 404


def test_connection_error_ends_in_error_state(context, fake_jira, conn):
    configure(context)
    # Hicbir yol tanimli degil: sahte sunucu 404 doner.
    group = repo.create_group(conn, "Takip", repo.KIND_MANUAL)
    repo.add_items(conn, group["id"], ["DEMO-1"])

    status = run(context)

    assert status["state"] == "error"
    assert status["error"]["code"] == "not_found"
    assert status["finished_at"]


# --- alan secici --------------------------------------------------------


def test_server_asks_for_navigable_fields(context, fake_jira, conn):
    configure(context, MODE_SERVER)
    jql = "project = DEMO"
    install(fake_jira, [issue("DEMO-1")], {jql: ["DEMO-1"]})
    repo.create_group(conn, "Filo", repo.KIND_FILTER, jql=jql)

    run(context)

    body = fake_jira.calls_to("POST", SEARCH_PATH)[0].json_body
    assert body["fields"] == ["*navigable"]


def test_cloud_asks_for_all_fields(context, fake_jira, conn):
    """Cloud'un /search/jql ucu *navigable kabul etmiyor; orada *all kullanilir."""
    configure(context, MODE_CLOUD)
    jql = "project = DEMO"
    install(fake_jira, [issue("DEMO-1")], {jql: ["DEMO-1"]}, path=CLOUD_SEARCH_PATH)
    fake_jira.json("GET", "/rest/api/3/field", CATALOG)
    repo.create_group(conn, "Filo", repo.KIND_FILTER, jql=jql)

    run(context)

    body = fake_jira.calls_to("POST", CLOUD_SEARCH_PATH)[0].json_body
    assert body["fields"] == ["*all"]
    assert refresh_module.field_selector(MODE_CLOUD) == ["*all"]


# --- tek is kilidi ve iptal ---------------------------------------------


def test_second_job_is_refused_while_one_runs(context, fake_jira, conn):
    configure(context)
    gate = threading.Event()
    jql = "project = DEMO"
    install(fake_jira, [issue("DEMO-1")], {jql: ["DEMO-1"]}, gate=gate)
    repo.create_group(conn, "Filo", repo.KIND_FILTER, jql=jql)

    context.refresh.start(context)
    _wait_until(lambda: context.refresh.running)
    try:
        with pytest.raises(repo.RepositoryError) as excinfo:
            context.refresh.start(context)
        assert excinfo.value.code == "refresh_running"
        assert excinfo.value.status == 409
    finally:
        gate.set()
        context.refresh.join(5)

    assert context.refresh.status()["state"] == "done"


def test_cancel_stops_between_groups_and_keeps_progress(context, fake_jira, conn):
    configure(context)
    gate = threading.Event()
    jqls = ["project = BIR", "project = IKI", "project = UC"]
    install(
        fake_jira,
        [issue("BIR-1"), issue("IKI-1"), issue("UC-1")],
        {jqls[0]: ["BIR-1"], jqls[1]: ["IKI-1"], jqls[2]: ["UC-1"]},
        gate=gate,
        gate_jql=jqls[1],  # ikinci grubun aramasi kapida bekler
    )
    groups = [
        repo.create_group(conn, name, repo.KIND_FILTER, jql=jql)
        for name, jql in zip(("Bir", "Iki", "Uc"), jqls)
    ]

    context.refresh.start(context)
    _wait_until(lambda: repo.list_item_keys(conn, groups[0]["id"]) == ["BIR-1"])
    assert context.refresh.cancel() is True
    gate.set()
    context.refresh.join(5)

    status = context.refresh.status()
    assert status["state"] == "cancelled"
    assert status["stage"] == "Iptal edildi"
    # Iptalden once islenenler duruyor, sonraki gruba hic gecilmedi.
    assert repo.list_item_keys(conn, groups[0]["id"]) == ["BIR-1"]
    assert repo.list_item_keys(conn, groups[2]["id"]) == []
    assert context.refresh.cancel() is False  # calismayan is iptal edilmez


def _wait_until(predicate, timeout: float = 5.0) -> None:
    deadline = threading.Event()
    for _ in range(int(timeout * 200)):
        if predicate():
            return
        deadline.wait(0.005)
    raise AssertionError("Beklenen duruma ulasilmadi.")
