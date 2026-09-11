"""Ag katmani: adres sirasi, vekil sunucu kipleri, hata ayrimi, zaman asimlari.

Hepsinin kaynagi ayni saha kusuru: kurum aginda "Baglantiyi sina" doksan
saniye bekletip anlamsiz bir hata veriyordu.
"""

from __future__ import annotations

import socket

import pytest
import requests
import urllib3.exceptions

from app import net
from app.jira_client import DEFAULT_TIMEOUT, MAX_ATTEMPTS, JiraError, create_client
from app.settings_store import (
    AUTH_PAT,
    MODE_SERVER,
    PROXY_DIRECT,
    PROXY_MANUAL,
    PROXY_SYSTEM,
    JiraConfig,
)

BASE = "https://jira.example.com"
FIELD_PATH = "/rest/api/2/field"
HOST = "jira.example.com"


def info(family, address, port=443):
    """`getaddrinfo` bicimi: (family, socktype, proto, canonname, sockaddr)."""
    sockaddr = (address, port) if family == socket.AF_INET else (address, port, 0, 0)
    return (family, socket.SOCK_STREAM, 6, "", sockaddr)


V4 = info(socket.AF_INET, "10.0.0.7")
V6 = info(socket.AF_INET6, "2001:db8::7")


def config(**extra):
    values = dict(mode=MODE_SERVER, base_url=BASE, auth_type=AUTH_PAT, secret="pat")
    values.update(extra)
    return JiraConfig(**values)


def build(fake_jira, sleeps, **extra):
    return create_client(config(**extra), session=fake_jira.session, sleep=sleeps.append)


# --- IPv4 onceligi --------------------------------------------------------


def test_ipv4_addresses_are_tried_first():
    ordered = net.order_addresses([V6, V4])
    assert [item[0] for item in ordered] == [socket.AF_INET, socket.AF_INET6]


def test_ordering_is_stable_within_a_family():
    first = info(socket.AF_INET, "10.0.0.1")
    second = info(socket.AF_INET, "10.0.0.2")
    assert net.order_addresses([V6, second, first]) == [second, first, V6]


def test_ordering_can_be_switched_off():
    assert net.order_addresses([V6, V4], ipv4_first=False) == [V6, V4]


def test_resolve_orders_the_system_answer():
    calls = []

    def fake_getaddrinfo(host, port, family, socktype):
        calls.append((host, port, family, socktype))
        return [V6, V4]

    ordered = net.resolve(HOST, 443, getaddrinfo=fake_getaddrinfo)
    assert calls == [(HOST, 443, socket.AF_UNSPEC, socket.SOCK_STREAM)]
    assert ordered[0][0] == socket.AF_INET


def test_resolve_keeps_the_system_order_when_disabled():
    ordered = net.resolve(HOST, 443, ipv4_first=False, getaddrinfo=lambda *a: [V6, V4])
    assert ordered[0][0] == socket.AF_INET6


def test_ipv4_first_session_mounts_our_adapter():
    session = net.build_session(ipv4_first=True)
    adapter = session.get_adapter("https://jira.example.com")
    assert isinstance(adapter, net.Ipv4FirstAdapter)
    pools = adapter.poolmanager.pool_classes_by_scheme
    assert pools["https"] is net.Ipv4FirstHTTPSConnectionPool
    assert pools["https"].ConnectionCls is net.Ipv4FirstHTTPSConnection


def test_plain_session_keeps_the_default_adapter():
    session = net.build_session(ipv4_first=False)
    assert not isinstance(session.get_adapter("https://jira.example.com"), net.Ipv4FirstAdapter)


def test_addresses_and_family_names_are_readable():
    assert net.addresses_of([V4, V6]) == ["10.0.0.7", "2001:db8::7"]
    assert net.family_name(socket.AF_INET) == "IPv4"
    assert net.family_name(socket.AF_INET6) == "IPv6"


# --- zaman asimi ----------------------------------------------------------


def test_timeout_is_a_connect_read_pair(fake_jira, sleeps):
    assert DEFAULT_TIMEOUT == (10.0, 30.0)
    fake_jira.json("GET", FIELD_PATH, [])
    build(fake_jira, sleeps).fetch_fields()
    assert fake_jira.calls_to("GET", FIELD_PATH)[0].kwargs["timeout"] == (10.0, 30.0)


# --- baglanti hatasi: tek deneme -----------------------------------------


def raising(exc):
    def request(method, url, **kwargs):
        raise exc

    return request


def test_connect_timeout_is_not_retried(fake_jira, sleeps):
    fake_jira.session.request = raising(requests.exceptions.ConnectTimeout("zaman asimi"))

    with pytest.raises(JiraError) as excinfo:
        build(fake_jira, sleeps).fetch_fields()

    assert excinfo.value.code == "connect_timeout"
    assert sleeps == []  # doksan saniye bekletme yok
    assert "10 saniye" in excinfo.value.message
    assert "Teşhis" in excinfo.value.message


def test_connection_refused_is_not_retried(fake_jira, sleeps):
    inner = ConnectionRefusedError(111, "Connection refused")
    wrapped = urllib3.exceptions.NewConnectionError(None, "Failed to establish a new connection")
    wrapped.__cause__ = inner
    fake_jira.session.request = raising(requests.exceptions.ConnectionError(wrapped))

    with pytest.raises(JiraError) as excinfo:
        build(fake_jira, sleeps).fetch_fields()

    assert excinfo.value.code == "connection_refused"
    assert sleeps == []


def test_dns_failure_is_reported_separately(fake_jira, sleeps):
    inner = socket.gaierror(-2, "Name or service not known")
    fake_jira.session.request = raising(requests.exceptions.ConnectionError(inner))

    with pytest.raises(JiraError) as excinfo:
        build(fake_jira, sleeps).fetch_fields()

    assert excinfo.value.code == "dns_error"
    assert sleeps == []


def test_read_timeout_is_still_retried(fake_jira, sleeps):
    fake_jira.session.request = raising(requests.exceptions.ReadTimeout("okuma"))

    with pytest.raises(JiraError) as excinfo:
        build(fake_jira, sleeps).fetch_fields()

    assert excinfo.value.code == "timeout"
    assert len(sleeps) == MAX_ATTEMPTS - 1


def test_error_messages_never_name_the_server(fake_jira, sleeps):
    cases = [
        requests.exceptions.ConnectTimeout("jira.example.com timed out"),
        requests.exceptions.ConnectionError(socket.gaierror(-2, "jira.example.com")),
    ]
    for exc in cases:
        fake_jira.session.request = raising(exc)
        with pytest.raises(JiraError) as excinfo:
            build(fake_jira, sleeps).fetch_fields()
        assert HOST not in excinfo.value.message
        assert net.SERVER_LABEL in excinfo.value.message


def test_ssl_error_points_at_the_ca_setting(fake_jira, sleeps):
    fake_jira.session.request = raising(requests.exceptions.SSLError("certificate verify failed"))

    with pytest.raises(JiraError) as excinfo:
        build(fake_jira, sleeps).fetch_fields()

    assert excinfo.value.code == "ssl_error"
    assert "CA" in excinfo.value.message


def test_proxy_error_suggests_direct_mode(fake_jira, sleeps):
    fake_jira.session.request = raising(requests.exceptions.ProxyError("tunnel failed"))

    with pytest.raises(JiraError) as excinfo:
        build(fake_jira, sleeps).fetch_fields()

    assert excinfo.value.code == "proxy_error"
    assert "Doğrudan bağlan" in excinfo.value.message


def test_classifier_walks_the_requests_wrapper():
    reason = urllib3.exceptions.NewConnectionError(None, "failed")
    reason.__cause__ = ConnectionRefusedError(111, "Connection refused")
    outer = requests.exceptions.ConnectionError(
        urllib3.exceptions.MaxRetryError(None, BASE, reason=reason)
    )
    code, _message = net.classify_connection_error(outer)
    assert code == "connection_refused"


# --- vekil sunucu ---------------------------------------------------------


def test_system_mode_falls_back_to_getproxies():
    proxies, source = net.effective_proxies(
        HOST, mode=PROXY_SYSTEM, system=lambda: {"https": "http://vekil.example.com:8080"}
    )
    assert proxies == {"https": "http://vekil.example.com:8080"}
    assert source == "sistem"


def test_system_mode_prefers_the_typed_fields():
    proxies, source = net.effective_proxies(
        HOST,
        mode=PROXY_SYSTEM,
        proxy_https="http://elle.example.com:3128",
        system=lambda: {"https": "http://vekil.example.com:8080"},
    )
    assert proxies == {"https": "http://elle.example.com:3128"}
    assert source == "ayar"


def test_manual_mode_never_reads_the_system():
    proxies, source = net.effective_proxies(
        HOST, mode=PROXY_MANUAL, system=lambda: {"https": "http://vekil.example.com:8080"}
    )
    assert proxies == {}
    assert source == "yok"


def test_direct_mode_disables_every_proxy():
    proxies, source = net.effective_proxies(
        HOST, mode=PROXY_DIRECT, proxy_https="http://elle.example.com:3128",
        system=lambda: {"https": "http://vekil.example.com:8080"},
    )
    assert proxies == {"http": "", "https": ""}
    assert source == "dogrudan"


def test_system_proxies_reads_urllib_and_keeps_only_http_schemes():
    raw = {"http": "http://v:8080", "https": "http://v:8080", "ftp": "ftp://v:21", "no": ""}
    assert net.system_proxies(lambda: raw) == {"http": "http://v:8080", "https": "http://v:8080"}


def test_system_proxies_survives_a_broken_registry():
    def boom():
        raise OSError("kayit defteri okunamadi")

    assert net.system_proxies(boom) == {}


@pytest.mark.parametrize(
    "host,no_proxy,expected",
    [
        ("jira.example.com", "example.com", True),
        ("jira.example.com", ".example.com", True),
        ("jira.example.com", "jira.example.com", True),
        ("jira.example.com", "baska.com, example.com", True),
        ("jira.example.com", "*", True),
        ("jira.example.com", "notexample.com", False),
        ("jira.example.com", "", False),
        ("", "example.com", False),
    ],
)
def test_no_proxy_matching(host, no_proxy, expected):
    assert net.bypasses_proxy(host, no_proxy) is expected


def test_no_proxy_blanks_the_proxy_map():
    proxies, source = net.effective_proxies(
        HOST,
        mode=PROXY_SYSTEM,
        no_proxy=".example.com",
        system=lambda: {"https": "http://vekil.example.com:8080"},
    )
    assert proxies["https"] == ""
    assert proxies["no_proxy"] == ".example.com"
    assert source == "atlandi"


def test_no_proxy_is_passed_along_when_it_does_not_match():
    proxies, _source = net.effective_proxies(
        HOST, mode=PROXY_MANUAL, proxy_https="http://v:8080", no_proxy="baska.com"
    )
    assert proxies == {"https": "http://v:8080", "no_proxy": "baska.com"}


def test_proxy_for_picks_the_scheme():
    proxies = {"http": "http://a:1", "https": "http://b:2"}
    assert net.proxy_for("https://jira.example.com/x", proxies) == "http://b:2"
    assert net.proxy_for("http://jira.example.com/x", proxies) == "http://a:1"
    assert net.proxy_for("https://jira.example.com", None) == ""


def test_split_host_port_handles_both_forms():
    assert net.split_host_port("http://vekil.example.com:3128") == ("vekil.example.com", 3128)
    assert net.split_host_port("vekil.example.com:3128") == ("vekil.example.com", 3128)
    assert net.split_host_port("vekil.example.com") == ("vekil.example.com", 8080)
    assert net.split_host_port("") == ("", 8080)


def test_pac_url_is_reported_but_never_resolved():
    assert net.pac_url(lambda: "http://wpad.example.com/proxy.pac") == (
        "http://wpad.example.com/proxy.pac"
    )
    assert net.pac_url(lambda: "") == ""

    def boom():
        raise OSError("kayit defteri yok")

    assert net.pac_url(boom) == ""


# --- istemcinin vekil sunucu davranisi ------------------------------------


def test_client_sends_the_system_proxy_when_fields_are_empty(fake_jira, sleeps, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://kurumsal.example.com:8080")
    fake_jira.json("GET", FIELD_PATH, [])

    build(fake_jira, sleeps, proxy_mode=PROXY_SYSTEM).fetch_fields()

    call = fake_jira.calls_to("GET", FIELD_PATH)[0]
    assert call.kwargs["proxies"]["https"] == "http://kurumsal.example.com:8080"


def test_direct_mode_ignores_the_environment_proxy(fake_jira, sleeps, monkeypatch):
    """Saha kusuru: https_proxy kurumsal vekile bakiyor, Jira ic agda."""
    monkeypatch.setenv("HTTPS_PROXY", "http://kurumsal.example.com:8080")
    fake_jira.json("GET", FIELD_PATH, [])

    client = build(fake_jira, sleeps, proxy_mode=PROXY_DIRECT)
    client.fetch_fields()

    call = fake_jira.calls_to("GET", FIELD_PATH)[0]
    assert call.kwargs["proxies"] == {"http": "", "https": ""}
    # trust_env kapali olmasa requests ortam degiskenini yine de karistirirdi.
    assert client.session.trust_env is False


def test_direct_mode_request_really_goes_out_without_a_proxy(monkeypatch):
    """Sahte adaptorle: requests'in kendi birlestirmesinden sonra vekil kalmiyor."""
    monkeypatch.setenv("HTTPS_PROXY", "http://kurumsal.example.com:8080")
    seen: dict[str, object] = {}

    class SpyAdapter(requests.adapters.HTTPAdapter):
        def send(self, request, **kwargs):
            seen["proxies"] = kwargs.get("proxies")
            response = requests.Response()
            response.status_code = 200
            response.url = request.url
            response._content = b"[]"
            response.headers["Content-Type"] = "application/json"
            return response

    client = create_client(config(proxy_mode=PROXY_DIRECT))
    client.session.mount("https://", SpyAdapter())
    client.fetch_fields()

    assert requests.utils.select_proxy(BASE + FIELD_PATH, seen["proxies"]) in ("", None)


def test_system_mode_still_lets_requests_see_the_environment(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://kurumsal.example.com:8080")
    client = create_client(config(proxy_mode=PROXY_SYSTEM))
    assert client.session.trust_env is True
    proxies = client._proxies()
    assert proxies["https"] == "http://kurumsal.example.com:8080"


def test_no_proxy_setting_reaches_the_request(fake_jira, sleeps, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://kurumsal.example.com:8080")
    fake_jira.json("GET", FIELD_PATH, [])

    build(fake_jira, sleeps, no_proxy=".example.com").fetch_fields()

    call = fake_jira.calls_to("GET", FIELD_PATH)[0]
    assert call.kwargs["proxies"]["https"] == ""
