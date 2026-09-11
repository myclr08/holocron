"""HTTP uclari: fastapi.testclient uzerinden, gercek ag yok."""

from __future__ import annotations

from app import __version__, db
from app.settings_store import AUTH_PAT, MODE_CLOUD, MODE_SERVER
from tests.fake_jira import issue, paged_v2

BASE = "https://jira.example.com"
PAT = "ornek-pat-degeri"


def configure(client, **overrides):
    payload = {
        "jira.mode": MODE_SERVER,
        "jira.base_url": BASE,
        "jira.auth_type": AUTH_PAT,
        "jira.secret": PAT,
    }
    payload.update(overrides)
    return client.put("/api/settings", json=payload)


def test_health(api_client, context):
    response = api_client.get("/api/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["version"] == __version__
    assert data["schema_version"] == db.SCHEMA_VERSION
    assert data["heartbeat"]["interval"] == 30


def test_heartbeat_resets_timer(api_client, context):
    context.heartbeat._last_beat -= 100  # nabiz eskitildi
    assert context.heartbeat.seconds_since_beat() > 99
    response = api_client.post("/api/heartbeat")
    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert context.heartbeat.seconds_since_beat() < 1


def test_shutdown_marks_stop_and_calls_hook(api_client, context):
    called = []
    context.shutdown_hook = lambda: called.append(True)
    assert api_client.post("/api/shutdown").json()["ok"] is True
    assert context.heartbeat.stop_requested is True
    assert called == [True]


def test_settings_round_trip_hides_secret(api_client):
    response = configure(api_client)
    assert response.status_code == 200
    settings = response.json()["settings"]
    assert settings["jira.base_url"] == BASE
    assert settings["secret_set"] is True
    assert PAT not in response.text

    again = api_client.get("/api/settings").json()["settings"]
    assert again["secret_set"] is True
    assert PAT not in str(again)


def test_settings_reject_invalid_mode(api_client):
    response = api_client.put("/api/settings", json={"jira.mode": "karanlik-taraf"})
    assert response.status_code == 400
    assert response.json() == {
        "error": {"code": "invalid_mode", "message": "Mod yalnızca 'server' veya 'cloud' olabilir."}
    }


def test_settings_reject_invalid_base_url(api_client):
    response = api_client.put("/api/settings", json={"jira.base_url": "jira.example.com"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_base_url"


def test_cloud_mode_is_accepted(api_client):
    response = api_client.put(
        "/api/settings",
        json={"jira.mode": MODE_CLOUD, "jira.email": "kullanici@example.com"},
    )
    assert response.status_code == 200
    assert response.json()["settings"]["jira.mode"] == MODE_CLOUD


def test_test_connection_endpoint(api_client, fake_jira):
    fake_jira.json("GET", "/rest/api/2/myself", {"displayName": "Demo Kullanici"})
    fake_jira.json("GET", "/rest/api/2/serverInfo", {"serverTitle": "Demo Jira", "version": "0.0.0"})
    configure(api_client)

    result = api_client.post("/api/jira/test").json()["result"]
    assert result["display_name"] == "Demo Kullanici"
    assert result["server_title"] == "Demo Jira"
    assert result["version"] == "0.0.0"


def test_test_connection_without_base_url(api_client):
    response = api_client.post("/api/jira/test")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "config_missing"


def test_test_connection_reports_auth_failure(api_client, fake_jira):
    fake_jira.json("GET", "/rest/api/2/myself", {"errorMessages": ["Yetkisiz"]}, status=401)
    configure(api_client)

    response = api_client.post("/api/jira/test")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "auth_failed"


def test_field_catalog_is_stored_and_listed(api_client, fake_jira, conn):
    catalog = [
        {"id": "summary", "name": "Ozet", "custom": False, "schema": {"type": "string"}},
        {"id": "customfield_10001", "name": "Ek alan", "custom": True, "schema": {"type": "number"}},
        {"name": "Kimliksiz"},  # id'siz kayit atlanir
    ]
    fake_jira.json("GET", "/rest/api/2/field", catalog)
    configure(api_client)

    refreshed = api_client.post("/api/jira/fields/refresh").json()
    assert refreshed["count"] == 2

    listed = api_client.get("/api/jira/fields").json()["fields"]
    by_id = {item["id"]: item for item in listed}
    assert by_id["customfield_10001"]["custom"] is True
    assert by_id["summary"]["schema_type"] == "string"
    assert conn.execute("SELECT COUNT(*) AS c FROM jira_fields").fetchone()["c"] == 2


def test_field_refresh_replaces_previous_catalog(api_client, fake_jira, conn):
    fake_jira.json("GET", "/rest/api/2/field", [{"id": "eski", "name": "Eski alan"}])
    configure(api_client)
    api_client.post("/api/jira/fields/refresh")

    fake_jira.json("GET", "/rest/api/2/field", [{"id": "yeni", "name": "Yeni alan"}])
    api_client.post("/api/jira/fields/refresh")

    ids = [row["id"] for row in conn.execute("SELECT id FROM jira_fields").fetchall()]
    assert ids == ["yeni"]


def test_unknown_api_path_uses_common_error_shape(api_client):
    response = api_client.get("/api/yok-boyle-bir-uc")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_pages_are_served(api_client):
    for path in ("/", "/settings"):
        response = api_client.get(path)
        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
    assert "HOLOCRON" in api_client.get("/").text
    assert "Jira bağlantısı" in api_client.get("/settings").text


def test_static_assets_are_served(api_client):
    assert api_client.get("/static/css/app.css").status_code == 200
    assert "#ffe81f" in api_client.get("/static/css/app.css").text.lower()
    assert api_client.get("/static/js/settings.js").status_code == 200


def test_search_is_reachable_through_context_client(context, fake_jira):
    """Ayarlardan kurulan istemci gercekten arama yapabiliyor mu."""
    context.settings.apply(
        {"jira.mode": MODE_SERVER, "jira.base_url": BASE, "jira.secret": PAT}
    )
    fake_jira.add("POST", "/rest/api/2/search", paged_v2([issue("DEMO-1"), issue("DEMO-2")]))
    client = context.client_factory(context.settings.jira_config())
    assert [item["key"] for item in client.search("project = DEMO")] == ["DEMO-1", "DEMO-2"]
