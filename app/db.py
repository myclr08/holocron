"""SQLite baglantisi ve surumlu sema goclari.

Goc listesi sirayla uygulanir; her goc idempotent olacak sekilde yazilir ki
yarim kalmis bir yukseltme tekrar calistirildiginda patlamasin.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterable
from datetime import datetime, timezone
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


def _migration_0003_tasks(conn: sqlite3.Connection) -> None:
    """Asama 6: kisisel kanban ("Gorevlerim").

    `issue_key` bilerek yabanci anahtar degildir: henuz cekilmemis, hatta
    hicbir grupta gecmeyen bir anahtar da goreve baglanabilsin diye yalnizca
    bicimi dogrulanir (`repository.parse_issue_keys`).
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS tasks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            title       TEXT NOT NULL,
            description TEXT,
            note        TEXT,
            due_date    TEXT,
            status      TEXT NOT NULL DEFAULT 'todo'
                        CHECK (status IN ('todo', 'doing', 'done')),
            issue_key   TEXT,
            position    INTEGER NOT NULL DEFAULT 0,
            created_at  TEXT,
            updated_at  TEXT,
            done_at     TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_tasks_column ON tasks(status, position, id);
        CREATE INDEX IF NOT EXISTS idx_tasks_issue ON tasks(issue_key);
        """
    )


def _migration_0004_mail(conn: sqlite3.Connection) -> None:
    """Asama 7: Outlook e-postalarindan gorev uretme.

    `mail_conversations` tekillestirmenin otoritesidir: bir konusma buraya bir
    kez yazildiktan sonra bir daha gorev uretmez. Gorev silinse bile satir
    KALIR (`state='task_deleted'`), yoksa silinen gorev bir sonraki taramada
    geri gelirdi.
    """
    _add_column(conn, "tasks", "source", "TEXT NOT NULL DEFAULT 'manual'")
    _add_column(conn, "tasks", "mail_conversation_id", "TEXT")
    _add_column(conn, "tasks", "mail_sender", "TEXT")
    _add_column(conn, "tasks", "mail_received_at", "TEXT")
    _add_column(conn, "tasks", "mail_count", "INTEGER DEFAULT 0")
    _add_column(conn, "tasks", "mail_last_at", "TEXT")
    _add_column(conn, "tasks", "mail_entry_id", "TEXT")
    _add_column(conn, "tasks", "mail_store_id", "TEXT")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS mail_conversations (
            conversation_id TEXT PRIMARY KEY,
            task_id         INTEGER,
            state           TEXT NOT NULL DEFAULT 'active'
                            CHECK (state IN ('active', 'task_deleted', 'ignored')),
            first_seen      TEXT,
            last_seen       TEXT
        );

        CREATE TABLE IF NOT EXISTS mail_messages (
            message_id      TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            task_id         INTEGER,
            subject         TEXT,
            sender          TEXT,
            received_at     TEXT,
            folder_path     TEXT,
            entry_id        TEXT,
            store_id        TEXT,
            seen_at         TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_mail_messages_conversation
            ON mail_messages(conversation_id, received_at);
        CREATE INDEX IF NOT EXISTS idx_mail_messages_task ON mail_messages(task_id);
        CREATE INDEX IF NOT EXISTS idx_mail_conversations_task
            ON mail_conversations(task_id);
        CREATE INDEX IF NOT EXISTS idx_tasks_mail_conversation
            ON tasks(mail_conversation_id);
        """
    )


def _migration_0005_mail_address_lists(conn: sqlite3.Connection) -> None:
    """Tek adres listesi -> ucu ayri (Kimden / Kime / CC).

    Eski `mail.addresses` hem gonderende hem alicida araniyordu; kullanici
    "bana gelenler" ile "benim yazdiklarim" arasini ayirmak isteyince liste
    uce bolundu. Eski deger uc anahtara da kopyalanir (davranis birebir ayni
    kalir), sonra silinir; goc bir kez calisir.
    """
    row = conn.execute("SELECT value FROM settings WHERE key = 'mail.addresses'").fetchone()
    value = (row["value"] if row else None) or ""
    if value.strip():
        for key in ("mail.from_addresses", "mail.to_addresses", "mail.cc_addresses"):
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
    conn.execute("DELETE FROM settings WHERE key = 'mail.addresses'")


# Sablon tohumunun bir kez atildigini soyleyen ic isaret (arayuze gosterilmez).
TEMPLATE_SEED_KEY = "teams.templates_seeded"


def _migration_0006_teams(conn: sqlite3.Connection) -> None:
    """Asama 8: kayittan Teams'e mesaj (derin baglanti, Graph API yok).

    `contacts` adres defteridir: kayda kisi eklenince buraya da duser, bir
    sonraki kayitta otomatik tamamlamada cikar. `sent_messages` yalnizca
    "acildi" kaydidir: Gonder'e kullanici basar, uygulama gonderildigini
    DOGRULAYAMAZ.

    NOT: buradaki `teams_channels` / `issue_channel` tablolari 0007'de
    DUSURULUR (kanala yazma kaldirildi). Bu goc gecmis kaydidir, oldugu gibi
    birakilir; yeni kurulumda iki tablo yaratilip hemen ardindan silinir.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS contacts (
            email      TEXT PRIMARY KEY,
            name       TEXT,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS issue_contacts (
            issue_key TEXT NOT NULL,
            email     TEXT NOT NULL,
            position  INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (issue_key, email)
        );

        CREATE INDEX IF NOT EXISTS idx_issue_contacts_email ON issue_contacts(email);

        CREATE TABLE IF NOT EXISTS teams_channels (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            url        TEXT NOT NULL,
            created_at TEXT
        );

        CREATE TABLE IF NOT EXISTS issue_channel (
            issue_key  TEXT PRIMARY KEY,
            channel_id INTEGER NOT NULL,
            FOREIGN KEY (channel_id) REFERENCES teams_channels(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS message_templates (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            name       TEXT NOT NULL,
            body       TEXT NOT NULL,
            position   INTEGER NOT NULL DEFAULT 0,
            is_default INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS sent_messages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            issue_key   TEXT NOT NULL,
            target_kind TEXT NOT NULL CHECK (target_kind IN ('people', 'channel')),
            target_text TEXT,
            body        TEXT,
            opened_at   TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_sent_messages_issue ON sent_messages(issue_key, id);
        """
    )
    # Tohum bir kez atilir ve `settings` icinde isaretlenir: kullanici
    # sablonlari sildiyse yarim kalmis bir yukseltme onlari geri getirmemeli.
    from .teams import SEED_TEMPLATES

    seeded = conn.execute(
        "SELECT value FROM settings WHERE key = ?", (TEMPLATE_SEED_KEY,)
    ).fetchone()
    if seeded is None:
        conn.executemany(
            "INSERT INTO message_templates (name, body, position, is_default) "
            "VALUES (?, ?, ?, ?)",
            [
                (name, body, index, 1 if is_default else 0)
                for index, (name, body, is_default) in enumerate(SEED_TEMPLATES)
            ],
        )
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, '1') "
            "ON CONFLICT(key) DO NOTHING",
            (TEMPLATE_SEED_KEY,),
        )


def _migration_0007_address_book(conn: sqlite3.Connection) -> None:
    """Adres defterine kaynak/tur, kanal hedefine veda.

    `source` elle girilen kisiyi kurum rehberinden geleni ayirir ("gal"
    tazelenirken elle yazilanlar silinmez), `kind` dagitim listelerini
    isaretler. Kanala mesaj gonderme kaldirildi (Teams kanal baglantilari
    mesaj on doldurmayi kabul etmiyordu, iki ayri akis tasimaya degmedi):
    0006 gecmise dokunulmadan birakilir, tablolar burada duser.
    """
    _add_column(conn, "contacts", "source", "TEXT NOT NULL DEFAULT 'manual'")
    _add_column(conn, "contacts", "kind", "TEXT NOT NULL DEFAULT 'person'")
    _add_column(conn, "contacts", "updated_at", "TEXT")
    conn.executescript(
        """
        DROP TABLE IF EXISTS issue_channel;
        DROP TABLE IF EXISTS teams_channels;
        """
    )


def _migration_0008_teams_calls(conn: sqlite3.Connection) -> None:
    """Asama 9: Teams arama gecmisi (yerel onbellekten okunan kendi kayitlarim).

    `call_id` birincil anahtardir: tekillestirmenin otoritesi odur. Ayni
    onbellek iki kez taranirsa satir yeniden yazilir, kopya birikmez.
    `raw_json` ham kaydi tasir; ileride yeni bir alan gerekirse yeniden
    taramaya gerek kalmaz.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS teams_calls (
            call_id           TEXT PRIMARY KEY,
            started_at        TEXT,
            ended_at          TEXT,
            connected_at      TEXT,
            duration_ms       INTEGER NOT NULL DEFAULT 0,
            direction         TEXT,
            state             TEXT,
            kind              TEXT,
            counterpart_id    TEXT,
            counterpart_name  TEXT,
            forwarded         TEXT,
            meeting_subject   TEXT,
            meeting_organizer TEXT,
            my_response       TEXT,
            participants_json TEXT,
            raw_json          TEXT,
            seen_at           TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_teams_calls_started ON teams_calls(started_at);
        CREATE INDEX IF NOT EXISTS idx_teams_calls_person
            ON teams_calls(counterpart_id, started_at);
        CREATE INDEX IF NOT EXISTS idx_teams_calls_kind ON teams_calls(kind, started_at);
        """
    )


def _migration_0009_call_threads(conn: sqlite3.Connection) -> None:
    """Teams arama gecmisi: sohbet bagi, grup sohbeti adi, davetliler.

    Gercek onbellekte grup/toplanti aramalarinin kimligi `threadId` ve
    `groupChatThreadId` alanlarinda duruyor: toplanti eslemesi saat
    yakinligindan cok bu bagla yapiliyor. `topic` grup sohbetinin kendi adi
    (`conversation-manager`), `attendees_json` ise takvimdeki DAVETLILER --
    aramaya gercekten katilanlar `participants_json` icinde.

    Tablo yerel bir onbellek dokumu oldugu icin yeni sutunlar bos baslar ve
    ilk taramada dolar; veri kaybi yok.
    """
    _add_column(conn, "teams_calls", "thread_id", "TEXT")
    _add_column(conn, "teams_calls", "group_thread_id", "TEXT")
    _add_column(conn, "teams_calls", "topic", "TEXT")
    _add_column(conn, "teams_calls", "attendees_json", "TEXT")
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_teams_calls_thread ON teams_calls(thread_id);
        CREATE INDEX IF NOT EXISTS idx_teams_calls_group_thread
            ON teams_calls(group_thread_id);
        """
    )


# E-posta sablonu tohumunun bir kez atildigini soyleyen ic isaret.
MAILSEND_SEED_KEY = "mailsend.templates_seeded"


def _migration_0010_mail_send(conn: sqlite3.Connection) -> None:
    """Asama 10: grup kayitlarini Excel ekiyle e-postayla gonderme (Outlook).

    `mail_templates` Kime/CC/Konu/Govde'yi birlikte tasir: "her hafta ayni
    kisilere ayni baslikla" isi tek secimle bitsin diye. `groups.mail_template_id`
    grubun varsayilanidir (NULL = listenin ilki). `mail_sends` GERCEK gonderim
    kaydidir; `mode` sutunu pencerenin mi acildigini yoksa `Send()` mi
    cagrildigini soyler.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS mail_templates (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            name         TEXT NOT NULL,
            to_addresses TEXT,
            cc_addresses TEXT,
            subject      TEXT,
            body         TEXT,
            attach_excel INTEGER NOT NULL DEFAULT 1,
            inline_table INTEGER NOT NULL DEFAULT 1,
            position     INTEGER NOT NULL DEFAULT 0,
            created_at   TEXT,
            updated_at   TEXT
        );

        CREATE TABLE IF NOT EXISTS mail_sends (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id    INTEGER,
            template_id INTEGER,
            to_text     TEXT,
            cc_text     TEXT,
            subject     TEXT,
            issue_count INTEGER NOT NULL DEFAULT 0,
            file_name   TEXT,
            mode        TEXT NOT NULL DEFAULT 'display'
                        CHECK (mode IN ('display', 'send')),
            sent_at     TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_mail_sends_group ON mail_sends(group_id, id);
        """
    )
    _add_column(conn, "groups", "mail_template_id", "INTEGER")

    # Tohum bir kez atilir: kullanici sablonu sildiyse yarim kalmis bir
    # yukseltme onu geri getirmemeli.
    from .mailsend import SEED_TEMPLATES

    seeded = conn.execute(
        "SELECT value FROM settings WHERE key = ?", (MAILSEND_SEED_KEY,)
    ).fetchone()
    if seeded is None:
        stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
        conn.executemany(
            "INSERT INTO mail_templates "
            "(name, to_addresses, cc_addresses, subject, body, attach_excel, inline_table, "
            " position, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, 1, 1, ?, ?, ?)",
            [
                (
                    seed["name"],
                    seed.get("to_addresses", ""),
                    seed.get("cc_addresses", ""),
                    seed["subject"],
                    seed["body"],
                    index,
                    stamp,
                    stamp,
                )
                for index, seed in enumerate(SEED_TEMPLATES)
            ],
        )
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, '1') ON CONFLICT(key) DO NOTHING",
            (MAILSEND_SEED_KEY,),
        )


def _migration_0011_call_source(conn: sqlite3.Connection) -> None:
    """Arama kaydinin nereden geldigi: arama gecmisi mi, toplanti sohbeti mi.

    `call-history` yalnizca baslatilan ya da gelen aramalari tutuyor; takvimden
    katilinan planli toplantilar orada hic gecmiyor. O toplantilar sohbetteki
    `<partlist type="ended">` mesajindan gelir ve `source='chat'` ile
    isaretlenir. Eski satirlar 'history' sayilir.
    """
    _add_column(conn, "teams_calls", "source", "TEXT NOT NULL DEFAULT 'history'")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_teams_calls_source ON teams_calls(source)")


# XP kural tohumunun bir kez atildigini soyleyen ic isaret.
RULES_SEED_KEY = "gamify.rules_seeded"


def _migration_0012_gamify(conn: sqlite3.Connection) -> None:
    """Asama 11: oyunlastirma -- "Sefer" (kampanya), XP defteri, rozetler.

    Tek aktif sefer kurali kismi tekil indeksle SEMADA durur: uygulama
    katmani unutsa bile ikinci bir aktif sefer acilamaz. Bitis tarihi gecen
    sefer `ended` olur, ozeti `ended_summary_json` icinde kalir; defter (XP
    olaylari) sefere baglidir, silinmez -- "sifirlanir" demek yeni seferin
    sifirdan baslamasi demektir, gecmisin silinmesi degil.

    `xp_events` uzerindeki tekil indeks cift XP'yi burada keser: ayni
    (sefer, tur, referans) ucluu bir kez yazilir. Referanssiz olay (elle
    duzeltme gibi) kisitin disinda kalir.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS campaigns (
            id                 INTEGER PRIMARY KEY AUTOINCREMENT,
            name               TEXT NOT NULL,
            starts_at          TEXT,
            ends_at            TEXT,
            target_xp          INTEGER NOT NULL DEFAULT 1000,
            status             TEXT NOT NULL DEFAULT 'active'
                               CHECK (status IN ('active', 'ended')),
            created_at         TEXT,
            ended_summary_json TEXT
        );

        -- Ayni anda tek aktif sefer: kisitin sahibi sema.
        CREATE UNIQUE INDEX IF NOT EXISTS idx_campaigns_single_active
            ON campaigns(status) WHERE status = 'active';

        CREATE TABLE IF NOT EXISTS xp_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL,
            at          TEXT NOT NULL,
            source      TEXT NOT NULL,
            kind        TEXT NOT NULL,
            points      INTEGER NOT NULL DEFAULT 0,
            ref         TEXT,
            title       TEXT,
            note        TEXT,
            FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_xp_events_once
            ON xp_events(campaign_id, kind, ref) WHERE ref IS NOT NULL AND ref <> '';
        CREATE INDEX IF NOT EXISTS idx_xp_events_when ON xp_events(campaign_id, at, id);
        CREATE INDEX IF NOT EXISTS idx_xp_events_source ON xp_events(campaign_id, source);

        CREATE TABLE IF NOT EXISTS xp_rules (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            source      TEXT NOT NULL,
            kind        TEXT NOT NULL,
            points      INTEGER NOT NULL DEFAULT 0,
            enabled     INTEGER NOT NULL DEFAULT 1,
            params_json TEXT
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_xp_rules_kind ON xp_rules(source, kind);

        CREATE TABLE IF NOT EXISTS badges (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            code        TEXT NOT NULL,
            earned_at   TEXT,
            campaign_id INTEGER NOT NULL,
            FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_badges_once ON badges(campaign_id, code);

        CREATE TABLE IF NOT EXISTS quests (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL,
            week_start  TEXT NOT NULL,
            code        TEXT NOT NULL,
            title       TEXT,
            target      INTEGER NOT NULL DEFAULT 1,
            progress    INTEGER NOT NULL DEFAULT 0,
            done_at     TEXT,
            points      INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_quests_once
            ON quests(campaign_id, week_start, code);

        CREATE TABLE IF NOT EXISTS streaks (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL,
            day         TEXT NOT NULL,
            kind        TEXT NOT NULL DEFAULT 'active',
            FOREIGN KEY (campaign_id) REFERENCES campaigns(id) ON DELETE CASCADE
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_streaks_day ON streaks(campaign_id, day);
        """
    )

    # Kural tohumu bir kez atilir: kullanici puani sifirladiysa yarim kalmis
    # bir yukseltme onu geri getirmemeli.
    from .gamify import SEED_RULES

    seeded = conn.execute("SELECT value FROM settings WHERE key = ?", (RULES_SEED_KEY,)).fetchone()
    if seeded is None:
        conn.executemany(
            "INSERT OR IGNORE INTO xp_rules (source, kind, points, enabled, params_json) "
            "VALUES (?, ?, ?, 1, ?)",
            [
                (rule["source"], rule["kind"], rule["points"], rule.get("params_json"))
                for rule in SEED_RULES
            ],
        )
        conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, '1') ON CONFLICT(key) DO NOTHING",
            (RULES_SEED_KEY,),
        )


def _migration_0013_group_detail_fields(conn: sqlite3.Connection) -> None:
    """Jira detay alanı seçimini filo bazında, null=varsayılan tümü olarak saklar."""
    _add_column(conn, "groups", "detail_fields_json", "TEXT")


def _migration_0014_calls_without_meetings(conn: sqlite3.Connection) -> None:
    """Aramalar filosundan TOPLANTI kavrami tumden cikti.

    Toplanti eslemesi (takvim) ve toplanti sohbetinden turetilen katilim
    kayitlari kaldirildi; geriye yalnizca **birebir** ve **grup** aramalari
    kaldi. Tablo bu yuzden yeniden kuruluyor:

    * toplanti satirlari (`kind='meeting'`) ve sohbetten turetilen satirlar
      (`source='chat'`) SILINIR -- ikisi de artik uretilmiyor, durmalari
      ekranda yanlis bir gecmis gosterirdi;
    * yalnizca toplantiya ait sutunlar (`meeting_subject`,
      `meeting_organizer`, `my_response`, `attendees_json`, `source`) duser;
    * sohbet sutunlari (`thread_id`, `group_thread_id`, `topic`) da duser:
      gruplama artik sohbet kimligiyle degil **katilimci kumesiyle** yapiliyor
      ve grup adi onbellekten aranmiyor.

    Kalan veri (aramalar) oldugu gibi tasinir; yeniden tarama gerekmez.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS teams_calls_new (
            call_id           TEXT PRIMARY KEY,
            started_at        TEXT,
            ended_at          TEXT,
            connected_at      TEXT,
            duration_ms       INTEGER NOT NULL DEFAULT 0,
            direction         TEXT,
            state             TEXT,
            kind              TEXT,
            counterpart_id    TEXT,
            counterpart_name  TEXT,
            forwarded         TEXT,
            participants_json TEXT,
            raw_json          TEXT,
            seen_at           TEXT
        );

        INSERT INTO teams_calls_new (
            call_id, started_at, ended_at, connected_at, duration_ms, direction,
            state, kind, counterpart_id, counterpart_name, forwarded,
            participants_json, raw_json, seen_at
        )
        SELECT
            call_id, started_at, ended_at, connected_at, duration_ms, direction,
            state, kind, counterpart_id, counterpart_name, forwarded,
            participants_json, raw_json, seen_at
        FROM teams_calls
        WHERE kind <> 'meeting' AND COALESCE(source, 'history') <> 'chat';

        DROP TABLE teams_calls;
        ALTER TABLE teams_calls_new RENAME TO teams_calls;

        CREATE INDEX IF NOT EXISTS idx_teams_calls_started ON teams_calls(started_at);
        CREATE INDEX IF NOT EXISTS idx_teams_calls_person
            ON teams_calls(counterpart_id, started_at);
        CREATE INDEX IF NOT EXISTS idx_teams_calls_kind ON teams_calls(kind, started_at);
        """
    )


def _migration_0015_gorusme_notlari(conn: sqlite3.Connection) -> None:
    """Asama 12: gorusme notlari (kayit -> yaziya dokme -> ozet).

    `gorusme_notu` hattin durum makinesidir: satir kayit baslar baslamaz
    yazilir ve her asama degisiminde guncellenir, boylece uygulama kapanip
    acilsa bile yarim kalmis is kuyruktan surdurulur.

    `gorusme_transkript` yalnizca "transkripti sakla" acikken doldurulur;
    ses dosyalari not hazir olunca zaten silinir. Arama icin FTS5 tablosu
    kurulur; sqlite FTS5'siz derlenmisse tablo hic olusmaz ve arama LIKE'a
    duser (`fts_var`).
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS gorusme_notu (
            id                       INTEGER PRIMARY KEY AUTOINCREMENT,
            call_id                  TEXT,
            baslangic                TEXT,
            bitis                    TEXT,
            sure_sn                  INTEGER NOT NULL DEFAULT 0,
            tur                      TEXT,
            baslik                   TEXT,
            durum                    TEXT NOT NULL DEFAULT 'kaydediliyor',
            hata                     TEXT,
            model                    TEXT,
            isleme_sn_birlestirme    INTEGER NOT NULL DEFAULT 0,
            isleme_sn_yaziya_dokme   INTEGER NOT NULL DEFAULT 0,
            isleme_sn_ozet           INTEGER NOT NULL DEFAULT 0,
            klasor                   TEXT,
            olusturma                TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_gorusme_notu_baslangic
            ON gorusme_notu(baslangic);
        CREATE INDEX IF NOT EXISTS idx_gorusme_notu_durum ON gorusme_notu(durum, id);
        CREATE INDEX IF NOT EXISTS idx_gorusme_notu_call ON gorusme_notu(call_id);

        CREATE TABLE IF NOT EXISTS gorusme_bolum (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            not_id     INTEGER NOT NULL,
            tur        TEXT NOT NULL,
            sira       INTEGER NOT NULL DEFAULT 0,
            metin      TEXT NOT NULL,
            kisi       TEXT,
            son_tarih  TEXT,
            FOREIGN KEY (not_id) REFERENCES gorusme_notu(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_gorusme_bolum_not
            ON gorusme_bolum(not_id, tur, sira);

        CREATE TABLE IF NOT EXISTS gorusme_katilimci (
            not_id  INTEGER NOT NULL,
            kimlik  TEXT NOT NULL,
            ad      TEXT,
            PRIMARY KEY (not_id, kimlik),
            FOREIGN KEY (not_id) REFERENCES gorusme_notu(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS gorusme_bag (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            not_id    INTEGER NOT NULL,
            jira_key  TEXT,
            gorev_id  INTEGER,
            FOREIGN KEY (not_id) REFERENCES gorusme_notu(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_gorusme_bag_not ON gorusme_bag(not_id);
        CREATE INDEX IF NOT EXISTS idx_gorusme_bag_key ON gorusme_bag(jira_key);
        CREATE INDEX IF NOT EXISTS idx_gorusme_bag_gorev ON gorusme_bag(gorev_id);

        CREATE TABLE IF NOT EXISTS gorusme_transkript (
            not_id  INTEGER PRIMARY KEY,
            metin   TEXT NOT NULL,
            FOREIGN KEY (not_id) REFERENCES gorusme_notu(id) ON DELETE CASCADE
        );
        """
    )
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS gorusme_fts "
            "USING fts5(metin, not_id UNINDEXED)"
        )
    except sqlite3.OperationalError:
        # FTS5'siz derlenmis sqlite: arama LIKE'a duser, goc yine gecerlidir.
        pass


def _migration_0016_gorusme_ve_aramalar_kaldirildi(conn: sqlite3.Connection) -> None:
    """Teams aramalari ve gorusme notlari ozelligi tumden kaldirildi (0.11.0).

    Kullanici kararı (19 Eylul 2026): Holocron artik arama gecmisi okumuyor,
    gorusme kaydetmiyor, not cikarmiyor. Bu yuzden ilgili tablolar DUSER --
    kodu duran bir tablonun verisi dosyada bos yer tutmaktan baska bir ise
    yaramaz. Eski gocler (8, 9, 11, 14, 15) tarihseldir: olduklari gibi
    kalirlar, bu goc onlarin kurdugunu yikar.

    DIKKAT: bu goc VERI SILER. Taranmis Teams aramalari ve uretilmis butun
    gorusme notlari (ozet, karar, aksiyon, transkript) geri gelmez.

    Copilot ayarlari KAYBOLMAZ: kullanicinin elle yazdigi yol ve vekil
    adresi `calls.*` altindan `copilot.*` altina tasinir; Copilot karti
    Ayarlar'da kaliyor. Geri kalan `calls.*` / gorusmeye ozel anahtarlar
    silinir.
    """
    conn.executescript(
        """
        DROP TABLE IF EXISTS teams_calls;
        DROP TABLE IF EXISTS gorusme_bolum;
        DROP TABLE IF EXISTS gorusme_katilimci;
        DROP TABLE IF EXISTS gorusme_bag;
        DROP TABLE IF EXISTS gorusme_transkript;
        DROP TABLE IF EXISTS gorusme_fts;
        DROP TABLE IF EXISTS gorusme_notu;
        """
    )
    # Copilot ayarlari yeni adlarina tasinir (mevcut yeni deger varsa dokunulmaz).
    for eski, yeni in COPILOT_AYAR_GOCU:
        row = conn.execute("SELECT value FROM settings WHERE key = ?", (eski,)).fetchone()
        deger = str(row["value"] or "") if row is not None else ""
        if deger:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO NOTHING",
                (yeni, deger),
            )
    conn.executescript(
        """
        DELETE FROM settings WHERE key LIKE 'calls.%';
        """
    )



def _migration_0017_rozet_etkinlikleri(conn: sqlite3.Connection) -> None:
    """Rozet kosullarinin okudugu kucuk etkinlik defteri (`gamify_events`).

    Rozetlerin buyuk cogunlugu zaten var olan tablolardan hesaplanir (gorevler,
    XP defteri, filolar, kisiler, gonderilen mesajlar). Iki is ise hicbir yerde
    iz birakmiyordu: Copilot ile metin "Duzelt" ve Excel'e disa aktarim. Ikisi
    de tek satirlik bir kayit olmadan olculemez, o yuzden bu tablo var.

    Tablo sefere BAGLI DEGILDIR: kullanicinin is gecmisidir, sefer degisince
    silinmez. Sefere ait sayim, satirin gunu seferin baslangicindan sonraysa
    yapilir (`gamify.badge_facts`). Boylece gecmis veriden hak edilmis rozetler
    geriye donuk verilebilir.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS gamify_events (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            at   TEXT NOT NULL,
            ref  TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_gamify_events_kind ON gamify_events(kind, at);
        """
    )


def _migration_0018_gorev_son_durum(conn: sqlite3.Connection) -> None:
    """Gorevlerde "Son durum" alani ve gecmisi.

    Aciklama gorevin ne oldugunu anlatir, "son durum" ise NEREDE kaldigini.
    Ikisi ayri alanlardir cunku son durum sik degisir ve her degisimi
    saklanir: `task_status_history` bir defterdir, satirlari degismez.
    Bosaltma da bir satirdir (bos metin), boylece "burasi temizlendi" bilgisi
    kaybolmaz. Gorev silinince gecmisi de gider (ON DELETE CASCADE).
    """
    _add_column(conn, "tasks", "son_durum", "TEXT")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS task_status_history (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            task_id   INTEGER NOT NULL,
            metin     TEXT,
            olusturma TEXT NOT NULL,
            FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_task_status_history_task
            ON task_status_history(task_id, id);
        """
    )


# Eski `calls.copilot_*` / ozet anahtarlarinin yeni `copilot.*` karsiliklari.
COPILOT_AYAR_GOCU: tuple[tuple[str, str], ...] = (
    ("calls.copilot_yolu", "copilot.yolu"),
    ("calls.copilot_yolu_son", "copilot.yolu_son"),
    ("calls.copilot_proxy", "copilot.proxy"),
    ("calls.ozet_modelleri", "copilot.modeller"),
    ("calls.ozet_model_son", "copilot.son_model"),
)


# Sira onemli: yeni goc her zaman listenin sonuna eklenir, mevcut satir degismez.
MIGRATIONS: list[tuple[int, str, Migration]] = [
    (1, "initial schema", _migration_0001_initial),
    (2, "local field history", _migration_0002_local_history),
    (3, "personal tasks", _migration_0003_tasks),
    (4, "mail intake", _migration_0004_mail),
    (5, "mail address lists", _migration_0005_mail_address_lists),
    (6, "teams messages", _migration_0006_teams),
    (7, "address book source", _migration_0007_address_book),
    (8, "teams calls", _migration_0008_teams_calls),
    (9, "teams call threads", _migration_0009_call_threads),
    (10, "mail send templates", _migration_0010_mail_send),
    (11, "teams call source", _migration_0011_call_source),
    (12, "gamify campaigns", _migration_0012_gamify),
    (13, "group Jira detail fields", _migration_0013_group_detail_fields),
    (14, "teams calls without meetings", _migration_0014_calls_without_meetings),
    (15, "meeting notes", _migration_0015_gorusme_notlari),
    (16, "drop teams calls and meeting notes", _migration_0016_gorusme_ve_aramalar_kaldirildi),
    (17, "gamify activity log", _migration_0017_rozet_etkinlikleri),
    (18, "task status field and history", _migration_0018_gorev_son_durum),
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
