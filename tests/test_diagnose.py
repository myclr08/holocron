"""Adim adim teshis: her halka ok/fail/skip uretiyor mu, oneriler dogru mu.

Hicbir test gercek soket acmaz; cozucu, baglayici, tunel ve TLS sondalari
disaridan verilir.
"""

from __future__ import annotations

import socket
import ssl

import pytest
import requests

from app import diagnose
from app.diagnose import FAIL, OK, SKIP, run_diagnostics
from app.jira_client import JiraError
from app.settings_store import (
    AUTH_PAT,
    MODE_SERVER,
    PROXY_DIRECT,
    PROXY_MANUAL,
    PROXY_SYSTEM,
    JiraConfig,
)

BASE = "https://jira.example.com"
HOST = "jira.example.com"
TOKEN = "cok-gizli-pat-degeri"


def config(**extra):
    values = dict(mode=MODE_SERVER, base_url=BASE, auth_type=AUTH_PAT, secret=TOKEN)
    values.update(extra)
    return JiraConfig(**values)


def info(family, address, port=443):
    sockaddr = (address, port) if family == socket.AF_INET else (address, port, 0, 0)
    return (family, socket.SOCK_STREAM, 6, "", sockaddr)


V4 = info(socket.AF_INET, "10.0.0.7")
V6 = info(socket.AF_INET6, "2001:db8::7")
PROXY_V4 = info(socket.AF_INET, "10.9.9.9", 8080)


class FakeSocket:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class FakeResponse:
    def __init__(self, status: int = 200) -> None:
        self.status_code = status


def resolver(mapping):
    """Ad → adres listesi; bilinmeyen ad `gaierror` verir."""

    def resolve(host, port, ipv4_first=True):
        if host not in mapping:
            raise socket.gaierror(-2, "Name or service not known")
        from app import net

        return net.order_addresses(mapping[host], ipv4_first)

    return resolve


def connector(opens):
    """Acilan adresler kumesi; digerleri zaman asimina duser."""
    attempts: list[str] = []

    def connect(family, sockaddr, timeout):
        address = sockaddr[0]
        attempts.append(address)
        if address in opens:
            return FakeSocket()
        raise TimeoutError("timed out")

    connect.attempts = attempts  # type: ignore[attr-defined]
    return connect


def run(cfg=None, **kwargs):
    """Varsayilani saglikli bir dunya olan kosucu."""
    defaults = dict(
        resolver=resolver({HOST: [V4, V6]}),
        connector=connector({"10.0.0.7"}),
        tunneler=lambda sock, host, port: "HTTP/1.1 200 Connection established",
        tls_prober=lambda sock, host, cfg: {
            "version": "TLSv1.3",
            "subject": HOST,
            "issuer": "Ornek CA",
            "not_after": "Jan 1 00:00:00 2030 GMT",
        },
        http_prober=lambda url, proxies, cfg: FakeResponse(200),
        client_factory=lambda cfg: _FakeClient(),
        system_proxies=lambda: {},
        pac_reader=lambda: "",
    )
    defaults.update(kwargs)
    return run_diagnostics(cfg or config(), **defaults)


class _FakeClient:
    def test_connection(self):
        return {"display_name": "Ornek Kullanici", "server_title": "Ornek Jira", "version": "9.4.0"}


def steps_by_key(result):
    return {step["key"]: step for step in result["steps"]}


# --- mutlu yol ------------------------------------------------------------


def test_all_steps_pass_on_a_healthy_network():
    result = run()
    assert result["ok"] is True
    keys = [step["key"] for step in result["steps"]]
    assert keys == ["url", "proxy", "dns", "tcp", "tcp_proxy", "tls", "http", "auth"]
    assert steps_by_key(result)["auth"]["status"] == OK
    assert "Ornek Kullanici" in steps_by_key(result)["auth"]["message"]


def test_every_step_reports_a_status_and_a_duration():
    for step in run()["steps"]:
        assert step["status"] in (OK, FAIL, SKIP)
        assert isinstance(step["ms"], int)
        assert step["message"]


def test_secret_never_appears_in_the_output():
    text = str(run(config(username="demo.kullanici")))
    assert TOKEN not in text
    assert "demo.kullanici" not in text
    # Sunucu adi girer: kullanici kendi ekranina bakiyor.
    assert HOST in text


# --- adres ayristirma -----------------------------------------------------


@pytest.mark.parametrize("bad", ["", "jira.example.com", "ftp://jira.example.com"])
def test_a_broken_address_stops_at_the_first_step(bad):
    result = run(config(base_url=bad))
    assert result["ok"] is False
    assert len(result["steps"]) == 1
    assert result["steps"][0]["key"] == "url"
    assert result["steps"][0]["status"] == FAIL


def test_the_port_comes_from_the_address_or_the_scheme():
    assert steps_by_key(run())["url"]["detail"]["port"] == 443
    result = run(
        config(base_url="http://jira.example.com:8080"),
        resolver=resolver({HOST: [info(socket.AF_INET, "10.0.0.7", 8080)]}),
    )
    assert steps_by_key(result)["url"]["detail"]["port"] == 8080
    assert steps_by_key(result)["tls"]["status"] == SKIP


# --- DNS ------------------------------------------------------------------


def test_dns_failure_stops_the_chain_and_lists_the_reason():
    result = run(resolver=resolver({}))
    keys = [step["key"] for step in result["steps"]]
    assert keys == ["url", "proxy", "dns"]
    dns = steps_by_key(result)["dns"]
    assert dns["status"] == FAIL
    assert HOST in dns["message"]
    assert result["ok"] is False


def test_dns_lists_both_families_in_ipv4_first_order():
    dns = steps_by_key(run(resolver=resolver({HOST: [V6, V4]})))["dns"]
    assert dns["detail"]["addresses"] == ["10.0.0.7", "2001:db8::7"]
    assert dns["detail"]["families"] == ["IPv4", "IPv6"]
    assert dns["detail"]["ipv4_first"] is True


def test_disabled_ipv4_first_is_reported_as_advice():
    result = run(config(ipv4_first=False), resolver=resolver({HOST: [V6, V4]}))
    dns = steps_by_key(result)["dns"]
    assert dns["detail"]["families"] == ["IPv6", "IPv4"]
    assert any("IPv4" in line for line in result["advice"])


# --- TCP ------------------------------------------------------------------


def test_tcp_tries_ipv4_first_and_says_which_address_opened():
    connect = connector({"10.0.0.7"})
    tcp = steps_by_key(run(resolver=resolver({HOST: [V6, V4]}), connector=connect))["tcp"]
    assert tcp["status"] == OK
    assert connect.attempts[0] == "10.0.0.7"
    assert "10.0.0.7:443" in tcp["message"]


def test_ipv6_first_shows_the_wasted_attempt():
    connect = connector({"10.0.0.7"})
    result = run(
        config(ipv4_first=False), resolver=resolver({HOST: [V6, V4]}), connector=connect
    )
    tcp = steps_by_key(result)["tcp"]
    # Ilk iki deneme TCP adiminin; ucuncusu TLS adiminin acilan adrese donusu.
    assert connect.attempts[:2] == ["2001:db8::7", "10.0.0.7"]
    assert tcp["status"] == OK
    attempts = tcp["detail"]["attempts"]
    assert attempts[0]["status"] == FAIL
    assert attempts[0]["message"] == "zaman aşımı"
    assert attempts[1]["status"] == OK


def test_no_address_opens_stops_before_tls():
    result = run(connector=connector(set()))
    keys = [step["key"] for step in result["steps"]]
    assert keys == ["url", "proxy", "dns", "tcp", "tcp_proxy"]
    tcp = steps_by_key(result)["tcp"]
    assert tcp["status"] == FAIL
    assert "10 saniye" in tcp["message"]
    assert any("vekil sunucu" in line.lower() for line in result["advice"])


def test_the_proxy_step_is_skipped_without_a_proxy():
    assert steps_by_key(run())["tcp_proxy"]["status"] == SKIP


# --- vekil sunucu ---------------------------------------------------------


def test_system_proxy_is_shown_with_its_source():
    result = run(system_proxies=lambda: {"https": "http://kurumsal.example.com:8080"},
                 resolver=resolver({HOST: [V4], "kurumsal.example.com": [PROXY_V4]}),
                 connector=connector({"10.0.0.7", "10.9.9.9"}))
    proxy = steps_by_key(result)["proxy"]
    assert proxy["status"] == OK
    assert proxy["detail"]["source"] == "sistem"
    assert "kurumsal.example.com" in proxy["message"]
    assert steps_by_key(result)["tcp_proxy"]["status"] == OK


def test_direct_mode_says_the_environment_is_ignored():
    proxy = steps_by_key(run(config(proxy_mode=PROXY_DIRECT)))["proxy"]
    assert proxy["status"] == OK
    assert proxy["detail"]["source"] == "dogrudan"
    assert "yok sayılıyor" in proxy["message"]


def test_the_field_of_the_saha_case_direct_works_proxy_tunnel_times_out():
    """Saha kusuru: https_proxy kurumsal vekile bakiyor, Jira ic agda.

    Dogrudan TCP aciliyor, CONNECT tuneli zaman asimina dusuyor: teshis
    "Dogrudan baglan" onerisini yazmali.
    """

    def tunnel(sock, host, port):
        raise TimeoutError("timed out")

    result = run(
        system_proxies=lambda: {"https": "http://kurumsal.example.com:8080"},
        resolver=resolver({HOST: [V4], "kurumsal.example.com": [PROXY_V4]}),
        connector=connector({"10.0.0.7", "10.9.9.9"}),
        tunneler=tunnel,
    )
    assert steps_by_key(result)["tcp"]["status"] == OK
    assert steps_by_key(result)["tcp_proxy"]["status"] == FAIL
    assert any("Doğrudan bağlan" in line for line in result["advice"])
    assert any("iç ağda" in line for line in result["advice"])


def test_a_refusing_proxy_tunnel_is_reported_with_its_answer():
    result = run(
        system_proxies=lambda: {"https": "http://kurumsal.example.com:8080"},
        resolver=resolver({HOST: [V4], "kurumsal.example.com": [PROXY_V4]}),
        connector=connector({"10.9.9.9"}),
        tunneler=lambda sock, host, port: "HTTP/1.1 403 Forbidden",
    )
    step = steps_by_key(result)["tcp_proxy"]
    assert step["status"] == FAIL
    assert "403" in step["message"]
    assert "Doğrudan bağlan" in step["message"]


def test_an_unreachable_proxy_is_named():
    result = run(
        system_proxies=lambda: {"https": "http://kurumsal.example.com:8080"},
        resolver=resolver({HOST: [V4], "kurumsal.example.com": [PROXY_V4]}),
        connector=connector({"10.0.0.7"}),
    )
    step = steps_by_key(result)["tcp_proxy"]
    assert step["status"] == FAIL
    assert "kurumsal.example.com:8080" in step["message"]


def test_pac_is_reported_but_never_resolved():
    result = run(pac_reader=lambda: "http://wpad.example.com/proxy.pac")
    proxy = steps_by_key(result)["proxy"]
    assert proxy["status"] == FAIL
    assert "PAC" in proxy["message"]
    assert "wpad.example.com/proxy.pac" in proxy["message"]
    assert any("PAC" in line for line in result["advice"])


def test_manual_mode_uses_only_the_typed_fields():
    result = run(
        config(proxy_mode=PROXY_MANUAL),
        system_proxies=lambda: {"https": "http://kurumsal.example.com:8080"},
    )
    proxy = steps_by_key(result)["proxy"]
    assert proxy["detail"]["proxy"] == ""
    assert proxy["status"] == SKIP


def test_no_proxy_entry_is_shown_as_skipped():
    result = run(
        config(no_proxy=".example.com", proxy_mode=PROXY_SYSTEM),
        system_proxies=lambda: {"https": "http://kurumsal.example.com:8080"},
    )
    proxy = steps_by_key(result)["proxy"]
    assert proxy["detail"]["source"] == "atlandi"
    assert "no_proxy" in proxy["message"]


# --- TLS ------------------------------------------------------------------


def test_tls_reports_the_certificate():
    tls = steps_by_key(run())["tls"]
    assert tls["status"] == OK
    assert "TLSv1.3" in tls["message"]
    assert tls["detail"]["issuer"] == "Ornek CA"


def test_a_verification_failure_points_at_the_corporate_ca():
    def boom(sock, host, cfg):
        error = ssl.SSLCertVerificationError("unable to get local issuer certificate")
        error.verify_message = "unable to get local issuer certificate"
        error.reason = "CERTIFICATE_VERIFY_FAILED"
        raise error

    result = run(tls_prober=boom)
    tls = steps_by_key(result)["tls"]
    assert tls["status"] == FAIL
    assert "CA" in tls["message"]
    assert any("CA" in line for line in result["advice"])
    # TLS dusunce HTTP ve kimlik denenmez.
    assert [step["key"] for step in result["steps"]][-1] == "tls"


def test_verification_off_is_stated_on_the_step():
    tls = steps_by_key(run(config(verify_ssl=False)))["tls"]
    assert "doğrulama kapalı" in tls["message"]


# --- HTTP ve kimlik -------------------------------------------------------


def test_a_401_from_serverinfo_still_counts_as_an_open_path():
    result = run(http_prober=lambda url, proxies, cfg: FakeResponse(401))
    http = steps_by_key(result)["http"]
    assert http["status"] == OK
    assert "401" in http["message"]


def test_a_404_blames_the_address_tail():
    result = run(http_prober=lambda url, proxies, cfg: FakeResponse(404))
    http = steps_by_key(result)["http"]
    assert http["status"] == FAIL
    assert "/jira" in http["message"]
    assert steps_by_key(result)["auth"]["status"] == SKIP


def test_a_dead_http_probe_is_classified():
    def boom(url, proxies, cfg):
        raise requests.exceptions.ConnectTimeout("timed out")

    http = steps_by_key(run(http_prober=boom))["http"]
    assert http["status"] == FAIL
    assert http["detail"]["code"] == "connect_timeout"
    # Teshis sondasi bes saniye bekler; istemcinin on saniyesi burada gecmez.
    assert "5 saniye" in http["message"]
    assert http["detail"]["via"] == "doğrudan"


def test_an_http_probe_through_a_proxy_says_so():
    def boom(url, proxies, cfg):
        raise requests.exceptions.ConnectTimeout("timed out")

    result = run(
        system_proxies=lambda: {"https": "http://kurumsal.example.com:8080"},
        resolver=resolver({HOST: [V4], "kurumsal.example.com": [PROXY_V4]}),
        connector=connector({"10.0.0.7", "10.9.9.9"}),
        http_prober=boom,
    )
    http = steps_by_key(result)["http"]
    assert http["detail"]["via"] == "vekil sunucu"
    assert "kurumsal.example.com:8080" in http["message"]
    assert "Doğrudan bağlan" in http["message"]


def test_bad_credentials_are_separated_from_network_trouble():
    class Refusing:
        def test_connection(self):
            raise JiraError("auth_failed", "Kimlik doğrulanamadı.")

    result = run(client_factory=lambda cfg: Refusing())
    auth = steps_by_key(result)["auth"]
    assert auth["status"] == FAIL
    assert auth["detail"]["code"] == "auth_failed"
    assert any("token" in line.lower() for line in result["advice"])
    # Ag adimlarinin hepsi yesil kaldi.
    assert steps_by_key(result)["http"]["status"] == OK


def test_the_summary_names_the_first_broken_step():
    result = run(resolver=resolver({}))
    assert "Ad çözümleme" in result["summary"]


# --- HTTP ucu -------------------------------------------------------------


def test_the_api_endpoint_returns_the_step_list(api_client, fake_jira, monkeypatch):
    api_client.put("/api/settings", json={"jira.base_url": BASE, "jira.secret": TOKEN})
    fake_jira.json("GET", "/rest/api/2/myself", {"displayName": "Ornek Kullanici"})
    fake_jira.json("GET", "/rest/api/2/serverInfo", {"serverTitle": "Ornek Jira"})

    monkeypatch.setattr(
        diagnose, "_default_connector", lambda family, sockaddr, timeout: FakeSocket()
    )
    monkeypatch.setattr(
        diagnose,
        "_default_tls_prober",
        lambda sock, host, cfg: {"version": "TLSv1.3", "subject": HOST, "issuer": "CA"},
    )
    monkeypatch.setattr(
        diagnose, "_default_http_prober", lambda url, proxies, cfg: FakeResponse(200)
    )
    monkeypatch.setattr(diagnose.net, "resolve", lambda host, port, ipv4_first=True: [V4])
    monkeypatch.setattr(diagnose.net, "pac_url", lambda reader=None: "")
    monkeypatch.setattr(diagnose.net, "system_proxies", lambda *a: {})

    payload = api_client.post("/api/settings/diagnose").json()

    assert payload["ok"] is True
    assert [step["key"] for step in payload["steps"]][:4] == ["url", "proxy", "dns", "tcp"]
    assert TOKEN not in str(payload)


def test_the_api_endpoint_reports_a_missing_address(api_client):
    payload = api_client.post("/api/settings/diagnose").json()
    assert payload["ok"] is False
    assert payload["steps"][0]["key"] == "url"
    assert payload["steps"][0]["status"] == FAIL
