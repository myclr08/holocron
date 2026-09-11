"""Uygulama boyunca paylasilan bagimliliklar tek yerde toplanir.

Boylece testler gercek dosya/ag kullanmadan kendi baglantisini ve sahte
oturumunu enjekte edebilir.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import requests

from . import db
from .jira_client import BaseJiraClient, create_client
from .lifecycle import Heartbeat
from .secrets import SecretBox
from .settings_store import JiraConfig, SettingsStore

ClientFactory = Callable[[JiraConfig], BaseJiraClient]


@dataclass
class AppContext:
    conn: sqlite3.Connection
    settings: SettingsStore
    heartbeat: Heartbeat
    client_factory: ClientFactory
    shutdown_hook: Callable[[], None] = field(default=lambda: None)

    def close(self) -> None:
        try:
            self.conn.close()
        except sqlite3.Error:
            pass


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
