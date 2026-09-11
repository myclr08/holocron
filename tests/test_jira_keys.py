"""Anahtar listesiyle cekme: 100'luk paketler ve gecersiz anahtarin ayiklanmasi."""

from __future__ import annotations

import re

from app.jira_client import KEY_CHUNK_SIZE, create_client, quote_jql_value
from app.settings_store import AUTH_PAT, MODE_SERVER, JiraConfig
from tests.fake_jira import FakeResponse, issue

BASE = "https://jira.example.com"
SEARCH_PATH = "/rest/api/2/search"

KEY_PATTERN = re.compile(r'"([^"]+)"')


def build(fake_jira, sleeps):
    config = JiraConfig(mode=MODE_SERVER, base_url=BASE, auth_type=AUTH_PAT, secret="pat")
    return create_client(config, session=fake_jira.session, sleep=sleeps.append)


def key_aware_handler(valid_keys: set[str]):
    """Gecerli anahtarlari dondurur; paket bilinmeyen anahtar iceriyorsa 400 verir."""

    def handler(call):
        jql = (call.json_body or {}).get("jql", "")
        requested = KEY_PATTERN.findall(jql)
        unknown = [key for key in requested if key not in valid_keys]
        if unknown:
            return FakeResponse(
                status=400,
                body={"errorMessages": [f"Kayit bulunamadi: {unknown[0]}"]},
            )
        found = [issue(key) for key in requested]
        return FakeResponse(body={"startAt": 0, "total": len(found), "issues": found})

    return handler


def test_keys_are_requested_in_chunks_of_100(fake_jira, sleeps):
    keys = [f"DEMO-{i}" for i in range(1, 251)]
    fake_jira.add("POST", SEARCH_PATH, key_aware_handler(set(keys)))

    batch = build(fake_jira, sleeps).fetch_issues_by_keys(keys)

    assert [item["key"] for item in batch.issues] == keys
    assert batch.invalid_keys == []
    calls = fake_jira.calls_to("POST", SEARCH_PATH)
    assert len(calls) == 3
    sizes = [len(KEY_PATTERN.findall(c.json_body["jql"])) for c in calls]
    assert sizes == [KEY_CHUNK_SIZE, KEY_CHUNK_SIZE, 50]
    assert calls[0].json_body["jql"].startswith("key in (")


def test_invalid_key_is_isolated_by_halving(fake_jira, sleeps):
    valid = [f"DEMO-{i}" for i in range(1, 8)]
    keys = valid[:3] + ["DEMO-YOK"] + valid[3:]
    fake_jira.add("POST", SEARCH_PATH, key_aware_handler(set(valid)))

    batch = build(fake_jira, sleeps).fetch_issues_by_keys(keys)

    assert sorted(item["key"] for item in batch.issues) == sorted(valid)
    assert batch.invalid_keys == ["DEMO-YOK"]
    # Ilk paket + ikiye bolmeler: tek tek sorgudan cok daha az cagri.
    assert 1 < len(fake_jira.calls_to("POST", SEARCH_PATH)) < len(keys) + 2


def test_multiple_invalid_keys_are_all_collected(fake_jira, sleeps):
    valid = [f"DEMO-{i}" for i in range(1, 11)]
    keys = valid + ["DEMO-YOK1", "DEMO-YOK2"]
    fake_jira.add("POST", SEARCH_PATH, key_aware_handler(set(valid)))

    batch = build(fake_jira, sleeps).fetch_issues_by_keys(keys)

    assert sorted(batch.invalid_keys) == ["DEMO-YOK1", "DEMO-YOK2"]
    assert len(batch.issues) == len(valid)


def test_all_keys_invalid(fake_jira, sleeps):
    fake_jira.add("POST", SEARCH_PATH, key_aware_handler(set()))
    batch = build(fake_jira, sleeps).fetch_issues_by_keys(["DEMO-1", "DEMO-2"])
    assert batch.issues == []
    assert sorted(batch.invalid_keys) == ["DEMO-1", "DEMO-2"]


def test_duplicate_and_blank_keys_are_cleaned(fake_jira, sleeps):
    fake_jira.add("POST", SEARCH_PATH, key_aware_handler({"DEMO-1", "DEMO-2"}))
    batch = build(fake_jira, sleeps).fetch_issues_by_keys(
        ["DEMO-1", " DEMO-2 ", "DEMO-1", "", "   ", "demo-1"]
    )
    requested = KEY_PATTERN.findall(fake_jira.calls_to("POST", SEARCH_PATH)[0].json_body["jql"])
    assert requested == ["DEMO-1", "DEMO-2"]
    assert len(batch.issues) == 2


def test_empty_key_list_does_not_call_jira(fake_jira, sleeps):
    batch = build(fake_jira, sleeps).fetch_issues_by_keys([])
    assert batch.issues == [] and batch.invalid_keys == []
    assert fake_jira.calls == []


def test_fields_are_forwarded_for_key_fetch(fake_jira, sleeps):
    fake_jira.add("POST", SEARCH_PATH, key_aware_handler({"DEMO-1"}))
    build(fake_jira, sleeps).fetch_issues_by_keys(["DEMO-1"], fields=["summary", "assignee"])
    assert fake_jira.calls_to("POST", SEARCH_PATH)[0].json_body["fields"] == ["summary", "assignee"]


def test_jql_quoting_escapes_dangerous_characters():
    assert quote_jql_value('DEMO"-1') == '"DEMO\\"-1"'
    assert quote_jql_value("DEMO\\1") == '"DEMO\\\\1"'


def test_non_400_errors_are_not_split(fake_jira, sleeps):
    import pytest

    from app.jira_client import JiraError

    fake_jira.json("POST", SEARCH_PATH, {"errorMessages": ["Yetkisiz"]}, status=401)
    with pytest.raises(JiraError) as excinfo:
        build(fake_jira, sleeps).fetch_issues_by_keys(["DEMO-1", "DEMO-2"])
    assert excinfo.value.status == 401
    assert len(fake_jira.calls_to("POST", SEARCH_PATH)) == 1
