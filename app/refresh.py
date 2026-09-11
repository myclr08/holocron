"""Guncelle isi: filtre gruplarini tazeler, kayitlari Jira'dan ceker.

Ayni anda tek is calisir. Is arka plan is parcaciginda doner, durum bellekte
tutulur (yeniden baslatinca sifirlanmasi dogru davranis: yarim kalmis is
surmez). Arayuz durumu yoklayarak ilerleme cubugu cizer.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable, Sequence

from . import repository
from .jira_client import KEY_CHUNK_SIZE, JiraError
from .settings_store import MODE_CLOUD

if TYPE_CHECKING:  # dairesel ice aktarimi onlemek icin yalnizca tip zamaninda
    from .context import AppContext

STATE_IDLE = "idle"
STATE_RUNNING = "running"
STATE_DONE = "done"
STATE_ERROR = "error"
STATE_CANCELLED = "cancelled"

# Server/DC "*navigable" ile tum gorunur alanlari verir; boylece kullanici
# sonradan hangi sutunu secerse secsin yeniden cekmeye gerek kalmaz.
# Cloud'un yeni /search/jql ucu "*navigable" kabul etmez, orada "*all" gerekir.
FIELDS_NAVIGABLE = ("*navigable",)
FIELDS_ALL = ("*all",)


def field_selector(mode: str) -> list[str]:
    return list(FIELDS_ALL if mode == MODE_CLOUD else FIELDS_NAVIGABLE)


@dataclass
class RefreshState:
    """Arayuze aynen gonderilen durum nesnesi."""

    state: str = STATE_IDLE
    stage: str = ""
    done: int = 0
    total: int = 0
    started_at: str | None = None
    finished_at: str | None = None
    group_id: int | None = None
    summary: dict[str, Any] = field(default_factory=dict)
    changed: dict[str, list[str]] = field(default_factory=dict)
    errors: list[dict[str, Any]] = field(default_factory=list)
    error: dict[str, str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "stage": self.stage,
            "done": self.done,
            "total": self.total,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "group_id": self.group_id,
            "summary": dict(self.summary),
            "changed": {key: list(value) for key, value in self.changed.items()},
            "errors": [dict(item) for item in self.errors],
            "error": dict(self.error) if self.error else None,
            "running": self.state == STATE_RUNNING,
        }


def empty_summary() -> dict[str, Any]:
    return {
        "fetched": 0,
        "new": 0,
        "updated": 0,
        "unchanged": 0,
        "not_found": [],
        "filter_groups": {},
    }


class RefreshManager:
    """Tek isi yoneten kilit + durum sahibi."""

    def __init__(self, now: Callable[[], str] = repository.now_iso) -> None:
        self._lock = threading.RLock()
        self._state = RefreshState()
        self._cancel = threading.Event()
        self._thread: threading.Thread | None = None
        self._now = now

    # --- disa donuk ---------------------------------------------------

    def status(self) -> dict[str, Any]:
        with self._lock:
            return self._state.to_dict()

    @property
    def running(self) -> bool:
        with self._lock:
            return self._state.state == STATE_RUNNING

    def start(self, context: "AppContext", group_id: int | None = None) -> dict[str, Any]:
        """Isi baslatir. Zaten calisiyorsa hata verir (tek is kurali)."""
        if group_id is not None:
            repository.require_group(context.conn, group_id)

        with self._lock:
            if self._state.state == STATE_RUNNING:
                raise repository.RepositoryError(
                    "refresh_running", "Guncelleme zaten suruyor.", status=409
                )
            self._cancel = threading.Event()
            self._state = RefreshState(
                state=STATE_RUNNING,
                stage="Hazirlaniyor",
                started_at=self._now(),
                group_id=group_id,
                summary=empty_summary(),
            )
            snapshot = self._state.to_dict()

        thread = threading.Thread(
            target=self._run,
            args=(context, group_id),
            name="holocron-refresh",
            daemon=True,
        )
        self._thread = thread
        thread.start()
        return snapshot

    def cancel(self) -> bool:
        with self._lock:
            if self._state.state != STATE_RUNNING:
                return False
            self._cancel.set()
            self._state.stage = "Iptal ediliyor"
            return True

    def join(self, timeout: float | None = None) -> None:
        """Testler icin: isin bitmesini bekler."""
        thread = self._thread
        if thread is not None:
            thread.join(timeout)

    def run_blocking(self, context: "AppContext", group_id: int | None = None) -> dict[str, Any]:
        """Is parcacigi acmadan calistirir; testler icin deterministik yol."""
        if group_id is not None:
            repository.require_group(context.conn, group_id)
        with self._lock:
            if self._state.state == STATE_RUNNING:
                raise repository.RepositoryError(
                    "refresh_running", "Guncelleme zaten suruyor.", status=409
                )
            self._cancel = threading.Event()
            self._state = RefreshState(
                state=STATE_RUNNING,
                stage="Hazirlaniyor",
                started_at=self._now(),
                group_id=group_id,
                summary=empty_summary(),
            )
        self._run(context, group_id)
        return self.status()

    # --- is govdesi ---------------------------------------------------

    def _run(self, context: "AppContext", group_id: int | None) -> None:
        conn = context.conn
        summary = empty_summary()
        report = repository.UpsertReport()
        try:
            config = context.settings.jira_config()
            client = context.client_factory(config)
            selector = field_selector(config.mode)

            self._touch(stage="Gruplar okunuyor")
            groups = self._groups_in_scope(conn, group_id)
            filter_groups = [g for g in groups if g["kind"] == repository.KIND_FILTER and g["jql"]]
            self._touch(total=len(filter_groups) + 1)

            if repository.field_count(conn) == 0:
                self._touch(stage="Alan katalogu cekiliyor")
                with context.db_lock:
                    repository.store_fields(conn, client.fetch_fields())

            fetched: set[str] = set()
            for index, group in enumerate(filter_groups, start=1):
                if self._cancel.is_set():
                    return self._finish(STATE_CANCELLED, summary, report)
                self._touch(stage=f"Filtre suzuluyor: {group['name']}", done=index - 1)
                try:
                    issues = client.search(group["jql"], fields=selector)
                except JiraError as exc:
                    # Bir grubun JQL'i hatali olabilir; is devam eder.
                    self._add_error(group, exc)
                    continue

                keys = [str(item.get("key") or "").upper() for item in issues if item.get("key")]
                with context.db_lock:
                    report.merge(repository.upsert_issues(conn, issues))
                    membership = repository.replace_filter_members(conn, group["id"], keys)
                fetched.update(keys)
                summary["filter_groups"][str(group["id"])] = {
                    "name": group["name"],
                    "added": membership["added"],
                    "removed": membership["removed"],
                }

            self._touch(done=len(filter_groups))
            if self._cancel.is_set():
                return self._finish(STATE_CANCELLED, summary, report)

            # JQL'den gelmeyen anahtarlar: manuel grup uyeleri ve iglenmis kayitlar.
            wanted = [
                key
                for key in self._member_keys(conn, groups)
                if key not in fetched
            ]
            chunks = [
                wanted[start : start + KEY_CHUNK_SIZE]
                for start in range(0, len(wanted), KEY_CHUNK_SIZE)
            ]
            self._touch(total=len(filter_groups) + max(len(chunks), 1))

            for index, chunk in enumerate(chunks, start=1):
                if self._cancel.is_set():
                    return self._finish(STATE_CANCELLED, summary, report)
                self._touch(
                    stage=f"Kayitlar cekiliyor ({index}/{len(chunks)})",
                    done=len(filter_groups) + index - 1,
                )
                batch = client.fetch_issues_by_keys(chunk, fields=selector)
                with context.db_lock:
                    report.merge(repository.upsert_issues(conn, batch.issues))
                summary["not_found"].extend(key.upper() for key in batch.invalid_keys)

            self._touch(
                stage="Tamamlandi",
                done=len(filter_groups) + max(len(chunks), 1),
                total=len(filter_groups) + max(len(chunks), 1),
            )
            self._finish(STATE_DONE, summary, report)
        except JiraError as exc:
            self._finish(STATE_ERROR, summary, report, error={"code": exc.code, "message": exc.message})
        except Exception as exc:  # beklenmeyen hata da temiz gorunmeli
            self._finish(
                STATE_ERROR,
                summary,
                report,
                error={"code": "refresh_failed", "message": str(exc) or exc.__class__.__name__},
            )

    # --- ic yardimcilar -----------------------------------------------

    def _groups_in_scope(self, conn: Any, group_id: int | None) -> list[dict[str, Any]]:
        if group_id is None:
            return repository.list_groups(conn)
        return [repository.require_group(conn, group_id)]

    def _member_keys(self, conn: Any, groups: Sequence[dict[str, Any]]) -> list[str]:
        seen: set[str] = set()
        ordered: list[str] = []
        for group in groups:
            for key in repository.list_item_keys(conn, group["id"]):
                upper = key.upper()
                if upper not in seen:
                    seen.add(upper)
                    ordered.append(upper)
        return ordered

    def _touch(self, **changes: Any) -> None:
        with self._lock:
            for name, value in changes.items():
                setattr(self._state, name, value)

    def _add_error(self, group: dict[str, Any], exc: JiraError) -> None:
        with self._lock:
            self._state.errors.append(
                {
                    "group_id": group["id"],
                    "name": group["name"],
                    "code": exc.code,
                    "message": exc.message,
                }
            )

    def _finish(
        self,
        state: str,
        summary: dict[str, Any],
        report: repository.UpsertReport,
        error: dict[str, str] | None = None,
    ) -> None:
        summary["fetched"] = report.fetched
        summary["new"] = len(report.new)
        summary["updated"] = len(report.updated)
        summary["unchanged"] = len(report.unchanged)
        summary["not_found"] = sorted(set(summary["not_found"]))
        with self._lock:
            self._state.state = state
            self._state.summary = summary
            self._state.changed = report.changed
            self._state.error = error
            self._state.finished_at = self._now()
            if state == STATE_DONE:
                self._state.stage = "Tamamlandi"
            elif state == STATE_CANCELLED:
                self._state.stage = "Iptal edildi"
            elif state == STATE_ERROR:
                self._state.stage = "Hata"
