"""Sefer motoru: XP kurallari, rutbeler, rozetler, haftalik emirler, seri.

Uc temel karar burada verilir:

1. **Ne zaman puan verilir?** Yalnizca *aktif* bir sefer varken. Bitis tarihi
   gecmis sefer once kapatilir (`ensure_campaign`), ondan sonra hicbir olay
   puan uretmez: "tarih gecince XP sifirlanir" kurali budur. Sayilar silinmez,
   biten seferin defteri ve ozeti durur; yeni sefer sifirdan baslar.

2. **Ayni olay iki kez puan verir mi?** Hayir. Tekillik `xp_events` uzerindeki
   (sefer, tur, referans) indeksinde; motor yalnizca referansi dogru kurar.
   Gorev "Yapildi"ya bir daha tasinirsa referans ayni kalir, ikinci puan yok.

3. **Gercek veriden ne olculur?** Haftalik emirler ve rozetler panonun ve
   gruplarin ANLIK durumundan olculur; ayri bir sayac tutulmaz. Boylece sayac
   ile ekran arasi tutarsizlik olusamaz.

Modul saftir: HTTP bilmez, `AppContext` uzerinden baglanti ve ayar okur.
Zaman disaridan verilebilir (`now`), testler kendi gununu enjekte eder.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Sequence

from . import fields as field_utils, repository, tasks as task_utils
from . import gamify_repo as store

# --- kaynaklar ve turler ------------------------------------------------

SOURCE_TASK = "task"
SOURCE_JIRA = "jira"
SOURCE_MAIL = "mail"
SOURCE_TEAMS = "teams"
SOURCE_STREAK = "streak"
SOURCE_QUEST = "quest"
SOURCE_BADGE = "badge"

SOURCES: tuple[str, ...] = (
    SOURCE_TASK,
    SOURCE_JIRA,
    SOURCE_MAIL,
    SOURCE_TEAMS,
    SOURCE_STREAK,
    SOURCE_QUEST,
    SOURCE_BADGE,
)

SOURCE_LABELS: dict[str, str] = {
    SOURCE_TASK: "Görev",
    SOURCE_JIRA: "Jira",
    SOURCE_MAIL: "E-posta",
    SOURCE_TEAMS: "Teams",
    SOURCE_STREAK: "Seri",
    SOURCE_QUEST: "Emir",
    SOURCE_BADGE: "Rozet",
}

# Olay turleri. Tekillik indeksi tur + referans uzerinde oldugu icin tur adlari
# kaynaklar arasinda da benzersizdir.
KIND_DONE = "done"
KIND_DONE_BEFORE_DUE = "done_before_due"
KIND_DONE_OVERDUE = "done_overdue"
KIND_MAIL_FAST = "mail_fast"
KIND_LEFT_GROUP = "left_filter_group"
KIND_STATUS_DONE = "status_done"
KIND_STATUS_PROGRESS = "status_progress"
KIND_STATUS_ASKED = "status_asked"
KIND_STREAK_DAY = "day"
KIND_QUEST_DONE = "quest_done"
KIND_BADGE_EARNED = "badge_earned"

# Kural tohumu: Ayarlar -> "Sefer" kartindan puanlar degistirilebilir.
SEED_RULES: tuple[dict[str, Any], ...] = (
    {"source": SOURCE_TASK, "kind": KIND_DONE, "points": 10, "params_json": None},
    {"source": SOURCE_TASK, "kind": KIND_DONE_BEFORE_DUE, "points": 5, "params_json": None},
    {"source": SOURCE_TASK, "kind": KIND_DONE_OVERDUE, "points": 5, "params_json": None},
    {"source": SOURCE_MAIL, "kind": KIND_MAIL_FAST, "points": 5, "params_json": None},
    # params_json.group_points: {"<grup id>": puan} -- grup basina puan.
    {"source": SOURCE_JIRA, "kind": KIND_LEFT_GROUP, "points": 15, "params_json": "{}"},
    {"source": SOURCE_JIRA, "kind": KIND_STATUS_DONE, "points": 20, "params_json": None},
    {"source": SOURCE_JIRA, "kind": KIND_STATUS_PROGRESS, "points": 5, "params_json": None},
    {"source": SOURCE_TEAMS, "kind": KIND_STATUS_ASKED, "points": 2,
     "params_json": '{"daily_limit": 3}'},
    {"source": SOURCE_STREAK, "kind": KIND_STREAK_DAY, "points": 3, "params_json": None},
    {"source": SOURCE_QUEST, "kind": KIND_QUEST_DONE, "points": 25, "params_json": None},
    {"source": SOURCE_BADGE, "kind": KIND_BADGE_EARNED, "points": 10, "params_json": None},
)

RULE_LABELS: dict[str, str] = {
    KIND_DONE: "Görev kapatıldı",
    KIND_DONE_BEFORE_DUE: "Son tarihinden önce kapatıldı (ek puan)",
    KIND_DONE_OVERDUE: "Gecikmiş görev kapatıldı",
    KIND_MAIL_FAST: "E-posta görevi 24 saat içinde ele alındı",
    KIND_LEFT_GROUP: "Kayıt filtre filosundan düştü",
    KIND_STATUS_DONE: "Jira kaydı tamamlandı",
    KIND_STATUS_PROGRESS: "Jira kaydı işleme alındı",
    KIND_STATUS_ASKED: "Son durum soruldu (günde en çok 3)",
    KIND_STREAK_DAY: "Seri: etkin iş günü",
    KIND_QUEST_DONE: "Haftalık emir tamamlandı",
    KIND_BADGE_EARNED: "Rozet kazanıldı",
}

# --- rutbeler -----------------------------------------------------------
#
# Esik sefer HEDEFINE gore hesaplanir: 1000 XP hedefli seferde Usta 600'de,
# 400 XP hedefli seferde 240'ta baslar. Boylece kisa sefer de tirmanma
# duygusu verir. Telifli karakter adi kullanilmaz; rutbeler genel adlardir.

RANKS: tuple[dict[str, Any], ...] = (
    {"code": "padawan", "label": "Padawan", "at": 0.0, "image": "rank-padawan.png"},
    {"code": "knight", "label": "Şövalye", "at": 0.25, "image": "rank-knight.png"},
    {"code": "master", "label": "Usta", "at": 0.60, "image": "rank-master.png"},
    {"code": "council", "label": "Konsey Üyesi", "at": 1.0, "image": "rank-council.png"},
    {"code": "legend", "label": "Efsane", "at": 1.5, "image": "rank-legend.png"},
)

# Gorseller koordinator tarafindan uretilip buraya konur; dosya yoksa arayuz
# kendi cizdigi SVG hologrami gosterir (ekran hicbir zaman bos kalmaz).
IMAGE_DIR = "/static/img/gamify/"

# --- rozetler -----------------------------------------------------------

BADGE_CLEAN_DESK = "clean_desk"
BADGE_FAST_REPLY = "fast_reply"
BADGE_CLOSER = "closer"
BADGE_FINISHER = "finisher"
BADGE_STREAK_5 = "streak_5"
BADGE_STREAK_20 = "streak_20"
BADGE_STREAK_60 = "streak_60"
BADGE_CARTOGRAPHER = "cartographer"
BADGE_ARCHIVIST = "archivist"
BADGE_ENVOY = "envoy"
BADGE_COMPLETE = "campaign_complete"

BADGES: tuple[dict[str, Any], ...] = (
    {"code": BADGE_CLEAN_DESK, "label": "Temiz Masa", "hint": "7 gün boyunca gecikmiş görev yok"},
    {"code": BADGE_FAST_REPLY, "label": "Hızlı Yanıt", "hint": "10 e-posta görevi 24 saat içinde ele alındı"},
    {"code": BADGE_CLOSER, "label": "Kapatıcı", "hint": "Bir seferde 25 görev kapatıldı"},
    {"code": BADGE_FINISHER, "label": "Bitirici", "hint": "Bir filtre filosundan 20 kayıt düştü"},
    {"code": BADGE_STREAK_5, "label": "Beş Gün", "hint": "5 iş günü kesintisiz seri"},
    {"code": BADGE_STREAK_20, "label": "Yirmi Gün", "hint": "20 iş günü kesintisiz seri"},
    {"code": BADGE_STREAK_60, "label": "Altmış Gün", "hint": "60 iş günü kesintisiz seri"},
    {"code": BADGE_CARTOGRAPHER, "label": "Haritacı", "hint": "Bütün filolarda sütun düzeni tanımlı"},
    {"code": BADGE_ARCHIVIST, "label": "Arşivci", "hint": "30 günden eski 20 tamamlanmış görev katlandı"},
    {"code": BADGE_ENVOY, "label": "Elçi", "hint": "20 kez son durum soruldu"},
    {"code": BADGE_COMPLETE, "label": "Sefer Tamam", "hint": "Sefer hedefine ulaşıldı"},
)

BADGE_CODES: tuple[str, ...] = tuple(badge["code"] for badge in BADGES)

# --- haftalik emirler ---------------------------------------------------

QUEST_OVERDUE = "overdue"
QUEST_DOING_LIMIT = "doing_limit"
QUEST_STALE_ASK = "stale_ask"
QUEST_MAIL = "mail_tasks"

# "Yapiliyor" sutununun saglikli ust siniri.
DOING_LIMIT = 5
# Filtre gruplarinda "bayatlamis" sayilma esigi (gun).
STALE_DAYS = 14
# Bir haftada en fazla bu kadar emir verilir.
QUEST_COUNT = 3
# "Son durumunu sor" emri kac kayitla sinirlanir (hafta icinde bitebilsin).
STALE_ASK_CAP = 5
# Tek Guncelle'de bir filtre filosundan en fazla kac dusus puanlanir.
# JQL degisiminde toplu dusus gercek is degildir; tavan onu kesip atar.
DROP_CAP = 20

# --- ayar anahtarlari ---------------------------------------------------

SETTING_GRACE_MONTH = "gamify.streak_grace_used_month"
SETTING_CLEAN_SINCE = "gamify.clean_since"
SETTING_DIGEST_WEEK = "gamify.digest_seen_week"

CLEAN_DESK_DAYS = 7


# --- zaman --------------------------------------------------------------


def moment_of(now: datetime | None = None) -> datetime:
    """Yerel saat; testler kendi anini verir."""
    if isinstance(now, datetime):
        return now
    return datetime.now()


def stamp_of(now: datetime | None = None) -> str:
    """Deftere yazilan an: her yerde oldugu gibi UTC ISO."""
    if now is None:
        return repository.now_iso()
    aware = now if now.tzinfo else now.astimezone()
    return aware.astimezone(timezone.utc).replace(microsecond=0).isoformat()


def today_of(now: datetime | None = None) -> str:
    return moment_of(now).date().isoformat()


def local_day(value: Any) -> str:
    """Saklanmis bir zaman damgasinin YEREL gunu (defterde UTC durur)."""
    parsed = field_utils.parse_moment(value)
    if parsed is None:
        return ""
    return parsed.astimezone().date().isoformat()


def parse_day(text: Any) -> date | None:
    try:
        return date.fromisoformat(str(text or "")[:10])
    except ValueError:
        return None


def is_workday(day: date) -> bool:
    return day.weekday() < 5


def previous_workday(day: date) -> date:
    cursor = day - timedelta(days=1)
    while not is_workday(cursor):
        cursor -= timedelta(days=1)
    return cursor


def week_start_of(day: date) -> str:
    """Haftanin pazartesisi (ISO)."""
    return (day - timedelta(days=day.weekday())).isoformat()


def days_between(first: str, last: str) -> int:
    start, end = parse_day(first), parse_day(last)
    if start is None or end is None:
        return 0
    return (end - start).days


# --- sefer yasam dongusu ------------------------------------------------


def ensure_rules(context: Any) -> int:
    """Eksik kurallari tamamlar (acilista bir kez)."""
    return store.seed_rules(context.connection(), SEED_RULES)


def ensure_campaign(context: Any, now: datetime | None = None) -> dict[str, Any] | None:
    """Aktif seferi dondurur; bitis tarihi gectiyse once kapatir.

    Uygulama acilisinda, Guncelle sonunda ve panel her acildiginda cagrilir:
    kullanici uygulamayi gunlerce acmasa da sefer dogru gunde kapanmis gorunur.
    """
    conn = context.connection()
    campaign = store.active_campaign(conn)
    if campaign is None:
        return None
    if campaign["ends_at"] and today_of(now) > campaign["ends_at"]:
        summary = build_summary(context, campaign, now)
        store.end_campaign(conn, campaign["id"], summary)
        context.settings.set(SETTING_CLEAN_SINCE, "")
        return None
    return campaign


def start_campaign(
    context: Any, name: Any, ends_at: Any, target_xp: Any = 1000, now: datetime | None = None
) -> dict[str, Any]:
    """Yeni sefer. Suresi dolmus sefer varsa once kapanir."""
    ensure_campaign(context, now)
    campaign = store.create_campaign(
        context.connection(), name, ends_at, target_xp, today=today_of(now)
    )
    # "Kac gundur gecikmis gorev yok" sayaci sefere aittir: yeni sefer eski
    # seferin temizligini devralmaz.
    context.settings.set(SETTING_CLEAN_SINCE, "")
    evaluate(context, now)
    return store.get_campaign(context.connection(), campaign["id"]) or campaign


def end_campaign(context: Any, now: datetime | None = None) -> dict[str, Any] | None:
    """Elle bitirme: ozet yazilir, defter sefere bagli kalir."""
    conn = context.connection()
    campaign = store.active_campaign(conn)
    if campaign is None:
        return None
    ended = store.end_campaign(conn, campaign["id"], build_summary(context, campaign, now))
    context.settings.set(SETTING_CLEAN_SINCE, "")
    return ended


def build_summary(
    context: Any, campaign: dict[str, Any], now: datetime | None = None
) -> dict[str, Any]:
    """Biten seferin donuk fotografi: toplam, rutbe, kaynak dagilimi, rozet, seri."""
    conn = context.connection()
    total = store.total_xp(conn, campaign["id"])
    rank = rank_of(total, campaign["target_xp"])
    earned = store.earned_badges(conn, campaign["id"])
    return {
        "name": campaign["name"],
        "starts_at": campaign["starts_at"],
        "ends_at": campaign["ends_at"],
        "target_xp": campaign["target_xp"],
        "total_xp": total,
        "percent": percent_of(total, campaign["target_xp"]),
        "rank": {"code": rank["code"], "label": rank["label"]},
        "sources": store.source_totals(conn, campaign["id"]),
        "badges": sorted(earned),
        "badge_count": len(earned),
        "streak": _best_streak(store.marked_days(conn, campaign["id"])),
        "quests_done": sum(
            1 for quest in store.list_quests(conn, campaign["id"]) if quest["done"]
        ),
        "events": store.count_events(conn, campaign["id"]),
    }


# --- rutbe hesabi -------------------------------------------------------


def percent_of(total: int, target: int) -> int:
    if target <= 0:
        return 0
    return max(0, round(total * 100 / target))


def rank_of(total: int, target: int) -> dict[str, Any]:
    """Toplam XP'nin dustugu rutbe (hedefe gore oransal)."""
    current = RANKS[0]
    for rank in RANKS:
        if target > 0 and total >= rank["at"] * target:
            current = rank
        elif target <= 0 and rank["at"] == 0:
            current = rank
    return current


def next_rank_of(total: int, target: int) -> dict[str, Any] | None:
    for rank in RANKS:
        if target > 0 and total < rank["at"] * target:
            return {
                "code": rank["code"],
                "label": rank["label"],
                "at": int(round(rank["at"] * target)),
                "remaining": int(round(rank["at"] * target)) - total,
            }
    return None


def rank_view(total: int, target: int) -> dict[str, Any]:
    rank = rank_of(total, target)
    return {
        "code": rank["code"],
        "label": rank["label"],
        "image": IMAGE_DIR + rank["image"],
        "at": int(round(rank["at"] * target)),
    }


# --- puan verme ---------------------------------------------------------


def _as_points(value: Any) -> int | None:
    """Kuraldaki grup puani gecersizse varsayilana dusulur (hata atilmaz)."""
    if value is None:
        return None
    try:
        points = int(value)
    except (TypeError, ValueError):
        return None
    return points if points >= 0 else None


def enabled_rule(conn: Any, kind: str) -> dict[str, Any] | None:
    """Kapali kural puan vermez; silinmis kural da yok sayilir."""
    rule = store.rules_map(conn).get(kind)
    if rule is None or not rule["enabled"]:
        return None
    return rule


def award(
    context: Any,
    source: str,
    kind: str,
    ref: str | None,
    title: str,
    note: str = "",
    points: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any] | None:
    """Tek bir defter satiri. Sefer yoksa, kural kapaliysa ya da olay zaten
    defterdeyse `None` doner."""
    campaign = ensure_campaign(context, now)
    if campaign is None:
        return None
    conn = context.connection()
    rule = enabled_rule(conn, kind)
    if rule is None:
        return None
    value = rule["points"] if points is None else int(points)
    if value <= 0:
        return None
    return store.add_event(
        conn, campaign["id"], source, kind, value, ref, title, note, at=stamp_of(now)
    )


# --- kancalar: gorevler -------------------------------------------------


def on_task_done(
    context: Any,
    before: dict[str, Any] | None,
    after: dict[str, Any],
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    """Gorev tasindi/guncellendi: kapatma puani ve "e-postaya hizli donus".

    `before` tasimadan onceki hali; yoksa yalnizca son durum bakilir. Ayni
    gorev icin kapatma puani bir kezdir (referans gorev kimligi).
    """
    awarded: list[dict[str, Any]] = []
    if not after:
        return awarded
    was_done = bool(before) and before.get("status") == repository.TASK_DONE
    is_done = after.get("status") == repository.TASK_DONE

    if is_done and not was_done:
        awarded.extend(_award_task_done(context, after, now))
    if after.get("status") in (repository.TASK_DOING, repository.TASK_DONE):
        fast = _award_mail_fast(context, after, now)
        if fast:
            awarded.append(fast)
    # Puan cikmasa bile degerlendirme calisir: gorev tasimak da "etkin gun"dur.
    # Hicbir alani degistirmeyen kayit (bos PUT) gun isaretlemez -- repository
    # degisiklik yoksa gorevi oldugu gibi geri dondurur.
    evaluate(context, now, touched=before is None or before != after)
    return [event for event in awarded if event]


# Bir gorevin kapanisi bu turlerden biriyle puanlanir; ikisi birden olmaz.
CLOSE_KINDS: tuple[str, ...] = (KIND_DONE, KIND_DONE_OVERDUE)


def _award_task_done(
    context: Any, task: dict[str, Any], now: datetime | None
) -> list[dict[str, Any]]:
    ref = f"task:{task['id']}"
    title = f"Görev kapatıldı: {task['title']}"
    # Tekillik indeksi (sefer, TUR, referans) uzerinde: ayni gorev once
    # "gecikmis" kapanip sonra son tarihi duzeltilip yeniden kapanirsa iki
    # AYRI tur olusur ve ikinci kez puan yazilirdi. Kapanis turlerinden biri
    # zaten defterdeyse hicbiri yeniden yazilmaz.
    if _already_scored(context, ref, CLOSE_KINDS):
        return []
    state = task_utils.due_state(task.get("due_date"), moment_of(now))
    events: list[dict[str, Any]] = []
    if state == task_utils.DUE_OVERDUE:
        # Gecikmis gorev de kapanmali; puan tam degil, yarim.
        event = award(
            context, SOURCE_TASK, KIND_DONE_OVERDUE, ref,
            f"{title} (gecikmiş)", now=now,
        )
    else:
        event = award(context, SOURCE_TASK, KIND_DONE, ref, title, now=now)
    if event:
        events.append(event)
    if state in (task_utils.DUE_TODAY, task_utils.DUE_SOON, task_utils.DUE_LATER):
        bonus = award(
            context, SOURCE_TASK, KIND_DONE_BEFORE_DUE, ref,
            f"Son tarihinden önce: {task['title']}", now=now,
        )
        if bonus:
            events.append(bonus)
    return events


def _already_scored(context: Any, ref: str, kinds: Sequence[str]) -> bool:
    """Bu referans bu turlerden biriyle daha once puanlandi mi."""
    campaign = store.active_campaign(context.connection())
    if campaign is None:
        return False
    placeholders = ",".join("?" for _ in kinds)
    row = context.connection().execute(
        f"SELECT COUNT(*) AS c FROM xp_events WHERE campaign_id = ? AND ref = ? "
        f"AND kind IN ({placeholders})",
        (campaign["id"], ref, *kinds),
    ).fetchone()
    return int(row["c"]) > 0


def _award_mail_fast(
    context: Any, task: dict[str, Any], now: datetime | None
) -> dict[str, Any] | None:
    """E-postadan gelen gorev 24 saat icinde ele alindiysa ek puan."""
    if task.get("source") != repository.TASK_SOURCE_MAIL:
        return None
    received = field_utils.parse_moment(task.get("mail_received_at"))
    if received is None:
        return None
    moment = moment_of(now)
    aware = moment if moment.tzinfo else moment.astimezone()
    if (aware - received) > timedelta(hours=24):
        return None
    return award(
        context, SOURCE_MAIL, KIND_MAIL_FAST, f"task:{task['id']}",
        f"E-postaya 24 saat içinde dönüldü: {task['title']}", now=now,
    )


# --- kancalar: Guncelle -------------------------------------------------


def on_refresh(context: Any, result: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    """Guncelle bitti: filtre grubundan dusen kayitlar ve durum gecisleri.

    Manuel gruptan elle cikarma buraya hic ugramaz; yalnizca JQL sonucundan
    dusen kayitlar puan uretir (`replace_filter_members` raporu).
    """
    campaign = ensure_campaign(context, now)
    if campaign is None:
        return {"points": 0, "events": 0}
    conn = context.connection()
    events: list[dict[str, Any]] = []

    params = (enabled_rule(conn, KIND_LEFT_GROUP) or {}).get("params") or {}
    group_points = params.get("group_points")
    if not isinstance(group_points, dict):
        group_points = {}
    for group_id, info in ((result or {}).get("filter_drops") or {}).items():
        override = _as_points(group_points.get(str(group_id)))
        # JQL'i daraltmak ya da sorgunun bir kez az sonuc dondurmesi yuzlerce
        # kaydi birden dusurebilir; bu "is bitti" demek degildir. Tek
        # Guncelle'de grup basina en fazla DROP_CAP kayit puanlanir.
        for key in (info.get("keys") or [])[:DROP_CAP]:
            event = award(
                context, SOURCE_JIRA, KIND_LEFT_GROUP, f"{group_id}:{key}",
                f"{key} '{info.get('name') or 'filo'}' filosundan düştü",
                points=override, now=now,
            )
            if event:
                events.append(event)

    for key, moves in ((result or {}).get("status_moves") or {}).items():
        event = _award_status_move(context, key, moves, now)
        if event:
            events.append(event)

    evaluate(context, now)
    return {"points": sum(event["points"] for event in events), "events": len(events)}


def _award_status_move(
    context: Any, key: str, move: dict[str, Any], now: datetime | None
) -> dict[str, Any] | None:
    """Kaydin durum KATEGORISI degistiyse puan; ayni gecis bir kez sayilir."""
    before = str(move.get("from") or "")
    after = str(move.get("to") or "")
    if after == before:
        return None
    if after == "done":
        return award(
            context, SOURCE_JIRA, KIND_STATUS_DONE, f"status:{key}",
            f"{key} tamamlandı", now=now,
        )
    if after == "indeterminate" and before == "new":
        return award(
            context, SOURCE_JIRA, KIND_STATUS_PROGRESS, f"progress:{key}",
            f"{key} işleme alındı", now=now,
        )
    return None


# --- kancalar: Teams ----------------------------------------------------


def on_status_asked(
    context: Any, issue_key: str, now: datetime | None = None
) -> dict[str, Any] | None:
    """Kayda "son durum" mesaji acildi. Gunde en fazla uc kez puan verir."""
    campaign = ensure_campaign(context, now)
    if campaign is None:
        return None
    conn = context.connection()
    rule = enabled_rule(conn, KIND_STATUS_ASKED)
    if rule is None:
        return None
    day = today_of(now)
    limit = _as_points((rule.get("params") or {}).get("daily_limit"))
    limit = 3 if limit is None else limit
    if limit > 0:
        today_events = [
            event
            for event in store.events_of_kind(conn, campaign["id"], KIND_STATUS_ASKED)
            if local_day(event["at"]) == day
        ]
        if len(today_events) >= limit:
            return None
    event = award(
        context, SOURCE_TEAMS, KIND_STATUS_ASKED, f"{day}:{issue_key}",
        f"{issue_key} için son durum soruldu", now=now,
    )
    evaluate(context, now, touched=True)
    return event


# --- degerlendirme: seri, emirler, rozetler -----------------------------


def evaluate(
    context: Any, now: datetime | None = None, touched: bool = False
) -> dict[str, Any]:
    """Her olaydan sonra ve panel acilisinda calisan tek degerlendirme.

    Sira onemli: once gun isareti (seri puani da bir XP olayidir), sonra
    emirler (biten emir puan verir), en sonda rozetler -- boylece "bu hafta
    kazanilan puan" rozet kosulunda dogru gorunur.

    `touched` bir kancadan geliyorsa gun kesin etkindir (gorev tasindi, durum
    soruldu): puan cikmasa bile seri devam eder. Panel acilisinda bu bilgi
    yoktur, o zaman gunun izine deftere ve pano zaman damgalarina bakilir.
    """
    campaign = ensure_campaign(context, now)
    if campaign is None:
        return {"campaign": None}
    _mark_streak(context, campaign, now, touched)
    quests = _sync_quests(context, campaign, now)
    badges = _check_badges(context, campaign, now)
    return {"campaign": campaign["id"], "quests": len(quests), "badges": badges}


def _touched_today(context: Any, campaign: dict[str, Any], day: str) -> bool:
    """O gun deftere bir olay dustu mu (panel acilisinda kullanilan yedek yol).

    Gorev hareketi kancadan `touched=True` ile gelir; burada `tasks.updated_at`
    OKUNMAZ: o sutunu posta taramasi ve Guncelle de yaziyor, kullanici hicbir
    sey yapmadan seri islenirdi.
    """
    rows = context.connection().execute(
        "SELECT at FROM xp_events WHERE campaign_id = ? ORDER BY id DESC LIMIT 200",
        (campaign["id"],),
    ).fetchall()
    return any(local_day(row["at"]) == day for row in rows)


def _mark_streak(
    context: Any, campaign: dict[str, Any], now: datetime | None, touched: bool = False
) -> None:
    """Bugunu isaretler, gerekiyorsa koruma harcar, seri puanini yazar."""
    conn = context.connection()
    day = parse_day(today_of(now))
    if day is None or not is_workday(day):
        return
    if not touched and not _touched_today(context, campaign, day.isoformat()):
        return
    if not store.mark_day(conn, campaign["id"], day.isoformat(), store.STREAK_ACTIVE):
        return
    _spend_grace(context, campaign, day)
    marks = store.marked_days(conn, campaign["id"])
    length = _streak_length(marks, day)
    award(
        context, SOURCE_STREAK, KIND_STREAK_DAY, f"day:{day.isoformat()}",
        f"Seri sürüyor: {length}. iş günü", now=now,
    )


def _spend_grace(context: Any, campaign: dict[str, Any], day: date) -> None:
    """Ayda bir "Güç koruması": kacirilan TEK is gununu affeder.

    Yalnizca kopukluk tam bir gunse ve o ay koruma harcanmamissa calisir;
    harcandigi ay ayarda isaretlenir.
    """
    conn = context.connection()
    settings = context.settings
    month = day.isoformat()[:7]
    if (settings.get(SETTING_GRACE_MONTH, "") or "") == month:
        return
    missed = previous_workday(day)
    marks = store.marked_days(conn, campaign["id"])
    if missed.isoformat() in marks:
        return
    before = previous_workday(missed)
    if before.isoformat() not in marks:
        return
    if campaign["starts_at"] and missed.isoformat() < campaign["starts_at"]:
        return
    store.mark_day(conn, campaign["id"], missed.isoformat(), store.STREAK_GRACE)
    settings.set(SETTING_GRACE_MONTH, month)


def _streak_length(marks: dict[str, str], day: date) -> int:
    """Bugunden (ya da son is gununden) geriye kesintisiz is gunu sayisi."""
    cursor = day
    if cursor.isoformat() not in marks:
        cursor = previous_workday(cursor)
    count = 0
    while cursor.isoformat() in marks and count < 500:
        count += 1
        cursor = previous_workday(cursor)
    return count


def _best_streak(marks: dict[str, str]) -> int:
    """Seferin en uzun kesintisiz serisi; ozet kartinda bu yazar."""
    best = 0
    for text in sorted(marks):
        day = parse_day(text)
        if day is None:
            continue
        best = max(best, _streak_length(marks, day))
    return best


def streak_state(
    context: Any, campaign: dict[str, Any], now: datetime | None = None
) -> dict[str, Any]:
    conn = context.connection()
    day = parse_day(today_of(now)) or date.today()
    marks = store.marked_days(conn, campaign["id"])
    month = day.isoformat()[:7]
    used = (context.settings.get(SETTING_GRACE_MONTH, "") or "") == month
    return {
        "days": _streak_length(marks, day),
        "today": marks.get(day.isoformat(), ""),
        "grace_used": used,
        "grace_month": month,
        "grace_text": "Güç koruması bu ay harcandı" if used else "Güç koruması hazır",
    }


# --- haftalik emirler ---------------------------------------------------


def _board_facts(context: Any, now: datetime | None) -> dict[str, Any]:
    """Emir ve rozet kosullarinin okudugu ANLIK pano durumu."""
    conn = context.connection()
    moment = moment_of(now)
    overdue = 0
    doing = 0
    mail_open = 0
    old_done = 0
    for task in repository.list_tasks(conn):
        if task["status"] != repository.TASK_DONE:
            if task_utils.due_state(task["due_date"], moment) == task_utils.DUE_OVERDUE:
                overdue += 1
            if task["status"] == repository.TASK_DOING:
                doing += 1
            if task["source"] == repository.TASK_SOURCE_MAIL:
                mail_open += 1
        elif task_utils.is_old_done(task, moment):
            old_done += 1
    return {"overdue": overdue, "doing": doing, "mail_open": mail_open, "old_done": old_done}


def stale_issues(context: Any, now: datetime | None = None) -> list[str]:
    """Filtre gruplarinda STALE_DAYS gundur guncellenmemis kayitlar."""
    conn = context.connection()
    moment = moment_of(now)
    aware = moment if moment.tzinfo else moment.astimezone()
    limit = aware - timedelta(days=STALE_DAYS)
    wanted: list[str] = []
    seen: set[str] = set()
    for group in repository.list_groups(conn):
        if group["kind"] != repository.KIND_FILTER:
            continue
        for key in repository.list_item_keys(conn, group["id"]):
            upper = key.upper()
            if upper not in seen:
                seen.add(upper)
                wanted.append(upper)
    stored = repository.get_issues(conn, wanted) if wanted else {}
    keys: list[str] = []
    for key in wanted:
        record = stored.get(key)
        if record is None:
            continue
        updated = field_utils.parse_moment(
            ((record.get("raw") or {}).get("fields") or {}).get("updated")
        )
        if updated is not None and updated < limit:
            keys.append(key)
    return keys


def _quest_candidates(context: Any, now: datetime | None) -> list[dict[str, Any]]:
    facts = _board_facts(context, now)
    picks: list[dict[str, Any]] = []
    if facts["overdue"]:
        picks.append(
            {
                "code": QUEST_OVERDUE,
                "title": f"{facts['overdue']} gecikmiş görevi kapat",
                "target": facts["overdue"],
            }
        )
    if facts["doing"] > DOING_LIMIT:
        picks.append(
            {
                "code": QUEST_DOING_LIMIT,
                "title": f"Yapılıyor'u {DOING_LIMIT}'in altına indir",
                "target": facts["doing"] - DOING_LIMIT,
            }
        )
    stale = stale_issues(context, now)
    if stale:
        wanted = min(len(stale), STALE_ASK_CAP)
        picks.append(
            {
                "code": QUEST_STALE_ASK,
                "title": f"{wanted} kaydın son durumunu sor",
                "target": wanted,
            }
        )
    if facts["mail_open"]:
        picks.append(
            {
                "code": QUEST_MAIL,
                "title": f"{facts['mail_open']} e-posta görevini ele al",
                "target": facts["mail_open"],
            }
        )
    return picks[:QUEST_COUNT]


def _quest_progress(
    context: Any, campaign: dict[str, Any], quest: dict[str, Any], now: datetime | None
) -> int:
    """Ilerleme sayacsiz olculur: hedef ile ANLIK durumun farki."""
    facts = _board_facts(context, now)
    code = quest["code"]
    if code == QUEST_OVERDUE:
        return max(0, quest["target"] - facts["overdue"])
    if code == QUEST_DOING_LIMIT:
        start = quest["target"] + DOING_LIMIT
        return max(0, start - facts["doing"])
    if code == QUEST_MAIL:
        return max(0, quest["target"] - facts["mail_open"])
    if code == QUEST_STALE_ASK:
        return min(quest["target"], _asked_stale_since(context, quest["week_start"], now))
    return quest["progress"]


def _asked_stale_since(context: Any, week_start: str, now: datetime | None) -> int:
    """Hafta icinde son durumu sorulan FARKLI *bayat* kayit sayisi.

    Emir "bayatlamis kayitlarin son durumunu sor" diyor; herhangi bes kayda
    mesaj atmak emri bitirmemeli. Sormak Jira'daki kaydi degistirmedigi icin
    kayit sorulduktan sonra da bayat listesinde kalir, kesisim dogru olcer.
    """
    rows = context.connection().execute(
        "SELECT DISTINCT issue_key, opened_at FROM sent_messages WHERE opened_at IS NOT NULL"
    ).fetchall()
    asked = {row["issue_key"] for row in rows if local_day(row["opened_at"]) >= week_start}
    return len(asked & set(stale_issues(context, now)))


def _sync_quests(
    context: Any, campaign: dict[str, Any], now: datetime | None
) -> list[dict[str, Any]]:
    """Haftanin emirlerini uretir (yoksa) ve ilerlemeyi olcer."""
    conn = context.connection()
    day = parse_day(today_of(now))
    if day is None:
        return []
    week = week_start_of(day)
    quests = store.list_quests(conn, campaign["id"], week)
    if not quests:
        points = (enabled_rule(conn, KIND_QUEST_DONE) or {}).get("points", 0)
        for candidate in _quest_candidates(context, now):
            store.add_quest(
                conn, campaign["id"], week, candidate["code"], candidate["title"],
                candidate["target"], int(points),
            )
        quests = store.list_quests(conn, campaign["id"], week)

    for quest in quests:
        progress = _quest_progress(context, campaign, quest, now)
        finished = progress >= quest["target"]
        if progress == quest["progress"] and (quest["done"] or not finished):
            continue
        store.set_quest_progress(
            conn, quest["id"], progress, stamp_of(now) if finished else None
        )
        if finished and not quest["done"]:
            award(
                context, SOURCE_QUEST, KIND_QUEST_DONE, f"quest:{week}:{quest['code']}",
                f"Emir tamamlandı: {quest['title']}",
                # Kartta yazan puan neyse o odenir; hafta ortasinda kural
                # degisse bile soz degismez.
                points=quest["points"] or None, now=now,
            )
    return store.list_quests(conn, campaign["id"], week)


# --- rozetler -----------------------------------------------------------


def _check_badges(context: Any, campaign: dict[str, Any], now: datetime | None) -> list[str]:
    conn = context.connection()
    earned = store.earned_badges(conn, campaign["id"])
    facts = badge_facts(context, campaign, now)
    fresh: list[str] = []
    for badge in BADGES:
        code = badge["code"]
        if code in earned or not _badge_met(code, facts):
            continue
        if store.earn_badge(conn, campaign["id"], code, stamp_of(now)):
            fresh.append(code)
            award(
                context, SOURCE_BADGE, KIND_BADGE_EARNED, f"badge:{code}",
                f"Rozet kazanıldı: {badge['label']}", now=now,
            )
    return fresh


def _badge_met(code: str, facts: dict[str, Any]) -> bool:
    if code == BADGE_CLEAN_DESK:
        return facts["clean_days"] >= CLEAN_DESK_DAYS
    if code == BADGE_FAST_REPLY:
        return facts["mail_fast"] >= 10
    if code == BADGE_CLOSER:
        return facts["tasks_done"] >= 25
    if code == BADGE_FINISHER:
        return facts["group_max"] >= 20
    if code == BADGE_STREAK_5:
        return facts["streak"] >= 5
    if code == BADGE_STREAK_20:
        return facts["streak"] >= 20
    if code == BADGE_STREAK_60:
        return facts["streak"] >= 60
    if code == BADGE_CARTOGRAPHER:
        return facts["all_columns"]
    if code == BADGE_ARCHIVIST:
        return facts["old_done"] >= 20
    if code == BADGE_ENVOY:
        return facts["asked"] >= 20
    if code == BADGE_COMPLETE:
        return facts["target_reached"]
    return False


def badge_facts(
    context: Any, campaign: dict[str, Any], now: datetime | None = None
) -> dict[str, Any]:
    """Rozet kosullarinin tamami tek okumada; her biri gercek veriden."""
    conn = context.connection()
    board = _board_facts(context, now)
    groups = repository.list_groups(conn)
    group_max = 0
    for group in groups:
        if group["kind"] != repository.KIND_FILTER:
            continue
        group_max = max(
            group_max,
            store.ref_prefix_count(conn, campaign["id"], KIND_LEFT_GROUP, f"{group['id']}:"),
        )
    asked = _asked_count(context, campaign)
    total = store.total_xp(conn, campaign["id"])
    return {
        "clean_days": _clean_days(context, board["overdue"], now),
        "mail_fast": store.count_events(conn, campaign["id"], (KIND_MAIL_FAST,)),
        "tasks_done": store.count_events(
            conn, campaign["id"], (KIND_DONE, KIND_DONE_OVERDUE)
        ),
        "group_max": group_max,
        "streak": _best_streak(store.marked_days(conn, campaign["id"])),
        "all_columns": bool(groups) and all(group["columns"] for group in groups),
        "old_done": board["old_done"],
        "asked": asked,
        "target_reached": campaign["target_xp"] > 0 and total >= campaign["target_xp"],
    }


def _asked_count(context: Any, campaign: dict[str, Any]) -> int:
    """Sefer basladiktan sonra kac kez son durum soruldu.

    Mesaj kaydi UTC damgali, sefer baslangici bir YEREL gun: karsilastirma
    `local_day` uzerinden yapilir, yoksa saat farki gun sinirinda yaniltir.
    Sayim mesaj kaydina bakar (puan olayina degil): gunluk tavan dolmus olsa
    da kullanici gercekten sormustur.
    """
    start = campaign["starts_at"] or ""
    rows = context.connection().execute(
        "SELECT opened_at FROM sent_messages WHERE opened_at IS NOT NULL"
    ).fetchall()
    return sum(1 for row in rows if local_day(row["opened_at"]) >= start)


def _clean_days(context: Any, overdue: int, now: datetime | None) -> int:
    """Kac gundur gecikmis gorev yok.

    Gunluk fotograf tutmak yerine "temizligin basladigi gun" ayarda saklanir:
    gecikmis gorev goruldugu anda sifirlanir, temiz gunlerde buyur.
    """
    settings = context.settings
    day = today_of(now)
    if overdue:
        if settings.get(SETTING_CLEAN_SINCE, ""):
            settings.set(SETTING_CLEAN_SINCE, "")
        return 0
    since = settings.get(SETTING_CLEAN_SINCE, "") or ""
    if not since:
        settings.set(SETTING_CLEAN_SINCE, day)
        return 1
    return max(1, days_between(since, day) + 1)


# --- silme ve yeniden degerlendirme --------------------------------------


def delete_history(context: Any, campaign_id: Any) -> dict[str, Any]:
    """Biten bir seferi defteri ve rozetleriyle birlikte siler."""
    return store.delete_campaign(context.connection(), campaign_id)


def delete_event(context: Any, event_id: Any, now: datetime | None = None) -> dict[str, Any]:
    """Defterden bir satir siler ve seferi bastan degerlendirir.

    Silmek satiri goturur, dogrudan bagli kayitlari temizler (o gunun seri
    isareti, rozet) ve ardindan kosulu artik saglanmayan rozetleri puanlariyla
    birlikte geri alir; toplam dustugu icin rutbe de geriye gidebilir.

    Iz birakilmaz: ayni olay ileride yeniden gerceklesirse (kayit tekrar
    filodan duser, gorev yeniden kapanir, gun yeniden etkin olur) puan normal
    sekilde yeniden yazilir. Silmek "bu olay hic olmadi" demektir, "bir daha
    sayma" demek degil.
    """
    conn = context.connection()
    campaign = ensure_campaign(context, now)
    if campaign is None:
        raise repository.RepositoryError(
            "campaign_missing", "Süren bir sefer yok.", status=404
        )
    event = store.get_event(conn, event_id)
    if event is None or event["campaign_id"] != campaign["id"]:
        raise repository.RepositoryError(
            "event_not_found", "Defter satırı bulunamadı.", status=404
        )

    store.delete_event(conn, event["id"])

    # Olayin dogrudan izleri: seri gunu ve rozet satiri.
    if event["kind"] == KIND_STREAK_DAY and event["ref"].startswith("day:"):
        store.clear_day(conn, campaign["id"], event["ref"][4:])
    if event["kind"] == KIND_BADGE_EARNED and event["ref"].startswith("badge:"):
        store.revoke_badge(conn, campaign["id"], event["ref"][6:])

    revoked = reevaluate(context, campaign, now)
    return {"event": event, "revoked": revoked}


def reevaluate(
    context: Any, campaign: dict[str, Any], now: datetime | None = None
) -> list[str]:
    """Kosulu artik saglanmayan rozetleri (ve puanlarini) geri alir.

    Bir rozetin dusmesi toplami dusurur, o da baska bir rozeti dusurebilir
    ("Sefer Tamam" hedefe bagli); bu yuzden durulana kadar donulur.
    """
    conn = context.connection()
    revoked: list[str] = []
    for _ in range(len(BADGES) + 1):
        facts = badge_facts(context, campaign, now)
        earned = store.earned_badges(conn, campaign["id"])
        gone = [code for code in earned if not _badge_met(code, facts)]
        if not gone:
            break
        for code in gone:
            store.revoke_badge(conn, campaign["id"], code)
            store.delete_events_by_ref(
                conn, campaign["id"], KIND_BADGE_EARNED, f"badge:{code}"
            )
            revoked.append(code)
    return revoked


# --- panel --------------------------------------------------------------


def _events_from(conn: Any, campaign: dict[str, Any], first_day: str) -> list[dict[str, Any]]:
    """Verilen yerel gunden bu yana olaylar (SQL siniri bir gun genis alinir)."""
    floor = parse_day(first_day)
    edge = (floor - timedelta(days=1)).isoformat() if floor else first_day
    return store.events_since(conn, campaign["id"], edge)


def week_stats(context: Any, campaign: dict[str, Any], now: datetime | None) -> dict[str, Any]:
    """Serit altindaki iki sayi: bu hafta kapanan gorev, dusen kayit."""
    conn = context.connection()
    day = parse_day(today_of(now)) or date.today()
    week = week_start_of(day)
    closed = 0
    left = 0
    points = 0
    for event in _events_from(conn, campaign, week):
        if local_day(event["at"]) < week:
            continue
        points += event["points"]
        if event["kind"] in (KIND_DONE, KIND_DONE_OVERDUE):
            closed += 1
        elif event["kind"] == KIND_LEFT_GROUP:
            left += 1
    return {"week_start": week, "tasks_done": closed, "issues_left": left, "xp": points}


def badge_wall(context: Any, campaign: dict[str, Any], now: datetime | None) -> list[dict[str, Any]]:
    earned = store.earned_badges(context.connection(), campaign["id"])
    wall: list[dict[str, Any]] = []
    for badge in BADGES:
        wall.append(
            {
                "code": badge["code"],
                "label": badge["label"],
                "hint": badge["hint"],
                "image": f"{IMAGE_DIR}badge-{badge['code']}.png",
                "earned": badge["code"] in earned,
                "earned_at": earned.get(badge["code"], ""),
            }
        )
    return wall


def ledger(
    context: Any, source: str = "", limit: int = store.LEDGER_LIMIT, now: datetime | None = None
) -> list[dict[str, Any]]:
    campaign = ensure_campaign(context, now) or store.active_campaign(context.connection())
    if campaign is None:
        last = store.list_campaigns(context.connection(), limit=1)
        if not last:
            return []
        campaign = last[0]
    events = store.list_events(context.connection(), campaign["id"], source, limit)
    for event in events:
        event["source_label"] = SOURCE_LABELS.get(event["source"], event["source"])
        event["day"] = local_day(event["at"])
    return events


def history(context: Any) -> list[dict[str, Any]]:
    """Biten seferlerin ozet kartlari (en yeniden eskiye)."""
    rows = store.list_campaigns(context.connection(), store.STATUS_ENDED)
    cards: list[dict[str, Any]] = []
    for row in rows:
        summary = row["summary"] or {}
        cards.append(
            {
                "id": row["id"],
                "name": row["name"],
                "starts_at": row["starts_at"],
                "ends_at": row["ends_at"],
                "target_xp": row["target_xp"],
                "total_xp": summary.get("total_xp", 0),
                "percent": summary.get("percent", 0),
                "rank": summary.get("rank") or {},
                "badges": summary.get("badges") or [],
                "streak": summary.get("streak", 0),
                "sources": summary.get("sources") or {},
            }
        )
    return cards


def digest(context: Any, campaign: dict[str, Any], now: datetime | None) -> dict[str, Any] | None:
    """Pazartesi acilisinda gosterilen "Holocron kaydı": gecen haftanin ozeti.

    Bir kez gorulunce ayarda isaretlenir; ayni hafta bir daha cikmaz.
    """
    day = parse_day(today_of(now)) or date.today()
    if day.weekday() != 0:
        return None
    week = week_start_of(day)
    if (context.settings.get(SETTING_DIGEST_WEEK, "") or "") == week:
        return None
    last_week = (day - timedelta(days=7)).isoformat()
    conn = context.connection()
    points = 0
    closed = 0
    total = store.total_xp(conn, campaign["id"])
    # Gecen haftanin BASINDAKI birikim: toplamdan o gunden sonraki her sey
    # dusulur -- BU haftaninkiler de. (Pazartesi karti cizilmeden once o gunun
    # seri puani yazilmis oluyor; onu dusmezsek rutbe geriye donuk sisirilir.)
    earlier = total
    for event in _events_from(conn, campaign, last_week):
        event_day = local_day(event["at"])
        if event_day < last_week:
            continue
        earlier -= event["points"]
        if event_day >= week:
            continue
        points += event["points"]
        if event["kind"] in (KIND_DONE, KIND_DONE_OVERDUE):
            closed += 1
    target = campaign["target_xp"]
    # Rutbe DEGISIMI: gecen haftanin basindaki rutbe ile bugunku.
    was = rank_of(earlier, target)
    now_rank = rank_of(total, target)
    return {
        # Kartin anlattigi hafta GECEN hafta; "gorduk" isareti ICINDE
        # bulundugumuz haftaya yazilir, o yuzden ikisi ayri alanda durur.
        "week_start": last_week,
        "seen_key": week,
        "xp": points,
        "tasks_done": closed,
        "rank": rank_view(total, target),
        "rank_before": {"code": was["code"], "label": was["label"]},
        "rank_changed": was["code"] != now_rank["code"],
    }


def panel(context: Any, now: datetime | None = None) -> dict[str, Any]:
    """Sefer panelinin tamami tek istekte: serit, emirler, rozetler, defter."""
    campaign = ensure_campaign(context, now)
    conn = context.connection()
    if campaign is None:
        return {
            "campaign": None,
            "ranks": [
                {
                    "code": rank["code"],
                    "label": rank["label"],
                    "at": rank["at"],
                    "image": IMAGE_DIR + rank["image"],
                }
                for rank in RANKS
            ],
            "image_dir": IMAGE_DIR,
            "history": history(context),
            "sources": [
                {"id": name, "label": SOURCE_LABELS[name]} for name in SOURCES
            ],
            "suggested_target": 1000,
        }

    evaluate(context, now)
    total = store.total_xp(conn, campaign["id"])
    target = campaign["target_xp"]
    day = today_of(now)
    week = week_start_of(parse_day(day) or date.today())
    return {
        "campaign": {
            "id": campaign["id"],
            "name": campaign["name"],
            "starts_at": campaign["starts_at"],
            "ends_at": campaign["ends_at"],
            "target_xp": target,
            "status": campaign["status"],
            "days_left": max(0, days_between(day, campaign["ends_at"])),
        },
        "xp": total,
        "percent": percent_of(total, target),
        "rank": rank_view(total, target),
        "next_rank": next_rank_of(total, target),
        "streak": streak_state(context, campaign, now),
        "quests": store.list_quests(conn, campaign["id"], week),
        "week": week_stats(context, campaign, now),
        "badges": badge_wall(context, campaign, now),
        "ledger": ledger(context, limit=store.LEDGER_LIMIT, now=now),
        "sources": [{"id": name, "label": SOURCE_LABELS[name]} for name in SOURCES],
        "source_totals": store.source_totals(conn, campaign["id"]),
        "history": history(context),
        "digest": digest(context, campaign, now),
        "image_dir": IMAGE_DIR,
        "ranks": [
            {
                "code": rank["code"],
                "label": rank["label"],
                "at": int(round(rank["at"] * target)),
                "image": IMAGE_DIR + rank["image"],
            }
            for rank in RANKS
        ],
    }


def rules_view(context: Any) -> list[dict[str, Any]]:
    rules = store.list_rules(context.connection())
    for rule in rules:
        rule["label"] = RULE_LABELS.get(rule["kind"], rule["kind"])
        rule["source_label"] = SOURCE_LABELS.get(rule["source"], rule["source"])
    return rules
