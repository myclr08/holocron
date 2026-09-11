"""429 ve gecici sunucu hatalarinda yeniden deneme davranisi."""

from __future__ import annotations

import pytest
import requests

from app.jira_client import BACKOFF_BASE, MAX_ATTEMPTS, JiraError, create_client
from app.settings_store import AUTH_PAT, MODE_SERVER, JiraConfig
from tests.fake_jira import FakeResponse

BASE = "https://jira.example.com"
FIELD_PATH = "/rest/api/2/field"


def build(fake_jira, sleeps):
    config = JiraConfig(mode=MODE_SERVER, base_url=BASE, auth_type=AUTH_PAT, secret="pat")
    return create_client(config, session=fake_jira.session, sleep=sleeps.append)


def test_rate_limit_is_retried_then_succeeds(fake_jira, sleeps):
    fake_jira.sequence(
        "GET",
        FIELD_PATH,
        [
            FakeResponse(status=429, body={"errorMessages": ["Cok fazla istek"]}),
            FakeResponse(status=200, body=[{"id": "summary", "name": "Ozet"}]),
        ],
    )

    fields = build(fake_jira, sleeps).fetch_fields()

    assert fields[0]["id"] == "summary"
    assert len(fake_jira.calls_to("GET", FIELD_PATH)) == 2
    assert sleeps == [BACKOFF_BASE]


def test_backoff_grows_exponentially(fake_jira, sleeps):
    fake_jira.sequence(
        "GET",
        FIELD_PATH,
        [
            FakeResponse(status=503, body={}),
            FakeResponse(status=503, body={}),
            FakeResponse(status=200, body=[]),
        ],
    )

    build(fake_jira, sleeps).fetch_fields()

    assert sleeps == [BACKOFF_BASE, BACKOFF_BASE * 2]
    assert len(fake_jira.calls_to("GET", FIELD_PATH)) == MAX_ATTEMPTS


def test_retry_after_header_is_respected(fake_jira, sleeps):
    fake_jira.sequence(
        "GET",
        FIELD_PATH,
        [
            FakeResponse(status=429, body={}, headers={"Retry-After": "7"}),
            FakeResponse(status=200, body=[]),
        ],
    )

    build(fake_jira, sleeps).fetch_fields()
    assert sleeps == [7.0]


def test_attempts_are_capped(fake_jira, sleeps):
    fake_jira.json("GET", FIELD_PATH, {"errorMessages": ["Hala mesgul"]}, status=429)

    with pytest.raises(JiraError) as excinfo:
        build(fake_jira, sleeps).fetch_fields()

    assert excinfo.value.code == "rate_limited"
    assert len(fake_jira.calls_to("GET", FIELD_PATH)) == MAX_ATTEMPTS
    assert len(sleeps) == MAX_ATTEMPTS - 1


def test_client_errors_are_not_retried(fake_jira, sleeps):
    fake_jira.json("GET", FIELD_PATH, {"errorMessages": ["Yetkisiz"]}, status=401)

    with pytest.raises(JiraError):
        build(fake_jira, sleeps).fetch_fields()

    assert len(fake_jira.calls_to("GET", FIELD_PATH)) == 1
    assert sleeps == []


def test_timeout_is_retried_then_raised(fake_jira, sleeps):
    def always_timeout(method, url, **kwargs):
        raise requests.exceptions.Timeout("zaman asimi")

    fake_jira.session.request = always_timeout

    with pytest.raises(JiraError) as excinfo:
        build(fake_jira, sleeps).fetch_fields()

    assert excinfo.value.code == "timeout"
    assert len(sleeps) == MAX_ATTEMPTS - 1


def test_ssl_error_is_reported_clearly(fake_jira, sleeps):
    def ssl_boom(method, url, **kwargs):
        raise requests.exceptions.SSLError("sertifika dogrulanamadi")

    fake_jira.session.request = ssl_boom

    with pytest.raises(JiraError) as excinfo:
        build(fake_jira, sleeps).fetch_fields()

    assert excinfo.value.code == "ssl_error"
    assert sleeps == []


def test_proxy_error_is_reported_clearly(fake_jira, sleeps):
    def proxy_boom(method, url, **kwargs):
        raise requests.exceptions.ProxyError("vekil sunucuya ulasilamadi")

    fake_jira.session.request = proxy_boom

    with pytest.raises(JiraError) as excinfo:
        build(fake_jira, sleeps).fetch_fields()

    assert excinfo.value.code == "proxy_error"


def test_non_json_body_on_success_is_reported(fake_jira, sleeps):
    fake_jira.add("GET", FIELD_PATH, lambda call: FakeResponse(status=200, text_body="merhaba"))

    with pytest.raises(JiraError) as excinfo:
        build(fake_jira, sleeps).fetch_fields()

    assert excinfo.value.code == "bad_response"
