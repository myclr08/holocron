from __future__ import annotations

from app import db

EXPECTED_TABLES = {
    "settings",
    "jira_fields",
    "issues",
    "local_fields",
    "local_values",
    "groups",
    "group_items",
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
