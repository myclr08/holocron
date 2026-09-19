"""Sefer verisi: kampanyalar, XP defteri, kurallar, rozetler, emirler, seri.

SQL burada durur, karar ve hesap `app/gamify.py` icinde. Ayri dosya olmasinin
sebebi `mailsend_repo` ile ayni: kendi tablolari, kendi ucu ve kendi ekrani
olan bir asama.

Iki kural semada yasar, kodda degil:

* **Tek aktif sefer** -- `campaigns(status)` uzerindeki kismi tekil indeks.
* **Cift XP yok** -- `xp_events(campaign_id, kind, ref)` tekil indeksi.

Boylece iki istek ayni anda ayni olayi yazmaya calissa bile ikincisi sessizce
duser; cagiran "yeni mi yazildi" cevabini donus degerinden okur.
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Iterable, Sequence

from . import fields as field_utils
from .repository import RepositoryError, now_iso

STATUS_ACTIVE = "active"
STATUS_ENDED = "ended"
STATUSES: tuple[str, ...] = (STATUS_ACTIVE, STATUS_ENDED)

NAME_LIMIT = 60
TARGET_MIN = 50
TARGET_MAX = 1_000_000
# Ekranda son 50 satir gorunur; "tumunu Excel'e" defterin tamamini yazar, bu
# yuzden ust sinir gercekci bir seferin asla ulasamayacagi kadar yuksek tutulur.
LEDGER_LIMIT = 50
LEDGER_MAX = 20000

# Seri isaretinin turu: kullanicinin kendi kazandigi gun / koruma ile affedilen.
STREAK_ACTIVE = "active"
STREAK_GRACE = "grace"


# --- dogrulama ----------------------------------------------------------


def clean_name(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise RepositoryError("invalid_name", "Sefer adı boş olamaz.")
    if len(text) > NAME_LIMIT:
        raise RepositoryError("invalid_name", f"Sefer adı en fazla {NAME_LIMIT} karakter.")
    return text


def clean_date(value: Any, code: str = "invalid_date", label: str = "Tarih") -> str:
    """ISO (YYYY-AA-GG) ya da GG.AA.YYYY kabul eder, ISO olarak saklar."""
    text = str(value or "").strip()
    if not text:
        raise RepositoryError(code, f"{label} boş olamaz.")
    try:
        cleaned = field_utils.normalize_local_value(field_utils.LOCAL_DATE, text)
    except field_utils.LocalValueError as exc:
        raise RepositoryError(code, str(exc)) from exc
    if not cleaned:
        raise RepositoryError(code, f"{label} boş olamaz.")
    return cleaned


def clean_target(value: Any) -> int:
    try:
        target = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise RepositoryError("invalid_target", "Hedef XP sayı olmalı.") from exc
    if target < TARGET_MIN or target > TARGET_MAX:
        raise RepositoryError(
            "invalid_target", f"Hedef XP {TARGET_MIN} ile {TARGET_MAX} arasında olmalı."
        )
    return target


# --- seferler -----------------------------------------------------------


def _loads(value: Any) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return None


def _campaign_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "starts_at": row["starts_at"] or "",
        "ends_at": row["ends_at"] or "",
        "target_xp": int(row["target_xp"] or 0),
        "status": row["status"],
        "created_at": row["created_at"] or "",
        "summary": _loads(row["ended_summary_json"]),
    }


def active_campaign(conn: sqlite3.Connection) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM campaigns WHERE status = ? ORDER BY id DESC LIMIT 1", (STATUS_ACTIVE,)
    ).fetchone()
    return _campaign_dict(row) if row else None


def get_campaign(conn: sqlite3.Connection, campaign_id: Any) -> dict[str, Any] | None:
    try:
        wanted = int(campaign_id)
    except (TypeError, ValueError):
        return None
    row = conn.execute("SELECT * FROM campaigns WHERE id = ?", (wanted,)).fetchone()
    return _campaign_dict(row) if row else None


def require_campaign(conn: sqlite3.Connection, campaign_id: Any) -> dict[str, Any]:
    campaign = get_campaign(conn, campaign_id)
    if campaign is None:
        raise RepositoryError("campaign_not_found", "Sefer bulunamadı.", status=404)
    return campaign


def list_campaigns(
    conn: sqlite3.Connection, status: str | None = None, limit: int = 50
) -> list[dict[str, Any]]:
    capped = max(1, min(int(limit or 50), 200))
    if status:
        rows = conn.execute(
            "SELECT * FROM campaigns WHERE status = ? ORDER BY id DESC LIMIT ?", (status, capped)
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM campaigns ORDER BY id DESC LIMIT ?", (capped,)
        ).fetchall()
    return [_campaign_dict(row) for row in rows]


def create_campaign(
    conn: sqlite3.Connection,
    name: Any,
    ends_at: Any,
    target_xp: Any = 1000,
    starts_at: str | None = None,
    today: str = "",
) -> dict[str, Any]:
    """Yeni sefer acar. Aktif sefer varken ikincisi acilmaz."""
    if active_campaign(conn) is not None:
        raise RepositoryError(
            "campaign_active", "Zaten süren bir sefer var; önce onu bitirin.", status=409
        )
    clean = clean_name(name)
    end = clean_date(ends_at, "invalid_end_date", "Bitiş tarihi")
    start = clean_date(starts_at, "invalid_start_date", "Başlangıç tarihi") if starts_at else (
        today or now_iso()[:10]
    )
    if end < start:
        raise RepositoryError("invalid_end_date", "Bitiş tarihi başlangıçtan önce olamaz.")
    target = clean_target(target_xp)
    with conn:
        cursor = conn.execute(
            "INSERT INTO campaigns (name, starts_at, ends_at, target_xp, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (clean, start, end, target, STATUS_ACTIVE, now_iso()),
        )
    return require_campaign(conn, int(cursor.lastrowid))  # type: ignore[arg-type]


def end_campaign(
    conn: sqlite3.Connection, campaign_id: Any, summary: dict[str, Any] | None = None
) -> dict[str, Any]:
    campaign = require_campaign(conn, campaign_id)
    if campaign["status"] == STATUS_ENDED:
        return campaign
    with conn:
        conn.execute(
            "UPDATE campaigns SET status = ?, ended_summary_json = ? WHERE id = ?",
            (STATUS_ENDED, json.dumps(summary or {}, ensure_ascii=False), campaign["id"]),
        )
    return require_campaign(conn, campaign["id"])


def delete_campaign(conn: sqlite3.Connection, campaign_id: Any) -> dict[str, Any]:
    """Biten bir seferi defteri, rozetleri, emirleri ve serisiyle siler.

    Aktif sefer silinmez: onun yolu "bitir" (`end_campaign`). Bagli satirlar
    semadaki ON DELETE CASCADE ile gider, burada tek satir silinir.
    """
    campaign = require_campaign(conn, campaign_id)
    if campaign["status"] != STATUS_ENDED:
        raise RepositoryError(
            "campaign_active",
            "Süren sefer silinemez; önce bitirin.",
            status=409,
        )
    with conn:
        conn.execute("DELETE FROM campaigns WHERE id = ?", (campaign["id"],))
    return campaign


# --- XP defteri ---------------------------------------------------------


def _event_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "campaign_id": row["campaign_id"],
        "at": row["at"],
        "source": row["source"],
        "kind": row["kind"],
        "points": int(row["points"] or 0),
        "ref": row["ref"] or "",
        "title": row["title"] or "",
        "note": row["note"] or "",
    }


def add_event(
    conn: sqlite3.Connection,
    campaign_id: int,
    source: str,
    kind: str,
    points: int,
    ref: str | None = None,
    title: str = "",
    note: str = "",
    at: str | None = None,
) -> dict[str, Any] | None:
    """Deftere bir satir yazar. Ayni (sefer, tur, referans) ikinci kez yazilmaz.

    Donus `None` ise olay zaten defterdeydi: cagiran bunu "puan verilmedi"
    diye okur, kullaniciya ikinci kez kutlama gostermez.
    """
    stamp = at or now_iso()
    with conn:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO xp_events (campaign_id, at, source, kind, points, ref, "
            "title, note) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (int(campaign_id), stamp, source, kind, int(points), ref or None, title, note),
        )
    if not cursor.rowcount:
        return None
    row = conn.execute("SELECT * FROM xp_events WHERE id = ?", (int(cursor.lastrowid),)).fetchone()
    return _event_dict(row) if row else None


def get_event(conn: sqlite3.Connection, event_id: Any) -> dict[str, Any] | None:
    try:
        wanted = int(event_id)
    except (TypeError, ValueError):
        return None
    row = conn.execute("SELECT * FROM xp_events WHERE id = ?", (wanted,)).fetchone()
    return _event_dict(row) if row else None


def delete_event(conn: sqlite3.Connection, event_id: int) -> bool:
    """Defter satirini siler.

    Iz birakmaz: tekillik `xp_events` uzerinde durdugu icin satir gidince
    anahtar serbest kalir; ayni olay ileride yeniden gerceklesirse (kayit
    tekrar filodan duser, gorev yeniden kapanir) normal sekilde yeniden puan
    yazilir.
    """
    with conn:
        cursor = conn.execute("DELETE FROM xp_events WHERE id = ?", (int(event_id),))
    return bool(cursor.rowcount)


def list_events(
    conn: sqlite3.Connection,
    campaign_id: int,
    source: str = "",
    limit: int = LEDGER_LIMIT,
) -> list[dict[str, Any]]:
    capped = max(1, min(int(limit or LEDGER_LIMIT), LEDGER_MAX))
    if source:
        rows = conn.execute(
            "SELECT * FROM xp_events WHERE campaign_id = ? AND source = ? "
            "ORDER BY at DESC, id DESC LIMIT ?",
            (int(campaign_id), source, capped),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM xp_events WHERE campaign_id = ? ORDER BY at DESC, id DESC LIMIT ?",
            (int(campaign_id), capped),
        ).fetchall()
    return [_event_dict(row) for row in rows]


def total_xp(conn: sqlite3.Connection, campaign_id: int) -> int:
    row = conn.execute(
        "SELECT COALESCE(SUM(points), 0) AS total FROM xp_events WHERE campaign_id = ?",
        (int(campaign_id),),
    ).fetchone()
    return int(row["total"] or 0)


def source_totals(conn: sqlite3.Connection, campaign_id: int) -> dict[str, int]:
    rows = conn.execute(
        "SELECT source, COALESCE(SUM(points), 0) AS total, COUNT(*) AS hits "
        "FROM xp_events WHERE campaign_id = ? GROUP BY source ORDER BY total DESC",
        (int(campaign_id),),
    ).fetchall()
    return {row["source"]: int(row["total"] or 0) for row in rows}


def count_events(
    conn: sqlite3.Connection,
    campaign_id: int,
    kinds: Sequence[str] | None = None,
    source: str = "",
) -> int:
    sql = "SELECT COUNT(*) AS c FROM xp_events WHERE campaign_id = ?"
    params: list[Any] = [int(campaign_id)]
    if kinds:
        sql += " AND kind IN (" + ",".join("?" for _ in kinds) + ")"
        params.extend(kinds)
    if source:
        sql += " AND source = ?"
        params.append(source)
    return int(conn.execute(sql, params).fetchone()["c"])


def events_of_kind(
    conn: sqlite3.Connection, campaign_id: int, kind: str, since: str = ""
) -> list[dict[str, Any]]:
    """Bir turun olaylari; gunluk tavan hesabi yerel gune gore Python'da yapilir."""
    if since:
        rows = conn.execute(
            "SELECT * FROM xp_events WHERE campaign_id = ? AND kind = ? AND at >= ? "
            "ORDER BY at, id",
            (int(campaign_id), kind, since),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM xp_events WHERE campaign_id = ? AND kind = ? ORDER BY at, id",
            (int(campaign_id), kind),
        ).fetchall()
    return [_event_dict(row) for row in rows]


def events_since(
    conn: sqlite3.Connection, campaign_id: int, since: str
) -> list[dict[str, Any]]:
    """Verilen zamandan sonraki olaylar.

    Hafta ozeti ve pazartesi karti bunu kullanir: defterin tamamini okumaya
    gerek yok. `since` KABA bir alt sinirdir (UTC damgasi, yerel gun degil);
    kesin gun elemesi cagirana aittir.
    """
    rows = conn.execute(
        "SELECT * FROM xp_events WHERE campaign_id = ? AND at >= ? ORDER BY at, id",
        (int(campaign_id), since),
    ).fetchall()
    return [_event_dict(row) for row in rows]


def ref_prefix_count(conn: sqlite3.Connection, campaign_id: int, kind: str, prefix: str) -> int:
    """'filtre grubu 3'ten kac kayit dustu' gibi sayimlar: referans on eki."""
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM xp_events WHERE campaign_id = ? AND kind = ? AND ref LIKE ?",
        (int(campaign_id), kind, prefix.replace("%", "") + "%"),
    ).fetchone()
    return int(row["c"])


# --- kurallar -----------------------------------------------------------


def _rule_dict(row: sqlite3.Row) -> dict[str, Any]:
    # params_json elle bozulmus olabilir; nesne degilse bos sozluk sayilir,
    # yoksa motor `params.get(...)` derken patlar ve butun puanlar duser.
    params = _loads(row["params_json"])
    return {
        "id": row["id"],
        "source": row["source"],
        "kind": row["kind"],
        "points": int(row["points"] or 0),
        "enabled": bool(row["enabled"]),
        "params": params if isinstance(params, dict) else {},
    }


def list_rules(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM xp_rules ORDER BY source, kind").fetchall()
    return [_rule_dict(row) for row in rows]


def rules_map(conn: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    """Tur -> kural. Kural yoksa motor tohumdaki varsayilani kullanir."""
    return {rule["kind"]: rule for rule in list_rules(conn)}


def seed_rules(conn: sqlite3.Connection, seeds: Iterable[dict[str, Any]]) -> int:
    """Eksik kural satirlarini tamamlar, var olanlara DOKUNMAZ.

    Goc bir kez calisir; sonraki bir surum yeni bir olay turu eklerse o tur
    mevcut veritabanlarinda hic olusmaz ve sessizce puan vermezdi. Acilista
    burasi cagrilir: `(source, kind)` tekil oldugu icin var olan satirin puani
    ve acik/kapali durumu korunur.
    """
    known = {rule["kind"] for rule in list_rules(conn)}
    missing = [seed for seed in seeds if seed["kind"] not in known]
    if not missing:
        return 0
    with conn:
        conn.executemany(
            "INSERT OR IGNORE INTO xp_rules (source, kind, points, enabled, params_json) "
            "VALUES (?, ?, ?, 1, ?)",
            [
                (seed["source"], seed["kind"], seed["points"], seed.get("params_json"))
                for seed in missing
            ],
        )
    return len(missing)


def update_rules(conn: sqlite3.Connection, items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Puan ve acik/kapali bilgisini gunceller; yeni kural yaratmaz."""
    known = {rule["kind"]: rule for rule in list_rules(conn)}
    with conn:
        for item in items or []:
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind") or "").strip()
            rule = known.get(kind)
            if rule is None:
                continue
            points = rule["points"]
            if item.get("points") is not None:
                try:
                    points = int(item["points"])
                except (TypeError, ValueError) as exc:
                    raise RepositoryError("invalid_points", "Puan sayı olmalı.") from exc
                if points < 0 or points > 1000:
                    raise RepositoryError("invalid_points", "Puan 0 ile 1000 arasında olmalı.")
            enabled = rule["enabled"]
            if item.get("enabled") is not None:
                enabled = bool(item["enabled"]) if isinstance(item["enabled"], bool) else str(
                    item["enabled"]
                ).strip().lower() in ("1", "true", "yes", "on", "evet")
            conn.execute(
                "UPDATE xp_rules SET points = ?, enabled = ? WHERE id = ?",
                (int(points), 1 if enabled else 0, rule["id"]),
            )
    return list_rules(conn)


# --- rozetler -----------------------------------------------------------


def earned_badges(conn: sqlite3.Connection, campaign_id: int) -> dict[str, str]:
    rows = conn.execute(
        "SELECT code, earned_at FROM badges WHERE campaign_id = ? ORDER BY id",
        (int(campaign_id),),
    ).fetchall()
    return {row["code"]: row["earned_at"] or "" for row in rows}


def earn_badge(
    conn: sqlite3.Connection, campaign_id: int, code: str, at: str | None = None
) -> bool:
    """Rozeti yazar; zaten kazanilmissa False doner (ikinci kutlama olmaz)."""
    with conn:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO badges (code, earned_at, campaign_id) VALUES (?, ?, ?)",
            (code, at or now_iso(), int(campaign_id)),
        )
    return bool(cursor.rowcount)


def revoke_badge(conn: sqlite3.Connection, campaign_id: int, code: str) -> bool:
    """Kosulu artik saglanmayan rozeti geri alir."""
    with conn:
        cursor = conn.execute(
            "DELETE FROM badges WHERE campaign_id = ? AND code = ?", (int(campaign_id), code)
        )
    return bool(cursor.rowcount)


def clear_day(conn: sqlite3.Connection, campaign_id: int, day: str) -> bool:
    """Gun isaretini kaldirir (o gunun seri puani silindiginde)."""
    with conn:
        cursor = conn.execute(
            "DELETE FROM streaks WHERE campaign_id = ? AND day = ?", (int(campaign_id), day)
        )
    return bool(cursor.rowcount)


def delete_events_by_ref(
    conn: sqlite3.Connection, campaign_id: int, kind: str, ref: str
) -> int:
    """Belirli bir olayi referansiyla siler (rozet geri alinirken puani da gider)."""
    with conn:
        cursor = conn.execute(
            "DELETE FROM xp_events WHERE campaign_id = ? AND kind = ? AND ref = ?",
            (int(campaign_id), kind, ref),
        )
    return cursor.rowcount or 0


# --- etkinlik defteri (rozet kosullari icin) ----------------------------
#
# `xp_events` puan defteridir: yalnizca puan veren, sefere bagli olaylar oraya
# yazilir. Burasi ise sefere bagli OLMAYAN kucuk is izleridir: "Duzelt" kac kez
# calistirildi, kac kez Excel'e dokuldu. Rozet kosullari bunlari okur.

ACTIVITY_FIX = "duzelt"
ACTIVITY_EXCEL = "excel"
ACTIVITY_KINDS: tuple[str, ...] = (ACTIVITY_FIX, ACTIVITY_EXCEL)


def log_activity(
    conn: sqlite3.Connection, kind: str, ref: str = "", at: str | None = None
) -> None:
    """Tek satirlik is izi. Bilinmeyen tur sessizce yok sayilir.

    Cagrildigi yerler kullanicinin bekledigi isi yapmakla mesgul (Excel
    uretimi, Copilot cagrisi); buradaki bir hata o isi DUSURMEMELI, o yuzden
    yazma hatasi yutulur.
    """
    if kind not in ACTIVITY_KINDS:
        return
    try:
        with conn:
            conn.execute(
                "INSERT INTO gamify_events (kind, at, ref) VALUES (?, ?, ?)",
                (kind, at or now_iso(), str(ref or "") or None),
            )
    except sqlite3.Error:
        return


def activity_stamps(conn: sqlite3.Connection, kind: str) -> list[str]:
    """Bir turun butun damgalari, eskiden yeniye."""
    try:
        rows = conn.execute(
            "SELECT at FROM gamify_events WHERE kind = ? ORDER BY at, id", (kind,)
        ).fetchall()
    except sqlite3.Error:
        return []
    return [str(row["at"] or "") for row in rows if row["at"]]


# --- haftalik emirler ---------------------------------------------------


def _quest_dict(row: sqlite3.Row) -> dict[str, Any]:
    target = int(row["target"] or 0)
    progress = int(row["progress"] or 0)
    return {
        "id": row["id"],
        "campaign_id": row["campaign_id"],
        "week_start": row["week_start"],
        "code": row["code"],
        "title": row["title"] or "",
        "target": target,
        "progress": progress,
        "done_at": row["done_at"] or "",
        "points": int(row["points"] or 0),
        "done": bool(row["done_at"]),
        "percent": min(100, round(progress * 100 / target)) if target else 0,
    }


def list_quests(
    conn: sqlite3.Connection, campaign_id: int, week_start: str = ""
) -> list[dict[str, Any]]:
    if week_start:
        rows = conn.execute(
            "SELECT * FROM quests WHERE campaign_id = ? AND week_start = ? ORDER BY id",
            (int(campaign_id), week_start),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM quests WHERE campaign_id = ? ORDER BY week_start DESC, id",
            (int(campaign_id),),
        ).fetchall()
    return [_quest_dict(row) for row in rows]


def add_quest(
    conn: sqlite3.Connection,
    campaign_id: int,
    week_start: str,
    code: str,
    title: str,
    target: int,
    points: int,
) -> dict[str, Any] | None:
    with conn:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO quests (campaign_id, week_start, code, title, target, "
            "progress, points) VALUES (?, ?, ?, ?, ?, 0, ?)",
            (int(campaign_id), week_start, code, title, max(1, int(target)), int(points)),
        )
    if not cursor.rowcount:
        return None
    row = conn.execute("SELECT * FROM quests WHERE id = ?", (int(cursor.lastrowid),)).fetchone()
    return _quest_dict(row) if row else None


def set_quest_progress(
    conn: sqlite3.Connection, quest_id: int, progress: int, done_at: str | None = None
) -> None:
    with conn:
        if done_at:
            conn.execute(
                "UPDATE quests SET progress = ?, done_at = COALESCE(done_at, ?) WHERE id = ?",
                (int(progress), done_at, int(quest_id)),
            )
        else:
            conn.execute(
                "UPDATE quests SET progress = ? WHERE id = ?", (int(progress), int(quest_id))
            )


# --- seri ---------------------------------------------------------------


def marked_days(conn: sqlite3.Connection, campaign_id: int) -> dict[str, str]:
    rows = conn.execute(
        "SELECT day, kind FROM streaks WHERE campaign_id = ? ORDER BY day",
        (int(campaign_id),),
    ).fetchall()
    return {row["day"]: row["kind"] for row in rows}


def mark_day(
    conn: sqlite3.Connection, campaign_id: int, day: str, kind: str = STREAK_ACTIVE
) -> bool:
    """Gunu isaretler; gun zaten isaretliyse False doner."""
    with conn:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO streaks (campaign_id, day, kind) VALUES (?, ?, ?)",
            (int(campaign_id), day, kind),
        )
    return bool(cursor.rowcount)
