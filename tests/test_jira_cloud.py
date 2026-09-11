"""Jira Cloud: token sayfalamasi ve Basic auth (e-posta + API token)."""

from __future__ import annotations

import base64

import pytest

from app.jira_client import CloudJiraClient, JiraError, create_client
from app.settings_store import MODE_CLOUD, JiraConfig
from tests.fake_jira import issue, paged_cloud

BASE = "https://demo.atlassian.net"
EMAIL = "kullanici@example.com"
TOKEN = "ornek-api-token"


def config(**overrides) -> JiraConfig:
    data = {"mode": MODE_CLOUD, "base_url": BASE, "email": EMAIL, "secret": TOKEN}
    data.update(overrides)
    return JiraConfig(**data)


def build(fake_jira, sleeps, **overrides) -> CloudJiraClient:
    fake_jira.base_url = BASE
    client = create_client(config(**overrides), session=fake_jira.session, sleep=sleeps.append)
    assert isinstance(client, CloudJiraClient)
    return client


def test_cloud_uses_basic_auth_with_email(fake_jira, sleeps):
    fake_jira.json("GET", "/rest/api/3/myself", {"displayName": "Demo Kullanici"})
    fake_jira.json("GET", "/rest/api/3/serverInfo", {"serverTitle": "Demo", "version": "1000.0.0"})

    result = build(fake_jira, sleeps).test_connection()

    header = fake_jira.calls_to("GET", "/rest/api/3/myself")[0].headers["Authorization"]
    assert base64.b64decode(header.split(" ", 1)[1]).decode() == f"{EMAIL}:{TOKEN}"
    assert result["display_name"] == "Demo Kullanici"


def test_cloud_search_uses_next_page_token(fake_jira, sleeps):
    issues = [issue(f"DEMO-{i}") for i in range(1, 231)]
    fake_jira.add("POST", "/rest/api/3/search/jql", paged_cloud(issues))

    found = build(fake_jira, sleeps).search("project = DEMO", fields=["summary"])

    assert len(found) == 230
    calls = fake_jira.calls_to("POST", "/rest/api/3/search/jql")
    assert len(calls) == 3
    assert "nextPageToken" not in calls[0].json_body
    assert calls[1].json_body["nextPageToken"] == "100"
    assert calls[2].json_body["nextPageToken"] == "200"
    assert calls[0].json_body["fields"] == ["summary"]


def test_cloud_search_stops_when_is_last(fake_jira, sleeps):
    issues = [issue(f"DEMO-{i}") for i in range(1, 51)]
    fake_jira.add("POST", "/rest/api/3/search/jql", paged_cloud(issues))

    found = build(fake_jira, sleeps).search("project = DEMO")
    assert len(found) == 50
    assert len(fake_jira.calls_to("POST", "/rest/api/3/search/jql")) == 1


def test_cloud_search_respects_max_results(fake_jira, sleeps):
    issues = [issue(f"DEMO-{i}") for i in range(1, 231)]
    fake_jira.add("POST", "/rest/api/3/search/jql", paged_cloud(issues))

    found = build(fake_jira, sleeps).search("project = DEMO", max_results=150)
    assert len(found) == 150
    sizes = [c.json_body["maxResults"] for c in fake_jira.calls_to("POST", "/rest/api/3/search/jql")]
    assert sizes == [100, 50]


def test_cloud_uses_v3_field_catalog(fake_jira, sleeps):
    fake_jira.json("GET", "/rest/api/3/field", [{"id": "summary", "name": "Summary"}])
    assert build(fake_jira, sleeps).fetch_fields()[0]["id"] == "summary"


def test_cloud_requires_email_and_token(fake_jira, sleeps):
    with pytest.raises(JiraError) as excinfo:
        build(fake_jira, sleeps, email="").fetch_fields()
    assert excinfo.value.code == "config_missing"
