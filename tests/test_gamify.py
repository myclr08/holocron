"""Sefer (oyunlastirma): goc, kural motoru, rutbe, seri, rozet, emir, API, arayuz.

Zaman her yerde disaridan verilir (`now=`): testler makinenin gunune bagli
kalmaz, hafta sonu calistirildiginda da ayni sonucu verir.
"""

from __future__ import annotations

import io
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from openpyxl import load_workbook

from app import db, desktop, export, gamify
from app import gamify_repo as store
from app import repository as repo
from app.settings_store import MODE_SERVER
from tests.fake_jira import issue

BASE = "https://jira.example.com"

# 14 Eylul 2026 pazartesi; butun testler bu haftanin icinde calisir.
MONDAY = datetime(2026, 9, 14, 10, 0)
TUESDAY = datetime(2026, 9, 15, 10, 0)
WEDNESDAY = datetime(2026, 9, 16, 10, 0)
SATURDAY = datetime(2026, 9, 19, 10, 0)

CATALOG = [
    {"id": "summary", "name": "Özet", "schema": {"type": "string"}},
    {"id": "status", "name": "Durum", "schema": {"type": "status"}},
    {"id": "updated", "name": "Güncelleme", "schema": {"type": "datetime"}},
]


def campaign(context, name="Sonbahar Seferi", ends_at="2026-12-31", target=1000, now=MONDAY):
    return gamify.start_campaign(context, name, ends_at, target, now=now)


def close_task(context, title="Rapor yaz", now=MONDAY, **extra):
    """Gorev yaratir, "Yapildi"ya tasir ve motoru bilgilendirir."""
    conn = context.connection()
    task = repo.create_task(conn, title, **extra)
    after = repo.move_task(conn, task["id"], status=repo.TASK_DONE)
    return gamify.on_task_done(context, task, after, now=now)


def kinds(events):
    return [event["kind"] for event in events]


def ledger_kinds(context):
    active = store.active_campaign(context.connection())
    return [event["kind"] for event in store.list_events(context.connection(), active["id"])]


# --- goc ----------------------------------------------------------------


def test_migration_creates_the_campaign_tables(conn):
    assert {"campaigns", "xp_events", "xp_rules", "badges", "quests", "streaks"} <= db.table_names(
        conn
    )
    assert db.SCHEMA_VERSION == 12


def test_rules_are_seeded_once(conn):
    rules = store.list_rules(conn)
    assert {rule["kind"] for rule in rules} == {seed["kind"] for seed in gamify.SEED_RULES}
    assert store.rules_map(conn)[gamify.KIND_LEFT_GROUP]["points"] == 15
    # Ikinci goc tohum atmaz: kullanici puani sifirladiysa geri gelmemeli.
    conn.execute("UPDATE xp_rules SET points = 0 WHERE kind = ?", (gamify.KIND_DONE,))
    conn.commit()
    db.migrate(conn)
    assert store.rules_map(conn)[gamify.KIND_DONE]["points"] == 0


def test_only_one_campaign_can_be_active(conn):
    store.create_campaign(conn, "Bir", "2026-12-31", 500, today="2026-09-14")
    with pytest.raises(repo.RepositoryError) as err:
        store.create_campaign(conn, "Iki", "2026-12-31", 500, today="2026-09-14")
    assert err.value.code == "campaign_active"
    # Sema da korur: kod atlansa bile ikinci aktif satir yazilamaz.
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO campaigns (name, status, target_xp) VALUES ('Uc', 'active', 100)"
        )
        conn.commit()
    conn.rollback()


def test_the_same_event_is_never_scored_twice(conn):
    made = store.create_campaign(conn, "Sefer", "2026-12-31", 500, today="2026-09-14")
    first = store.add_event(conn, made["id"], "task", "done", 10, "task:1", "Bir")
    again = store.add_event(conn, made["id"], "task", "done", 10, "task:1", "Bir")
    assert first is not None and again is None
    assert store.total_xp(conn, made["id"]) == 10


# --- kural motoru -------------------------------------------------------


def test_no_campaign_means_no_points(context):
    assert close_task(context) == []
    assert store.active_campaign(context.connection()) is None


def test_closing_a_task_scores_and_never_repeats(context):
    made = campaign(context)
    events = close_task(context)
    assert kinds(events) == [gamify.KIND_DONE]
    assert store.total_xp(context.connection(), made["id"]) == 10 + 3  # gorev + seri gunu

    conn = context.connection()
    task = repo.list_tasks(conn)[0]
    back = repo.move_task(conn, task["id"], status=repo.TASK_TODO)
    again = repo.move_task(conn, task["id"], status=repo.TASK_DONE)
    assert gamify.on_task_done(context, back, again, now=MONDAY) == []
    assert store.total_xp(conn, made["id"]) == 13


def test_a_task_closed_before_its_due_date_earns_the_bonus(context):
    campaign(context)
    events = close_task(context, due_date="2026-09-20")
    assert kinds(events) == [gamify.KIND_DONE, gamify.KIND_DONE_BEFORE_DUE]


def test_an_overdue_task_earns_half_and_no_bonus(context):
    campaign(context)
    events = close_task(context, due_date="2026-09-01")
    assert kinds(events) == [gamify.KIND_DONE_OVERDUE]
    assert events[0]["points"] == 5
    assert "gecikmiş" in events[0]["title"]


def test_a_disabled_rule_gives_nothing(context):
    campaign(context)
    store.update_rules(context.connection(), [{"kind": gamify.KIND_DONE, "enabled": False}])
    assert close_task(context) == []


def test_rule_points_can_be_changed(context):
    campaign(context)
    store.update_rules(context.connection(), [{"kind": gamify.KIND_DONE, "points": 40}])
    assert close_task(context)[0]["points"] == 40


def test_points_are_validated(context):
    campaign(context)
    with pytest.raises(repo.RepositoryError):
        store.update_rules(context.connection(), [{"kind": gamify.KIND_DONE, "points": 5000}])


def test_a_mail_task_answered_within_a_day_scores(context):
    made = campaign(context)
    conn = context.connection()
    task = repo.create_task(conn, "Teklif yanıtla")
    conn.execute(
        "UPDATE tasks SET source = 'mail', mail_received_at = ? WHERE id = ?",
        ((MONDAY - timedelta(hours=3)).astimezone(timezone.utc).isoformat(), task["id"]),
    )
    conn.commit()
    fresh = repo.get_task(conn, task["id"])
    after = repo.move_task(conn, task["id"], status=repo.TASK_DOING)
    events = gamify.on_task_done(context, fresh, after, now=MONDAY)
    assert kinds(events) == [gamify.KIND_MAIL_FAST]
    assert store.total_xp(conn, made["id"]) == 5 + 3


def test_a_stale_mail_task_earns_no_speed_bonus(context):
    campaign(context)
    conn = context.connection()
    task = repo.create_task(conn, "Eski posta")
    conn.execute(
        "UPDATE tasks SET source = 'mail', mail_received_at = ? WHERE id = ?",
        ((MONDAY - timedelta(days=4)).astimezone(timezone.utc).isoformat(), task["id"]),
    )
    conn.commit()
    fresh = repo.get_task(conn, task["id"])
    after = repo.move_task(conn, task["id"], status=repo.TASK_DOING)
    assert gamify.on_task_done(context, fresh, after, now=MONDAY) == []


# --- Guncelle kancasi ---------------------------------------------------


def refresh_result(group_id, name="Açık işler", keys=("DEMO-2",), moves=None):
    return {
        "filter_drops": {str(group_id): {"name": name, "keys": list(keys)}},
        "status_moves": moves or {},
    }


def test_a_record_leaving_a_filter_group_scores(context, conn):
    made = campaign(context)
    group = repo.create_group(conn, "Açık işler", repo.KIND_FILTER, jql="project = DEMO")

    report = gamify.on_refresh(context, refresh_result(group["id"]), now=MONDAY)

    assert report == {"points": 15, "events": 1}
    event = store.list_events(conn, made["id"], source=gamify.SOURCE_JIRA)[0]
    assert event["title"] == "DEMO-2 'Açık işler' filosundan düştü"
    # Ayni Guncelle iki kez kosarsa ikinci puan yok.
    assert gamify.on_refresh(context, refresh_result(group["id"]), now=MONDAY)["points"] == 0


def test_group_points_can_be_overridden_per_group(context, conn):
    campaign(context)
    group = repo.create_group(conn, "Kritik", repo.KIND_FILTER, jql="project = DEMO")
    conn.execute(
        "UPDATE xp_rules SET params_json = ? WHERE kind = ?",
        ('{"group_points": {"%s": 40}}' % group["id"], gamify.KIND_LEFT_GROUP),
    )
    conn.commit()

    assert gamify.on_refresh(context, refresh_result(group["id"]), now=MONDAY)["points"] == 40


def test_removing_an_item_by_hand_gives_nothing(context, conn):
    """Manuel gruptan elle cikarma puan vermez: kanca yalnizca JQL sonucunu okur."""
    campaign(context)
    group = repo.create_group(conn, "Takip", repo.KIND_MANUAL)
    repo.add_items(conn, group["id"], ["DEMO-1"])
    repo.remove_item(conn, group["id"], "DEMO-1")

    assert gamify.SOURCE_JIRA not in store.source_totals(
        conn, store.active_campaign(conn)["id"]
    )


def test_a_record_moving_to_done_scores(context, conn):
    campaign(context)
    moves = {"DEMO-1": {"from": "indeterminate", "to": "done"}}
    report = gamify.on_refresh(context, refresh_result(1, keys=(), moves=moves), now=MONDAY)
    assert report["points"] == 20
    assert gamify.KIND_STATUS_DONE in ledger_kinds(context)


def test_a_record_moving_into_progress_scores_less(context, conn):
    campaign(context)
    moves = {"DEMO-1": {"from": "new", "to": "indeterminate"}}
    assert gamify.on_refresh(context, refresh_result(1, keys=(), moves=moves), now=MONDAY)[
        "points"
    ] == 5


def test_status_moves_reach_the_refresh_summary(context, conn, fake_jira):
    """Kategori gecisi Guncelle raporunda tasiniyor mu (motorun girdisi)."""
    repo.upsert_issues(
        conn, [issue("DEMO-1", "Bir", status={"name": "Açık", "statusCategory": {"key": "new"}})]
    )
    report = repo.upsert_issues(
        conn,
        [issue("DEMO-1", "Bir", status={"name": "Bitti", "statusCategory": {"key": "done"}})],
    )
    assert report.status_moves == {"DEMO-1": {"from": "new", "to": "done"}}


# --- Teams: son durum soruldu -------------------------------------------


def test_asking_for_status_is_capped_at_three_a_day(context):
    made = campaign(context)
    for index in range(5):
        gamify.on_status_asked(context, f"DEMO-{index}", now=MONDAY)
    asked = store.count_events(context.connection(), made["id"], (gamify.KIND_STATUS_ASKED,))
    assert asked == 3
    # Ertesi gun tavan yenilenir.
    gamify.on_status_asked(context, "DEMO-9", now=TUESDAY)
    assert store.count_events(
        context.connection(), made["id"], (gamify.KIND_STATUS_ASKED,)
    ) == 4


# --- rutbeler -----------------------------------------------------------


@pytest.mark.parametrize(
    "total,code",
    [(0, "padawan"), (249, "padawan"), (250, "knight"), (599, "knight"), (600, "master"),
     (999, "master"), (1000, "council"), (1499, "council"), (1500, "legend")],
)
def test_rank_thresholds_follow_the_target(total, code):
    assert gamify.rank_of(total, 1000)["code"] == code


def test_rank_thresholds_scale_with_a_smaller_target():
    assert gamify.rank_of(100, 400)["code"] == "knight"
    assert gamify.rank_of(240, 400)["code"] == "master"


def test_the_next_rank_says_what_is_missing():
    nxt = gamify.next_rank_of(100, 1000)
    assert nxt["code"] == "knight" and nxt["remaining"] == 150
    assert gamify.next_rank_of(2000, 1000) is None


# --- sefer bitisi -------------------------------------------------------


def test_the_campaign_ends_when_its_date_passes_and_xp_starts_over(context):
    campaign(context, ends_at="2026-09-16", now=MONDAY)
    close_task(context, now=MONDAY)
    conn = context.connection()

    assert gamify.ensure_campaign(context, WEDNESDAY) is not None  # son gun sayilir
    assert gamify.ensure_campaign(context, datetime(2026, 9, 17, 9, 0)) is None

    ended = store.list_campaigns(conn, store.STATUS_ENDED)[0]
    assert ended["summary"]["total_xp"] == 13
    assert ended["summary"]["rank"]["code"] == "padawan"
    assert ended["summary"]["sources"] == {"task": 10, "streak": 3}

    # Biten seferde puan toplanmaz.
    assert close_task(context, "Yeni iş", now=datetime(2026, 9, 17, 9, 0)) == []

    # Yeni sefer sifirdan baslar; eski defter durur.
    fresh = campaign(context, "İkinci", "2026-12-31", 500, now=datetime(2026, 9, 17, 9, 0))
    assert store.total_xp(conn, fresh["id"]) == 0
    assert store.total_xp(conn, ended["id"]) == 13


def test_a_campaign_can_be_ended_by_hand(context):
    campaign(context)
    close_task(context)
    ended = gamify.end_campaign(context, now=MONDAY)
    assert ended["status"] == "ended"
    assert ended["summary"]["total_xp"] == 13
    assert gamify.end_campaign(context, now=MONDAY) is None


def test_campaign_fields_are_validated(context):
    with pytest.raises(repo.RepositoryError):
        gamify.start_campaign(context, "", "2026-12-31", 500, now=MONDAY)
    with pytest.raises(repo.RepositoryError):
        gamify.start_campaign(context, "Sefer", "yarın", 500, now=MONDAY)
    with pytest.raises(repo.RepositoryError):
        gamify.start_campaign(context, "Sefer", "2026-12-31", 5, now=MONDAY)
    with pytest.raises(repo.RepositoryError) as err:
        gamify.start_campaign(context, "Sefer", "2026-09-01", 500, now=MONDAY)
    assert err.value.code == "invalid_end_date"


# --- seri ---------------------------------------------------------------


def test_the_streak_counts_workdays_and_skips_the_weekend(context):
    made = campaign(context, now=datetime(2026, 9, 11, 10, 0))  # cuma
    close_task(context, "Cuma", now=datetime(2026, 9, 11, 10, 0))
    close_task(context, "Pazartesi", now=MONDAY)

    marks = store.marked_days(context.connection(), made["id"])
    assert set(marks) == {"2026-09-11", "2026-09-14"}
    # Hafta sonu arada olsa da seri kopmaz.
    assert gamify.streak_state(context, store.active_campaign(context.connection()), MONDAY)[
        "days"
    ] == 2


def test_the_weekend_itself_is_never_marked(context):
    campaign(context, now=SATURDAY)
    close_task(context, now=SATURDAY)
    made = store.active_campaign(context.connection())
    assert store.marked_days(context.connection(), made["id"]) == {}
    assert gamify.KIND_STREAK_DAY not in ledger_kinds(context)


def test_the_force_protection_forgives_one_missed_day_a_month(context):
    made = campaign(context, now=datetime(2026, 9, 7, 10, 0))  # pazartesi
    close_task(context, "Bir", now=datetime(2026, 9, 7, 10, 0))
    # 8 Eylul atlanir, 9 Eylul'de devam edilir.
    close_task(context, "İki", now=datetime(2026, 9, 9, 10, 0))

    marks = store.marked_days(context.connection(), made["id"])
    assert marks.get("2026-09-08") == store.STREAK_GRACE
    state = gamify.streak_state(context, store.active_campaign(context.connection()),
                                datetime(2026, 9, 9, 10, 0))
    assert state["days"] == 3 and state["grace_used"] is True

    # Ayni ay ikinci koruma yok: 11 Eylul atlanirsa seri kopar.
    close_task(context, "Üç", now=datetime(2026, 9, 14, 10, 0))
    marks = store.marked_days(context.connection(), made["id"])
    assert "2026-09-11" not in marks
    assert gamify.streak_state(context, store.active_campaign(context.connection()), MONDAY)[
        "days"
    ] == 1


def test_moving_a_task_without_points_still_keeps_the_streak(context):
    """Gorev "Yapiliyor"a tasindi: puan yok ama gun etkin sayilir."""
    made = campaign(context)
    conn = context.connection()
    task = repo.create_task(conn, "Başla")
    after = repo.move_task(conn, task["id"], status=repo.TASK_DOING)
    gamify.on_task_done(context, task, after, now=MONDAY)
    assert store.marked_days(conn, made["id"]) == {"2026-09-14": "active"}


# --- haftalik emirler ---------------------------------------------------


def test_quests_are_built_from_real_data(context, conn):
    campaign(context)
    repo.create_task(conn, "Geciken", due_date="2026-09-01")
    for index in range(7):
        task = repo.create_task(conn, f"İş {index}")
        repo.move_task(conn, task["id"], status=repo.TASK_DOING)

    gamify.evaluate(context, MONDAY)
    made = store.active_campaign(conn)
    quests = {quest["code"]: quest for quest in store.list_quests(conn, made["id"], "2026-09-14")}

    assert quests[gamify.QUEST_OVERDUE]["target"] == 1
    assert quests[gamify.QUEST_OVERDUE]["title"] == "1 gecikmiş görevi kapat"
    assert quests[gamify.QUEST_DOING_LIMIT]["target"] == 2
    assert len(quests) <= gamify.QUEST_COUNT


def test_a_finished_quest_scores_once(context, conn):
    campaign(context)
    task = repo.create_task(conn, "Geciken", due_date="2026-09-01")
    gamify.evaluate(context, MONDAY)

    after = repo.move_task(conn, task["id"], status=repo.TASK_DONE)
    gamify.on_task_done(context, task, after, now=MONDAY)

    made = store.active_campaign(conn)
    quest = store.list_quests(conn, made["id"], "2026-09-14")[0]
    assert quest["done"] and quest["progress"] == 1
    assert ledger_kinds(context).count(gamify.KIND_QUEST_DONE) == 1

    gamify.evaluate(context, MONDAY)
    assert ledger_kinds(context).count(gamify.KIND_QUEST_DONE) == 1


def test_a_new_week_brings_new_orders(context, conn):
    campaign(context)
    repo.create_task(conn, "Geciken", due_date="2026-09-01")
    gamify.evaluate(context, MONDAY)
    gamify.evaluate(context, datetime(2026, 9, 21, 10, 0))  # sonraki pazartesi

    made = store.active_campaign(conn)
    weeks = {quest["week_start"] for quest in store.list_quests(conn, made["id"])}
    assert weeks == {"2026-09-14", "2026-09-21"}


def test_stale_records_become_an_order(context, conn):
    campaign(context)
    repo.store_fields(conn, CATALOG)
    group = repo.create_group(conn, "Filo", repo.KIND_FILTER, jql="project = DEMO")
    repo.add_items(conn, group["id"], ["DEMO-1", "DEMO-2"])
    old = (MONDAY - timedelta(days=40)).astimezone(timezone.utc).isoformat()
    repo.upsert_issues(
        conn, [issue("DEMO-1", "Bir", updated=old), issue("DEMO-2", "İki", updated=old)]
    )

    assert gamify.stale_issues(context, MONDAY) == ["DEMO-1", "DEMO-2"]
    gamify.evaluate(context, MONDAY)
    made = store.active_campaign(conn)
    quest = store.list_quests(conn, made["id"], "2026-09-14")[0]
    assert quest["code"] == gamify.QUEST_STALE_ASK
    assert quest["title"] == "2 kaydın son durumunu sor"


# --- guc dengesi --------------------------------------------------------


def call_row(call_id, kind, minutes, started):
    return {
        "call_id": call_id,
        "started_at": started,
        "ended_at": started,
        "duration_ms": minutes * 60 * 1000,
        "kind": kind,
        "state": "accepted",
        "direction": "incoming",
    }


def test_the_record_reports_the_rank_change_and_last_weeks_meetings(context, conn):
    made = campaign(context, target=100, now=datetime(2026, 9, 7, 10, 0))
    close_task(context, "Bir", now=datetime(2026, 9, 9, 10, 0))  # 13 XP -> Sovalye
    repo.import_calls(
        conn,
        [
            call_row("a", "meeting", 90, datetime(2026, 9, 9, 9, 0).astimezone(timezone.utc).isoformat()),
            call_row("b", "meeting", 30, MONDAY.astimezone(timezone.utc).isoformat()),  # bu hafta
        ],
    )

    record = gamify.panel(context, MONDAY)["digest"]
    assert record["rank_before"]["code"] == "padawan"
    assert record["rank"]["code"] == "knight" and record["rank_changed"] is True
    # Kart GECEN haftayi anlatir: bu haftanin toplantisi sayilmaz.
    assert record["meeting_text"] == "1 sa 30 dk"


def test_the_force_balance_measures_the_meeting_load(context, conn):
    campaign(context)
    start = MONDAY.astimezone(timezone.utc).isoformat()
    repo.import_calls(
        conn,
        [
            call_row("a", "meeting", 600, start),  # 10 saat
            call_row("b", "group_call", 120, start),  # 2 saat
            call_row("c", "one_to_one", 600, start),  # sayilmaz
        ],
    )

    balance = gamify.force_balance(context, MONDAY)
    # 12 saat / (5 gun x 8 saat) = %30 -> sari.
    assert balance["percent"] == 30
    assert balance["level"] == "yellow"
    assert balance["text"] == "bu hafta 12 sa 0 dk toplantı"
    assert balance["has_data"] is True


def test_the_focus_budget_can_be_changed(context, conn):
    campaign(context)
    context.settings.set(gamify.SETTING_FOCUS_HOURS, "4")
    repo.import_calls(
        conn, [call_row("a", "meeting", 600, MONDAY.astimezone(timezone.utc).isoformat())]
    )
    balance = gamify.force_balance(context, MONDAY)
    assert balance["focus_hours"] == 4.0 and balance["percent"] == 50
    assert balance["level"] == "red"


def test_without_call_data_the_gauge_says_so(context):
    campaign(context)
    assert gamify.force_balance(context, MONDAY)["has_data"] is False


# --- rozetler -----------------------------------------------------------


def fill_events(context, kind, count, source=gamify.SOURCE_TASK, prefix="x"):
    made = store.active_campaign(context.connection())
    for index in range(count):
        store.add_event(
            context.connection(), made["id"], source, kind, 1, f"{prefix}{index}", "dolgu"
        )


def earned(context):
    made = store.active_campaign(context.connection())
    return set(store.earned_badges(context.connection(), made["id"]))


def test_the_closer_badge_needs_twenty_five_closed_tasks(context):
    campaign(context)
    fill_events(context, gamify.KIND_DONE, 24)
    gamify.evaluate(context, MONDAY)
    assert gamify.BADGE_CLOSER not in earned(context)

    fill_events(context, gamify.KIND_DONE, 1, prefix="y")
    gamify.evaluate(context, MONDAY)
    assert gamify.BADGE_CLOSER in earned(context)
    # Rozet kendi puanini bir kez verir.
    assert ledger_kinds(context).count(gamify.KIND_BADGE_EARNED) == 1


def test_the_finisher_badge_counts_one_group(context, conn):
    campaign(context)
    first = repo.create_group(conn, "Bir", repo.KIND_FILTER, jql="a")
    second = repo.create_group(conn, "İki", repo.KIND_FILTER, jql="b")
    made = store.active_campaign(conn)
    for index in range(15):
        store.add_event(conn, made["id"], "jira", gamify.KIND_LEFT_GROUP, 1,
                        f"{first['id']}:A-{index}", "x")
        store.add_event(conn, made["id"], "jira", gamify.KIND_LEFT_GROUP, 1,
                        f"{second['id']}:B-{index}", "x")
    gamify.evaluate(context, MONDAY)
    assert gamify.BADGE_FINISHER not in earned(context)

    for index in range(15, 20):
        store.add_event(conn, made["id"], "jira", gamify.KIND_LEFT_GROUP, 1,
                        f"{first['id']}:A-{index}", "x")
    gamify.evaluate(context, MONDAY)
    assert gamify.BADGE_FINISHER in earned(context)


def test_the_fast_reply_badge_needs_ten_quick_mail_tasks(context):
    campaign(context)
    fill_events(context, gamify.KIND_MAIL_FAST, 10, source=gamify.SOURCE_MAIL)
    gamify.evaluate(context, MONDAY)
    assert gamify.BADGE_FAST_REPLY in earned(context)


def test_streak_badges_follow_the_streak(context):
    made = campaign(context, now=datetime(2026, 9, 7, 10, 0))
    conn = context.connection()
    for day in ("2026-09-07", "2026-09-08", "2026-09-09", "2026-09-10", "2026-09-11"):
        store.mark_day(conn, made["id"], day)
    gamify.evaluate(context, datetime(2026, 9, 11, 12, 0))
    assert gamify.BADGE_STREAK_5 in earned(context)
    assert gamify.BADGE_STREAK_20 not in earned(context)


def test_the_cartographer_badge_wants_columns_everywhere(context, conn):
    campaign(context)
    first = repo.create_group(conn, "Bir", repo.KIND_MANUAL)
    second = repo.create_group(conn, "İki", repo.KIND_MANUAL)
    repo.update_group(conn, first["id"], {"columns": ["issuekey", "summary"]})
    gamify.evaluate(context, MONDAY)
    assert gamify.BADGE_CARTOGRAPHER not in earned(context)

    repo.update_group(conn, second["id"], {"columns": ["issuekey"]})
    gamify.evaluate(context, MONDAY)
    assert gamify.BADGE_CARTOGRAPHER in earned(context)


def test_the_archivist_badge_counts_folded_tasks(context, conn):
    campaign(context)
    old = (MONDAY - timedelta(days=60)).astimezone(timezone.utc).isoformat()
    for index in range(20):
        task = repo.create_task(conn, f"Eski {index}", status=repo.TASK_DONE)
        conn.execute("UPDATE tasks SET done_at = ? WHERE id = ?", (old, task["id"]))
    conn.commit()
    gamify.evaluate(context, MONDAY)
    assert gamify.BADGE_ARCHIVIST in earned(context)


def test_the_envoy_badge_counts_asked_records(context, conn):
    campaign(context)
    for index in range(20):
        repo.record_sent_message(conn, f"DEMO-{index}", "people", "a@example.com", "Son durum?")
    # Kayitlar seferin icinde acilmis sayilsin (kayit saati makinenin saati).
    conn.execute("UPDATE sent_messages SET opened_at = ?", (MONDAY.isoformat(),))
    conn.commit()
    gamify.evaluate(context, MONDAY)
    assert gamify.BADGE_ENVOY in earned(context)


def test_the_balance_badge_wants_a_light_meeting_week(context, conn):
    campaign(context)
    repo.import_calls(
        conn, [call_row("a", "meeting", 60, MONDAY.astimezone(timezone.utc).isoformat())]
    )
    gamify.evaluate(context, MONDAY)
    assert gamify.BADGE_BALANCE in earned(context)


def test_a_heavy_meeting_week_earns_no_balance_badge(context, conn):
    campaign(context)
    repo.import_calls(
        conn, [call_row("a", "meeting", 20 * 60, MONDAY.astimezone(timezone.utc).isoformat())]
    )
    gamify.evaluate(context, MONDAY)
    assert gamify.BADGE_BALANCE not in earned(context)


def test_the_clean_desk_badge_needs_a_week_without_overdue(context, conn):
    campaign(context)
    gamify.evaluate(context, MONDAY)
    assert gamify.BADGE_CLEAN_DESK not in earned(context)

    gamify.evaluate(context, MONDAY + timedelta(days=6))
    assert gamify.BADGE_CLEAN_DESK in earned(context)


def test_an_overdue_task_resets_the_clean_desk_count(context, conn):
    campaign(context)
    gamify.evaluate(context, MONDAY)
    repo.create_task(conn, "Geciken", due_date="2026-09-01")
    gamify.evaluate(context, MONDAY + timedelta(days=3))
    assert context.settings.get(gamify.SETTING_CLEAN_SINCE, "") == ""
    gamify.evaluate(context, MONDAY + timedelta(days=8))
    assert gamify.BADGE_CLEAN_DESK not in earned(context)


def test_reaching_the_target_earns_the_campaign_badge(context):
    campaign(context, target=50)
    close_task(context)
    gamify.evaluate(context, MONDAY)
    assert gamify.BADGE_COMPLETE not in earned(context)

    fill_events(context, gamify.KIND_DONE, 1, prefix="big")
    conn = context.connection()
    made = store.active_campaign(conn)
    conn.execute("UPDATE xp_events SET points = 100 WHERE ref = 'big0'")
    conn.commit()
    gamify.evaluate(context, MONDAY)
    assert gamify.BADGE_COMPLETE in earned(context)


def test_every_badge_code_has_a_wall_tile(context):
    made = campaign(context)
    wall = gamify.badge_wall(context, made, MONDAY)
    assert [tile["code"] for tile in wall] == list(gamify.BADGE_CODES)
    for tile in wall:
        assert tile["image"] == f"/static/img/gamify/badge-{tile['code']}.png"
        assert tile["hint"]


# --- panel --------------------------------------------------------------


def test_the_panel_carries_everything_the_screen_draws(context):
    campaign(context, target=200)
    close_task(context)
    data = gamify.panel(context, MONDAY)

    assert data["campaign"]["name"] == "Sonbahar Seferi"
    assert data["xp"] == 13 and data["percent"] == 6  # 13/200
    assert data["rank"]["image"] == "/static/img/gamify/rank-padawan.png"
    assert data["next_rank"]["label"] == "Şövalye"
    assert data["week"]["tasks_done"] == 1
    assert len(data["badges"]) == len(gamify.BADGES)
    assert data["ledger"][0]["source_label"]
    assert data["force"]["level"] == "green"
    assert data["campaign"]["days_left"] == (
        (datetime(2026, 12, 31) - datetime(2026, 9, 14)).days
    )


def test_the_panel_without_a_campaign_invites_one(context):
    data = gamify.panel(context, MONDAY)
    assert data["campaign"] is None
    assert data["suggested_target"] == 1000
    assert [rank["code"] for rank in data["ranks"]][0] == "padawan"


def test_the_monday_record_shows_last_week_once(context):
    campaign(context, now=datetime(2026, 9, 7, 10, 0))
    close_task(context, now=datetime(2026, 9, 9, 10, 0))

    record = gamify.panel(context, MONDAY)["digest"]
    assert record["xp"] == 13 and record["tasks_done"] == 1
    assert record["week_start"] == "2026-09-07" and record["seen_key"] == "2026-09-14"
    # Rutbe degisimi de kartta: 0 XP'den 13 XP'ye, hedef 1000 -> ikisi de Padawan.
    assert record["rank_before"]["code"] == "padawan" and record["rank_changed"] is False
    assert record["meeting_text"] == "—"

    context.settings.set(gamify.SETTING_DIGEST_WEEK, "2026-09-14")
    assert gamify.panel(context, MONDAY)["digest"] is None
    # Sali gunu hic cikmaz.
    context.settings.set(gamify.SETTING_DIGEST_WEEK, "")
    assert gamify.panel(context, TUESDAY)["digest"] is None


def test_history_keeps_the_ended_campaigns(context):
    campaign(context, "Birinci", "2026-09-16", 500, now=MONDAY)
    close_task(context, now=MONDAY)
    gamify.ensure_campaign(context, datetime(2026, 9, 17, 9, 0))

    cards = gamify.history(context)
    assert len(cards) == 1
    assert cards[0]["name"] == "Birinci" and cards[0]["total_xp"] == 13
    assert cards[0]["rank"]["label"] == "Padawan"


# --- API ----------------------------------------------------------------


def start_via_api(api_client, name="Sefer", ends_at="2026-12-31", target=1000):
    response = api_client.post(
        "/api/campaign", json={"name": name, "ends_at": ends_at, "target_xp": target}
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_the_api_starts_and_reports_a_campaign(api_client):
    data = start_via_api(api_client)
    assert data["campaign"]["name"] == "Sefer"
    assert api_client.get("/api/campaign").json()["campaign"]["target_xp"] == 1000


def test_the_api_refuses_a_second_campaign(api_client):
    start_via_api(api_client)
    response = api_client.post(
        "/api/campaign", json={"name": "İkinci", "ends_at": "2026-12-31", "target_xp": 500}
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "campaign_active"


def test_the_api_reports_a_bad_date(api_client):
    response = api_client.post("/api/campaign", json={"name": "Sefer", "ends_at": ""})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "invalid_end_date"


def test_closing_a_task_over_the_api_scores(api_client, conn):
    start_via_api(api_client)
    task = api_client.post("/api/tasks", json={"title": "Rapor"}).json()["task"]
    api_client.post(f"/api/tasks/{task['id']}/move", json={"status": "done"})

    data = api_client.get("/api/campaign").json()
    assert data["xp"] >= 10
    titles = [event["title"] for event in data["ledger"]]
    assert "Görev kapatıldı: Rapor" in titles


def test_editing_a_task_into_done_scores_too(api_client):
    start_via_api(api_client)
    task = api_client.post("/api/tasks", json={"title": "Düzenle"}).json()["task"]
    api_client.put(f"/api/tasks/{task['id']}", json={"status": "done"})
    assert api_client.get("/api/campaign").json()["xp"] >= 10


def test_the_ledger_can_be_filtered(api_client):
    start_via_api(api_client)
    task = api_client.post("/api/tasks", json={"title": "Rapor"}).json()["task"]
    api_client.post(f"/api/tasks/{task['id']}/move", json={"status": "done"})

    all_rows = api_client.get("/api/campaign/ledger").json()
    assert all_rows["events"]
    only_tasks = api_client.get("/api/campaign/ledger?source=task").json()["events"]
    assert {event["source"] for event in only_tasks} == {"task"}
    assert [item["id"] for item in all_rows["sources"]][0] == "task"


def test_the_ledger_can_be_exported(api_client):
    start_via_api(api_client)
    task = api_client.post("/api/tasks", json={"title": "Rapor"}).json()["task"]
    api_client.post(f"/api/tasks/{task['id']}/move", json={"status": "done"})

    response = api_client.get("/api/campaign/ledger.xlsx")
    assert response.status_code == 200
    assert response.headers["content-type"] == export.MEDIA_TYPE
    sheet = load_workbook(io.BytesIO(response.content)).active
    assert sheet.title == "XP defteri"
    assert [cell.value for cell in sheet[1]] == list(export.LEDGER_HEADERS)
    titles = [sheet.cell(row=line, column=5).value for line in range(2, sheet.max_row + 1)]
    assert "Görev kapatıldı: Rapor" in titles
    points = sheet.cell(row=2, column=4).value
    assert isinstance(points, int)


def test_the_rules_can_be_read_and_written(api_client):
    data = api_client.get("/api/campaign/rules").json()
    assert data["focus_hours"] == 8.0
    assert any(rule["label"] == "Görev kapatıldı" for rule in data["rules"])

    written = api_client.put(
        "/api/campaign/rules",
        json={"rules": [{"kind": "done", "points": 25}], "focus_hours": 6},
    ).json()
    assert written["focus_hours"] == 6.0
    assert next(rule for rule in written["rules"] if rule["kind"] == "done")["points"] == 25


def test_the_api_ends_a_campaign_and_lists_history(api_client):
    start_via_api(api_client)
    ended = api_client.post("/api/campaign/end").json()
    assert ended["ended"]["name"] == "Sefer"
    assert ended["panel"]["campaign"] is None
    history = api_client.get("/api/campaign/history").json()["campaigns"]
    assert len(history) == 1 and history[0]["name"] == "Sefer"


def test_the_monday_record_can_be_dismissed(api_client, context):
    start_via_api(api_client)
    response = api_client.post("/api/campaign/digest-seen", json={"week_start": "2026-09-14"})
    assert response.json() == {"ok": True, "week_start": "2026-09-14"}
    assert context.settings.get(gamify.SETTING_DIGEST_WEEK, "") == "2026-09-14"


def test_asking_the_status_over_the_api_scores(api_client, conn, monkeypatch):
    monkeypatch.setattr(desktop, "open_url", lambda url: True)
    start_via_api(api_client)
    repo.store_fields(conn, CATALOG)
    api_client.put("/api/settings", json={"jira.base_url": BASE, "jira.mode": MODE_SERVER})
    group = api_client.post("/api/groups", json={"name": "Filom", "kind": "manual"}).json()["group"]
    api_client.post(f"/api/groups/{group['id']}/items", json={"text": "DEMO-1"})
    repo.upsert_issues(conn, [issue("DEMO-1", "Yazıcı arızası")])
    api_client.put(
        "/api/issues/DEMO-1/contacts", json={"contacts": [{"email": "a@example.com"}]}
    )

    api_client.post("/api/issues/DEMO-1/teams-open", json={})

    ledger = api_client.get("/api/campaign/ledger?source=teams").json()["events"]
    assert [event["kind"] for event in ledger] == ["status_asked"]
    assert ledger[0]["title"] == "DEMO-1 için son durum soruldu"


def test_a_refresh_that_drops_a_record_scores_over_the_api(api_client, context, conn, fake_jira):
    from tests.test_refresh import configure, install

    start_via_api(api_client)
    configure(context)
    jql = "project = DEMO"
    install(fake_jira, [issue("DEMO-1"), issue("DEMO-2")], {jql: ["DEMO-1", "DEMO-2"]})
    group = repo.create_group(conn, "Açık işler", repo.KIND_FILTER, jql=jql)
    context.refresh.run_blocking(context)

    install(fake_jira, [issue("DEMO-1"), issue("DEMO-2")], {jql: ["DEMO-1"]})
    status = context.refresh.run_blocking(context)

    assert status["filter_drops"][str(group["id"])]["keys"] == ["DEMO-2"]
    ledger = api_client.get("/api/campaign/ledger?source=jira").json()["events"]
    assert [event["title"] for event in ledger] == ["DEMO-2 'Açık işler' filosundan düştü"]


# --- arayuz kancalari ---------------------------------------------------


def test_the_campaign_entry_sits_in_the_hangar(api_client):
    page = api_client.get("/").text
    entry = page.split('id="group-list"')[0]
    assert 'id="campaign-entry"' in entry
    assert ">Sefer<" in entry
    assert 'id="campaign-badge"' in entry
    # Serit sari: --saber-yellow'a baglanan sinif.
    campaign_at = entry.index('id="campaign-entry"')
    assert "color-yellow" in entry[campaign_at:]
    assert campaign_at < entry.index('id="tasks-entry"')


def test_campaign_view_hooks_are_on_the_page(api_client):
    page = api_client.get("/").text
    for marker in (
        'id="campaign-view"',
        ">SEFER<",
        'id="campaign-hero"',
        'id="campaign-quests"',
        "Haftalık görev emirleri",
        'id="campaign-force"',
        "Güç dengesi",
        'id="campaign-badges"',
        "Rozet duvarı",
        'id="campaign-ledger"',
        "XP defteri",
        'id="ledger-sources"',
        'id="ledger-export"',
        "Tümünü Excel'e",
        'id="campaign-history"',
        "Geçmiş seferler",
        'id="campaign-digest"',
        'id="campaign-empty"',
        'id="campaign-name"',
        'id="campaign-ends"',
        'id="campaign-target"',
        "Sefer başlat",
        'id="campaign-end"',
        "/static/js/campaign.js",
    ):
        assert marker in page, marker


def test_campaign_script_covers_the_panel(api_client):
    script = api_client.get("/static/js/campaign.js").text
    for marker in (
        "/api/campaign",
        "/api/campaign/ledger",
        "campaign/ledger.xlsx",
        "/api/campaign/end",
        "/api/campaign/digest-seen",
        "function renderHero",
        "function renderQuests",
        "function renderForce",
        "function renderBadgeWall",
        "function renderLedger",
        "function renderHistory",
        "function renderDigest",
        "function celebrateRank",
        "function startCampaign",
        "function endCampaign",
        '"Holocron kaydı"',
        "Rütbe atladınız",
        "DIGEST_MS = 10000",
    ):
        assert marker in script, marker


def test_the_panel_falls_back_to_its_own_drawing(api_client):
    """Gorsel dosyasi yoksa ekran bos kalmaz: kendi SVG hologramimiz cizilir."""
    script = api_client.get("/static/js/campaign.js").text
    assert "function hologram" in script
    assert "function badgeHologram" in script
    # Yedek yalnizca yukleme hatasinda devreye girer.
    assert 'art.addEventListener("error"' in script
    assert 'image.addEventListener("error"' in script
    css = api_client.get("/static/css/app.css").text
    for marker in (".holo", ".holo-frame", ".holo-pip", ".rank-art", ".badge-art"):
        assert marker in css, marker


def test_the_expected_image_names_are_fixed(api_client):
    """Dosya adlari sabittir: koordinator uretip `img/gamify/` altina koyar."""
    data = api_client.get("/api/campaign").json()
    assert data["image_dir"] == "/static/img/gamify/"
    assert [rank["image"] for rank in data["ranks"]] == [
        "/static/img/gamify/rank-padawan.png",
        "/static/img/gamify/rank-knight.png",
        "/static/img/gamify/rank-master.png",
        "/static/img/gamify/rank-council.png",
        "/static/img/gamify/rank-legend.png",
    ]


def test_campaign_styles_are_defined(api_client):
    css = api_client.get("/static/css/app.css").text
    for name in (
        ".hero-strip",
        ".xp-bar",
        ".xp-fill",
        ".hero-streak",
        ".quest-card",
        ".quest-fill",
        ".force-gauge",
        ".force-fill.force-green",
        ".force-fill.force-yellow",
        ".force-fill.force-red",
        ".badge-wall",
        ".badge-tile.locked",
        ".ledger-line",
        ".history-card",
        ".holocron-record",
        "@keyframes rank-up",
    ):
        assert name in css, name
    # Serit rengi tema degiskeninden gelir, sabit renk yok.
    assert "border-left: 3px solid var(--saber-yellow)" in css


def test_the_campaign_card_is_on_the_settings_page(api_client):
    page = api_client.get("/settings").text
    for marker in (
        'id="campaign-card"',
        ">Sefer<",
        'id="campaign-rules"',
        'id="campaign-focus"',
        "Günlük odak bütçesi",
        'id="campaign-grace"',
        'id="campaign-rules-save"',
        "/static/js/campaign-settings.js",
    ):
        assert marker in page, marker
    script = api_client.get("/static/js/campaign-settings.js").text
    assert "/api/campaign/rules" in script
    assert "Güç koruması" in script


def test_campaign_texts_are_proper_turkish(api_client):
    page = api_client.get("/").text
    # Yalnizca bu asamanin yazdigi bolum olculur; eski yorumlar ASCII kalabilir.
    section = page.split('id="campaign-view"')[1].split("</section>")[0]
    script = api_client.get("/static/js/campaign.js").text
    for word in ("Gorev", "Rutbe", "gunluk", "Gecmis", "Guc", "basla", "Sefer basla"):
        assert word not in section, word
        assert word not in script, word
    for marker in ("Rozet duvarı", "Güç dengesi", "Geçmiş seferler", "Haftalık görev emirleri"):
        assert marker in section, marker


def test_the_new_scripts_parse():
    """node --check: sozdizimi hatasi sessizce bos panele donusmesin."""
    import shutil
    import subprocess
    from pathlib import Path

    node = shutil.which("node")
    if node is None:
        pytest.skip("node yok")
    static = Path(__file__).resolve().parent.parent / "app" / "static" / "js"
    for name in ("campaign.js", "campaign-settings.js"):
        result = subprocess.run(
            [node, "--check", str(static / name)], capture_output=True, text=True
        )
        assert result.returncode == 0, f"{name}: {result.stderr}"


def test_the_image_folder_ships_with_the_app():
    """Gorseller sonradan konacak; klasorun kendisi pakete girsin."""
    from pathlib import Path

    folder = Path(__file__).resolve().parent.parent / "app" / "static" / "img" / "gamify"
    assert folder.is_dir()


def test_the_ledger_is_written_before_the_job_reports_done(context, conn, fake_jira):
    """Arayuz "tamamlandi" gorup rozeti tazeledeginde puan defterde olmali."""
    from tests.test_refresh import configure, install

    campaign(context)
    configure(context)
    jql = "project = DEMO"
    install(fake_jira, [issue("DEMO-1"), issue("DEMO-2")], {jql: ["DEMO-1", "DEMO-2"]})
    group = repo.create_group(conn, "Açık işler", repo.KIND_FILTER, jql=jql)
    context.refresh.run_blocking(context)

    install(fake_jira, [issue("DEMO-1"), issue("DEMO-2")], {jql: ["DEMO-1"]})
    status = context.refresh.run_blocking(context)

    assert status["state"] == "done"
    made = store.active_campaign(conn)
    assert store.count_events(conn, made["id"], (gamify.KIND_LEFT_GROUP,)) == 1
    assert status["filter_drops"][str(group["id"])]["keys"] == ["DEMO-2"]


# --- gozden gecirme sonrasi sertlestirmeler ------------------------------


def test_an_early_morning_meeting_still_counts(context, conn):
    """UTC damgasi / yerel gun: pazartesi 01:00'deki toplanti pazara kaymamali."""
    campaign(context)
    local_start = MONDAY.replace(hour=1, minute=0)
    repo.import_calls(
        conn, [call_row("a", "meeting", 60, local_start.astimezone(timezone.utc).isoformat())]
    )
    assert gamify.meeting_ms(context, "2026-09-14") == 60 * 60 * 1000
    assert gamify.force_balance(context, MONDAY)["duration_text"] == "1 sa 0 dk"


def test_messages_sent_before_the_campaign_do_not_count(context, conn):
    """Elçi rozeti seferin icinde sorulanlari sayar."""
    campaign(context)
    for index in range(20):
        repo.record_sent_message(conn, f"DEMO-{index}", "people", "a@example.com", "Son durum?")
    conn.execute("UPDATE sent_messages SET opened_at = ?", ("2026-09-01T08:00:00+00:00",))
    conn.commit()
    gamify.evaluate(context, MONDAY)
    assert gamify.BADGE_ENVOY not in earned(context)


def test_a_mass_drop_is_capped(context, conn):
    """JQL daraltilinca yuzlerce kayit dusebilir; bu "is bitti" demek degil."""
    campaign(context)
    group = repo.create_group(conn, "Açık işler", repo.KIND_FILTER, jql="project = DEMO")
    keys = [f"DEMO-{index}" for index in range(60)]

    report = gamify.on_refresh(context, refresh_result(group["id"], keys=keys), now=MONDAY)

    assert report["events"] == gamify.DROP_CAP
    assert report["points"] == gamify.DROP_CAP * 15


def test_a_broken_rule_parameter_does_not_kill_the_scoring(context, conn):
    campaign(context)
    group = repo.create_group(conn, "Açık işler", repo.KIND_FILTER, jql="project = DEMO")
    conn.execute(
        "UPDATE xp_rules SET params_json = ? WHERE kind = ?",
        ('["bozuk"]', gamify.KIND_LEFT_GROUP),
    )
    conn.execute(
        "UPDATE xp_rules SET params_json = ? WHERE kind = ?",
        ('{"daily_limit": "üç"}', gamify.KIND_STATUS_ASKED),
    )
    conn.commit()

    assert gamify.on_refresh(context, refresh_result(group["id"]), now=MONDAY)["points"] == 15
    assert gamify.on_status_asked(context, "DEMO-9", now=MONDAY) is not None


def test_a_group_point_override_that_is_junk_falls_back(context, conn):
    campaign(context)
    group = repo.create_group(conn, "Açık işler", repo.KIND_FILTER, jql="project = DEMO")
    conn.execute(
        "UPDATE xp_rules SET params_json = ? WHERE kind = ?",
        ('{"group_points": {"%s": "çok"}}' % group["id"], gamify.KIND_LEFT_GROUP),
    )
    conn.commit()
    assert gamify.on_refresh(context, refresh_result(group["id"]), now=MONDAY)["points"] == 15


def test_a_task_touched_by_the_machine_does_not_feed_the_streak(context, conn):
    """Posta taramasi `updated_at` yazar; kullanici hicbir sey yapmadi."""
    made = campaign(context)
    repo.create_task(conn, "Postadan düşen")
    conn.execute("UPDATE tasks SET updated_at = ?", (MONDAY.isoformat(),))
    conn.commit()

    gamify.evaluate(context, MONDAY)
    assert store.marked_days(conn, made["id"]) == {}


def test_a_save_that_changes_nothing_does_not_feed_the_streak(context, conn):
    made = campaign(context)
    task = repo.create_task(conn, "Duran görev")
    same = repo.update_task(conn, task["id"], {})
    gamify.on_task_done(context, task, same, now=MONDAY)
    assert store.marked_days(conn, made["id"]) == {}


def test_the_stale_order_only_counts_stale_records(context, conn):
    campaign(context)
    repo.store_fields(conn, CATALOG)
    group = repo.create_group(conn, "Filo", repo.KIND_FILTER, jql="project = DEMO")
    repo.add_items(conn, group["id"], ["DEMO-1", "DEMO-2"])
    old = (MONDAY - timedelta(days=40)).astimezone(timezone.utc).isoformat()
    repo.upsert_issues(
        conn, [issue("DEMO-1", "Bir", updated=old), issue("DEMO-2", "İki", updated=old)]
    )
    gamify.evaluate(context, MONDAY)

    # Alakasiz kayitlara mesaj: emir ilerlemiyor.
    for key in ("BASKA-1", "BASKA-2", "BASKA-3"):
        repo.record_sent_message(conn, key, "people", "a@example.com", "Son durum?")
    conn.execute("UPDATE sent_messages SET opened_at = ?", (MONDAY.isoformat(),))
    conn.commit()
    gamify.evaluate(context, MONDAY)
    made = store.active_campaign(conn)
    quest = store.list_quests(conn, made["id"], "2026-09-14")[0]
    assert quest["progress"] == 0

    # Bayat kayda sorulunca ilerliyor.
    repo.record_sent_message(conn, "DEMO-1", "people", "a@example.com", "Son durum?")
    conn.execute("UPDATE sent_messages SET opened_at = ?", (MONDAY.isoformat(),))
    conn.commit()
    gamify.evaluate(context, MONDAY)
    quest = store.list_quests(conn, made["id"], "2026-09-14")[0]
    assert quest["progress"] == 1


def test_a_reopened_task_cannot_bank_both_close_kinds(context, conn):
    """Gecikmis kapanan gorev, son tarihi duzeltilip yeniden kapatilirsa
    ikinci bir kapanis puani almamali."""
    made = campaign(context)
    task = repo.create_task(conn, "Geciken", due_date="2026-09-01")
    after = repo.move_task(conn, task["id"], status=repo.TASK_DONE)
    gamify.on_task_done(context, task, after, now=MONDAY)
    assert store.total_xp(conn, made["id"]) == 5 + 3

    back = repo.move_task(conn, task["id"], status=repo.TASK_TODO)
    repo.update_task(conn, task["id"], {"due_date": "2026-09-30"})
    again = repo.move_task(conn, task["id"], status=repo.TASK_DONE)
    assert gamify.on_task_done(context, back, again, now=MONDAY) == []
    assert store.total_xp(conn, made["id"]) == 8


def test_a_new_campaign_does_not_inherit_the_clean_desk_count(context):
    campaign(context, "Birinci", "2026-09-16", 500, now=MONDAY)
    gamify.evaluate(context, MONDAY)
    assert context.settings.get(gamify.SETTING_CLEAN_SINCE, "") == "2026-09-14"

    gamify.end_campaign(context, now=MONDAY)
    assert context.settings.get(gamify.SETTING_CLEAN_SINCE, "") == ""

    campaign(context, "İkinci", "2026-12-31", 500, now=datetime(2026, 9, 21, 10, 0))
    gamify.evaluate(context, datetime(2026, 9, 21, 10, 0))
    assert gamify.BADGE_CLEAN_DESK not in earned(context)


def test_the_order_pays_what_its_card_promised(context, conn):
    campaign(context)
    repo.create_task(conn, "Geciken", due_date="2026-09-01")
    gamify.evaluate(context, MONDAY)
    # Emir uretildikten SONRA kural degisti: kart 25 diyor, 25 odenir.
    store.update_rules(conn, [{"kind": gamify.KIND_QUEST_DONE, "points": 200}])

    task = repo.list_tasks(conn)[0]
    after = repo.move_task(conn, task["id"], status=repo.TASK_DONE)
    gamify.on_task_done(context, task, after, now=MONDAY)

    made = store.active_campaign(conn)
    quest = store.list_quests(conn, made["id"], "2026-09-14")[0]
    event = [
        item
        for item in store.list_events(conn, made["id"])
        if item["kind"] == gamify.KIND_QUEST_DONE
    ][0]
    assert quest["points"] == 25 and event["points"] == 25


def test_a_missing_rule_is_seeded_on_startup(context, conn):
    conn.execute("DELETE FROM xp_rules WHERE kind = ?", (gamify.KIND_DONE,))
    conn.commit()
    assert gamify.KIND_DONE not in store.rules_map(conn)

    assert gamify.ensure_rules(context) == 1
    assert store.rules_map(conn)[gamify.KIND_DONE]["points"] == 10
    # Ikinci cagri var olan satira dokunmaz.
    store.update_rules(conn, [{"kind": gamify.KIND_DONE, "points": 1}])
    assert gamify.ensure_rules(context) == 0
    assert store.rules_map(conn)[gamify.KIND_DONE]["points"] == 1


def test_the_record_does_not_credit_this_week_to_last_weeks_rank(context, conn):
    """`rank_before` gecen haftanin BASINDAKI rutbedir.

    Pazartesi karti cizilmeden once o gunun puani deftere dusuyor; o puan
    "gecen hafta boyle basladik" sayisina karismamali, yoksa rutbe atlamasi
    gorunmez olur.
    """
    campaign(context, target=100, now=datetime(2026, 9, 7, 10, 0))
    # Gecen hafta 13 XP (10 gorev + 3 seri): esik 25, hala Padawan.
    close_task(context, "Geçen hafta", now=datetime(2026, 9, 9, 10, 0))
    # Bu pazartesi bol puan: tek basina Sovalye esigini gecer.
    for index in range(3):
        close_task(context, f"Bu hafta {index}", now=MONDAY)

    record = gamify.panel(context, MONDAY)["digest"]

    assert record["xp"] == 13, "kart yalnızca geçen haftayı anlatır"
    # Bu haftanin puani "gecen haftanin basi"na sayilirsa burada Şövalye görünür.
    assert record["rank_before"]["code"] == "padawan"
    assert record["rank"]["code"] == "knight"
    assert record["rank_changed"] is True
