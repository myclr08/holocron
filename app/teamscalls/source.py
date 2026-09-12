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

# Kayitlarin hangi klasorden okundugu: gecici kopya ya da canli klasor.
SOURCE_COPY = "copy"
SOURCE_LIVE = "live"


def empty_diagnostics() -> dict[str, Any]:
    """Tarama teshisi: kac dosya kopyalandi, kac tanesi atlandi, nereden okundu."""
    return {
        "copied": 0,
        "skipped": 0,
        "skipped_kinds": {},
        "source": "",
        "warning": "",
        # Kac veritabani acildi ve okuma kac milisaniye surdu.
        "databases": 0,
        "read_ms": 0,
    }

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

    Zaman alanlari ISO 8601 UTC metnidir (`2026-09-11T08:30:00Z` gibi) ama
    epoch milisaniye ya da hazir `datetime` de kabul edilir: Teams'in kendi
    store'lari her ucunu de kullaniyor. Cevrim `intake` icinde yapilir ki ham
    kayit oldugu gibi saklanabilsin.

    `thread_id` toplanti sohbetinin (`19:meeting_...@thread.v2`),
    `group_thread_id` ise grup sohbetinin kimligidir; takvim ve sohbet
    eslemesi bunlarla yapilir.
    """

    call_id: str
    start_time: Any = ""
    end_time: Any = ""
    connect_time: Any = ""
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
    group_thread_id: str = ""
    subject: str = ""
    participants: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class CalendarRecord:
    """Takvim kaydi; toplanti aramasini adiyla eslestirmek icin.

    `start_time` / `end_time` Teams'in takvim store'unda **`datetime`** olarak
    durur (JS `Date` nesnesi olarak seri hale getirilmis); eski surumlerde ve
    disa aktarimlarda yerel saat metni ("YYYY-MM-DD HH:MM:SS") ya da epoch
    milisaniye de gorulur. Ucu de kabul edilir; cevrim `intake` icinde.

    `cid` toplanti sohbetinin kimligidir (`skypeTeamsDataObj.cid`): aramanin
    `threadId` degeriyle esitse eslesme KESINDIR, zaman yakinligina gerek yok.
    """

    event_id: str = ""
    start_time: Any = ""
    end_time: Any = ""
    subject: str = ""
    organizer_name: str = ""
    organizer_address: str = ""
    my_response: str = ""
    is_online_meeting: bool = False
    is_cancelled: bool = False
    location: str = ""
    event_type: str = ""
    show_as: str = ""
    cid: str = ""
    meeting_url: str = ""
    attendees: list[str] = field(default_factory=list)


@dataclass
class ThreadRecord:
    """Sohbet (konusma) kaydi: grup aramasinin adi ve uyeleri.

    Grup aramasinin `groupChatThreadId` degeri buradaki kimlige denk gelir;
    baslik "Grup araması" yerine sohbetin kendi adi olur.
    """

    thread_id: str = ""
    topic: str = ""
    members: list[str] = field(default_factory=list)
    member_names: list[str] = field(default_factory=list)


@runtime_checkable
class CallSource(Protocol):
    """Arama gecmisi kaynagi sozlesmesi.

    Tek cagri dort sey dondurur: aramalar, takvim kayitlari, kimlik -> ad
    sozlugu ve sohbetler.
    """

    def read(self) -> tuple[
        list[CallRecord], list[CalendarRecord], dict[str, str], list[ThreadRecord]
    ]:
        """(aramalar, takvim kayitlari, kimlik -> ad, sohbetler).

        Dordu birlikte doner cunku hepsi ayni veritabani acilisindan cikar;
        ayri ayri okumak onbellegi dort kez acmak demek olurdu. Eski uc'lu
        donusler de kabul edilir (`intake.scan` dorde tamamlar).
        """


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


def decode_text(value: Any) -> str:
    """Ham degeri metne cevirir. Teams bazi dizgeleri **bytes** olarak yaziyor.

    `originatorParticipant.displayName`, takvim konusu, profil adi: hepsi
    kimi kayitta `str`, kimisinde `bytes` geliyor. Bytes once UTF-8 ile
    cozulur; gecerli UTF-8 degilse latin-1 denenir -- eski Windows kod
    sayfasindan gelen adlar (`\xdc` -> "Ü") boyle okunur. Latin-1 de bos
    birakirsa son care UTF-8'in "replace" kipidir: hicbir durumda ham
    `b'...'` gosterimi ekrana cikmaz.
    """
    if value is None:
        return ""
    if isinstance(value, (bytes, bytearray, memoryview)):
        raw = bytes(value)
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            pass
        text = raw.decode("latin-1", "replace")
        return text if text.strip() else raw.decode("utf-8", "replace")
    return str(value)


def clean_text(value: Any) -> str:
    """Tek satirlik metin: bytes cozulur, bosluklar kirpilir, None bos metne duser."""
    return decode_text(value).strip()


def jsonable(value: Any) -> Any:
    """Ham kaydi JSON'a yazilabilir hale getirir (bytes -> metin, tarih -> ISO)."""
    if isinstance(value, (bytes, bytearray, memoryview)):
        return decode_text(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def as_int(value: Any) -> int | None:
    """Sayiya cevirir; bool ve cevrilemeyen deger None doner."""
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


# 13 haneli epoch araligi (milisaniye): 2001 ile 2286 arasi.
EPOCH_MS_LOW = 1_000_000_000_000
EPOCH_MS_HIGH = 9_999_999_999_999


def epoch_moment(value: Any) -> datetime | None:
    """13 haneli epoch milisaniye -> UTC damga; degilse None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = int(value)
    if not (EPOCH_MS_LOW <= number <= EPOCH_MS_HIGH):
        return None
    try:
        return datetime.fromtimestamp(number / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):  # pragma: no cover
        return None


def parse_utc(value: Any) -> datetime | None:
    """Arama damgasi -> saat dilimli an.

    Uc bicim gelir: hazir `datetime`, 13 haneli epoch milisaniye
    (`originalArrivalTime`) ve ISO 8601 metni. Saat dilimi yazmayan metin
    UTC sayilir: arama kayitlarinin damgasi Teams'te her zaman UTC'dir.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    stamped = epoch_moment(value)
    if stamped is not None:
        return stamped
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
    """Takvim damgasi -> saat dilimli an.

    Takvim store'u **`datetime`** yaziyor (JS `Date`) ve okuyucu bunu saat
    dilimsiz veriyor; degerin kendisi **UTC**'dir (epoch'tan uretilmis).
    Saat dilimsiz bir `datetime`i yerel saat sanmak butun takvimi UTC ile
    yerel saat arasindaki fark kadar kaydiriyordu -- 10:00'daki toplanti
    07:00 gorunuyor, ±10 dakikalik eslesme hic tutmuyordu. Kayittaki
    `utcOffset` / `eventTimeZone` alanlari bu yuzden UYGULANMAZ: deger zaten
    UTC, cift cevrim olurdu.

    Epoch milisaniye de UTC'dir. Yalnizca METIN bicimi ("YYYY-MM-DD
    HH:MM:SS") yerel saat sayilir; o bicim disa aktarimlardan gelir.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    stamped = epoch_moment(value)
    if stamped is not None:
        return stamped
    text = clean_text(value)
    if len(text) < 10:
        return None
    candidate = text[:-1] + "+00:00" if text.endswith(("Z", "z")) else text
    try:
        moment = datetime.fromisoformat(candidate)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.astimezone()


# --- alan degerlerinin normalizasyonu ------------------------------------
#
# Teams bu alanlari tutarli buyuk/kucuk harfle yazmiyor ("Incoming" /
# "incoming", "twoParty" / "TwoParty"). Karsilastirma her zaman casefold
# yapilir, saklanan deger ise tek bir kanonik bicime cevrilir.

_DIRECTION_MAP = {value.casefold(): value for value in (DIRECTION_IN, DIRECTION_OUT)}
_STATE_MAP = {value.casefold(): value for value in (STATE_ACCEPTED, STATE_MISSED, STATE_DECLINED)}
_TYPE_MAP = {value.casefold(): value for value in (TYPE_TWO_PARTY, TYPE_MULTI_PARTY)}


def _canonical(value: Any, table: dict[str, str]) -> str:
    """Bilinen degeri kanonik yazimina cevirir; bilinmeyeni oldugu gibi birakir."""
    text = clean_text(value)
    return table.get(text.casefold(), text)


def canonical_direction(value: Any) -> str:
    return _canonical(value, _DIRECTION_MAP)


def canonical_state(value: Any) -> str:
    return _canonical(value, _STATE_MAP)


def canonical_type(value: Any) -> str:
    return _canonical(value, _TYPE_MAP)


def iso_text(moment: datetime | None) -> str:
    if moment is None:
        return ""
    return moment.isoformat()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
