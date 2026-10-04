"""Ambar: GitHub REST API istemcisi (yalnizca okuma + PR acma).

Yeni bagimlilik yok: `requests` uygulamada zaten var ve Jira istemcisiyle ayni
ag katmanini (`app/net.py`: IPv4 onceligi, vekil sunucu kipi, ozel CA)
kullanir. Taban adres varsayilan `https://api.github.com`; GitHub Enterprise
icin `https://ghe.example.com/api/v3` verilir.

Token hicbir zaman loglanmaz ve hata metinlerine girmez: log satirinda yalnizca
yontem, yol ve durum kodu durur.

Bu istemcinin yapabildigi tek yazma `POST /repos/{depo}/pulls`'tur (PR acmak).
Birlestirme, dal silme, ana dala yazma ucu burada yoktur.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlparse

import requests

from . import net

log = logging.getLogger("holocron.ambar")

DEFAULT_API_URL = "https://api.github.com"
PER_PAGE = 100
MAX_PAGES = 10


class GitHubError(Exception):
    def __init__(self, code: str, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


@dataclass
class PullRequest:
    number: int
    title: str
    author: str
    merged_at: str
    merge_sha: str
    url: str
    head_ref: str = ""
    base_ref: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "number": self.number,
            "title": self.title,
            "author": self.author,
            "merged_at": self.merged_at,
            "merge_sha": self.merge_sha,
            "url": self.url,
            "head_ref": self.head_ref,
        }


def parse_time(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def normalize_api_url(value: str) -> str:
    text = (value or "").strip().rstrip("/")
    return text or DEFAULT_API_URL


def _pull(item: dict[str, Any]) -> PullRequest:
    return PullRequest(
        number=int(item.get("number") or 0),
        title=str(item.get("title") or ""),
        author=str((item.get("user") or {}).get("login") or ""),
        merged_at=str(item.get("merged_at") or ""),
        merge_sha=str(item.get("merge_commit_sha") or ""),
        url=str(item.get("html_url") or ""),
        head_ref=str((item.get("head") or {}).get("ref") or ""),
        base_ref=str((item.get("base") or {}).get("ref") or ""),
    )


class GitHub:
    """Tek depo adina degil, tek token adina istemci."""

    def __init__(
        self,
        api_url: str,
        token: str,
        *,
        session: requests.Session | None = None,
        proxies: dict[str, str] | None = None,
        verify: bool | str = True,
        timeout: tuple[float, float] | float = net.DEFAULT_TIMEOUT,
    ) -> None:
        if not token:
            raise GitHubError(
                "token_missing",
                "GitHub token tanımlı değil: Ayarlar → Ambar → GitHub token.",
            )
        self.api_url = normalize_api_url(api_url)
        if not self.api_url.startswith(("http://", "https://")):
            raise GitHubError("invalid_api_url", "API adresi http:// veya https:// ile başlamalı.")
        self._token = token
        self.session = session or net.build_session()
        self.proxies = proxies
        self.verify = verify
        self.timeout = timeout

    def __repr__(self) -> str:  # token repr'a da girmesin
        return f"GitHub(api_url={self.api_url!r})"

    # --- alt yapi -----------------------------------------------------

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"token {self._token}",
            "User-Agent": "Holocron-Ambar",
        }

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        url = self.api_url + path
        try:
            response = self.session.request(
                method,
                url,
                headers=self._headers(),
                params=params,
                json=body,
                timeout=self.timeout,
                proxies=self.proxies,
                verify=self.verify,
            )
        except requests.exceptions.SSLError as exc:
            raise GitHubError(
                "ssl_error",
                "GitHub'a SSL doğrulaması başarısız: kurum kök sertifikası eksik olabilir "
                "(Ayarlar → Ağ → Özel CA dosyası).",
            ) from exc
        except requests.exceptions.ProxyError as exc:
            raise GitHubError(
                "proxy_error",
                "Vekil sunucu GitHub isteğini geçirmedi. Ayarlar → Ambar → Vekil sunucu "
                "alanını ya da Ayarlar → Ağ'ı kontrol edin.",
            ) from exc
        except requests.exceptions.RequestException as exc:
            code, _message = net.classify_connection_error(exc)
            raise GitHubError(
                code,
                "GitHub'a bağlanılamadı. Kurum ağında vekil sunucu gerekiyorsa Ayarlar → Ambar → "
                "Vekil sunucu alanına yazın.",
            ) from exc
        log.info("ambar: %s %s -> %s", method, path, response.status_code)
        if response.status_code >= 400:
            raise self._error(response)
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise GitHubError("bad_response", "GitHub'dan okunamayan bir cevap geldi.") from exc

    def _error(self, response: requests.Response) -> GitHubError:
        status = response.status_code
        api_message = ""
        try:
            payload = response.json()
            api_message = str(payload.get("message") or "")
            errors = payload.get("errors") or []
            extra = [
                str(item.get("message") or item.get("code") or "")
                for item in errors
                if isinstance(item, dict)
            ]
            if any(extra):
                api_message = (api_message + ": " + "; ".join(x for x in extra if x)).strip(": ")
        except ValueError:
            api_message = ""
        # Guvenlik: GitHub mesajinda token gecmez, yine de temizlenir.
        api_message = api_message.replace(self._token, "***")[:300]
        if status == 401:
            return GitHubError("unauthorized", "GitHub token geçersiz ya da süresi dolmuş.", status)
        if status == 403:
            if response.headers.get("X-RateLimit-Remaining") == "0":
                return GitHubError(
                    "rate_limited", "GitHub istek sınırı doldu; biraz sonra yeniden deneyin.", status
                )
            return GitHubError(
                "forbidden",
                "GitHub isteği reddetti: token'ın bu depoya yetkisi yok ya da kuruluş için SSO "
                f"onayı verilmemiş. {api_message}".strip(),
                status,
            )
        if status == 404:
            return GitHubError(
                "not_found",
                "Depo bulunamadı ya da token'ın bu depoya erişimi yok (repo yetkisi gerekir).",
                status,
            )
        if status == 422:
            return GitHubError("unprocessable", f"GitHub isteği kabul etmedi: {api_message}", status)
        return GitHubError("http_error", f"GitHub {status} döndürdü. {api_message}".strip(), status)

    @staticmethod
    def repo_path(full_name: str) -> str:
        owner, _, name = full_name.partition("/")
        return f"/repos/{quote(owner, safe='')}/{quote(name, safe='')}"

    # --- uclar --------------------------------------------------------

    def repository(self, full_name: str) -> dict[str, Any]:
        data = self.request("GET", self.repo_path(full_name)) or {}
        return {
            "full_name": str(data.get("full_name") or full_name),
            "default_branch": str(data.get("default_branch") or ""),
            "can_push": bool((data.get("permissions") or {}).get("push", True)),
        }

    def merged_pulls(self, full_name: str, base: str, since: datetime) -> list[PullRequest]:
        """Ana dala `since`ten sonra birlesen PR'lar (yeniden eskiye).

        Liste `updated` sirasiyla gelir; birlesme ani guncelleme anindan sonra
        olamayacagi icin sayfadaki en eski guncelleme `since`in gerisine
        dustugunde durulur.
        """
        found: list[PullRequest] = []
        for page in range(1, MAX_PAGES + 1):
            items = self.request(
                "GET",
                self.repo_path(full_name) + "/pulls",
                params={
                    "state": "closed",
                    "base": base,
                    "sort": "updated",
                    "direction": "desc",
                    "per_page": PER_PAGE,
                    "page": page,
                },
            ) or []
            if not isinstance(items, list) or not items:
                break
            oldest: datetime | None = None
            for item in items:
                updated = parse_time(str(item.get("updated_at") or ""))
                if updated is not None and (oldest is None or updated < oldest):
                    oldest = updated
                merged = parse_time(str(item.get("merged_at") or ""))
                if merged is None or merged < since:
                    continue
                found.append(_pull(item))
            if len(items) < PER_PAGE or (oldest is not None and oldest < since):
                break
        found.sort(key=lambda pr: pr.merged_at, reverse=True)
        return found

    def pull(self, full_name: str, number: int) -> PullRequest:
        data = self.request("GET", f"{self.repo_path(full_name)}/pulls/{int(number)}") or {}
        return _pull(data)

    def open_ambar_pulls(self, full_name: str, prefix: str) -> list[dict[str, Any]]:
        """Acik, dali `ambar/` ile baslayan PR'lar: onay bekleyen ambar isleri."""
        items = self.request(
            "GET",
            self.repo_path(full_name) + "/pulls",
            params={"state": "open", "per_page": PER_PAGE},
        ) or []
        result: list[dict[str, Any]] = []
        for item in items if isinstance(items, list) else []:
            pr = _pull(item)
            if pr.head_ref.startswith(prefix):
                result.append(
                    {"number": pr.number, "title": pr.title, "url": pr.url, "branch": pr.head_ref}
                )
        return result

    def create_pull(
        self, full_name: str, *, title: str, head: str, base: str, body: str
    ) -> dict[str, Any]:
        data = self.request(
            "POST",
            self.repo_path(full_name) + "/pulls",
            body={"title": title, "head": head, "base": base, "body": body},
        ) or {}
        return {"number": int(data.get("number") or 0), "url": str(data.get("html_url") or "")}


def host_of(api_url: str) -> str:
    return urlparse(normalize_api_url(api_url)).hostname or ""
