"""Uygulama boyunca paylasilan bagimliliklar tek yerde toplanir.

Boylece testler gercek dosya/ag kullanmadan kendi baglantisini ve sahte
oturumunu enjekte edebilir.
"""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import requests

from . import db
from .jira_client import BaseJiraClient, create_client
from .lifecycle import Heartbeat
from .mail import default_sender, default_source
from .refresh import RefreshManager
from .secrets import SecretBox
from .settings_store import JiraConfig, SettingsStore

ClientFactory = Callable[[JiraConfig], BaseJiraClient]
# Posta kaynagi fabrikasi: uretimde Outlook, testlerde bellek ici sahte kaynak.
MailFactory = Callable[..., Any]
# Posta gondericisi fabrikasi: uretimde Outlook, testlerde sahte gonderici.
SenderFactory = Callable[..., Any]


@dataclass
class AppContext:
    conn: sqlite3.Connection
    settings: SettingsStore
    heartbeat: Heartbeat
    client_factory: ClientFactory
    shutdown_hook: Callable[[], None] = field(default=lambda: None)
    refresh: RefreshManager = field(default_factory=RefreshManager)
    mail_factory: MailFactory = field(default=default_source)
    sender_factory: SenderFactory = field(default=default_sender)
    # Yazma islemleri bu kilitle sirayla girer (okumalar paralel kalabilir).
    db_lock: threading.RLock = field(default_factory=threading.RLock)

    def __post_init__(self) -> None:
        self._threads = threading.local()
        self._threads.conn = self.conn
        self._extra: list[sqlite3.Connection] = []
        self._extra_lock = threading.Lock()
        self._db_file = _database_file(self.conn)
        # Ayar okumalari da is parcacigina ait baglantidan gecsin.
        bind = getattr(self.settings, "bind", None)
        if callable(bind):
            bind(self.connection)

    def connection(self) -> sqlite3.Connection:
        """Bu is parcacigina ait sqlite baglantisi.

        Tek baglantiyi es zamanli isteklerde paylasmak sqlite'i bozuyordu
        ("bad parameter or other API misuse"): sayfa acilisinda paralel giden
        `/api/settings` ve grid istekleri ayni baglanti uzerinde ust uste
        biniyordu. Her is parcacigi artik kendi baglantisini aciyor; yazmalar
        yine `db_lock` ile sirayla giriyor.
        """
        existing = getattr(self._threads, "conn", None)
        if existing is not None:
            return existing
        if not self._db_file:
            # Bellek ici veritabani baska baglantidan gorulemez; tek baglanti kalir.
            return self.conn
        fresh = db.connect(self._db_file)
        self._threads.conn = fresh
        with self._extra_lock:
            self._extra.append(fresh)
        return fresh

    def close(self) -> None:
        with self._extra_lock:
            extra = list(self._extra)
            self._extra.clear()
        for connection in (*extra, self.conn):
            try:
                connection.close()
            except sqlite3.Error:
                pass


def _database_file(conn: sqlite3.Connection) -> str:
    """Baglantinin dosya yolu; bellek ici veritabaninda bos metin doner."""
    try:
        for row in conn.execute("PRAGMA database_list").fetchall():
            if row["name"] == "main":
                return str(row["file"] or "")
    except sqlite3.Error:
        return ""
    return ""


def default_client_factory(session: requests.Session | None = None) -> ClientFactory:
    def factory(config: JiraConfig) -> BaseJiraClient:
        return create_client(config, session=session)

    return factory


def build_context(
    db_file: Path | str | None = None,
    key_file: Path | None = None,
    heartbeat: Heartbeat | None = None,
    client_factory: ClientFactory | None = None,
) -> AppContext:
    conn = db.open_database(db_file)
    store = SettingsStore(conn, SecretBox(key_file=key_file))
    return AppContext(
        conn=conn,
        settings=store,
        heartbeat=heartbeat or Heartbeat(),
        client_factory=client_factory or default_client_factory(),
    )
