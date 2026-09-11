"""Sahte Jira sunucusu.

Yeni bagimlilik eklemeden requests.Session.request cagrisini yakalar; boylece
testler gercek aga hic cikmaz.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable

import requests

Handler = Callable[["RecordedCall"], "FakeResponse"]


@dataclass
class FakeResponse:
    status: int = 200
    body: Any = None
    headers: dict[str, str] = field(default_factory=dict)
    text_body: str | None = None


@dataclass
class RecordedCall:
    method: str
    url: str
    path: str
    json_body: dict[str, Any] | None
    params: dict[str, Any] | None
    headers: dict[str, str]
    kwargs: dict[str, Any]


class FakeJira:
    """Yol bazli yonlendirme yapan sahte sunucu."""

    def __init__(self, base_url: str = "https://jira.example.com") -> None:
        self.base_url = base_url.rstrip("/")
        self.routes: dict[tuple[str, str], Handler] = {}
        self.calls: list[RecordedCall] = []
        self.session = requests.Session()
        self.session.request = self._dispatch  # type: ignore[method-assign]

    def route(self, method: str, path: str) -> Callable[[Handler], Handler]:
        def decorator(handler: Handler) -> Handler:
            self.routes[(method.upper(), path)] = handler
            return handler

        return decorator

    def add(self, method: str, path: str, handler: Handler) -> None:
        self.routes[(method.upper(), path)] = handler

    def json(self, method: str, path: str, body: Any, status: int = 200) -> None:
        """Sabit cevap veren kisayol."""
        self.add(method, path, lambda call: FakeResponse(status=status, body=body))

    def sequence(self, method: str, path: str, responses: list[FakeResponse]) -> None:
        """Ardisik cagrilarda sirayla cevap verir (yeniden deneme testleri icin)."""
        queue = list(responses)

        def handler(call: RecordedCall) -> FakeResponse:
            return queue.pop(0) if len(queue) > 1 else queue[0]

        self.add(method, path, handler)

    def calls_to(self, method: str, path: str) -> list[RecordedCall]:
        return [c for c in self.calls if c.method == method.upper() and c.path == path]

    # --- ic isleyis ----------------------------------------------------

    def _dispatch(self, method: str, url: str, **kwargs: Any) -> requests.Response:
        path = url[len(self.base_url) :] if url.startswith(self.base_url) else url
        call = RecordedCall(
            method=method.upper(),
            url=url,
            path=path,
            json_body=kwargs.get("json"),
            params=kwargs.get("params"),
            headers=dict(kwargs.get("headers") or {}),
            kwargs=kwargs,
        )
        self.calls.append(call)

        handler = self.routes.get((call.method, path))
        if handler is None:
            return _build(FakeResponse(404, {"errorMessages": ["Yol tanimli degil: " + path]}), url)
        return _build(handler(call), url)


def _build(fake: FakeResponse, url: str) -> requests.Response:
    response = requests.Response()
    response.status_code = fake.status
    response.url = url
    response.headers.update(fake.headers)
    if fake.text_body is not None:
        response._content = fake.text_body.encode("utf-8")
    else:
        response._content = json.dumps(fake.body if fake.body is not None else {}).encode("utf-8")
        response.headers.setdefault("Content-Type", "application/json")
    return response


def issue(key: str, summary: str = "Ornek kayit", **extra: Any) -> dict[str, Any]:
    """Sahte kayit. Ek alanlar dogrudan fields altina konur."""
    fields: dict[str, Any] = {"summary": summary}
    fields.update(extra)
    return {"id": _stable_id(key), "key": key, "fields": fields}


def _stable_id(key: str) -> str:
    # hash() surecten surece degisir; testlerin kararli olmasi icin sabit uretim.
    total = 0
    for char in key:
        total = (total * 31 + ord(char)) % 100000
    return str(total)


def key_search(known: dict[str, dict[str, Any]]) -> Handler:
    """key in (...) sorgularini karsilar; bilinmeyen anahtar iceren paket 400 doner."""

    def handler(call: RecordedCall) -> FakeResponse:
        jql = (call.json_body or {}).get("jql", "")
        wanted = re.findall(r'"([^"]+)"', jql)
        missing = [key for key in wanted if key.upper() not in known]
        if missing:
            return FakeResponse(
                status=400,
                body={"errorMessages": ["Bilinmeyen kayit anahtari: " + ", ".join(missing)]},
            )
        found = [known[key.upper()] for key in wanted]
        return FakeResponse(
            body={"startAt": 0, "maxResults": len(found), "total": len(found), "issues": found}
        )

    return handler


def paged_v2(issues: list[dict[str, Any]], page_size: int = 100) -> Handler:
    """Jira Server/DC sayfalamasi: startAt / maxResults / total."""

    def handler(call: RecordedCall) -> FakeResponse:
        body = call.json_body or {}
        start = int(body.get("startAt", 0))
        size = min(int(body.get("maxResults", page_size)), page_size)
        window = issues[start : start + size]
        return FakeResponse(
            body={
                "startAt": start,
                "maxResults": size,
                "total": len(issues),
                "issues": window,
            }
        )

    return handler


def paged_cloud(issues: list[dict[str, Any]], page_size: int = 100) -> Handler:
    """Jira Cloud sayfalamasi: nextPageToken / isLast."""

    def handler(call: RecordedCall) -> FakeResponse:
        body = call.json_body or {}
        token = body.get("nextPageToken")
        start = int(token) if token else 0
        size = min(int(body.get("maxResults", page_size)), page_size)
        window = issues[start : start + size]
        end = start + len(window)
        is_last = end >= len(issues)
        payload: dict[str, Any] = {"issues": window, "isLast": is_last}
        if not is_last:
            payload["nextPageToken"] = str(end)
        return FakeResponse(body=payload)

    return handler
