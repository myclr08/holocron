"""Jira Server / Data Center + PAT: birincil senaryo."""

from __future__ import annotations

import base64

import pytest

from app.jira_client import JiraError, ServerJiraClient, create_client
from app.settings_store import AUTH_BASIC, AUTH_PAT, MODE_SERVER, JiraConfig
from tests.fake_jira import FakeResponse, issue, paged_v2

BASE = "https://jira.example.com"
PAT = "ornek-pat-degeri"


def config(**overrides) -> JiraConfig:
    data = {
        "mode": MODE_SERVER,
        "base_url": BASE,
        "auth_type": AUTH_PAT,
        "secret": PAT,
    }
    data.update(overrides)
    return JiraConfig(**data)


def build(fake_jira, sleeps, **overrides) -> ServerJiraClient:
    client = create_client(config(**overrides), session=fake_jira.session, sleep=sleeps.append)
    assert isinstance(client, ServerJiraClient)
    return client


def test_pat_uses_bearer_header(fake_jira, sleeps):
    fake_jira.json("GET", "/rest/api/2/myself", {"displayName": "Demo Kullanici"})
    fake_jira.json("GET", "/rest/api/2/serverInfo", {"serverTitle": "Demo Jira", "version": "0.0.0"})

    result = build(fake_jira, sleeps).test_connection()

    call = fake_jira.calls_to("GET", "/rest/api/2/myself")[0]
    assert call.headers["Authorization"] == f"Bearer {PAT}"
    assert result["display_name"] == "Demo Kullanici"
    assert result["server_title"] == "Demo Jira"
    assert result["version"] == "0.0.0"
    assert result["mode"] == MODE_SERVER


def test_test_connection_survives_missing_server_info(fake_jira, sleeps):
    fake_jira.json("GET", "/rest/api/2/myself", {"name": "demo.kullanici"})
    fake_jira.json("GET", "/rest/api/2/serverInfo", {"errorMessages": ["yetki yok"]}, status=403)

    result = build(fake_jira, sleeps).test_connection()
    assert result["display_name"] == "demo.kullanici"
    assert result["server_title"] == ""


def test_basic_auth_option(fake_jira, sleeps):
    fake_jira.json("GET", "/rest/api/2/myself", {"displayName": "Demo"})
    fake_jira.json("GET", "/rest/api/2/serverInfo", {})

    build(fake_jira, sleeps, auth_type=AUTH_BASIC, username="demo.kullanici").test_connection()

    header = fake_jira.calls_to("GET", "/rest/api/2/myself")[0].headers["Authorization"]
    assert header.startswith("Basic ")
    decoded = base64.b64decode(header.split(" ", 1)[1]).decode()
    assert decoded == f"demo.kullanici:{PAT}"


def test_basic_auth_requires_username(fake_jira, sleeps):
    client = build(fake_jira, sleeps, auth_type=AUTH_BASIC, username="")
    with pytest.raises(JiraError) as excinfo:
        client.test_connection()
    assert excinfo.value.code == "config_missing"


def test_missing_secret_is_reported(fake_jira, sleeps):
    client = build(fake_jira, sleeps, secret="")
    with pytest.raises(JiraError) as excinfo:
        client.fetch_fields()
    assert excinfo.value.code == "config_missing"


def test_search_uses_v2_endpoint_and_paginates(fake_jira, sleeps):
    issues = [issue(f"DEMO-{i}") for i in range(1, 251)]
    fake_jira.add("POST", "/rest/api/2/search", paged_v2(issues))

    found = build(fake_jira, sleeps).search("project = DEMO", fields=["summary", "status"])

    assert [item["key"] for item in found] == [item["key"] for item in issues]
    calls = fake_jira.calls_to("POST", "/rest/api/2/search")
    assert len(calls) == 3
    assert [c.json_body["startAt"] for c in calls] == [0, 100, 200]
    assert calls[0].json_body["fields"] == ["summary", "status"]
    assert calls[0].json_body["maxResults"] == 100
    assert "expand" not in calls[0].json_body


def test_search_respects_max_results(fake_jira, sleeps):
    issues = [issue(f"DEMO-{i}") for i in range(1, 251)]
    fake_jira.add("POST", "/rest/api/2/search", paged_v2(issues))

    found = build(fake_jira, sleeps).search("project = DEMO", max_results=120)

    assert len(found) == 120
    sizes = [c.json_body["maxResults"] for c in fake_jira.calls_to("POST", "/rest/api/2/search")]
    assert sizes == [100, 20]


def test_search_stops_on_empty_page(fake_jira, sleeps):
    fake_jira.json("POST", "/rest/api/2/search", {"startAt": 0, "total": 0, "issues": []})
    assert build(fake_jira, sleeps).search("project = DEMO") == []
    assert len(fake_jira.calls_to("POST", "/rest/api/2/search")) == 1


def test_fetch_fields_returns_catalog(fake_jira, sleeps):
    catalog = [
        {"id": "summary", "name": "Ozet", "custom": False, "schema": {"type": "string"}},
        {"id": "customfield_10001", "name": "Ek alan", "custom": True, "schema": {"type": "number"}},
    ]
    fake_jira.json("GET", "/rest/api/2/field", catalog)
    assert build(fake_jira, sleeps).fetch_fields() == catalog


def test_bad_credentials_raise_auth_error(fake_jira, sleeps):
    fake_jira.json("GET", "/rest/api/2/myself", {"errorMessages": ["Yetkisiz"]}, status=401)
    with pytest.raises(JiraError) as excinfo:
        build(fake_jira, sleeps).test_connection()
    assert excinfo.value.code == "auth_failed"
    assert excinfo.value.status == 401


def test_html_error_body_is_tolerated(fake_jira, sleeps):
    fake_jira.add(
        "GET",
        "/rest/api/2/field",
        lambda call: FakeResponse(status=500, text_body="<html>hata</html>"),
    )
    with pytest.raises(JiraError) as excinfo:
        build(fake_jira, sleeps).fetch_fields()
    assert excinfo.value.code == "http_500"


def test_proxy_ca_and_verify_are_passed_through(fake_jira, sleeps):
    fake_jira.json("GET", "/rest/api/2/field", [])
    build(
        fake_jira,
        sleeps,
        proxy_https="http://proxy.example.com:8080",
        ca_file="/tmp/ornek-ca.pem",
    ).fetch_fields()
    call = fake_jira.calls_to("GET", "/rest/api/2/field")[0]
    assert call.kwargs["proxies"] == {"https": "http://proxy.example.com:8080"}
    assert call.kwargs["verify"] == "/tmp/ornek-ca.pem"
    assert call.kwargs["timeout"] == 30.0


def test_verify_can_be_disabled(fake_jira, sleeps):
    fake_jira.json("GET", "/rest/api/2/field", [])
    build(fake_jira, sleeps, verify_ssl=False, ca_file="/tmp/ornek-ca.pem").fetch_fields()
    assert fake_jira.calls_to("GET", "/rest/api/2/field")[0].kwargs["verify"] is False


def test_empty_base_url_is_rejected():
    with pytest.raises(JiraError) as excinfo:
        create_client(config(base_url=""))
    assert excinfo.value.code == "config_missing"


def test_unknown_mode_is_rejected():
    with pytest.raises(JiraError) as excinfo:
        create_client(config(mode="karanlik-taraf"))
    assert excinfo.value.code == "config_invalid"
