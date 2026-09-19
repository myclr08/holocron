"""Rozet katalogu: butunluk, her kategoriden kazanma, geriye donuk degerlendirme.

`test_gamify.py` Sefer motorunun tamamini sinar; burasi yalnizca rozetlere
bakar, cunku katalog 50'yi asti ve kendi kurallari var: her rozetin bir
simgesi, bir kategorisi, bir nadirligi ve GERCEK veriden olculen bir kurali
olmali.

Zaman her yerde disaridan verilir (`now=`): testler makinenin gunune bagli
kalmaz. Saatle ilgili rozetlerde damga YEREL saatten uretilir (`stamp_of`),
boylece test makinenin saat diliminden bagimsizdir.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

from app import gamify
from tools import rozet_simgeleri
from app import gamify_repo as store
from app import repository as repo

ROOT = Path(__file__).resolve().parent.parent
ICONS = ROOT / "app" / "static" / "rozetler"

MONDAY = datetime(2026, 9, 14, 10, 0)
FRIDAY = datetime(2026, 9, 18, 14, 30)


def campaign(context, target=1000, now=MONDAY, starts_at=None, ends_at="2026-12-31"):
    conn = context.connection()
    made = store.create_campaign(
        conn, "Sonbahar Seferi", ends_at, target,
        starts_at=starts_at, today=now.date().isoformat(),
    )
    gamify.ensure_rules(context)
    return made


def earned(context):
    made = store.active_campaign(context.connection())
    return set(store.earned_badges(context.connection(), made["id"]))


def add(context, kind, count, source=gamify.SOURCE_TASK, prefix="x", points=1, at=None):
    """Deftere dolgu olay yazar; rozet kosullarinin okudugu sey budur."""
    made = store.active_campaign(context.connection())
    for index in range(count):
        store.add_event(
            context.connection(), made["id"], source, kind, points,
            f"{prefix}{index}", "dolgu", at=at,
        )


def tile(context, code, now=MONDAY):
    made = store.active_campaign(context.connection())
    wall = gamify.badge_wall(context, made, now)
    return next(item for item in wall if item["code"] == code)


# --- katalog butunlugu ---------------------------------------------------


def test_the_catalog_is_big_and_varied():
    assert len(gamify.BADGES) >= 32
    codes = [badge["code"] for badge in gamify.BADGES]
    labels = [badge["label"] for badge in gamify.BADGES]
    assert len(set(codes)) == len(codes), "rozet kimlikleri benzersiz olmali"
    assert len(set(labels)) == len(labels), "rozet adlari benzersiz olmali"
    # Her kategoride en az iki rozet var: sekmeler bos kalmaz.
    for category in gamify.BADGE_CATEGORIES:
        mine = [badge for badge in gamify.BADGES if badge["category"] == category["code"]]
        assert len(mine) >= 2, category["code"]
    # Her nadirlik gercekten kullaniliyor.
    for rarity in gamify.RARITIES:
        assert any(badge["rarity"] == rarity["code"] for badge in gamify.BADGES)


def test_every_badge_has_a_rule_a_category_and_a_rarity():
    kurali_olan = (
        set(gamify.SERIES_BADGES) | set(gamify.GAUGE_BADGES)
        | set(gamify.FLAG_BADGES) | set(gamify.RANK_BADGES)
    )
    for badge in gamify.BADGES:
        assert badge["code"] in kurali_olan, f"{badge['code']} kuralsız"
        assert badge["category"] in gamify.CATEGORY_LABELS
        assert badge["rarity"] in gamify.RARITY_LABELS
        assert badge["hint"] and badge["label"]


def test_every_badge_has_its_own_line_drawn_icon():
    """Her rozetin ayri, 24x24, cizgi tabanli SVG'si var; emoji yok."""
    seen: set[str] = set()
    for badge in gamify.BADGES:
        path = ICONS / f"{badge['code']}.svg"
        assert path.exists(), f"{badge['code']} simgesi yok"
        text = path.read_text(encoding="utf-8")
        assert 'viewBox="0 0 24 24"' in text
        assert "stroke" in text
        # Nadirlik halkanin rengini belirler.
        assert rozet_simgeleri.HALKA_RENGI[badge["rarity"]] in text
        assert not re.search(r"[\U0001F300-\U0001FAFF☀-➿]", text), "emoji yok"
        assert text not in seen, f"{badge['code']} baska bir rozetle ayni cizim"
        seen.add(text)


# --- kategori kategori kazanma -------------------------------------------


def test_task_badges_follow_closed_tasks(context):
    campaign(context)
    add(context, gamify.KIND_DONE, 9)
    gamify.evaluate(context, MONDAY)
    assert gamify.BADGE_FIRST_TASK in earned(context)
    assert gamify.BADGE_TASK_10 not in earned(context)
    assert tile(context, gamify.BADGE_TASK_10)["progress"] == 9

    add(context, gamify.KIND_DONE, 1, prefix="y")
    gamify.evaluate(context, MONDAY)
    assert gamify.BADGE_TASK_10 in earned(context)


def test_jira_badges_follow_records_leaving_a_fleet(context):
    campaign(context)
    add(context, gamify.KIND_LEFT_GROUP, 25, source=gamify.SOURCE_JIRA, prefix="1:A-")
    gamify.evaluate(context, MONDAY)
    assert gamify.BADGE_DROP_25 in earned(context)
    assert gamify.BADGE_DROP_100 not in earned(context)


def test_a_day_with_three_projects_earns_its_badge(context):
    campaign(context)
    made = store.active_campaign(context.connection())
    for key in ("ABC-1", "DEF-2", "GHI-3"):
        store.add_event(
            context.connection(), made["id"], gamify.SOURCE_JIRA, gamify.KIND_STATUS_DONE,
            20, f"status:{key}", key, at=gamify.stamp_of(MONDAY),
        )
    gamify.evaluate(context, MONDAY)
    assert gamify.BADGE_THREE_PROJECTS in earned(context)


def test_streak_badges_climb_with_the_streak(context):
    made = campaign(context, now=datetime(2026, 8, 3, 10, 0), starts_at="2026-08-03")
    conn = context.connection()
    day = datetime(2026, 8, 3).date()
    marked = 0
    while marked < 30:
        if gamify.is_workday(day):
            store.mark_day(conn, made["id"], day.isoformat())
            marked += 1
        day += timedelta(days=1)
    gamify.evaluate(context, datetime(2026, 9, 11, 12, 0))
    assert gamify.BADGE_STREAK_5 in earned(context)
    assert gamify.BADGE_STREAK_30 in earned(context)
    assert gamify.BADGE_STREAK_60 not in earned(context)


def test_the_shield_badge_needs_a_rescued_day(context):
    made = campaign(context, now=MONDAY)
    conn = context.connection()
    store.mark_day(conn, made["id"], "2026-09-14")
    gamify.evaluate(context, MONDAY)
    assert gamify.BADGE_GRACE_SAVED not in earned(context)

    store.mark_day(conn, made["id"], "2026-09-15", store.STREAK_GRACE)
    gamify.evaluate(context, MONDAY)
    assert gamify.BADGE_GRACE_SAVED in earned(context)


def test_the_first_order_badge_follows_a_finished_quest(context):
    campaign(context)
    add(context, gamify.KIND_QUEST_DONE, 1, source=gamify.SOURCE_QUEST, prefix="q", points=25)
    gamify.evaluate(context, MONDAY)
    assert gamify.BADGE_FIRST_QUEST in earned(context)


def test_a_monday_sweep_of_every_order_earns_its_badge(context):
    made = campaign(context)
    conn = context.connection()
    for code in (gamify.QUEST_OVERDUE, gamify.QUEST_MAIL):
        quest = store.add_quest(conn, made["id"], "2026-09-14", code, code, 1, 25)
        store.set_quest_progress(conn, quest["id"], 1, done_at=gamify.stamp_of(MONDAY))
    gamify.evaluate(context, MONDAY)
    assert gamify.BADGE_QUEST_MONDAY in earned(context)


def test_xp_and_rank_badges_follow_the_total(context):
    campaign(context, target=1000)
    add(context, gamify.KIND_DONE, 10, points=100)
    gamify.evaluate(context, MONDAY)
    assert gamify.BADGE_XP_1000 in earned(context)
    # Hedefin %25'i Sovalye, %60'i Usta.
    assert gamify.BADGE_RANK_KNIGHT in earned(context)
    assert gamify.BADGE_RANK_LEGEND not in earned(context)


def test_a_big_day_earns_the_three_hundred_badge(context):
    campaign(context, target=100000)
    add(context, gamify.KIND_DONE, 3, points=100, at=gamify.stamp_of(MONDAY))
    gamify.evaluate(context, MONDAY)
    assert gamify.BADGE_XP_DAY_300 in earned(context)


def test_ritual_badges_read_the_clock(context):
    campaign(context)
    dawn = gamify.stamp_of(datetime(2026, 9, 14, 7, 10))
    add(context, gamify.KIND_DONE, 10, prefix="dawn", at=dawn)
    gamify.evaluate(context, MONDAY)
    assert gamify.BADGE_DAWN_10 in earned(context)
    assert gamify.BADGE_FRIDAY_5 not in earned(context)

    friday = gamify.stamp_of(FRIDAY)
    add(context, gamify.KIND_DONE, 5, prefix="fri", at=friday)
    gamify.evaluate(context, FRIDAY)
    assert gamify.BADGE_FRIDAY_5 in earned(context)


def test_contact_badges_count_the_address_book(context, conn):
    campaign(context)
    for index in range(20):
        repo.upsert_contact(conn, f"kisi{index}@example.com", f"Kişi {index}")
    gamify.evaluate(context, MONDAY)
    assert gamify.BADGE_CONTACTS_20 in earned(context)


def test_tool_badges_read_the_activity_log_and_the_fleets(context, conn):
    campaign(context)
    for index in range(5):
        repo.create_group(conn, f"Filo {index}", repo.KIND_FILTER, jql="project = DEMO")
    store.log_activity(conn, store.ACTIVITY_FIX, "bir", at=gamify.stamp_of(MONDAY))
    store.log_activity(conn, store.ACTIVITY_EXCEL, "tasks", at=gamify.stamp_of(MONDAY))
    gamify.evaluate(context, MONDAY)

    won = earned(context)
    assert gamify.BADGE_FIRST_FLEET in won and gamify.BADGE_FLEET_5 in won
    assert gamify.BADGE_FIRST_FIX in won and gamify.BADGE_FIRST_EXCEL in won
    assert gamify.BADGE_FIX_25 not in won
    assert tile(context, gamify.BADGE_FIX_25)["progress"] == 1


def test_every_category_can_be_earned(context, conn):
    """Katalogdaki her kategoriden en az bir rozet gercekten kazanilabiliyor."""
    campaign(context, target=200)
    add(context, gamify.KIND_DONE, 10, points=10,
        at=gamify.stamp_of(datetime(2026, 9, 14, 7, 10)))
    add(context, gamify.KIND_LEFT_GROUP, 25, source=gamify.SOURCE_JIRA, prefix="1:A-")
    add(context, gamify.KIND_QUEST_DONE, 1, source=gamify.SOURCE_QUEST, prefix="q")
    made = store.active_campaign(conn)
    for index in range(5):
        store.mark_day(conn, made["id"], f"2026-09-{7 + index:02d}")
    for index in range(20):
        repo.upsert_contact(conn, f"kisi{index}@example.com", f"Kişi {index}")
    repo.create_group(conn, "Filo", repo.KIND_FILTER, jql="project = DEMO")
    store.log_activity(conn, store.ACTIVITY_FIX, "bir", at=gamify.stamp_of(MONDAY))
    gamify.evaluate(context, MONDAY)

    won = earned(context)
    kazanilan = {gamify.BADGES_BY_CODE[code]["category"] for code in won}
    assert kazanilan == {item["code"] for item in gamify.BADGE_CATEGORIES}


# --- geriye donuk degerlendirme ------------------------------------------


def test_old_history_earns_its_badges_with_the_real_date(context):
    """Sefer basindan beri biriken gecmis, panel ilk acildiginda odenir."""
    campaign(context, starts_at="2026-08-03", now=MONDAY)
    stamps = [
        gamify.stamp_of(datetime(2026, 8, 3, 9, 0) + timedelta(days=index))
        for index in range(10)
    ]
    made = store.active_campaign(context.connection())
    for index, stamp in enumerate(stamps):
        store.add_event(
            context.connection(), made["id"], gamify.SOURCE_TASK, gamify.KIND_DONE,
            10, f"task:{index}", "Eski iş", at=stamp,
        )

    gamify.evaluate(context, MONDAY)

    won = store.earned_badges(context.connection(), made["id"])
    assert gamify.BADGE_FIRST_TASK in won and gamify.BADGE_TASK_10 in won
    # Kazanma tarihi BUGUN degil, esigi dolduran olayin gunu.
    assert won[gamify.BADGE_FIRST_TASK] == stamps[0]
    assert won[gamify.BADGE_TASK_10] == stamps[9]
    # Defter satiri da o tarihte durur.
    events = store.list_events(context.connection(), made["id"])
    line = next(item for item in events if item["ref"] == f"badge:{gamify.BADGE_TASK_10}")
    assert line["at"] == stamps[9]


def test_a_flag_badge_without_a_history_lands_on_today(context, conn):
    campaign(context, starts_at="2026-08-03", now=MONDAY)
    for index in range(5):
        repo.create_group(conn, f"Filo {index}", repo.KIND_FILTER, jql="project = DEMO")
    gamify.evaluate(context, MONDAY)
    made = store.active_campaign(conn)
    won = store.earned_badges(conn, made["id"])
    assert won[gamify.BADGE_FLEET_5][:10] == "2026-09-14"


# --- puan, duvar ve panel -------------------------------------------------


def test_rarity_scales_what_a_badge_pays(context):
    """Efsanevi rozet yaygin rozetin dort kati oder."""
    campaign(context, target=1000000)
    add(context, gamify.KIND_DONE, 200)
    gamify.evaluate(context, MONDAY)

    events = {item["ref"]: item["points"] for item in store.list_events(
        context.connection(), store.active_campaign(context.connection())["id"], limit=500
    )}
    assert events[f"badge:{gamify.BADGE_FIRST_TASK}"] == 10   # yaygin
    assert events[f"badge:{gamify.BADGE_CLOSER}"] == 20       # nadir
    assert events[f"badge:{gamify.BADGE_TASK_200}"] == 40     # efsanevi


def test_a_locked_tile_says_how_to_earn_it(context):
    campaign(context)
    add(context, gamify.KIND_DONE, 12)
    gamify.evaluate(context, MONDAY)

    locked = tile(context, gamify.BADGE_CLOSER)
    assert locked["earned"] is False
    assert locked["progress"] == 12 and locked["target"] == 25
    assert locked["percent"] == 48 and locked["show_progress"] is True
    assert locked["hint"] and locked["rarity_label"] == "Nadir"
    assert locked["category_label"] == "Görev"

    won = tile(context, gamify.BADGE_FIRST_TASK)
    assert won["earned"] is True and won["earned_at"] and won["percent"] == 100


def test_the_panel_ships_the_categories_and_rarities(context):
    campaign(context)
    data = gamify.panel(context, MONDAY)
    assert len(data["badges"]) == len(gamify.BADGES)
    assert [item["code"] for item in data["badge_categories"]] == [
        item["code"] for item in gamify.BADGE_CATEGORIES
    ]
    assert [item["label"] for item in data["rarities"]] == ["Yaygın", "Nadir", "Efsanevi"]


# --- uclar ve arayuz ------------------------------------------------------


def test_exporting_to_excel_leaves_a_trace_for_the_badge(api_client, context):
    assert api_client.get("/api/tasks/export.xlsx").status_code == 200
    assert store.activity_stamps(context.connection(), store.ACTIVITY_EXCEL)


def test_the_badge_wall_screen_has_tabs_and_progress(api_client):
    page = api_client.get("/").text
    assert 'id="badge-tabs"' in page and 'id="badge-score"' in page

    script = api_client.get("/static/js/campaign.js").text
    for marker in ("renderBadgeTabs", "badge-group-head", "badgeTile", "badge.icon"):
        assert marker in script, marker

    css = api_client.get("/static/css/app.css").text
    for marker in (".badge-fill", ".badge-rarity", ".badge-group-head",
                   ".badge-art img.badge-svg", ".badge-tile.locked"):
        assert marker in css, marker


def test_the_icons_are_served(api_client):
    answer = api_client.get(f"/static/rozetler/{gamify.BADGE_FIRST_TASK}.svg")
    assert answer.status_code == 200
    assert "svg" in answer.headers["content-type"]


# --- demo tohumu ----------------------------------------------------------


def test_the_demo_seed_fills_the_wall_without_finishing_it(context, conn):
    from tools.demo import tohum

    tohum.seferi_kur(context, datetime(2026, 9, 14, 12, 0))
    made = store.active_campaign(conn)
    won = store.earned_badges(conn, made["id"])
    assert len(won) >= 10

    wall = gamify.badge_wall(context, made, datetime(2026, 9, 14, 12, 0))
    yarida = [
        item for item in wall
        if not item["earned"] and item["show_progress"] and 0 < item["percent"] < 100
    ]
    assert len(yarida) >= 3, "birkaç rozet ilerleme çubuğuyla görünmeli"
