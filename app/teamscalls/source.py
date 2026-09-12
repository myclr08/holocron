"""Kaynak-bagimsiz Teams arama gecmisi modeli.

Burada ne IndexedDB var ne Windows: arama kaydi, takvim kaydi ve `CallSource`
sozlesmesi duruyor. Yerel onbellek okuyucusu (`teams_cache.py`) ve testlerin
bellek ici kaynagi (`fake.py`) ayni sozlesmeyi uygular, boylece is mantigi
(`intake.py`) Windows'a hic bakmadan sinanabilir.

Veri, Teams istemcisinin **Aramalar -> Gecmis** ekraninda zaten gorunen
kullanicinin KENDI arama gecmisidir; baska kullanicinin verisi yoktur ve
hicbir sey disari gitmez.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

# Yeni Teams'in (MSTeams_8wekyb3d8bbwe) WebView2 profili altindaki IndexedDB.
TEAMS_PACKAGE = "MSTeams_8wekyb3d8bbwe"
TEAMS_RELATIVE = (
    Path("Packages")
    / TEAMS_PACKAGE
    / "LocalCache"
    / "Microsoft"
    / "MSTeams"
    / "EBWebView"
    / "WV2Profile_tfw"
    / "IndexedDB"
    / "https_teams.microsoft.com_0.indexeddb.leveldb"
)

# Teams acikken LevelDB kilitli olabilir; klasor once buraya kopyalanir.
COPY_DIR_NAME = "holocron-teams-calls"

# `callDirection`
DIRECTION_IN = "Incoming"
DIRECTION_OUT = "Outgoing"
DIRECTIONS: tuple[str, ...] = (DIRECTION_IN, DIRECTION_OUT)

# `callState`
STATE_ACCEPTED = "Accepted"
STATE_MISSED = "Missed"
STATE_DECLINED = "Declined"
STATES: tuple[str, ...] = (STATE_ACCEPTED, STATE_MISSED, STATE_DECLINED)

# `callType`
TYPE_TWO_PARTY = "TwoParty"
TYPE_MULTI_PARTY = "MultiParty"

# Takvimde atlanan kayitlar: yineleyen serinin sablonu ve "ofiste degilim".
EVENT_RECURRING_MASTER = "RecurringMaster"
SHOW_AS_OOF = "Oof"


class CallsError(Exception):
    """Kullaniciya gosterilecek arama gecmisi hatasi (arayuz kodu okur)."""

    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def is_supported() -> bool:
    """Yeni Teams'in yerel onbellegi yalnizca Windows'ta var."""
    return sys.platform == "win32"


def unavailable() -> CallsError:
    return CallsError(
        "feature_unavailable",
        "Bu özellik yalnız Windows'ta yeni Teams ile çalışır.",
    )


@dataclass
class CallRecord:
    """Tek bir arama; kaynagi ne olursa olsun ayni bicimde gelir.

    Zaman alanlari ISO 8601 UTC metnidir (`2026-09-11T08:30:00Z` gibi);
    cevrim `intake` icinde yapilir ki ham kayit oldugu gibi saklanabilsin.
    """

    call_id: str
    start_time: str = ""
    end_time: str = ""
    connect_time: str = ""
    duration_ms: int | None = None
    direction: str = ""
    state: str = ""
    call_type: str = ""
    originator_id: str = ""
    originator_name: str = ""
    target_id: str = ""
    target_name: str = ""
    forwarded: str = ""
    thread_id: str = ""
    subject: str = ""
    participants: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class CalendarRecord:
    """Takvim kaydi; toplanti aramasini adiyla eslestirmek icin.

    `start_time` / `end_time` Teams'in takvim store'unda YEREL saat metnidir
    ("YYYY-MM-DD HH:MM:SS"); arama kayitlarindaki UTC damgasiyla ayni anda
    karsilastirilabilmesi icin `intake` ikisini de saat dilimli hale getirir.
    """

    event_id: str = ""
    start_time: str = ""
    end_time: str = ""
    subject: str = ""
    organizer_name: str = ""
    my_response: str = ""
    is_online_meeting: bool = False
    location: str = ""
    event_type: str = ""
    show_as: str = ""
    attendees: list[str] = field(default_factory=list)


@runtime_checkable
class CallSource(Protocol):
    """Arama gecmisi kaynagi sozlesmesi.

    Tek cagri uc sey dondurur: aramalar, takvim kayitlari ve kimlik -> ad
    sozlugu. Uc parca birlikte gelir cunku hepsi ayni veritabani acilisindan
    cikar; ayri ayri okumak onbellegi uc kez acmak demek olurdu.
    """

    def read(self) -> tuple[list[CallRecord], list[CalendarRecord], dict[str, str]]:
        """(aramalar, takvim kayitlari, kimlik -> ad)."""


# --- yollar --------------------------------------------------------------


def default_cache_path(env: dict[str, str] | None = None) -> Path:
    """Yeni Teams'in varsayilan IndexedDB yolu (%LOCALAPPDATA% altinda)."""
    source = env if env is not None else os.environ
    base = source.get("LOCALAPPDATA") or source.get("LocalAppData") or ""
    if not base:
        home = Path(source.get("USERPROFILE") or Path.home())
        base = str(home / "AppData" / "Local")
    return Path(base) / TEAMS_RELATIVE


def blob_dir_for(leveldb: Path) -> Path:
    """`...leveldb` -> `...blob` (Chromium buyuk degerleri orada tutar)."""
    return Path(leveldb).with_suffix(".blob")


def temp_root(env: dict[str, str] | None = None) -> Path:
    source = env if env is not None else os.environ
    base = source.get("TEMP") or source.get("TMP") or source.get("TMPDIR") or "/tmp"
    return Path(base) / COPY_DIR_NAME


# --- saf yardimcilar -----------------------------------------------------


def clean_text(value: Any) -> str:
    """Tek satirlik metin: bosluklar kirpilir, None bos metne duser."""
    if value is None:
        return ""
    return str(value).strip()


def as_int(value: Any) -> int | None:
    """Sayiya cevirir; bool ve cevrilemeyen deger None doner."""
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def parse_utc(value: Any) -> datetime | None:
    """ISO 8601 metni (ya da hazir `datetime`) -> saat dilimli damga.

    Saat dilimi yazmayan metin UTC sayilir: arama kayitlarinin damgasi
    Teams'te her zaman UTC'dir.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = clean_text(value)
    if len(text) < 10:
        return None
    candidate = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        moment = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def parse_local(value: Any) -> datetime | None:
    """Takvimin yerel saat metni ("YYYY-MM-DD HH:MM:SS") -> yerel damga.

    Saat dilimi tasiyan bir deger gelirse oldugu gibi kabul edilir; takvim
    store'u bunu yazmaz ama kaynak degisirse kayit dusmesin.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.astimezone()
    text = clean_text(value)
    if len(text) < 10:
        return None
    candidate = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        moment = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.astimezone()


def iso_text(moment: datetime | None) -> str:
    if moment is None:
        return ""
    return moment.isoformat()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
