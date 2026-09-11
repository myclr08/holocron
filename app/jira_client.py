"""Jira REST istemcisi: Cloud ve Server/Data Center icin ortak arayuz.

Bu modul yalnizca disa donuk cagrilarla ilgilenir; is mantigi (gruplar,
secilen sutunlar, disa aktarim) burada durmaz.
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence
from urllib.parse import urlparse

import requests

from . import net
from .settings_store import AUTH_BASIC, MODE_CLOUD, MODE_SERVER, PROXY_DIRECT, JiraConfig

# (baglanti, okuma): baglanti 10 sn'de kurulmali, cevap icin 30 sn beklenir.
DEFAULT_TIMEOUT: tuple[float, float] = net.DEFAULT_TIMEOUT
PAGE_SIZE = 100
KEY_CHUNK_SIZE = 100
MAX_ATTEMPTS = 3
BACKOFF_BASE = 1.0

# Yeniden denenecek durumlar: hiz siniri ve gecici sunucu hatalari.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})

# Baglanti kurulamadiginda yeniden denemiyoruz. Kurum aginda ucuncu deneme
# kullaniciyi doksan saniye bekletip ayni hatayi veriyordu; ilk hata hemen
# donsun, sebebi de yazsin.
CONNECT_ERROR_CODES = frozenset(
    {"connect_timeout", "connection_refused", "dns_error", "ssl_error", "proxy_error"}
)


class JiraError(Exception):
    """Tek tip Jira hatasi."""

    def __init__(self, code: str, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message}


@dataclass
class IssueBatch:
    """Anahtar listesiyle cekilen kayitlar ve ayiklanan gecersiz anahtarlar."""

    issues: list[dict[str, Any]] = field(default_factory=list)
    invalid_keys: list[str] = field(default_factory=list)


def _basic_header(user: str, secret: str) -> str:
    raw = f"{user}:{secret}".encode("utf-8")
    return "Basic " + base64.b64encode(raw).decode("ascii")


def quote_jql_value(value: str) -> str:
    """JQL dizgesi icin guvenli tirnaklama."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


class BaseJiraClient:
    """Ortak HTTP davranisi: kimlik, vekil sunucu, yeniden deneme, hata cevirisi."""

    api_root = "/rest/api/2"

    def __init__(
        self,
        config: JiraConfig,
        session: requests.Session | None = None,
        sleep: Callable[[float], None] = time.sleep,
        timeout: tuple[float, float] | float = DEFAULT_TIMEOUT,
    ) -> None:
        if not config.base_url:
            raise JiraError("config_missing", "Jira adresi boş.")
        self.config = config
        direct = config.proxy_mode == PROXY_DIRECT
        self.session = session or net.build_session(
            ipv4_first=config.ipv4_first,
            trust_env=not direct,
        )
        if direct:
            # Disaridan verilen oturumda da ortam degiskenleri devre disi:
            # kurum makinesindeki https_proxy ic agdaki Jira'yi gormuyor.
            self.session.trust_env = False
        self._sleep = sleep
        self.timeout = timeout
        self.host = urlparse(self.base_url).hostname or ''

    # --- alt yapi -----------------------------------------------------

    @property
    def base_url(self) -> str:
        return self.config.base_url.rstrip("/")

    def _auth_header(self) -> str:
        raise NotImplementedError

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": self._auth_header(),
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Atlassian-Token": "no-check",
        }

    def _proxies(self) -> dict[str, str] | None:
        """Kipe gore vekil sunucu haritasi; bos donerse requests kendi bildigini yapar."""
        proxies, _source = net.proxies_for_config(self.config, self.host)
        return proxies or None

    def _verify(self) -> bool | str:
        if not self.config.verify_ssl:
            return False
        if self.config.ca_file:
            return self.config.ca_file
        return True

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        allow_status: Sequence[int] = (),
    ) -> requests.Response:
        url = f"{self.base_url}{path}"
        last_error: JiraError | None = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = self.session.request(
                    method,
                    url,
                    headers=self._headers(),
                    params=params,
                    json=json_body,
                    timeout=self.timeout,
                    proxies=self._proxies(),
                    verify=self._verify(),
                )
            except requests.exceptions.SSLError as exc:
                raise self._connect_error("ssl_error", exc) from exc
            except requests.exceptions.ProxyError as exc:
                raise self._connect_error("proxy_error", exc) from exc
            except requests.exceptions.ConnectTimeout as exc:
                raise self._connect_error("connect_timeout", exc) from exc
            except requests.exceptions.ConnectionError as exc:
                # DNS, reddedilen kapi, kopan el sikisma: hepsi tek denemede biter.
                code, message = net.classify_connection_error(exc)
                raise JiraError(code, message) from exc
            except requests.exceptions.Timeout as exc:
                # Okuma zaman asimi gecici olabilir; burada yeniden deneme surer.
                last_error = JiraError(
                    "timeout",
                    f"{net.SERVER_LABEL} {int(net.READ_TIMEOUT)} saniyede cevap vermedi. "
                    "Sunucu yoğun olabilir, birazdan yeniden deneyin.",
                )
                if attempt == MAX_ATTEMPTS:
                    raise last_error from exc
                self._backoff(attempt, None)
                continue
            except requests.exceptions.RequestException as exc:
                raise JiraError("network_error", net.NETWORK_MESSAGE) from exc

            status = response.status_code
            if status in allow_status or status < 400:
                return response

            if status in RETRY_STATUSES and attempt < MAX_ATTEMPTS:
                self._backoff(attempt, response)
                continue

            raise self._error_from_response(response)

        # Buraya yalnizca tum denemeler zaman asimiyla bittiyse gelinir.
        raise last_error or JiraError("unknown", "Beklenmeyen istek hatası.")

    def _connect_error(self, code: str, exc: BaseException) -> JiraError:
        """Baglanti hatasini kullanicinin okuyacagi hale getirir; sunucu adi gecmez."""
        if code == "ssl_error":
            detail = net.redact_host(str(exc), self.host)
            return JiraError(
                "ssl_error",
                "SSL doğrulaması başarısız: kurumunuzun kök sertifikası eksik olabilir. "
                "Ayarlar → Ağ → Özel CA dosyası alanına kurum sertifikasını verin. "
                f"Ayrıntı: {detail}",
            )
        if code == "proxy_error":
            detail = net.redact_host(str(exc), self.host)
            return JiraError(
                "proxy_error",
                "Vekil sunucuya ulaşılamadı. Jira iç ağdaysa Ayarlar → Ağ → "
                "\"Doğrudan bağlan\" seçeneğini deneyin. "
                f"Ayrıntı: {detail}",
            )
        _code, message = net.classify_connection_error(exc)
        return JiraError(code, message)

    def _backoff(self, attempt: int, response: requests.Response | None) -> None:
        delay = BACKOFF_BASE * (2 ** (attempt - 1))
        if response is not None:
            header = response.headers.get("Retry-After")
            if header:
                try:
                    delay = max(delay, float(header))
                except ValueError:
                    pass
        self._sleep(delay)

    def _error_from_response(self, response: requests.Response) -> JiraError:
        status = response.status_code
        detail = _extract_message(response)
        if status in (401, 403):
            code = "auth_failed"
            message = detail or "Kimlik dogrulanamadi, kullanici veya token hatali."
        elif status == 404:
            code = "not_found"
            message = detail or "Adres bulunamadı, temel URL yanlış olabilir."
        elif status == 400:
            code = "bad_request"
            message = detail or "İstek reddedildi (JQL veya alan adı hatalı olabilir)."
        elif status == 429:
            code = "rate_limited"
            message = detail or "Jira hız sınırı uyguladı."
        else:
            code = f"http_{status}"
            message = detail or f"Jira {status} döndü."
        return JiraError(code, message, status=status)

    @staticmethod
    def _json(response: requests.Response) -> dict[str, Any]:
        try:
            data = response.json()
        except (ValueError, json.JSONDecodeError) as exc:
            raise JiraError("bad_response", "Jira geçerli JSON döndürmedi.") from exc
        if not isinstance(data, (dict, list)):
            raise JiraError("bad_response", "Beklenmeyen cevap biçimi.")
        return data  # type: ignore[return-value]

    # --- ortak arayuz --------------------------------------------------

    def test_connection(self) -> dict[str, Any]:
        me = self._json(self._request("GET", f"{self.api_root}/myself"))
        info: dict[str, Any] = {}
        try:
            info = self._json(self._request("GET", f"{self.api_root}/serverInfo"))
        except JiraError:
            # Sunucu bilgisi bazi kurulumlarda yetki ister; baglanti yine de gecerli.
            info = {}
        return {
            "display_name": me.get("displayName") or me.get("name") or "",
            "account": me.get("emailAddress") or me.get("name") or "",
            "server_title": info.get("serverTitle") or "",
            "version": info.get("version") or None,
            "base_url": self.base_url,
            "mode": self.config.mode,
        }

    def fetch_fields(self) -> list[dict[str, Any]]:
        data = self._json(self._request("GET", f"{self.api_root}/field"))
        if not isinstance(data, list):
            raise JiraError("bad_response", "Alan kataloğu liste değil.")
        return data

    def search(
        self,
        jql: str,
        fields: Sequence[str] | None = None,
        max_results: int | None = None,
    ) -> list[dict[str, Any]]:
        raise NotImplementedError

    def fetch_issues_by_keys(
        self,
        keys: Iterable[str],
        fields: Sequence[str] | None = None,
    ) -> IssueBatch:
        """Anahtarlari 100'luk paketlerle ceker.

        Gecersiz bir anahtar tum paketi 400 ile dusurur; paket ikiye bolunerek
        saglam anahtarlar kurtarilir, kalan tekil anahtar gecersiz sayilir.
        """
        unique = _dedupe([key.strip() for key in keys if key and key.strip()])
        batch = IssueBatch()
        for start in range(0, len(unique), KEY_CHUNK_SIZE):
            chunk = unique[start : start + KEY_CHUNK_SIZE]
            self._fetch_chunk(chunk, fields, batch)
        return batch

    def _fetch_chunk(
        self,
        chunk: list[str],
        fields: Sequence[str] | None,
        batch: IssueBatch,
    ) -> None:
        if not chunk:
            return
        jql = "key in (" + ", ".join(quote_jql_value(key) for key in chunk) + ")"
        try:
            batch.issues.extend(self.search(jql, fields=fields))
            return
        except JiraError as exc:
            if exc.status != 400:
                raise
        if len(chunk) == 1:
            batch.invalid_keys.append(chunk[0])
            return
        middle = len(chunk) // 2
        self._fetch_chunk(chunk[:middle], fields, batch)
        self._fetch_chunk(chunk[middle:], fields, batch)


class CloudJiraClient(BaseJiraClient):
    """Atlassian Cloud: Basic auth (e-posta + API token), token tabanli sayfalama."""

    api_root = "/rest/api/3"

    def _auth_header(self) -> str:
        if not self.config.email or not self.config.secret:
            raise JiraError("config_missing", "Cloud için e-posta ve API token gerekir.")
        return _basic_header(self.config.email, self.config.secret)

    def search(
        self,
        jql: str,
        fields: Sequence[str] | None = None,
        max_results: int | None = None,
    ) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        next_token: str | None = None

        while True:
            remaining = None if max_results is None else max_results - len(collected)
            if remaining is not None and remaining <= 0:
                break
            page_size = PAGE_SIZE if remaining is None else min(PAGE_SIZE, remaining)
            body: dict[str, Any] = {"jql": jql, "maxResults": page_size}
            if fields:
                body["fields"] = list(fields)
            if next_token:
                body["nextPageToken"] = next_token

            data = self._json(self._request("POST", f"{self.api_root}/search/jql", json_body=body))
            issues = data.get("issues") or []
            collected.extend(issues)

            next_token = data.get("nextPageToken")
            is_last = bool(data.get("isLast")) or not next_token
            if is_last or not issues:
                break

        if max_results is not None:
            return collected[:max_results]
        return collected


class ServerJiraClient(BaseJiraClient):
    """Jira Server / Data Center: Bearer PAT ya da Basic, startAt sayfalamasi."""

    api_root = "/rest/api/2"

    def _auth_header(self) -> str:
        if not self.config.secret:
            raise JiraError("config_missing", "Kişisel erişim anahtarı (PAT) ya da parola gerekir.")
        if self.config.auth_type == AUTH_BASIC:
            if not self.config.username:
                raise JiraError("config_missing", "Basic kimlik için kullanıcı adı gerekir.")
            return _basic_header(self.config.username, self.config.secret)
        return f"Bearer {self.config.secret}"

    def search(
        self,
        jql: str,
        fields: Sequence[str] | None = None,
        max_results: int | None = None,
    ) -> list[dict[str, Any]]:
        collected: list[dict[str, Any]] = []
        start_at = 0

        while True:
            remaining = None if max_results is None else max_results - len(collected)
            if remaining is not None and remaining <= 0:
                break
            page_size = PAGE_SIZE if remaining is None else min(PAGE_SIZE, remaining)
            body: dict[str, Any] = {"jql": jql, "startAt": start_at, "maxResults": page_size}
            if fields:
                body["fields"] = list(fields)

            data = self._json(self._request("POST", f"{self.api_root}/search", json_body=body))
            issues = data.get("issues") or []
            collected.extend(issues)
            if not issues:
                break

            start_at += len(issues)
            total = data.get("total")
            if isinstance(total, int) and start_at >= total:
                break

        if max_results is not None:
            return collected[:max_results]
        return collected


def create_client(
    config: JiraConfig,
    session: requests.Session | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> BaseJiraClient:
    """Moda gore dogru istemciyi kurar."""
    if config.mode == MODE_CLOUD:
        return CloudJiraClient(config, session=session, sleep=sleep)
    if config.mode == MODE_SERVER:
        return ServerJiraClient(config, session=session, sleep=sleep)
    raise JiraError("config_invalid", f"Bilinmeyen mod: {config.mode}")


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        upper = value.upper()
        if upper in seen:
            continue
        seen.add(upper)
        result.append(value)
    return result


def _extract_message(response: requests.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        text = (response.text or "").strip()
        return text[:300]
    if isinstance(payload, dict):
        messages = payload.get("errorMessages")
        if isinstance(messages, list) and messages:
            return "; ".join(str(item) for item in messages)[:300]
        errors = payload.get("errors")
        if isinstance(errors, dict) and errors:
            return "; ".join(f"{k}: {v}" for k, v in errors.items())[:300]
        message = payload.get("message")
        if message:
            return str(message)[:300]
    return ""
