"""Kisisel kanban: goc, CRUD, sutun sirasi, son tarih durumu, Excel ve arayuz kancalari."""

from __future__ import annotations

import io
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest
from openpyxl import load_workbook

from app import db, repository as repo, tasks as task_utils
from app.settings_store import MODE_SERVER
from tests.fake_jira import issue

BASE = "https://jira.example.com"

CATALOG = [
    {"id": "summary", "name": "Özet", "schema": {"type": "string"}},
    {"id": "status", "name": "Durum", "schema": {"type": "status"}},
]


def seed_jira(api_client, conn, keys=("DEMO-1",)):
    """Kayitlari cekilmis gibi yazar; bagli kart ozet ve durum gosterebilsin."""
    repo.store_fields(conn, CATALOG)
    api_client.put("/api/settings", json={"jira.base_url": BASE, "jira.mode": MODE_SERVER})
    repo.upsert_issues(
        conn,
        [
            issue(
                key,
                f"Özet {key}",
                status={"name": "Açık", "statusCategory": {"key": "indeterminate"}},
            )
            for key in keys
        ],
    )


def make_task(api_client, title="Rapor yaz", **extra):
    payload = {"title": title}
    payload.update(extra)
    response = api_client.post("/api/tasks", json=payload)
    assert response.status_code == 200, response.text
    return response.json()["task"]


def board(api_client, **params):
    query = "&".join(f"{name}={value}" for name, value in params.items())
    response = api_client.get(f"/api/tasks{'?' + query if query else ''}")
    assert response.status_code == 200, response.text
    return response.json()


def column(data, status):
    return next(item for item in data["columns"] if item["status"] == status)


def titles(data, status):
    return [task["title"] for task in column(data, status)["tasks"]]


# --- goc ----------------------------------------------------------------


def test_migration_creates_the_tasks_table(conn):
    assert "tasks" in db.table_names(conn)
    assert repo.list_tasks(conn) == []
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    # Asama 7 posta sutunlarini ekledi; asagidaki liste kanbanin cekirdegi.
    assert columns >= {
        "id",
        "title",
        "description",
        "note",
        "due_date",
        "status",
        "issue_key",
        "position",
        "created_at",
        "updated_at",
        "done_at",
    }


def test_task_status_is_constrained(conn):
    conn.execute("INSERT INTO tasks (title, status) VALUES ('Bir', 'todo')")
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO tasks (title, status) VALUES ('Iki', 'belki')")
        conn.commit()


def test_title_is_required_at_the_database_level(conn):
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO tasks (title) VALUES (NULL)")
        conn.commit()


# --- CRUD ---------------------------------------------------------------


def test_task_lifecycle(api_client):
    empty = board(api_client)
    assert [item["status"] for item in empty["columns"]] == ["todo", "doing", "done"]
    assert [item["label"] for item in empty["columns"]] == ["Yapılacak", "Yapılıyor", "Yapıldı"]
    assert empty["summary"] == {"open": 0, "overdue": 0}

    task = make_task(
        api_client,
        "Rapor yaz",
        description="Aylık özet",
        note="Önce veriyi topla",
        due_date="11.09.2026",
    )
    assert task["status"] == "todo"
    assert task["due_date"] == "2026-09-11"  # GG.AA.YYYY ISO'ya cevrilir
    assert task["done_at"] is None
    assert task["issue"] is None

    data = board(api_client)
    assert titles(data, "todo") == ["Rapor yaz"]
    assert column(data, "todo")["count"] == 1

    updated = api_client.put(
        f"/api/tasks/{task['id']}", json={"title": "Rapor yaz ve gönder", "note": ""}
    ).json()["task"]
    assert updated["title"] == "Rapor yaz ve gönder"
    assert updated["note"] == ""

    assert api_client.delete(f"/api/tasks/{task['id']}").json() == {"ok": True}
    assert column(board(api_client), "todo")["count"] == 0


def test_empty_title_is_refused(api_client):
    response = api_client.post("/api/tasks", json={"title": "   "})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_title"


def test_unknown_status_is_refused(api_client):
    response = api_client.post("/api/tasks", json={"title": "Bir", "status": "belki"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_status"


def test_unknown_task_is_404(api_client):
    response = api_client.put("/api/tasks/999", json={"title": "Yok"})
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "task_not_found"


def test_bad_due_date_is_refused(api_client):
    response = api_client.post("/api/tasks", json={"title": "Bir", "due_date": "yarın"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_due_date"


# --- Jira bagi ----------------------------------------------------------


def test_invalid_issue_key_is_refused(api_client):
    response = api_client.post("/api/tasks", json={"title": "Bir", "issue_key": "salatalık"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_value"


def test_issue_key_accepts_links_and_can_be_cleared(api_client):
    task = make_task(api_client, issue_key=f"{BASE}/browse/DEMO-1")
    assert task["issue_key"] == "DEMO-1"
    # Kayit henuz cekilmedi: bag kurulur, fetched false doner.
    assert task["issue"]["fetched"] is False
    assert task["issue"]["summary"] == ""

    cleared = api_client.put(f"/api/tasks/{task['id']}", json={"issue_key": ""}).json()["task"]
    assert cleared["issue_key"] == "" and cleared["issue"] is None


def test_linked_issue_carries_summary_and_status(api_client, conn):
    seed_jira(api_client, conn)
    make_task(api_client, "Kaydı incele", issue_key="demo-1")

    card = column(board(api_client), "todo")["tasks"][0]
    assert card["issue"] == {
        "key": "DEMO-1",
        "summary": "Özet DEMO-1",
        "status_text": "Açık",
        "status_category": "indeterminate",
        "fetched": True,
        "url": f"{BASE}/browse/DEMO-1",
    }


def test_key_completion_lists_known_keys(api_client, conn):
    seed_jira(api_client, conn, keys=("DEMO-1", "DEMO-2", "BASKA-7"))
    group = api_client.post("/api/groups", json={"name": "Filom"}).json()["group"]
    # Cekilmemis ama bir grupta duran anahtar da listeye girer.
    api_client.post(f"/api/groups/{group['id']}/items", json={"text": "DEMO-9"})

    keys = api_client.get("/api/issues/keys?q=DE").json()["keys"]
    assert [item["key"] for item in keys] == ["DEMO-1", "DEMO-2", "DEMO-9"]
    assert keys[0]["summary"] == "Özet DEMO-1"
    assert keys[2] == {"key": "DEMO-9", "summary": "", "fetched": False}

    assert len(api_client.get("/api/issues/keys").json()["keys"]) == 4
    assert api_client.get("/api/issues/keys?q=YOK").json()["keys"] == []


def test_key_completion_is_capped(api_client, conn):
    repo.upsert_issues(conn, [issue(f"DEMO-{number}") for number in range(1, 40)])
    assert len(api_client.get("/api/issues/keys?q=DEMO").json()["keys"]) == 20


# --- sutun degisimi ve sira ---------------------------------------------


def test_move_between_columns_stamps_done_at(api_client):
    task = make_task(api_client, "Bir")

    doing = api_client.post(
        f"/api/tasks/{task['id']}/move", json={"status": "doing"}
    ).json()["task"]
    assert doing["status"] == "doing" and doing["done_at"] is None

    done = api_client.post(f"/api/tasks/{task['id']}/move", json={"status": "done"}).json()["task"]
    assert done["status"] == "done" and done["done_at"]

    back = api_client.post(f"/api/tasks/{task['id']}/move", json={"status": "todo"}).json()["task"]
    assert back["status"] == "todo" and back["done_at"] is None


def test_status_change_through_put_also_stamps_done_at(api_client):
    task = make_task(api_client, "Bir")
    done = api_client.put(f"/api/tasks/{task['id']}", json={"status": "done"}).json()["task"]
    assert done["done_at"]
    back = api_client.put(f"/api/tasks/{task['id']}", json={"status": "doing"}).json()["task"]
    assert back["done_at"] is None


def test_move_places_the_card_at_the_wanted_position(api_client):
    first = make_task(api_client, "Bir")
    second = make_task(api_client, "İki")
    third = make_task(api_client, "Üç")
    assert titles(board(api_client), "todo") == ["Bir", "İki", "Üç"]

    api_client.post(f"/api/tasks/{third['id']}/move", json={"status": "todo", "position": 0})
    assert titles(board(api_client), "todo") == ["Üç", "Bir", "İki"]

    # Sutun degisiminde de sira gecerli: bastan ikinci sıraya.
    api_client.post(f"/api/tasks/{first['id']}/move", json={"status": "doing"})
    api_client.post(f"/api/tasks/{second['id']}/move", json={"status": "doing", "position": 0})
    assert titles(board(api_client), "doing") == ["İki", "Bir"]
    assert titles(board(api_client), "todo") == ["Üç"]


def test_move_clamps_a_position_outside_the_column(api_client):
    first = make_task(api_client, "Bir")
    make_task(api_client, "İki")
    api_client.post(f"/api/tasks/{first['id']}/move", json={"status": "todo", "position": 99})
    assert titles(board(api_client), "todo") == ["İki", "Bir"]


def test_reorder_rewrites_one_column(api_client):
    first = make_task(api_client, "Bir")
    second = make_task(api_client, "İki")
    third = make_task(api_client, "Üç")

    response = api_client.post(
        "/api/tasks/reorder", json={"status": "todo", "ids": [third["id"], first["id"]]}
    )
    assert response.status_code == 200
    # Listede gecmeyen gorev mevcut sirasiyla sona eklenir.
    assert titles(response.json(), "todo") == ["Üç", "Bir", "İki"]
    assert [second["id"]] == [
        task["id"] for task in column(board(api_client), "todo")["tasks"] if task["title"] == "İki"
    ]


def test_reorder_rejects_non_list(api_client):
    response = api_client.post("/api/tasks/reorder", json={"ids": "hepsi"})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_order"


def test_deleting_a_card_renumbers_the_column(conn):
    first = repo.create_task(conn, "Bir")
    second = repo.create_task(conn, "İki")
    third = repo.create_task(conn, "Üç")
    repo.delete_task(conn, second["id"])
    assert [(task["id"], task["position"]) for task in repo.list_tasks(conn, "todo")] == [
        (first["id"], 0),
        (third["id"], 1),
    ]


# --- son tarih durumu ---------------------------------------------------


def test_due_state_is_computed_against_an_injectable_today():
    today = date(2026, 9, 11)
    assert task_utils.due_state("2026-09-10", today) == "overdue"
    assert task_utils.due_state("2026-09-11", today) == "today"
    assert task_utils.due_state("2026-09-14", today) == "soon"  # 3 gun sinirda
    assert task_utils.due_state("2026-09-15", today) == "later"
    assert task_utils.due_state("", today) == "none"
    assert task_utils.due_state(None, today) == "none"


def test_board_marks_the_overdue_cards(api_client, conn):
    yesterday = (date.today() - timedelta(days=1)).isoformat()
    make_task(api_client, "Geciken", due_date=yesterday)
    make_task(api_client, "Tarihsiz")

    data = board(api_client)
    states = {task["title"]: task["due_state"] for task in column(data, "todo")["tasks"]}
    assert states == {"Geciken": "overdue", "Tarihsiz": "none"}
    assert data["summary"] == {"open": 2, "overdue": 1}
    assert api_client.get("/api/tasks/summary").json() == {"open": 2, "overdue": 1}


def test_summary_ignores_finished_cards(api_client):
    task = make_task(api_client, "Geciken", due_date=(date.today() - timedelta(days=5)).isoformat())
    api_client.post(f"/api/tasks/{task['id']}/move", json={"status": "done"})
    assert api_client.get("/api/tasks/summary").json() == {"open": 0, "overdue": 0}


# --- eski biten kartlar -------------------------------------------------


def finish_long_ago(conn, task_id, days=45):
    moment = datetime.now(timezone.utc) - timedelta(days=days)
    conn.execute(
        "UPDATE tasks SET status = 'done', done_at = ? WHERE id = ?",
        (moment.replace(microsecond=0).isoformat(), task_id),
    )
    conn.commit()


def test_old_done_cards_are_hidden_but_counted(api_client, conn):
    fresh = make_task(api_client, "Yeni biten")
    api_client.post(f"/api/tasks/{fresh['id']}/move", json={"status": "done"})
    old = make_task(api_client, "Eski biten")
    finish_long_ago(conn, old["id"])

    data = board(api_client)
    assert titles(data, "done") == ["Yeni biten"]
    assert data["old_done_count"] == 1

    shown = board(api_client, include_old_done=1)
    assert sorted(titles(shown, "done")) == ["Eski biten", "Yeni biten"]
    assert shown["old_done_count"] == 1


def test_old_done_threshold_is_thirty_days(conn):
    task = repo.create_task(conn, "Bir", status="done")
    today = date(2026, 9, 11)
    task["done_at"] = "2026-08-12T09:00:00+00:00"  # 30 gun once: hala gorunur
    assert task_utils.is_old_done(task, today) is False
    task["done_at"] = "2026-08-11T09:00:00+00:00"  # 31 gun once: gizlenir
    assert task_utils.is_old_done(task, today) is True


# --- arama --------------------------------------------------------------


def test_search_covers_title_note_and_linked_summary(api_client, conn):
    seed_jira(api_client, conn)
    make_task(api_client, "Rapor yaz", note="müşteriye gönder")
    make_task(api_client, "Toplantı", description="Salı sabahı")
    make_task(api_client, "Kaydı incele", issue_key="DEMO-1")

    assert titles(board(api_client, q="rapor"), "todo") == ["Rapor yaz"]
    assert titles(board(api_client, q="MÜŞTERİYE"), "todo") == ["Rapor yaz"]
    assert titles(board(api_client, q="salı"), "todo") == ["Toplantı"]
    assert titles(board(api_client, q="demo-1"), "todo") == ["Kaydı incele"]
    assert titles(board(api_client, q="Özet DEMO"), "todo") == ["Kaydı incele"]
    assert titles(board(api_client, q="bulunmayan"), "todo") == []


# --- Excel --------------------------------------------------------------


def read_sheet(response):
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )
    book = load_workbook(io.BytesIO(response.content))
    return book.active


def test_task_export_writes_one_sheet_with_real_cells(api_client, conn):
    seed_jira(api_client, conn)
    make_task(
        api_client,
        "Rapor yaz",
        description="Aylık özet",
        note="Önce veriyi topla",
        due_date="2026-09-11",
        issue_key="DEMO-1",
    )
    done = make_task(api_client, "Biten iş")
    api_client.post(f"/api/tasks/{done['id']}/move", json={"status": "done"})

    sheet = read_sheet(api_client.get("/api/tasks/export.xlsx"))
    assert sheet.title == "Görevlerim"
    assert [cell.value for cell in sheet[1]] == [
        "Durum",
        "Ad",
        "Açıklama",
        "Not",
        "Son tarih",
        "Jira kaydı",
        "Jira özeti",
        "Jira durumu",
        "Oluşturma",
        "Tamamlanma",
    ]

    first = {cell.column_letter: cell for cell in sheet[2]}
    assert first["A"].value == "Yapılacak"
    assert first["B"].value == "Rapor yaz"
    assert first["C"].value == "Aylık özet"
    assert first["D"].value == "Önce veriyi topla"
    # Gercek tarih hucresi, metin degil.
    assert first["E"].value == datetime(2026, 9, 11)
    assert first["E"].number_format == "DD.MM.YYYY"
    assert first["F"].value == "DEMO-1"
    assert first["F"].hyperlink.target == f"{BASE}/browse/DEMO-1"
    assert first["G"].value == "Özet DEMO-1"
    assert first["H"].value == "Açık"
    assert isinstance(first["I"].value, datetime)
    assert first["J"].value is None

    second = {cell.column_letter: cell for cell in sheet[3]}
    assert second["A"].value == "Yapıldı"
    assert isinstance(second["J"].value, datetime)
    assert second["J"].number_format == "DD.MM.YYYY HH:MM"
    assert sheet.max_row == 3


def test_task_export_can_be_narrowed_to_one_column(api_client):
    make_task(api_client, "Açık iş")
    done = make_task(api_client, "Biten iş")
    api_client.post(f"/api/tasks/{done['id']}/move", json={"status": "done"})

    sheet = read_sheet(api_client.get("/api/tasks/export.xlsx?status=done"))
    assert [row[1].value for row in sheet.iter_rows(min_row=2)] == ["Biten iş"]

    response = api_client.get("/api/tasks/export.xlsx?status=belki")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_status"


def test_task_export_keeps_old_done_cards(api_client, conn):
    old = make_task(api_client, "Eski biten")
    finish_long_ago(conn, old["id"])
    assert board(api_client)["old_done_count"] == 1

    sheet = read_sheet(api_client.get("/api/tasks/export.xlsx"))
    assert [row[1].value for row in sheet.iter_rows(min_row=2)] == ["Eski biten"]


def test_task_export_file_name_is_dated(api_client):
    make_task(api_client, "Bir")
    disposition = api_client.get("/api/tasks/export.xlsx").headers["content-disposition"]
    assert f"Gorevlerim-{date.today().isoformat()}.xlsx" in disposition
