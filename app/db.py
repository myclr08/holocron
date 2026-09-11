"""SQLite baglantisi ve surumlu sema goclari.

Goc listesi sirayla uygulanir; her goc idempotent olacak sekilde yazilir ki
yarim kalmis bir yukseltme tekrar calistirildiginda patlamasin.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable
from pathlib import Path

from . import paths

Migration = Callable[[sqlite3.Connection], None]


def _migration_0001_initial(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key   TEXT PRIMARY KEY,
            value TEXT
        );

        CREATE TABLE IF NOT EXISTS jira_fields (
            id          TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            schema_type TEXT,
            custom      INTEGER NOT NULL DEFAULT 0,
            raw_json    TEXT,
            fetched_at  TEXT
        );

        CREATE TABLE IF NOT EXISTS issues (
            key        TEXT PRIMARY KEY,
            jira_id    TEXT,
            raw_json   TEXT,
            fetched_at TEXT
        );

        CREATE TABLE IF NOT EXISTS local_fields (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT NOT NULL,
            type         TEXT NOT NULL,
            options_json TEXT,
            position     INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS local_values (
            issue_key TEXT NOT NULL,
            field_id  INTEGER NOT NULL,
            value     TEXT,
            PRIMARY KEY (issue_key, field_id),
            FOREIGN KEY (field_id) REFERENCES local_fields(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS groups (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT NOT NULL,
            kind         TEXT NOT NULL CHECK (kind IN ('manual', 'filter')),
            jql          TEXT,
            color        TEXT,
            columns_json TEXT,
            sort_json    TEXT,
            position     INTEGER NOT NULL DEFAULT 0,
            created_at   TEXT
        );

        CREATE TABLE IF NOT EXISTS group_items (
            group_id  INTEGER NOT NULL,
            issue_key TEXT NOT NULL,
            pinned    INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (group_id, issue_key),
            FOREIGN KEY (group_id) REFERENCES groups(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_group_items_issue ON group_items(issue_key);
        CREATE INDEX IF NOT EXISTS idx_local_values_field ON local_values(field_id);
        """
    )


def _has_column(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row["name"] == column for row in rows)


def _add_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    """ALTER TABLE ADD COLUMN idempotent degil; once sutunun varligina bakilir."""
    if not _has_column(conn, table, column):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _migration_0002_local_history(conn: sqlite3.Connection) -> None:
    """Asama 3: yerel alanlarda gecmis takibi ve deger zaman damgasi."""
    _add_column(conn, "local_fields", "track_history", "INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "local_fields", "created_at", "TEXT")
    _add_column(conn, "local_values", "updated_at", "TEXT")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS local_value_history (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            issue_key  TEXT NOT NULL,
            field_id   INTEGER NOT NULL,
            old_value  TEXT,
            new_value  TEXT,
            changed_at TEXT NOT NULL,
            FOREIGN KEY (field_id) REFERENCES local_fields(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_local_history_cell
            ON local_value_history(issue_key, field_id, id);
        CREATE INDEX IF NOT EXISTS idx_local_history_field
            ON local_value_history(field_id);
        """
    )


# Sira onemli: yeni goc her zaman listenin sonuna eklenir, mevcut satir degismez.
MIGRATIONS: list[tuple[int, str, Migration]] = [
    (1, "initial schema", _migration_0001_initial),
    (2, "local field history", _migration_0002_local_history),
]

SCHEMA_VERSION = MIGRATIONS[-1][0]


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    """Yapilandirilmis baglanti dondurur (goc uygulanmaz)."""
    target = str(path or paths.db_path())
    conn = sqlite3.connect(target, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def current_version(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone()
    if row is None:
        return 0
    value = conn.execute("SELECT MAX(version) AS v FROM schema_version").fetchone()
    return int(value["v"]) if value and value["v"] is not None else 0


def migrate(conn: sqlite3.Connection, migrations: Iterable[tuple[int, str, Migration]] | None = None) -> int:
    """Eksik goclari uygular, ulasilan surumu dondurur."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version    INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    conn.commit()

    version = current_version(conn)
    for number, name, func in (migrations if migrations is not None else MIGRATIONS):
        if number <= version:
            continue
        func(conn)
        conn.execute(
            "INSERT OR REPLACE INTO schema_version (version, name) VALUES (?, ?)",
            (number, name),
        )
        conn.commit()
        version = number
    return version


def open_database(path: Path | str | None = None) -> sqlite3.Connection:
    """Baglantiyi acar ve semayi guncel surume getirir."""
    conn = connect(path)
    migrate(conn)
    return conn


def table_names(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    return {row["name"] for row in rows}
