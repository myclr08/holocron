from __future__ import annotations

from app import db

EXPECTED_TABLES = {
    "settings",
    "jira_fields",
    "issues",
    "local_fields",
    "local_values",
    "local_value_history",
    "groups",
    "group_items",
    "tasks",
    "schema_version",
}


def test_migration_creates_all_tables(conn):
    assert EXPECTED_TABLES <= db.table_names(conn)


def test_schema_version_recorded(conn):
    assert db.current_version(conn) == db.SCHEMA_VERSION


def test_migrate_is_idempotent(conn):
    before = conn.execute("SELECT COUNT(*) AS c FROM schema_version").fetchone()["c"]
    db.migrate(conn)
    db.migrate(conn)
    after = conn.execute("SELECT COUNT(*) AS c FROM schema_version").fetchone()["c"]
    assert before == after == db.SCHEMA_VERSION


def test_fresh_database_starts_at_zero(tmp_path):
    conn = db.connect(tmp_path / "bos.db")
    try:
        assert db.current_version(conn) == 0
        assert db.migrate(conn) == db.SCHEMA_VERSION
    finally:
        conn.close()


def test_new_migration_is_applied_on_top(tmp_path):
    conn = db.connect(tmp_path / "goc.db")
    try:
        db.migrate(conn)

        def add_table(connection):
            connection.execute("CREATE TABLE IF NOT EXISTS deneme (id INTEGER PRIMARY KEY)")

        extended = list(db.MIGRATIONS) + [(db.SCHEMA_VERSION + 1, "deneme", add_table)]
        assert db.migrate(conn, extended) == db.SCHEMA_VERSION + 1
        assert "deneme" in db.table_names(conn)
        # Ikinci calistirmada yeniden uygulanmaz.
        assert db.migrate(conn, extended) == db.SCHEMA_VERSION + 1
    finally:
        conn.close()


def test_group_kind_is_constrained(conn):
    conn.execute(
        "INSERT INTO groups (name, kind, position) VALUES (?, ?, ?)", ("Manuel", "manual", 0)
    )
    conn.commit()
    import sqlite3

    import pytest

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO groups (name, kind) VALUES (?, ?)", ("Hatali", "sihirli"))
        conn.commit()


def test_group_items_cascade_on_group_delete(conn):
    cur = conn.execute("INSERT INTO groups (name, kind) VALUES ('Takip', 'manual')")
    group_id = cur.lastrowid
    conn.execute(
        "INSERT INTO group_items (group_id, issue_key, pinned) VALUES (?, ?, ?)",
        (group_id, "DEMO-1", 1),
    )
    conn.commit()
    conn.execute("DELETE FROM groups WHERE id = ?", (group_id,))
    conn.commit()
    assert conn.execute("SELECT COUNT(*) AS c FROM group_items").fetchone()["c"] == 0


def test_issue_key_is_primary_key(conn):
    conn.execute("INSERT INTO issues (key, jira_id) VALUES ('DEMO-1', '1')")
    conn.execute(
        "INSERT INTO issues (key, jira_id) VALUES ('DEMO-1', '2') "
        "ON CONFLICT(key) DO UPDATE SET jira_id = excluded.jira_id"
    )
    conn.commit()
    rows = conn.execute("SELECT jira_id FROM issues").fetchall()
    assert len(rows) == 1 and rows[0]["jira_id"] == "2"


# --- asama 3 gocu -------------------------------------------------------


def _columns(conn, table):
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_stage_two_database_is_upgraded_in_place(tmp_path):
    """Asama 2'de kalmis bir veritabani acildiginda 0002 uzerine uygulanir."""
    conn = db.connect(tmp_path / "eski.db")
    try:
        only_first = db.MIGRATIONS[:1]
        assert db.migrate(conn, only_first) == 1
        assert "local_value_history" not in db.table_names(conn)
        assert "track_history" not in _columns(conn, "local_fields")

        # Icinde veri varken yukseltme yapilir; veri kaybolmamali.
        conn.execute(
            "INSERT INTO local_fields (name, type, position) VALUES ('Not', 'text', 0)"
        )
        conn.execute(
            "INSERT INTO local_values (issue_key, field_id, value) VALUES ('DEMO-1', 1, 'eski')"
        )
        conn.commit()

        assert db.migrate(conn) == db.SCHEMA_VERSION
        assert "local_value_history" in db.table_names(conn)
        assert {"track_history", "created_at"} <= _columns(conn, "local_fields")
        assert "updated_at" in _columns(conn, "local_values")
        row = conn.execute("SELECT value FROM local_values WHERE field_id = 1").fetchone()
        assert row["value"] == "eski"
        assert conn.execute("SELECT track_history FROM local_fields").fetchone()["track_history"] == 0
    finally:
        conn.close()


def test_stage_three_migration_is_idempotent(tmp_path):
    conn = db.connect(tmp_path / "tekrar.db")
    try:
        db.migrate(conn)
        db.migrate(conn, db.MIGRATIONS[1:2])  # zaten uygulanmis, atlanir
        columns = _columns(conn, "local_fields")
        assert len([name for name in columns if name == "track_history"]) == 1
    finally:
        conn.close()


def test_local_history_cascades_when_field_is_dropped(conn):
    conn.execute("INSERT INTO local_fields (name, type, position) VALUES ('Not', 'text', 0)")
    field_id = conn.execute("SELECT id FROM local_fields").fetchone()["id"]
    conn.execute(
        "INSERT INTO local_value_history (issue_key, field_id, old_value, new_value, changed_at) "
        "VALUES ('DEMO-1', ?, NULL, 'ilk', '2026-09-11T09:05:00+00:00')",
        (field_id,),
    )
    conn.commit()
    conn.execute("DELETE FROM local_fields WHERE id = ?", (field_id,))
    conn.commit()
    assert conn.execute("SELECT COUNT(*) AS c FROM local_value_history").fetchone()["c"] == 0
