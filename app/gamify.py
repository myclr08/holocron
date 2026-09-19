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
from pathlib import Path
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
#
# Katalog TEK yerde durur: kod, ad, ipucu, kategori, nadirlik. Kazanma kurali
# asagidaki uc tablodan birindedir ve hepsi GERCEK veriden olculur:
#
# * `SERIES_BADGES` -- sayilabilir olaylarin sirali damga listesi. Ilerleme
#   listenin uzunlugu, kazanma ani N. damgadir; boylece gecmis veriden hak
#   edilmis rozet dogru TARIHLE verilir (geriye donuk degerlendirme).
# * `GAUGE_BADGES` -- anlik bir sayi (seri uzunlugu, kisi sayisi, toplam XP).
#   Gecmis damgasi yoktur, kazanma ani bugundur.
# * `FLAG_BADGES` -- "oldu / olmadi" kosullari (bir ayin her is gunu dolu,
#   ayni gun uc ayri proje, emirler pazartesi bitti).
#
# Rutbe rozetleri ayri tutulur: esikleri seferin HEDEFINE oranlidir.
#
# Telifli karakter adi kullanilmaz; adlar genel uzay/sefer sozlugundendir.

# "Temiz Masa" kac gun gecikmis gorevsiz gecmesini ister.
CLEAN_DESK_DAYS = 7

# Gorev
BADGE_FIRST_TASK = "first_task"
BADGE_TASK_10 = "task_10"
BADGE_CLOSER = "closer"
BADGE_TASK_50 = "task_50"
BADGE_TASK_200 = "task_200"
BADGE_TASK_DAY_5 = "task_day_5"
BADGE_EARLY_10 = "early_10"
BADGE_NOTED_20 = "noted_20"
BADGE_LINKED_25 = "linked_25"
BADGE_CLEAN_DESK = "clean_desk"
BADGE_ARCHIVIST = "archivist"

# Jira akisi
BADGE_FIRST_ISSUE = "first_issue"
BADGE_FINISHER = "finisher"
BADGE_DROP_25 = "drop_25"
BADGE_DROP_100 = "drop_100"
BADGE_DROP_500 = "drop_500"
BADGE_WEEK_20 = "week_20"
BADGE_OLD_ISSUE = "old_issue"
BADGE_THREE_PROJECTS = "three_projects"
BADGE_LOCAL_50 = "local_50"

# Seri
BADGE_STREAK_5 = "streak_5"
BADGE_STREAK_20 = "streak_20"
BADGE_STREAK_30 = "streak_30"
BADGE_STREAK_60 = "streak_60"
BADGE_GRACE_SAVED = "grace_saved"
BADGE_STREAK_AGAIN = "streak_again_10"

# Haftalik emirler
BADGE_FIRST_QUEST = "first_quest"
BADGE_QUEST_4_WEEKS = "quest_4_weeks"
BADGE_QUEST_MONDAY = "quest_monday"

# XP ve rutbe
BADGE_XP_1000 = "xp_1000"
BADGE_XP_5000 = "xp_5000"
BADGE_XP_20000 = "xp_20000"
BADGE_RANK_KNIGHT = "rank_knight"
BADGE_RANK_MASTER = "rank_master"
BADGE_RANK_COUNCIL = "rank_council"
BADGE_RANK_LEGEND = "rank_legend"
BADGE_XP_DAY_300 = "xp_day_300"
BADGE_COMPLETE = "campaign_complete"

# Zaman ve ritm
BADGE_DAWN_10 = "dawn_10"
BADGE_FRIDAY_5 = "friday_5"
BADGE_FULL_MONTH = "full_month"
BADGE_YEAR_FIRST = "year_first"

# Iletisim
BADGE_ENVOY = "envoy"
BADGE_MAIL_10 = "mail_10"
BADGE_TEAMS_10 = "teams_10"
BADGE_CONTACTS_20 = "contacts_20"
BADGE_FAST_REPLY = "fast_reply"

# Kesif ve araclar
BADGE_FIRST_FIX = "first_fix"
BADGE_FIX_25 = "fix_25"
BADGE_FIRST_EXCEL = "first_excel"
BADGE_FIRST_FLEET = "first_fleet"
BADGE_FLEET_5 = "fleet_5"
BADGE_FIRST_LOCAL_FIELD = "first_local_field"
BADGE_CARTOGRAPHER = "cartographer"

CATEGORY_TASK = "gorev"
CATEGORY_JIRA = "jira"
CATEGORY_STREAK = "seri"
CATEGORY_QUEST = "emir"
CATEGORY_RANK = "rutbe"
CATEGORY_RITUAL = "ritim"
CATEGORY_CONTACT = "iletisim"
CATEGORY_TOOL = "kesif"

BADGE_CATEGORIES: tuple[dict[str, str], ...] = (
    {"code": CATEGORY_TASK, "label": "Görev"},
    {"code": CATEGORY_JIRA, "label": "Jira akışı"},
    {"code": CATEGORY_STREAK, "label": "Seri"},
    {"code": CATEGORY_QUEST, "label": "Haftalık emirler"},
    {"code": CATEGORY_RANK, "label": "XP ve rütbe"},
    {"code": CATEGORY_RITUAL, "label": "Zaman ve ritim"},
    {"code": CATEGORY_CONTACT, "label": "İletişim"},
    {"code": CATEGORY_TOOL, "label": "Keşif ve araçlar"},
)

CATEGORY_LABELS: dict[str, str] = {
    item["code"]: item["label"] for item in BADGE_CATEGORIES
}

RARITY_COMMON = "yaygin"
RARITY_RARE = "nadir"
RARITY_LEGEND = "efsanevi"

# Nadirlik hem halkanin rengini hem rozetin odedigi XP carpanini belirler:
# "Rozet kazanildi" kuralindaki puan carpanla olceklenir.
RARITIES: tuple[dict[str, Any], ...] = (
    {"code": RARITY_COMMON, "label": "Yaygın", "multiplier": 1},
    {"code": RARITY_RARE, "label": "Nadir", "multiplier": 2},
    {"code": RARITY_LEGEND, "label": "Efsanevi", "multiplier": 4},
)

RARITY_LABELS: dict[str, str] = {item["code"]: item["label"] for item in RARITIES}
RARITY_MULTIPLIERS: dict[str, int] = {
    item["code"]: int(item["multiplier"]) for item in RARITIES
}


def _badge(code: str, label: str, hint: str, category: str, rarity: str) -> dict[str, Any]:
    return {
        "code": code,
        "label": label,
        "hint": hint,
        "category": category,
        "rarity": rarity,
    }


BADGES: tuple[dict[str, Any], ...] = (
    # --- Gorev ---
    _badge(BADGE_FIRST_TASK, "İlk Adım", "İlk görevi kapat", CATEGORY_TASK, RARITY_COMMON),
    _badge(BADGE_TASK_10, "Onlu Devriye", "10 görev kapat", CATEGORY_TASK, RARITY_COMMON),
    _badge(BADGE_CLOSER, "Kapatıcı", "25 görev kapat", CATEGORY_TASK, RARITY_RARE),
    _badge(BADGE_TASK_50, "Elli Sefer", "50 görev kapat", CATEGORY_TASK, RARITY_RARE),
    _badge(BADGE_TASK_200, "İki Yüz Görev", "200 görev kapat", CATEGORY_TASK, RARITY_LEGEND),
    _badge(BADGE_TASK_DAY_5, "Yoğun Uçuş", "Bir günde 5 görev kapat",
           CATEGORY_TASK, RARITY_RARE),
    _badge(BADGE_EARLY_10, "Zamanın Önünde", "Son tarihinden önce 10 görev bitir",
           CATEGORY_TASK, RARITY_COMMON),
    _badge(BADGE_NOTED_20, "Seyir Defteri", "Notu olan 20 görev biriktir",
           CATEGORY_TASK, RARITY_COMMON),
    _badge(BADGE_LINKED_25, "Bağlantı Subayı", "Jira kaydına bağlı 25 görev",
           CATEGORY_TASK, RARITY_RARE),
    _badge(BADGE_CLEAN_DESK, "Temiz Masa", "7 gün boyunca gecikmiş görev yok",
           CATEGORY_TASK, RARITY_RARE),
    _badge(BADGE_ARCHIVIST, "Arşivci", "30 günden eski 20 tamamlanmış görev katlandı",
           CATEGORY_TASK, RARITY_COMMON),
    # --- Jira akisi ---
    _badge(BADGE_FIRST_ISSUE, "İlk Kapanış", "İlk Jira kaydı tamamlandı",
           CATEGORY_JIRA, RARITY_COMMON),
    _badge(BADGE_FINISHER, "Bitirici", "Bir filtre filosundan 20 kayıt düştü",
           CATEGORY_JIRA, RARITY_RARE),
    _badge(BADGE_DROP_25, "Filo Süpürgesi", "Filtre filolarından 25 kayıt düştü",
           CATEGORY_JIRA, RARITY_COMMON),
    _badge(BADGE_DROP_100, "Yüz Kayıt", "Filtre filolarından 100 kayıt düştü",
           CATEGORY_JIRA, RARITY_RARE),
    _badge(BADGE_DROP_500, "Beş Yüz Kayıt", "Filtre filolarından 500 kayıt düştü",
           CATEGORY_JIRA, RARITY_LEGEND),
    _badge(BADGE_WEEK_20, "Haftanın Fırtınası", "Bir hafta içinde 20 kayıt düştü",
           CATEGORY_JIRA, RARITY_RARE),
    _badge(BADGE_OLD_ISSUE, "Arkeolog", "30 günden uzun açık kalmış bir kaydı kapat",
           CATEGORY_JIRA, RARITY_RARE),
    _badge(BADGE_THREE_PROJECTS, "Üç Cephe", "Aynı gün üç farklı projeden kayıt ilerlet",
           CATEGORY_JIRA, RARITY_RARE),
    _badge(BADGE_LOCAL_50, "Kayıt Tutan", "50 yerel alan hücresi doldur",
           CATEGORY_JIRA, RARITY_COMMON),
    # --- Seri ---
    _badge(BADGE_STREAK_5, "Beş Gün", "5 iş günü kesintisiz seri",
           CATEGORY_STREAK, RARITY_COMMON),
    _badge(BADGE_STREAK_20, "Yirmi Gün", "20 iş günü kesintisiz seri",
           CATEGORY_STREAK, RARITY_RARE),
    _badge(BADGE_STREAK_30, "Otuz Gün", "30 iş günü kesintisiz seri",
           CATEGORY_STREAK, RARITY_RARE),
    _badge(BADGE_STREAK_60, "Altmış Gün", "60 iş günü kesintisiz seri",
           CATEGORY_STREAK, RARITY_LEGEND),
    _badge(BADGE_GRACE_SAVED, "Güç Kalkanı", "Güç koruması bir seriyi kurtardı",
           CATEGORY_STREAK, RARITY_RARE),
    _badge(BADGE_STREAK_AGAIN, "Küllerinden", "Seri kırıldıktan sonra yeniden 10 güne çık",
           CATEGORY_STREAK, RARITY_RARE),
    # --- Haftalik emirler ---
    _badge(BADGE_FIRST_QUEST, "İlk Emir", "Bir haftalık emri tamamla",
           CATEGORY_QUEST, RARITY_COMMON),
    _badge(BADGE_QUEST_4_WEEKS, "Dört Hafta Disiplin",
           "Dört hafta üst üste o haftanın bütün emirlerini bitir",
           CATEGORY_QUEST, RARITY_LEGEND),
    _badge(BADGE_QUEST_MONDAY, "Pazartesi Fırtınası",
           "Bir haftanın bütün emirlerini pazartesi bitir", CATEGORY_QUEST, RARITY_RARE),
    # --- XP ve rutbe ---
    _badge(BADGE_XP_1000, "Bin Işık", "Bir seferde 1.000 XP topla",
           CATEGORY_RANK, RARITY_COMMON),
    _badge(BADGE_XP_5000, "Beş Bin Işık", "Bir seferde 5.000 XP topla",
           CATEGORY_RANK, RARITY_RARE),
    _badge(BADGE_XP_20000, "Yirmi Bin Işık", "Bir seferde 20.000 XP topla",
           CATEGORY_RANK, RARITY_LEGEND),
    _badge(BADGE_RANK_KNIGHT, "Şövalye Yemini", "Şövalye rütbesine çık",
           CATEGORY_RANK, RARITY_COMMON),
    _badge(BADGE_RANK_MASTER, "Usta Kürsüsü", "Usta rütbesine çık",
           CATEGORY_RANK, RARITY_RARE),
    _badge(BADGE_RANK_COUNCIL, "Konsey Koltuğu", "Konsey Üyesi rütbesine çık",
           CATEGORY_RANK, RARITY_RARE),
    _badge(BADGE_RANK_LEGEND, "Efsane Adı", "Efsane rütbesine çık",
           CATEGORY_RANK, RARITY_LEGEND),
    _badge(BADGE_XP_DAY_300, "Tek Günde Üç Yüz", "Bir günde 300 XP topla",
           CATEGORY_RANK, RARITY_RARE),
    _badge(BADGE_COMPLETE, "Sefer Tamam", "Sefer hedefine ulaşıldı",
           CATEGORY_RANK, RARITY_RARE),
    # --- Zaman ve ritim ---
    _badge(BADGE_DAWN_10, "Şafak Nöbeti", "Sabah 08:00'den önce 10 görev kapat",
           CATEGORY_RITUAL, RARITY_RARE),
    _badge(BADGE_FRIDAY_5, "Cuma Kapanışı", "Cuma öğleden sonra 5 görev kapat",
           CATEGORY_RITUAL, RARITY_COMMON),
    _badge(BADGE_FULL_MONTH, "Dolu Ay", "Bir ayın her iş gününde etkin ol",
           CATEGORY_RITUAL, RARITY_LEGEND),
    _badge(BADGE_YEAR_FIRST, "Yılın İlk Nöbeti", "Yılın ilk iş gününde etkin ol",
           CATEGORY_RITUAL, RARITY_RARE),
    # --- Iletisim ---
    _badge(BADGE_ENVOY, "Elçi", "20 kez son durum soruldu",
           CATEGORY_CONTACT, RARITY_COMMON),
    _badge(BADGE_MAIL_10, "Posta Kuryesi", "E-posta ile 10 kayıt gönder",
           CATEGORY_CONTACT, RARITY_COMMON),
    _badge(BADGE_TEAMS_10, "Kanal Sesi", "10 farklı kayıt için Teams mesajı aç",
           CATEGORY_CONTACT, RARITY_COMMON),
    _badge(BADGE_CONTACTS_20, "Adres Defteri", "Adres defterinde 20 kişi biriktir",
           CATEGORY_CONTACT, RARITY_COMMON),
    _badge(BADGE_FAST_REPLY, "Hızlı Yanıt", "10 e-posta görevi 24 saat içinde ele alındı",
           CATEGORY_CONTACT, RARITY_RARE),
    # --- Kesif ve araclar ---
    _badge(BADGE_FIRST_FIX, "İlk Düzeltme", "Copilot ile ilk metin düzeltmesi",
           CATEGORY_TOOL, RARITY_COMMON),
    _badge(BADGE_FIX_25, "Metin Ustası", "25 metin düzeltmesi", CATEGORY_TOOL, RARITY_RARE),
    _badge(BADGE_FIRST_EXCEL, "İlk Döküm", "İlk Excel dışa aktarımı",
           CATEGORY_TOOL, RARITY_COMMON),
    _badge(BADGE_FIRST_FLEET, "İlk Filo", "İlk filoyu yarat", CATEGORY_TOOL, RARITY_COMMON),
    _badge(BADGE_FLEET_5, "Filo Komutanı", "5 filo yönet", CATEGORY_TOOL, RARITY_RARE),
    _badge(BADGE_FIRST_LOCAL_FIELD, "İlk Yerel Alan", "İlk yerel alanı tanımla",
           CATEGORY_TOOL, RARITY_COMMON),
    _badge(BADGE_CARTOGRAPHER, "Haritacı", "Bütün filolarda sütun düzeni tanımlı",
           CATEGORY_TOOL, RARITY_RARE),
)

BADGE_CODES: tuple[str, ...] = tuple(badge["code"] for badge in BADGES)
BADGES_BY_CODE: dict[str, dict[str, Any]] = {badge["code"]: badge for badge in BADGES}

# Rozet simgeleri: her kod icin ayri bir SVG (24x24, cizgi tabanli, kategori
# cercevesi + nadirlik halkasi). `tools/rozet_simgeleri.py` uretir.
ICON_DIR = "/static/rozetler/"

# Kod -> (seri adi, esik). Seri = sirali damga listesi.
SERIES_BADGES: dict[str, tuple[str, int]] = {
    BADGE_FIRST_TASK: ("task_done", 1),
    BADGE_TASK_10: ("task_done", 10),
    BADGE_CLOSER: ("task_done", 25),
    BADGE_TASK_50: ("task_done", 50),
    BADGE_TASK_200: ("task_done", 200),
    BADGE_EARLY_10: ("task_early", 10),
    BADGE_FIRST_ISSUE: ("issue_done", 1),
    BADGE_DROP_25: ("issue_drop", 25),
    BADGE_DROP_100: ("issue_drop", 100),
    BADGE_DROP_500: ("issue_drop", 500),
    BADGE_FIRST_QUEST: ("quest_done", 1),
    BADGE_FAST_REPLY: ("mail_fast", 10),
    BADGE_DAWN_10: ("dawn", 10),
    BADGE_FRIDAY_5: ("friday", 5),
    BADGE_FIRST_FIX: ("fix", 1),
    BADGE_FIX_25: ("fix", 25),
    BADGE_FIRST_EXCEL: ("excel", 1),
}

SERIES_NAMES: tuple[str, ...] = (
    "task_done", "task_early", "issue_done", "issue_drop", "quest_done",
    "mail_fast", "dawn", "friday", "fix", "excel",
)

# Kod -> (olcu adi, esik). Olcu anlik sayidir; kazanma ani bugundur.
GAUGE_BADGES: dict[str, tuple[str, int]] = {
    BADGE_NOTED_20: ("noted_tasks", 20),
    BADGE_LINKED_25: ("linked_tasks", 25),
    BADGE_CLEAN_DESK: ("clean_days", CLEAN_DESK_DAYS),
    BADGE_ARCHIVIST: ("old_done", 20),
    BADGE_FINISHER: ("group_max", 20),
    BADGE_LOCAL_50: ("local_values", 50),
    BADGE_STREAK_5: ("streak", 5),
    BADGE_STREAK_20: ("streak", 20),
    BADGE_STREAK_30: ("streak", 30),
    BADGE_STREAK_60: ("streak", 60),
    BADGE_XP_1000: ("xp", 1000),
    BADGE_XP_5000: ("xp", 5000),
    BADGE_XP_20000: ("xp", 20000),
    BADGE_ENVOY: ("asked", 20),
    BADGE_MAIL_10: ("mail_issues", 10),
    BADGE_TEAMS_10: ("teams_issues", 10),
    BADGE_CONTACTS_20: ("contacts", 20),
    BADGE_FIRST_FLEET: ("fleets", 1),
    BADGE_FLEET_5: ("fleets", 5),
    BADGE_FIRST_LOCAL_FIELD: ("local_fields", 1),
}

GAUGE_NAMES: tuple[str, ...] = (
    "noted_tasks", "linked_tasks", "clean_days", "old_done", "group_max",
    "local_values", "streak", "xp", "asked", "mail_issues", "teams_issues",
    "contacts", "fleets", "local_fields",
)

# Kod -> bayrak adi. "Oldu ya da olmadi": ara ilerleme anlamli degildir.
FLAG_BADGES: dict[str, str] = {
    BADGE_TASK_DAY_5: "task_day_5",
    BADGE_WEEK_20: "week_20",
    BADGE_OLD_ISSUE: "old_issue",
    BADGE_THREE_PROJECTS: "three_projects",
    BADGE_GRACE_SAVED: "grace_saved",
    BADGE_STREAK_AGAIN: "streak_again",
    BADGE_QUEST_4_WEEKS: "quest_4_weeks",
    BADGE_QUEST_MONDAY: "quest_monday",
    BADGE_XP_DAY_300: "xp_day_300",
    BADGE_FULL_MONTH: "full_month",
    BADGE_YEAR_FIRST: "year_first",
    BADGE_COMPLETE: "target_reached",
    BADGE_CARTOGRAPHER: "all_columns",
}

FLAG_NAMES: tuple[str, ...] = tuple(sorted(set(FLAG_BADGES.values())))

# Rutbe rozetleri: esik seferin hedefine oranlidir, o yuzden ayri tablo.
RANK_BADGES: dict[str, str] = {
    BADGE_RANK_KNIGHT: "knight",
    BADGE_RANK_MASTER: "master",
    BADGE_RANK_COUNCIL: "council",
    BADGE_RANK_LEGEND: "legend",
}

# Padawan rozeti YOKTUR: sefer o rutbeyle baslar, kazanilacak bir sey degil.

# Bir gunde kac XP "buyuk gun" sayilir.
BIG_DAY_XP = 300
# Bir haftada kac kayit dusmesi "firtina" sayilir.
STORM_WEEK_DROPS = 20
# Bir gunde kac gorev kapanmasi "yogun ucus" sayilir.
BUSY_DAY_TASKS = 5
# Kac gunden uzun acik kalmis kayit "eski" sayilir.
OLD_ISSUE_DAYS = 30
# Sabah nobeti bu saatten once biten gorevleri sayar.
DAWN_HOUR = 8
# Cuma kapanisi bu saatten sonra biten gorevleri sayar.
FRIDAY_HOUR = 13
# "Kullerinden" rozeti icin kirilmadan sonra ulasilmasi gereken seri.
STREAK_AGAIN_DAYS = 10
# "Dort hafta disiplin" icin ust uste tam hafta sayisi.
QUEST_WEEKS = 4


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
    at: str | None = None,
) -> dict[str, Any] | None:
    """Tek bir defter satiri. Sefer yoksa, kural kapaliysa ya da olay zaten
    defterdeyse `None` doner.

    `at` verilirse satir O ANA yazilir: geriye donuk verilen rozet defterde de
    gercek tarihiyle durur.
    """
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
        conn, campaign["id"], source, kind, value, ref, title, note,
        at=at or stamp_of(now),
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


# --- rozetler: degerlendirme ---------------------------------------------


def _check_badges(context: Any, campaign: dict[str, Any], now: datetime | None) -> list[str]:
    """Hak edilmis ama henuz verilmemis rozetleri yazar.

    Kosullar gecmis veriden olculdugu icin bu ayni zamanda GERIYE DONUK
    degerlendirmedir: eski bir kurulumda panel ilk acildiginda (ya da ilk
    Guncelle'de) gecmisten hak edilen rozetler topluca duser. Kazanma tarihi
    mumkunse gercek olayin damgasidir, degilse bugun.
    """
    conn = context.connection()
    earned = store.earned_badges(conn, campaign["id"])
    facts = badge_facts(context, campaign, now)
    rule = enabled_rule(conn, KIND_BADGE_EARNED)
    base = int(rule["points"]) if rule else 0
    fresh: list[str] = []
    for badge in BADGES:
        code = badge["code"]
        if code in earned or not _badge_met(code, facts):
            continue
        at = _badge_at(code, facts) or stamp_of(now)
        if store.earn_badge(conn, campaign["id"], code, at):
            fresh.append(code)
            award(
                context, SOURCE_BADGE, KIND_BADGE_EARNED, f"badge:{code}",
                f"Rozet kazanıldı: {badge['label']}",
                points=base * RARITY_MULTIPLIERS.get(badge["rarity"], 1) or None,
                now=now, at=at,
            )
    return fresh


def badge_progress(code: str, facts: dict[str, Any]) -> tuple[int, int]:
    """Rozetin (ilerleme, hedef) ikilisi. Kilitli kutucuktaki "12/25" budur.

    Bayrak rozetlerinde ara deger yoktur: 0/1 ya da 1/1.
    """
    series = SERIES_BADGES.get(code)
    if series is not None:
        name, need = series
        return min(len(facts["series"].get(name, ())), need), need
    gauge = GAUGE_BADGES.get(code)
    if gauge is not None:
        name, need = gauge
        return min(int(facts["gauges"].get(name, 0)), need), need
    rank_code = RANK_BADGES.get(code)
    if rank_code is not None:
        need = int(facts["rank_needs"].get(rank_code, 0))
        return (min(int(facts["gauges"].get("xp", 0)), need), need) if need > 0 else (0, 1)
    flag = FLAG_BADGES.get(code)
    if flag is not None:
        return (1, 1) if facts["flags"].get(flag) else (0, 1)
    return 0, 1


def _badge_met(code: str, facts: dict[str, Any]) -> bool:
    value, need = badge_progress(code, facts)
    return need > 0 and value >= need


def _badge_at(code: str, facts: dict[str, Any]) -> str:
    """Kazanmanin GERCEK ani, bilinebiliyorsa.

    Sayilabilir rozetlerde esigi dolduran olayin damgasidir: gecmis veriden
    verilen rozet "bugun kazanildi" diye gorunmez.
    """
    series = SERIES_BADGES.get(code)
    if series is None:
        return ""
    name, need = series
    stamps = facts["series"].get(name) or []
    return stamps[need - 1] if len(stamps) >= need else ""


# --- rozetler: gercekler --------------------------------------------------


def _local_hour(value: Any) -> int:
    """Saklanmis damganin YEREL saati; okunamazsa -1."""
    parsed = field_utils.parse_moment(value)
    if parsed is None:
        return -1
    return parsed.astimezone().hour


def _project_of(key: str) -> str:
    """'ABC-123' -> 'ABC'. Proje onu yoksa bos."""
    text = str(key or "").strip().upper()
    return text.split("-")[0] if "-" in text else ""


def badge_facts(
    context: Any, campaign: dict[str, Any], now: datetime | None = None
) -> dict[str, Any]:
    """Butun rozet kosullari tek okumada; hepsi gercek veriden.

    Uc kova doner: `series` (sirali damga listeleri), `gauges` (anlik sayilar),
    `flags` (oldu/olmadi). Ayrica rutbe esikleri (`rank_needs`) -- onlar sefer
    hedefine oranli oldugu icin katalogda sabit duramaz.
    """
    conn = context.connection()
    board = _board_facts(context, now)
    start = campaign["starts_at"] or ""

    series: dict[str, list[str]] = {name: [] for name in SERIES_NAMES}
    day_tasks: dict[str, int] = {}
    day_points: dict[str, int] = {}
    day_projects: dict[str, set[str]] = {}
    week_drops: dict[str, int] = {}
    done_keys: dict[str, str] = {}
    active_days: set[str] = set()

    events = store.list_events(conn, campaign["id"], limit=store.LEDGER_MAX)
    for event in sorted(events, key=lambda item: (item["at"], item["id"])):
        stamp = event["at"]
        kind = event["kind"]
        day = local_day(stamp)
        active_days.add(day)
        day_points[day] = day_points.get(day, 0) + int(event["points"])
        if kind in (KIND_DONE, KIND_DONE_OVERDUE):
            series["task_done"].append(stamp)
            day_tasks[day] = day_tasks.get(day, 0) + 1
            hour = _local_hour(stamp)
            if 0 <= hour < DAWN_HOUR:
                series["dawn"].append(stamp)
            weekday = parse_day(day)
            if weekday is not None and weekday.weekday() == 4 and hour >= FRIDAY_HOUR:
                series["friday"].append(stamp)
        elif kind == KIND_DONE_BEFORE_DUE:
            series["task_early"].append(stamp)
        elif kind == KIND_MAIL_FAST:
            series["mail_fast"].append(stamp)
        elif kind == KIND_QUEST_DONE:
            series["quest_done"].append(stamp)
        elif kind == KIND_STATUS_DONE:
            series["issue_done"].append(stamp)
            key = event["ref"].split(":", 1)[-1]
            done_keys.setdefault(key.upper(), stamp)
            project = _project_of(key)
            if project:
                day_projects.setdefault(day, set()).add(project)
        elif kind == KIND_LEFT_GROUP:
            series["issue_drop"].append(stamp)
            parsed = parse_day(day)
            if parsed is not None:
                week = week_start_of(parsed)
                week_drops[week] = week_drops.get(week, 0) + 1
            project = _project_of(event["ref"].split(":", 1)[-1])
            if project:
                day_projects.setdefault(day, set()).add(project)

    # Sefere bagli olmayan is izleri: seferin baslangicindan sonrakiler sayilir.
    for name, kind in (("fix", store.ACTIVITY_FIX), ("excel", store.ACTIVITY_EXCEL)):
        series[name] = [
            stamp for stamp in store.activity_stamps(conn, kind) if local_day(stamp) >= start
        ]

    marks = store.marked_days(conn, campaign["id"])
    total = store.total_xp(conn, campaign["id"])
    target = int(campaign["target_xp"] or 0)

    gauges = {
        "noted_tasks": 0,
        "linked_tasks": 0,
        "clean_days": _clean_days(context, board["overdue"], now),
        "old_done": board["old_done"],
        "group_max": 0,
        "local_values": _local_value_count(conn),
        "streak": _best_streak(marks),
        "xp": total,
        "asked": _asked_count(context, campaign),
        "mail_issues": _mailed_issue_count(conn, start),
        "teams_issues": len(_asked_keys(conn, start)),
        "contacts": _row_count(conn, "contacts"),
        "fleets": 0,
        "local_fields": len(repository.list_local_fields(conn)),
    }

    groups = repository.list_groups(conn)
    gauges["fleets"] = len(groups)
    for group in groups:
        if group["kind"] != repository.KIND_FILTER:
            continue
        gauges["group_max"] = max(
            gauges["group_max"],
            store.ref_prefix_count(conn, campaign["id"], KIND_LEFT_GROUP, f"{group['id']}:"),
        )

    for task in repository.list_tasks(conn):
        if (task.get("note") or "").strip():
            gauges["noted_tasks"] += 1
        if (task.get("issue_key") or "").strip():
            gauges["linked_tasks"] += 1

    quests = store.list_quests(conn, campaign["id"])
    flags = {
        "task_day_5": any(count >= BUSY_DAY_TASKS for count in day_tasks.values()),
        "week_20": any(count >= STORM_WEEK_DROPS for count in week_drops.values()),
        "xp_day_300": any(points >= BIG_DAY_XP for points in day_points.values()),
        "three_projects": any(len(names) >= 3 for names in day_projects.values()),
        "old_issue": _closed_an_old_issue(conn, done_keys),
        "grace_saved": store.STREAK_GRACE in marks.values(),
        "streak_again": _streak_after_break(marks),
        "quest_4_weeks": _full_weeks_in_a_row(quests) >= QUEST_WEEKS,
        "quest_monday": _finished_a_week_on_monday(quests),
        "full_month": _full_month(marks, active_days),
        "year_first": _worked_year_opening(active_days),
        "target_reached": target > 0 and total >= target,
        "all_columns": bool(groups) and all(group["columns"] for group in groups),
    }

    return {
        "series": series,
        "gauges": gauges,
        "flags": flags,
        "rank_needs": {
            rank["code"]: int(round(rank["at"] * target)) for rank in RANKS
        },
    }


def _row_count(conn: Any, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()["c"])


def _local_value_count(conn: Any) -> int:
    """Doldurulmus yerel alan hucresi sayisi (bos deger sayilmaz)."""
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM local_values WHERE value IS NOT NULL AND TRIM(value) <> ''"
    ).fetchone()
    return int(row["c"])


def _mailed_issue_count(conn: Any, start: str) -> int:
    """Sefer basladiktan sonra e-postayla gonderilen kayit sayisi."""
    rows = conn.execute("SELECT issue_count, sent_at FROM mail_sends").fetchall()
    return sum(
        int(row["issue_count"] or 0) for row in rows if local_day(row["sent_at"]) >= start
    )


def _asked_keys(conn: Any, start: str) -> set[str]:
    """Sefer icinde Teams mesaji acilan FARKLI kayitlar."""
    rows = conn.execute(
        "SELECT issue_key, opened_at FROM sent_messages WHERE opened_at IS NOT NULL"
    ).fetchall()
    return {row["issue_key"] for row in rows if local_day(row["opened_at"]) >= start}


def _closed_an_old_issue(conn: Any, done_keys: dict[str, str]) -> bool:
    """30 gunden uzun acik kalmis bir kayit kapatildi mi.

    Kaydin `created` alani yereldeki kopyadan okunur; kayit silinmisse ya da
    tarih okunamiyorsa o kayit sessizce atlanir (yanlis rozetten yoksun rozet
    yegdir).
    """
    if not done_keys:
        return False
    stored = repository.get_issues(conn, list(done_keys))
    for key, stamp in done_keys.items():
        record = stored.get(key)
        if record is None:
            continue
        created = field_utils.parse_moment(
            ((record.get("raw") or {}).get("fields") or {}).get("created")
        )
        closed = field_utils.parse_moment(stamp)
        if created is None or closed is None:
            continue
        if (closed - created) >= timedelta(days=OLD_ISSUE_DAYS):
            return True
    return False


def _streak_after_break(marks: dict[str, str]) -> bool:
    """Seri kirildiktan SONRA yeniden 10 is gunune ulasildi mi.

    Ilk seri sayilmaz: rozetin anlattigi sey "dusup kalkmak". Bir serinin
    "yeniden" olmasi, baslangicindan onceki is gununun isaretsiz olmasi ve
    ondan once de en az bir isaretli gun bulunmasidir.
    """
    days = sorted(day for day in marks if parse_day(day) is not None)
    if not days:
        return False
    first = parse_day(days[0])
    for text in days:
        day = parse_day(text)
        if day is None or _streak_length(marks, day) < STREAK_AGAIN_DAYS:
            continue
        # Serinin basi: geriye dogru isaretsiz ilk gune kadar yuru.
        head = day
        while True:
            earlier = previous_workday(head)
            if earlier.isoformat() not in marks:
                break
            head = earlier
        if first is not None and head > first:
            return True
    return False


def _full_weeks_in_a_row(quests: list[dict[str, Any]]) -> int:
    """Butun emirleri biten en uzun ARDISIK hafta dizisi."""
    weeks: dict[str, list[dict[str, Any]]] = {}
    for quest in quests:
        weeks.setdefault(quest["week_start"], []).append(quest)
    best = 0
    run = 0
    previous: date | None = None
    for week in sorted(weeks):
        day = parse_day(week)
        if day is None:
            continue
        whole = bool(weeks[week]) and all(item["done"] for item in weeks[week])
        if not whole:
            run = 0
            previous = day
            continue
        run = run + 1 if previous is not None and (day - previous).days == 7 else 1
        best = max(best, run)
        previous = day
    return best


def _finished_a_week_on_monday(quests: list[dict[str, Any]]) -> bool:
    """Bir haftanin butun emirleri o haftanin pazartesisi bitti mi."""
    weeks: dict[str, list[dict[str, Any]]] = {}
    for quest in quests:
        weeks.setdefault(quest["week_start"], []).append(quest)
    for week, items in weeks.items():
        if not items or not all(item["done"] for item in items):
            continue
        if all(local_day(item["done_at"]) == week for item in items):
            return True
    return False


def _full_month(marks: dict[str, str], active_days: set[str]) -> bool:
    """Bir takvim ayinin HER is gununde etkinlik var mi.

    Icinde bulunulan ay sayilmaz: ay bitmeden "dolu" denemez. Gun ya seri
    isaretiyle ya da deftere dusmus bir olayla dolu sayilir.
    """
    filled = set(marks) | set(active_days)
    months = {day[:7] for day in filled if len(day) >= 7}
    if not months:
        return False
    newest = max(months)
    for month in sorted(months):
        if month == newest:
            continue
        first = parse_day(month + "-01")
        if first is None:
            continue
        cursor = first
        whole = True
        while cursor.isoformat()[:7] == month:
            if is_workday(cursor) and cursor.isoformat() not in filled:
                whole = False
                break
            cursor += timedelta(days=1)
        if whole:
            return True
    return False


def _worked_year_opening(active_days: set[str]) -> bool:
    """Yilin ILK is gununde deftere bir sey dustu mu."""
    for day in active_days:
        parsed = parse_day(day)
        if parsed is None or not is_workday(parsed):
            continue
        opening = date(parsed.year, 1, 1)
        while not is_workday(opening):
            opening += timedelta(days=1)
        if parsed == opening:
            return True
    return False


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


def _painted_badges() -> set[str]:
    """Elle cizilmis PNG'si OLAN rozetler.

    Hepsinin PNG'si yok (54 rozete 54 resim cizilmedi); olmayan icin arayuz
    rozetin kendi SVG simgesini gosterir. Dosya varligi burada bir kez okunur,
    yoksa her cizimde 40 tane bos istek atilirdi.
    """
    global _PAINTED
    if _PAINTED is None:
        folder = Path(__file__).resolve().parent / "static" / "img" / "gamify"
        try:
            names = {item.name for item in folder.iterdir()}
        except OSError:
            names = set()
        _PAINTED = {code for code in BADGE_CODES if f"badge-{code}.png" in names}
    return _PAINTED


_PAINTED: set[str] | None = None


def badge_wall(
    context: Any, campaign: dict[str, Any], now: datetime | None
) -> list[dict[str, Any]]:
    """Rozet duvari: kazanilmis / kilitli, kategori, nadirlik, ilerleme.

    Kilitli kutucuk da bilgi tasir: ipucu ("nasil kazanilir") ve "12/25"
    ilerlemesi. Boylece duvar bir gorev listesi gibi de okunur.
    """
    earned = store.earned_badges(context.connection(), campaign["id"])
    facts = badge_facts(context, campaign, now)
    painted = _painted_badges()
    wall: list[dict[str, Any]] = []
    for badge in BADGES:
        code = badge["code"]
        value, need = badge_progress(code, facts)
        won = code in earned
        wall.append(
            {
                "code": code,
                "label": badge["label"],
                "hint": badge["hint"],
                "category": badge["category"],
                "category_label": CATEGORY_LABELS.get(badge["category"], badge["category"]),
                "rarity": badge["rarity"],
                "rarity_label": RARITY_LABELS.get(badge["rarity"], badge["rarity"]),
                "icon": f"{ICON_DIR}{code}.svg",
                "image": f"{IMAGE_DIR}badge-{code}.png" if code in painted else "",
                "earned": won,
                "earned_at": earned.get(code, ""),
                "progress": need if won else value,
                "target": need,
                # Tek adimlik rozette "1/1" bilgi tasimaz; cubuk gizlenir.
                "show_progress": need > 1,
                "percent": 100 if won else (min(100, round(value * 100 / need)) if need else 0),
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
        "badge_categories": [dict(item) for item in BADGE_CATEGORIES],
        "rarities": [
            {"code": item["code"], "label": item["label"]} for item in RARITIES
        ],
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
