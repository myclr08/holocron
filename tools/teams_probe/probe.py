"""Yeni Teams yerel onbelleginin YAPI sondasi (icerik okumaz).

Yeni Teams (Windows) sohbet, arama ve takvim verisini bir Chromium
IndexedDB'sinde tutar. Bu sonda o veritabanini acar ve **yalnizca yapisini**
yazar: hangi veritabani, hangi object store, kac kayit, hangi alan adlari,
hangi tipler, tarih alanlarinin en eski/en yeni degeri.

## Ne YAZILMAZ

Hicbir dizge (string) degeri. Ne mesaj metni, ne kisi adi, ne e-posta, ne
konu basligi. Dizge alanlari icin yalnizca uzunluk istatistigi (ortalama /
en buyuk) dokulur. Cikti baskasina yapistirilmak uzere uretilir; kisisel
icerik tasimamasi tasarim geregidir, yan etki degil.

## Ne YAZILIR

* veritabani adi, kaynak, object store listesi
* store basina kayit sayisi ve ilk 200 kaydin alan yollari (`a.b.c`, derinlik
  en fazla 4), alan tipleri, dizge uzunluk istatistigi
* tarih gibi gorunen alanlarin en eski/en yeni degeri
* `.blob` klasorunun varligi ve dosya sayisi
* okunamayan store/kayit sayilari ve istisna TURLERI (mesaj yok: mesajin
  icinde anahtar ya da yol gecebilir)

## Calistirma

    python probe.py                 # yolu %LOCALAPPDATA%'dan kurar
    python probe.py --path "D:\\kopya\\...leveldb"

Teams acikken LevelDB kilitli olabilir; klasor once `%TEMP%` altina kopyalanir,
kopyalanamayan dosya atlanir ve sayilir. Sonuc `teams-probe.txt`.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import xml.etree.ElementTree as ET
import os
import shutil
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

# --- ayarlar -------------------------------------------------------------

# Yeni Teams'in (MSTeams_8wekyb3d8bbwe) WebView2 profili altindaki IndexedDB.
TEAMS_PACKAGE = "MSTeams_8wekyb3d8bbwe"
TEAMS_RELATIVE = Path(
    "Packages"
) / TEAMS_PACKAGE / "LocalCache" / "Microsoft" / "MSTeams" / "EBWebView" / "WV2Profile_tfw" / (
    "IndexedDB"
) / "https_teams.microsoft.com_0.indexeddb.leveldb"

COPY_DIR_NAME = "holocron-teams-probe"
OUTPUT_NAME = "teams-probe.txt"

# Her store'dan kac kayit orneklenir.
SAMPLE_LIMIT = 200
# Alan yolu bu derinlikten sonra kesilir: `a.b.c.d` evet, `a.b.c.d.e` hayir.
MAX_DEPTH = 4
# Sozlukte/listede bu kadar ogeden sonrasina bakilmaz (patolojik kayitlar var).
MAX_FANOUT = 60

# Adinda bunlardan biri gecen alan tarih adayidir.
DATE_HINTS = ("time", "date", "created", "arrival", "ts", "composetime")

# 13 haneli epoch araligi: 2001-09-09 ile 2286-11-20 arasi (milisaniye).
EPOCH_MS_LOW = 1_000_000_000_000
EPOCH_MS_HIGH = 9_999_999_999_999

# Adinda bunlardan biri gecen store bir sonraki adimin adayidir.
CANDIDATE_HINTS = ("conversation", "message", "thread", "call", "people", "contact", "reply")

# Kullanicinin asil sorusunu (kiminle ne kadar gorustum) tasiyan veritabanlari;
# bunlarin TUM alan adlari ayrica dokulur.
TARGET_DB_HINTS = ("call-history", "calendar", "profile", "contact")


# --- gizlilik suzgeci ----------------------------------------------------
#
# Bu bolum sondanin kalbidir: buradan disari hicbir dizge degeri sizmaz.


def value_kind(value: Any) -> str:
    """Degerin tipi -- degerin KENDISI degil."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "bytes"
    if isinstance(value, dt.datetime):
        return "datetime"
    if isinstance(value, (list, tuple, set)):
        return "list"
    if isinstance(value, dict):
        return "dict"
    return "other"


def redact_value(value: Any) -> str:
    """Tek bir degerin ciktiya girebilecek TEK gosterimi.

    Dizgeler ve ikili veri yalnizca uzunluk olarak gecer; sayi, boolean ve
    tarih oldugu gibi yazilabilir (bunlar kisisel icerik degil). Sozluk ve
    liste yalnizca eleman sayisiyla gecer.

    Sondanin butun cikti yollari bu fonksiyondan gecer; bir dizge degerinin
    dosyaya dusmesinin tek yolu burayi degistirmektir.
    """
    kind = value_kind(value)
    if kind == "str":
        return f"str(len={len(value)})"
    if kind == "bytes":
        return f"bytes(len={len(value)})"
    if kind in ("int", "float", "bool"):
        return f"{kind}({value})"
    if kind == "datetime":
        return f"datetime({format_moment(value)})"
    if kind == "list":
        return f"list(n={len(value)})"
    if kind == "dict":
        return f"dict(n={len(value)})"
    if kind == "null":
        return "null"
    return "other"


def format_moment(moment: dt.datetime) -> str:
    """Tarihi saniye cozunurlugunde yazar; saat dilimi eki korunur."""
    try:
        return moment.replace(microsecond=0).isoformat()
    except (ValueError, OverflowError):  # pragma: no cover - akil disi damga
        return "?"


def name_is_datish(name: str) -> bool:
    """Alan adi tarih vaat ediyor mu? (`startTime`, `createdAt`, `ts` ...)"""
    marker = str(name or "").casefold()
    return any(hint in marker for hint in DATE_HINTS)


def epoch_ms_to_moment(value: Any) -> dt.datetime | None:
    """13 haneli epoch milisaniye -> tarih; degilse None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = int(value)
    if not (EPOCH_MS_LOW <= number <= EPOCH_MS_HIGH):
        return None
    try:
        return dt.datetime.fromtimestamp(number / 1000, tz=dt.timezone.utc)
    except (OverflowError, OSError, ValueError):  # pragma: no cover
        return None


def parse_moment(name: str, value: Any) -> dt.datetime | None:
    """Degeri tarihe cevirmeyi dener.

    Uc yol: gercek `datetime`, 13 haneli epoch (ad ne olursa olsun), ve adi
    tarih vaat eden alanlarda ISO benzeri metin. Ucuncu yol bilerek dar:
    ciktiya giren sey tarihtir, metnin kendisi degil.
    """
    if isinstance(value, dt.datetime):
        return value
    stamped = epoch_ms_to_moment(value)
    if stamped is not None:
        return stamped
    if isinstance(value, str) and name_is_datish(name):
        return parse_iso(value)
    return None


def parse_iso(text: str) -> dt.datetime | None:
    """"2026-09-12T08:30:00Z" ve "2026-09-12 08:30:00" bicimlerini cozer."""
    raw = str(text or "").strip()
    if len(raw) < 10 or len(raw) > 40:
        return None
    candidate = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    try:
        return dt.datetime.fromisoformat(candidate)
    except ValueError:
        return None


@dataclass
class FieldStat:
    """Tek bir alan yolu icin toplanan sayilar. Deger tutulmaz."""

    path: str
    seen: int = 0
    kinds: Counter = field(default_factory=Counter)
    str_total: int = 0
    str_max: int = 0
    str_count: int = 0
    oldest: dt.datetime | None = None
    newest: dt.datetime | None = None

    def observe(self, name: str, value: Any) -> None:
        self.seen += 1
        kind = value_kind(value)
        self.kinds[kind] += 1
        if kind == "str":
            self.str_count += 1
            self.str_total += len(value)
            self.str_max = max(self.str_max, len(value))
        moment = parse_moment(name, value)
        if moment is not None:
            aware = moment if moment.tzinfo else moment.replace(tzinfo=dt.timezone.utc)
            self.oldest = aware if self.oldest is None else min(self.oldest, aware)
            self.newest = aware if self.newest is None else max(self.newest, aware)

    @property
    def is_dated(self) -> bool:
        return self.oldest is not None

    def type_text(self) -> str:
        return "/".join(name for name, _ in self.kinds.most_common())

    def detail_text(self) -> str:
        """Alanin ciktidaki tek satirlik ozeti. Dizge degeri iceremez."""
        parts = [f"{self.seen}x", self.type_text() or "?"]
        if self.str_count:
            average = round(self.str_total / self.str_count)
            parts.append(f"uzunluk ort {average} / maks {self.str_max}")
        if self.is_dated:
            parts.append(f"{format_moment(self.oldest)} .. {format_moment(self.newest)}")
        return ", ".join(parts)


def walk_fields(value: Any, prefix: str = "", depth: int = 1) -> Iterator[tuple[str, str, Any]]:
    """Kaydi gezip `(yol, alan adi, deger)` uretir.

    Liste elemanlari `alan[]` altinda toplanir: `attendees[0]`, `attendees[1]`
    ayri alanlar degil, ayni alanin ornekleridir.
    """
    if depth > MAX_DEPTH:
        return
    if isinstance(value, dict):
        for index, (key, item) in enumerate(value.items()):
            if index >= MAX_FANOUT:
                break
            name = str(key)
            path = f"{prefix}.{name}" if prefix else name
            yield path, name, item
            if isinstance(item, (dict, list, tuple)):
                yield from walk_fields(item, path, depth + 1)
    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            if index >= MAX_FANOUT:
                break
            path = f"{prefix}[]" if prefix else "[]"
            name = prefix.rsplit(".", 1)[-1] if prefix else ""
            yield path, name, item
            if isinstance(item, (dict, list, tuple)):
                yield from walk_fields(item, path, depth + 1)


def summarize_records(records: Iterable[Any], limit: int = SAMPLE_LIMIT) -> dict[str, FieldStat]:
    """Kayit ornegini alan yolu istatistigine cevirir.

    Girdi ham kayit degerleridir (genelde sozluk). Cikti ciktiya basilabilir
    olan her seydir; icinde hicbir dizge degeri yoktur.
    """
    stats: dict[str, FieldStat] = {}
    for index, record in enumerate(records):
        if index >= limit:
            break
        if not isinstance(record, (dict, list, tuple)):
            # Kok deger sozluk degilse (sayi, metin) yine de tipi sayilir.
            entry = stats.setdefault("(kok)", FieldStat("(kok)"))
            entry.observe("", record)
            continue
        for path, name, value in walk_fields(record):
            entry = stats.setdefault(path, FieldStat(path))
            entry.observe(name, value)
    return stats


# --- kopyalama -----------------------------------------------------------


@dataclass
class CopyReport:
    """Kopyalamanin sonucu: nereye, kac dosya, kac atlama."""

    target: Path
    copied: int = 0
    skipped: int = 0
    reasons: Counter = field(default_factory=Counter)


def copy_tree(source: Path, target: Path) -> CopyReport:
    """Klasoru kopyalar; acilamayan dosyayi atlar ve sayar.

    Teams acikken LevelDB'nin LOCK ve bazi .log dosyalari kilitlidir; tek bir
    `PermissionError` butun sondayi dusurmemeli. Atlanan dosya sayisi ve
    istisna TURLERI raporlanir (mesaj yok: icinde yol gecebilir).
    """
    report = CopyReport(target=target)
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)

    def copy_file(src: str, dst: str, *, follow_symlinks: bool = True) -> Any:
        """`copytree`nin dosya kopyalayicisi; hatayi yutar ve sayar.

        `copytree` bir dosyada patlayinca kalanini da atiyor; kilitli tek bir
        `LOCK` dosyasi butun sondayi dusurmesin diye hata burada yakalanir.
        """
        try:
            result = shutil.copy2(src, dst, follow_symlinks=follow_symlinks)
        except OSError as exc:
            report.skipped += 1
            report.reasons[type(exc).__name__] += 1
            return dst
        report.copied += 1
        return result

    try:
        shutil.copytree(source, target, copy_function=copy_file, dirs_exist_ok=True)
    except shutil.Error as exc:  # pragma: no cover - klasor duzeyinde hata
        report.skipped += len(getattr(exc, "args", [[]])[0] or [])
        report.reasons["Error"] += 1
    except OSError as exc:  # pragma: no cover - kok klasor okunamadi
        report.reasons[type(exc).__name__] += 1
    return report


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
    return leveldb.with_suffix(".blob")


def temp_root(env: dict[str, str] | None = None) -> Path:
    source = env if env is not None else os.environ
    base = source.get("TEMP") or source.get("TMP") or source.get("TMPDIR") or "/tmp"
    return Path(base) / COPY_DIR_NAME


# --- okuma ---------------------------------------------------------------


def vendor_dir() -> Path:
    """`vendor/` once yaninda (zip paketi), sonra depodaki `app/vendor/`."""
    here = Path(__file__).resolve().parent
    local = here / "vendor"
    if (local / "ccl_chromium_reader").is_dir():
        return local
    return here.parent.parent / "app" / "vendor"


def load_reader() -> Any:
    """Vendor'lanan IndexedDB okuyucusunu yukler."""
    target = str(vendor_dir())
    if target not in sys.path:
        sys.path.insert(0, target)
    from ccl_chromium_reader import ccl_chromium_indexeddb  # noqa: PLC0415

    return ccl_chromium_indexeddb


@dataclass
class StoreReport:
    name: str
    records: int = 0
    failed: int = 0
    reasons: Counter = field(default_factory=Counter)
    stats: dict[str, FieldStat] = field(default_factory=dict)


@dataclass
class DatabaseReport:
    name: str
    origin: str = ""
    number: int = 0
    version: str = "?"
    stores: list[StoreReport] = field(default_factory=list)
    failed_stores: int = 0
    reasons: Counter = field(default_factory=Counter)


def read_store(store: Any, limit: int = SAMPLE_LIMIT) -> StoreReport:
    """Tek object store: kac kayit var, ilk `limit` kaydin yapisi ne?

    Kayit sayisi icin hepsi dolasilir (deger cozulmesi gerekiyor, ne yazik ki
    ucuz degil); alan istatistigi yalnizca ilk `limit` kayittan cikar.
    """
    report = StoreReport(name=str(store.name))
    sample: list[Any] = []
    try:
        for record in store.iterate_records():
            report.records += 1
            if len(sample) < limit:
                sample.append(getattr(record, "value", None))
    except Exception as exc:  # sonda: hangi store patlarsa patlasin devam
        report.failed += 1
        report.reasons[type(exc).__name__] += 1
    report.stats = summarize_records(sample, limit)
    return report


def read_database(wrapper: Any, db_id: Any, limit: int = SAMPLE_LIMIT) -> DatabaseReport:
    database = wrapper[db_id.dbid_no]
    report = DatabaseReport(
        name=str(db_id.name), origin=str(db_id.origin), number=int(db_id.dbid_no)
    )
    try:
        report.version = str(database.object_store_count)
    except Exception as exc:  # pragma: no cover - bozuk ust veri
        report.reasons[type(exc).__name__] += 1

    for store in database:
        try:
            report.stores.append(read_store(store, limit))
        except Exception as exc:
            report.failed_stores += 1
            report.reasons[type(exc).__name__] += 1
    return report


def read_all(
    leveldb: Path, blob: Path | None, limit: int = SAMPLE_LIMIT
) -> tuple[list[DatabaseReport], Counter]:
    reader = load_reader()
    problems: Counter = Counter()
    reports: list[DatabaseReport] = []
    blob_arg = str(blob) if blob and blob.is_dir() else None
    wrapper = reader.WrappedIndexDB(str(leveldb), blob_arg)
    try:
        for db_id in wrapper.database_ids:
            try:
                reports.append(read_database(wrapper, db_id, limit))
            except Exception as exc:
                problems[type(exc).__name__] += 1
    finally:
        try:
            wrapper.close()
        except Exception:  # pragma: no cover
            pass
    return reports, problems


# --- rapor ---------------------------------------------------------------


def is_candidate(name: str) -> bool:
    marker = str(name or "").casefold()
    return any(hint in marker for hint in CANDIDATE_HINTS)


def is_target_database(name: str) -> bool:
    """Aramalar/toplantilar icin asil hedef veritabani mi?"""
    marker = str(name or "").casefold()
    if "replychain" in marker:
        return False
    return any(hint in marker for hint in TARGET_DB_HINTS)


def render(
    leveldb: Path,
    blob: Path | None,
    copy_report: CopyReport | None,
    reports: list[DatabaseReport],
    problems: Counter,
    seconds: float,
) -> str:
    """Butun ciktiyi kurar. Buraya giren her sey `redact_value` suzgecinden
    ya da alan ADLARINDAN gelir; ham deger gecmez."""
    lines: list[str] = []
    add = lines.append

    add("Holocron - yeni Teams IndexedDB yapi sondasi")
    add("=" * 52)
    add("")
    add("Bu dosya YAPI bilgisi tasir: veritabani, store ve alan ADLARI, tipler,")
    add("sayilar ve tarihler. Hicbir dizge degeri (mesaj, ad, e-posta, konu)")
    add("yazilmaz; dizge alanlari yalnizca uzunluk olarak gecer.")
    add("")
    add(f"Kaynak klasor   : {leveldb.name}")
    if copy_report is not None:
        add(f"Kopya           : {copy_report.target}")
        add(f"Kopyalanan      : {copy_report.copied} dosya")
        skipped = f"{copy_report.skipped} dosya"
        if copy_report.reasons:
            skipped += " (" + ", ".join(
                f"{name} x{count}" for name, count in copy_report.reasons.most_common()
            ) + ")"
        add(f"Atlanan         : {skipped}")
    if blob is None or not blob.is_dir():
        add("blob klasoru    : yok")
    else:
        count = sum(1 for item in blob.rglob("*") if item.is_file())
        add(f"blob klasoru    : var, {count} dosya")
    add(f"Veritabani      : {len(reports)}")
    if problems:
        add(
            "Okunamayan db   : "
            + ", ".join(f"{name} x{count}" for name, count in problems.most_common())
        )
    add(f"Sure            : {seconds:.1f} sn")
    add("")

    for report in sorted(reports, key=lambda item: item.name.casefold()):
        add("-" * 52)
        add(f"VERITABANI: {report.name}")
        add(f"  kaynak       : {report.origin}")
        add(f"  numara       : {report.number}")
        add(f"  store sayisi : {report.version}")
        if report.failed_stores:
            add(
                f"  okunamayan   : {report.failed_stores} store ("
                + ", ".join(f"{n} x{c}" for n, c in report.reasons.most_common())
                + ")"
            )
        add("")
        for store in report.stores:
            add(f"  [{store.name}] kayit: {store.records}")
            if store.failed:
                add(
                    "    okuma hatasi: "
                    + ", ".join(f"{n} x{c}" for n, c in store.reasons.most_common())
                )
            if not store.stats:
                add("    (alan yok ya da ornek bos)")
            for path in sorted(store.stats):
                add(f"    {path}: {store.stats[path].detail_text()}")
            add("")

    add("=" * 52)
    add("ADAY STORE'LAR")
    add("(adinda conversation/message/thread/call/people/contact/reply gecenler)")
    add("")
    found = False
    for report in sorted(reports, key=lambda item: item.name.casefold()):
        for store in report.stores:
            if not is_candidate(store.name) and not is_candidate(report.name):
                continue
            found = True
            add(f"  {report.name} / {store.name}  ({store.records} kayit)")
            for path in sorted(store.stats):
                add(f"      {path}: {store.stats[path].type_text()}")
            add("")
    if not found:
        add("  (aday bulunamadi)")
    add("")

    add("=" * 52)
    add("HEDEF VERITABANLARI (arama gecmisi, takvim, kisi cozumu)")
    add("")
    hit = False
    for report in sorted(reports, key=lambda item: item.name.casefold()):
        if not is_target_database(report.name):
            continue
        hit = True
        add(f"  {report.name}")
        for store in report.stores:
            add(f"    [{store.name}] {store.records} kayit")
            for path in sorted(store.stats):
                stat = store.stats[path]
                mark = "  <- tarih" if stat.is_dated else ""
                add(f"      {path}: {stat.type_text()}{mark}")
        add("")
    if not hit:
        add("  (bu makinede bulunamadi)")

    add("")
    add("Son.")
    return "\n".join(lines) + "\n"


# --- komut satiri --------------------------------------------------------


# --- toplanti katilimi sondasi (--meetings) ------------------------------
#
# Asil soru: "hangi toplantiya katildim, kac dakika kaldim". Cevabi
# `replychain-manager` veritabanindaki `replychains` store'u tasiyor: toplanti
# sohbetine Teams bir sistem mesaji yaziyor ve icinde kisi basina sure olan
# bir `<partlist>` bloku duruyor. Bu bolum O YAPIYI olcer; hicbir ad, mesaj
# metni ya da sure DEGERI basilmaz -- yalnizca etiket adlari, sayilar ve
# tarih araliklari.

REPLYCHAIN_ROLE = "replychain-manager"
REPLYCHAIN_STORE = "replychains"
PROFILE_ROLE = "profiles"
CALL_ROLE = "call-history-manager"

MEETING_PREFIX = "19:meeting_"
THREAD_PREFIX = "19:"

MEETINGS_OUTPUT_NAME = "teams-meetings-probe.txt"

# XML etiket adi: deger degil, YALNIZCA ad toplanir.
_TAG_NAME = re.compile(r"<\s*([A-Za-z][\w:-]*)", re.ASCII)
_PARTLIST_TYPE = re.compile(r'<partlist\b[^>]*\btype\s*=\s*"([^"]*)"', re.IGNORECASE)
_PART_COUNT = re.compile(r"<part\b", re.IGNORECASE)
_HAS_DURATION = re.compile(r"<duration\b", re.IGNORECASE)
_IDENTITY = re.compile(r'\bidentity\s*=\s*"([^"]*)"', re.IGNORECASE)


def database_segment(name: Any) -> str:
    """`Teams:calendar:react-web-client:...` -> `calendar`."""
    text = str(name or "").strip()
    parts = text.split(":")
    return parts[1] if len(parts) > 1 else text


@dataclass
class ChainReport:
    """Tek bir sohbet turu icin (toplanti / grup) toplanan sayilar."""

    label: str
    threads: int = 0
    messages: int = 0
    message_types: Counter = field(default_factory=Counter)
    call_events: int = 0
    partlists: int = 0
    partlist_types: Counter = field(default_factory=Counter)
    tags: set = field(default_factory=set)
    parts_total: int = 0
    parts_max: int = 0
    with_duration: int = 0
    mine: int = 0
    # Kendi kimligimin nasil eslestigi: tam mi, yalnizca GUID mi.
    mine_exact: int = 0
    mine_guid: int = 0
    event_types: Counter = field(default_factory=Counter)
    meeting_types: Counter = field(default_factory=Counter)
    skeletons: dict = field(default_factory=dict)
    oldest: dt.datetime | None = None
    newest: dt.datetime | None = None

    def observe_moment(self, moment: dt.datetime | None) -> None:
        if moment is None:
            return
        aware = moment if moment.tzinfo else moment.replace(tzinfo=dt.timezone.utc)
        self.oldest = aware if self.oldest is None else min(self.oldest, aware)
        self.newest = aware if self.newest is None else max(self.newest, aware)

    @property
    def parts_average(self) -> float:
        return round(self.parts_total / self.partlists, 1) if self.partlists else 0.0


def identity_path(values: Iterable[str], my_mri: str) -> str:
    """Kendi kimligim bu listede nasil geciyor: `tam`, `guid` ya da `yok`.

    `part identity` bazen `8:orgid:<guid>`, bazen `8:<guid>` ya da farkli
    harf buyuklugunde geliyor; tam esleme sarti kayit dusuruyordu.
    """
    mine = str(my_mri or "").strip().casefold()
    if not mine:
        return "yok"
    guid = mine.rsplit(":", 1)[-1]
    found = ""
    for value in values:
        candidate = str(value or "").strip().casefold()
        if candidate == mine:
            return "tam"
        if guid and candidate.rsplit(":", 1)[-1] == guid:
            found = "guid"
    return found or "yok"


def content_tags(text: str) -> set:
    """Iceriden YALNIZCA etiket adlari (deger yok)."""
    return {name.casefold() for name in _TAG_NAME.findall(text)}


def scan_message(report: ChainReport, message: Any, my_mri: str) -> None:
    """Tek mesaji olcer. Icerikten yalnizca yapi bilgisi cikar."""
    if not isinstance(message, dict):
        return
    report.messages += 1
    kind = str(message.get("messageType") or "").strip()
    report.message_types[kind or "(bos)"] += 1

    content = message.get("content")
    text = content.decode("utf-8", "replace") if isinstance(content, bytes) else str(content or "")
    is_event = "call" in kind.casefold() or "event" in kind.casefold()
    has_list = "<partlist" in text.casefold()
    if not (is_event or has_list):
        return

    report.call_events += 1
    report.observe_moment(parse_moment("originalArrivalTime", message.get("originalArrivalTime")))
    if not has_list:
        return

    report.partlists += 1
    report.tags |= content_tags(text)
    for name, target in (("calleventtype", report.event_types), ("meetingtype", report.meeting_types)):
        # Bunlar sabit sozcuklerdir (ended/started/Scheduled...), kisisel degil.
        found = re.search(rf"<{name}\b[^>]*>\s*([^<]{{0,40}})\s*</{name}\s*>", text, re.IGNORECASE)
        if found:
            target[found.group(1).strip() or "(bos)"] += 1
    if len(report.skeletons) < 3:
        lines = skeleton_of(text)
        report.skeletons.setdefault(skeleton_key(lines), lines)
    found = _PARTLIST_TYPE.search(text)
    report.partlist_types[(found.group(1) if found else "(yok)").casefold()] += 1
    count = len(_PART_COUNT.findall(text))
    report.parts_total += count
    report.parts_max = max(report.parts_max, count)
    if _HAS_DURATION.search(text):
        report.with_duration += 1
    if my_mri:
        how = identity_path(_IDENTITY.findall(text), my_mri)
        if how == "tam":
            report.mine += 1
            report.mine_exact += 1
        elif how == "guid":
            report.mine += 1
            report.mine_guid += 1


_MRI_IN_TEXT = re.compile(
    r"8:orgid:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.IGNORECASE
)
_GUID_ONLY = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


def mri_from_database_name(name: Any) -> str:
    """Veritabani adindan kullanicinin kendi kimligi.

    Ad `Teams:<rol>:react-web-client:<kiraci>:<kullanici>:<dil>` bicimindedir;
    besinci segment kullanicinin GUID'idir. Bazi adlar kimligi dogrudan
    `8:orgid:<guid>` olarak tasir.
    """
    text = str(name or "").strip()
    found = _MRI_IN_TEXT.search(text)
    if found:
        return found.group(0).casefold()
    parts = text.split(":")
    if len(parts) > 4 and _GUID_ONLY.match(parts[4]):
        return f"8:orgid:{parts[4].casefold()}"
    return ""


def find_my_mri(wrapper: Any) -> tuple[str, str]:
    """(kimlik, nereden bulundu). Kimlik ciktiya YAZILMAZ, yol yazilir.

    Once veritabani adlari (veritabani acilmadan), sonra `replychains`
    icinde `isSentByCurrentUser` isaretli bir mesajin `creator` alani.
    (`call-history.userParticipantId` KULLANILMAZ: sahada hicbir katilimci
    listesinde bulunamadi -- o alan arama basina katilimci kimligi.)
    """
    for db_id in wrapper.database_ids:
        marker = mri_from_database_name(db_id.name)
        if marker:
            return marker, "veritabani adi"

    for db_id in wrapper.database_ids:
        if database_segment(db_id.name).casefold() != REPLYCHAIN_ROLE:
            continue
        try:
            database = wrapper[db_id.dbid_no]
        except Exception:  # pragma: no cover - bozuk ust veri
            continue
        for store in database:
            if str(store.name) != REPLYCHAIN_STORE:
                continue
            try:
                for record in store.iterate_records():
                    value = getattr(record, "value", None)
                    if not isinstance(value, dict):
                        continue
                    messages = value.get("messageMap")
                    if not isinstance(messages, dict):
                        continue
                    for message in messages.values():
                        if isinstance(message, dict) and message.get("isSentByCurrentUser"):
                            marker = str(message.get("creator") or "").strip()
                            if marker:
                                return marker, "isSentByCurrentUser"
            except Exception:  # pragma: no cover
                return "", "okunamadi"
    return "", "bulunamadi"


# --- icerik iskeleti (deger yok, yalnizca yapi) --------------------------


def skeleton_of(text: str, limit: int = 40) -> list[str]:
    """Icerigin etiket agaci: etiket adi + oznitelik ADLARI + metin uzunlugu.

    Ciktiya hicbir deger girmez; `name(len 23)` der, adin kendisini yazmaz.
    """
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        try:
            root = ET.fromstring(f"<holocron>{text}</holocron>")
        except ET.ParseError:
            return ["(cozulemedi)"]

    lines: list[str] = []

    def walk(node: Any, depth: int) -> None:
        if len(lines) >= limit:
            return
        tag = str(node.tag).rsplit("}", 1)[-1]
        names = ",".join(sorted(str(key) for key in (node.attrib or {})))
        body = (node.text or "").strip()
        parts = [tag]
        if names:
            parts.append(f"({names})")
        if body:
            parts.append(f"(len {len(body)})")
        lines.append("  " * depth + "".join(parts))
        for child in list(node):
            walk(child, depth + 1)

    walk(root, 0)
    return lines


def skeleton_key(lines: Iterable[str]) -> str:
    return "\n".join(lines)


def scan_meetings(wrapper: Any) -> tuple[ChainReport, ChainReport, str, str]:
    """`replychains` store'unu tarar: toplanti sohbetleri ve grup sohbetleri."""
    meetings = ChainReport("toplanti sohbetleri (19:meeting_)")
    groups = ChainReport("diger 19: sohbetleri")
    my_mri, how = find_my_mri(wrapper)

    for db_id in wrapper.database_ids:
        if database_segment(db_id.name).casefold() != REPLYCHAIN_ROLE:
            continue
        try:
            database = wrapper[db_id.dbid_no]
        except Exception:  # pragma: no cover - bozuk ust veri
            continue
        for store in database:
            if str(store.name) != REPLYCHAIN_STORE:
                continue
            for record in store.iterate_records():
                value = getattr(record, "value", None)
                if not isinstance(value, dict):
                    continue
                thread = str(value.get("conversationId") or "").strip()
                if not thread.startswith(THREAD_PREFIX):
                    continue
                report = meetings if thread.casefold().startswith(MEETING_PREFIX) else groups
                messages = value.get("messageMap")
                if not isinstance(messages, dict):
                    continue
                report.threads += 1
                for message in messages.values():
                    scan_message(report, message, my_mri)
    return meetings, groups, my_mri, how


def render_meetings(
    leveldb: Path, reports: Iterable[ChainReport], how: str, seconds: float
) -> str:
    """Katilim sondasinin ciktisi. Ad, metin ve sure DEGERI icermez."""
    lines: list[str] = []
    add = lines.append
    add("Holocron - toplanti katilimi yapi sondasi")
    add("=" * 52)
    add("")
    add("Bu dosya YAPI bilgisi tasir: etiket ADLARI, sayilar ve tarih araliklari.")
    add("Hicbir ad, mesaj metni ya da sure degeri yazilmaz.")
    add("")
    add(f"Kaynak klasor     : {leveldb.name}")
    add(f"Kendi kimligim    : {how}")
    add(f"Sure              : {seconds:.1f} sn")
    add("")

    for report in reports:
        add("-" * 52)
        add(f"{report.label.upper()}")
        add(f"  sohbet            : {report.threads}")
        add(f"  mesaj             : {report.messages}")
        add(f"  arama/olay mesaji : {report.call_events}")
        add(f"  partlist tasiyan  : {report.partlists}")
        if report.partlist_types:
            add(
                "  partlist type     : "
                + ", ".join(f"{name} x{count}" for name, count in report.partlist_types.most_common())
            )
        if report.tags:
            add("  icerik etiketleri : " + ", ".join(sorted(report.tags)))
        add(f"  part sayisi       : ort {report.parts_average} / maks {report.parts_max}")
        add(f"  duration tasiyan  : {report.with_duration}")
        add(
            f"  kendim gecen      : {report.mine} "
            f"(tam {report.mine_exact}, guid {report.mine_guid})"
        )
        if report.oldest is not None:
            add(f"  tarih araligi     : {format_moment(report.oldest)} .. {format_moment(report.newest)}")
        if report.event_types:
            add(
                "  calleventtype     : "
                + ", ".join(f"{name} x{count}" for name, count in report.event_types.most_common())
            )
        if report.meeting_types:
            add(
                "  meetingtype       : "
                + ", ".join(f"{name} x{count}" for name, count in report.meeting_types.most_common())
            )
        add("  mesaj turleri     :")
        for name, count in report.message_types.most_common(15):
            add(f"    {name}: {count}")
        if report.skeletons:
            add("  icerik iskeleti (ilk 3 farkli yapi; deger yok):")
            # Degisken adi bilerek `lines` degil: disaridaki cikti listesini
            # golgelerse rapor tek bir iskelete iner.
            for index, shape in enumerate(report.skeletons.values(), start=1):
                add(f"    --- {index} ---")
                for line in shape:
                    add("    " + line)
        add("")

    add("Son.")
    return "\n".join(lines) + "\n"


def run_meetings(
    path: Path | None = None, output: Path | None = None, no_copy: bool = False
) -> int:
    """`--meetings`: yalnizca toplanti katilimi yapisini olcer."""
    started = time.monotonic()
    leveldb = Path(path) if path else default_cache_path()
    if not leveldb.is_dir():
        print(f"Klasor bulunamadi: {leveldb}", file=sys.stderr)
        return 2

    blob = blob_dir_for(leveldb)
    source = leveldb
    blob_source = blob if blob.is_dir() else None
    if not no_copy:
        root = temp_root()
        report = copy_tree(leveldb, root / leveldb.name)
        source = report.target
        if blob_source is not None:
            blob_source = copy_tree(blob_source, root / blob.name).target

    reader = load_reader()
    wrapper = reader.WrappedIndexDB(str(source), str(blob_source) if blob_source else None)
    try:
        meetings, groups, my_mri, how = scan_meetings(wrapper)
    finally:
        try:
            wrapper.close()
        except Exception:  # pragma: no cover
            pass

    seconds = time.monotonic() - started
    text = render_meetings(leveldb, (meetings, groups), how, seconds)
    destination = Path(output) if output else Path.cwd() / MEETINGS_OUTPUT_NAME
    destination.write_text(text, encoding="utf-8")
    print(f"Toplanti sohbeti : {meetings.threads}")
    print(f"partlist mesaji  : {meetings.partlists}")
    print(f"Sure             : {seconds:.1f} sn")
    print(f"Yazildi          : {destination}")
    print("Icinde kisisel deger yok: yalnizca etiket adlari, sayilar, tarihler.")
    return 0



def run(
    path: Path | None = None,
    output: Path | None = None,
    no_copy: bool = False,
    limit: int = SAMPLE_LIMIT,
) -> int:
    started = time.monotonic()
    leveldb = Path(path) if path else default_cache_path()
    if not leveldb.is_dir():
        print(f"Klasor bulunamadi: {leveldb}", file=sys.stderr)
        print("Yolu --path ile verebilirsiniz.", file=sys.stderr)
        return 2

    blob = blob_dir_for(leveldb)
    copy_report: CopyReport | None = None
    source = leveldb
    blob_source = blob if blob.is_dir() else None

    if not no_copy:
        root = temp_root()
        copy_report = copy_tree(leveldb, root / leveldb.name)
        source = copy_report.target
        if blob_source is not None:
            blob_copy = copy_tree(blob_source, root / blob.name)
            copy_report.copied += blob_copy.copied
            copy_report.skipped += blob_copy.skipped
            copy_report.reasons.update(blob_copy.reasons)
            blob_source = blob_copy.target

    try:
        reports, problems = read_all(source, blob_source, limit)
    except Exception as exc:
        # Kopya bozuksa canli klasore bir sans daha: LevelDB kilidi okumayi
        # her zaman engellemiyor.
        if no_copy or source == leveldb:
            print(f"Okunamadi ({type(exc).__name__}).", file=sys.stderr)
            return 1
        print(f"Kopya okunamadi ({type(exc).__name__}), canli klasor deneniyor.", file=sys.stderr)
        try:
            reports, problems = read_all(leveldb, blob if blob.is_dir() else None, limit)
        except Exception as second:
            print(f"Okunamadi ({type(second).__name__}).", file=sys.stderr)
            return 1

    seconds = time.monotonic() - started
    text = render(leveldb, blob, copy_report, reports, problems, seconds)
    destination = Path(output) if output else Path.cwd() / OUTPUT_NAME
    destination.write_text(text, encoding="utf-8")

    stores = sum(len(item.stores) for item in reports)
    records = sum(store.records for item in reports for store in item.stores)
    print(f"Veritabani : {len(reports)}")
    print(f"Store      : {stores}")
    print(f"Kayit      : {records}")
    if copy_report is not None:
        print(f"Kopya      : {copy_report.copied} dosya, {copy_report.skipped} atlandi")
    print(f"Sure       : {seconds:.1f} sn")
    print(f"Yazildi    : {destination}")
    print("Icinde kisisel deger yok: yalnizca alan adlari, sayilar, tarihler.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Yeni Teams IndexedDB yapi sondasi (icerik okumaz)"
    )
    parser.add_argument("--path", type=Path, help="IndexedDB klasoru (varsayilan: Teams'inki)")
    parser.add_argument("--output", type=Path, help=f"cikti dosyasi (varsayilan {OUTPUT_NAME})")
    parser.add_argument(
        "--no-copy", action="store_true", help="kopyalamadan dogrudan oku (Teams kapaliyken)"
    )
    parser.add_argument(
        "--limit", type=int, default=SAMPLE_LIMIT, help=f"ornek kayit sayisi (varsayilan {SAMPLE_LIMIT})"
    )
    parser.add_argument(
        "--meetings",
        action="store_true",
        help="yalnizca toplanti katilimi yapisini olcer (replychains)",
    )
    args = parser.parse_args(argv)
    if args.meetings:
        return run_meetings(args.path, args.output, args.no_copy)
    return run(args.path, args.output, args.no_copy, max(1, int(args.limit)))


if __name__ == "__main__":
    raise SystemExit(main())
