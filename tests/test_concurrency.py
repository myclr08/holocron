"""Es zamanli istekler: her is parcacigi kendi sqlite baglantisini kullanir.

Sayfa acilisinda `/api/settings`, `/api/groups` ve grid istekleri paralel gider.
Tek sqlite baglantisi paylasildiginda bu "bad parameter or other API misuse"
hatasi uretiyordu; asagidaki testler o regresyonu tutar.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from app import repository as repo
from app.settings_store import MODE_SERVER
from tests.fake_jira import issue

BASE = "https://jira.example.com"

CATALOG = [
    {"id": "summary", "name": "Özet", "schema": {"type": "string"}},
    {"id": "status", "name": "Durum", "schema": {"type": "status"}},
]


def seed(api_client, conn):
    repo.store_fields(conn, CATALOG)
    api_client.put("/api/settings", json={"jira.base_url": BASE, "jira.mode": MODE_SERVER})
    group = api_client.post("/api/groups", json={"name": "Filom"}).json()["group"]
    keys = [f"DEMO-{number}" for number in range(1, 11)]
    api_client.post(f"/api/groups/{group['id']}/items", json={"text": " ".join(keys)})
    repo.upsert_issues(conn, [issue(key, f"Özet {key}") for key in keys])
    return group


def test_parallel_requests_all_succeed(api_client, conn):
    group = seed(api_client, conn)
    paths = ["/api/settings", f"/api/groups/{group['id']}/issues", "/api/groups", "/api/health"]

    def call(index: int) -> int:
        return api_client.get(paths[index % len(paths)]).status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(call, range(32)))

    assert codes == [200] * 32


def test_parallel_writes_and_reads_stay_consistent(api_client, conn):
    group = seed(api_client, conn)
    field = api_client.post(
        "/api/local-fields", json={"name": "Not", "type": "text", "track_history": True}
    ).json()["field"]

    def call(index: int) -> int:
        key = f"DEMO-{(index % 10) + 1}"
        if index % 3 == 0:
            response = api_client.put(
                f"/api/issues/{key}/local/{field['id']}", json={"value": f"deger {index}"}
            )
        elif index % 3 == 1:
            response = api_client.get(f"/api/groups/{group['id']}/issues")
        else:
            response = api_client.get(f"/api/issues/{key}")
        return response.status_code

    with ThreadPoolExecutor(max_workers=8) as pool:
        codes = list(pool.map(call, range(30)))

    assert codes == [200] * 30
    assert api_client.get(f"/api/groups/{group['id']}/issues").status_code == 200


def test_export_runs_next_to_other_requests(api_client, conn):
    group = seed(api_client, conn)

    def call(index: int):
        if index % 2:
            return api_client.get("/api/settings").status_code
        return api_client.get(f"/api/groups/{group['id']}/export.xlsx").status_code

    with ThreadPoolExecutor(max_workers=6) as pool:
        codes = list(pool.map(call, range(20)))

    assert codes == [200] * 20
