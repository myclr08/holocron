"""Yeni Teams'in yerel IndexedDB onbelleginden arama gecmisi okuyucusu.

Yalnizca Windows'ta is gorur ve yalnizca **kullanicinin kendi makinesindeki
kendi verisini** okur: Teams istemcisinin "Aramalar -> Gecmis" ekraninda
zaten gorunen kayitlar. Hicbir ag cagrisi yoktur, hicbir sey disari gitmez.

Bilinmesi gerekenler:

* **Kilit.** Teams acikken LevelDB dosyalari kilitli olabilir; klasor once
  `%TEMP%\\holocron-teams-calls\\` altina kopyalanir. Kopyalama once normal
  okumayi, olmazsa Windows'un paylasimli acma bayraklarini
  (`FILE_SHARE_READ|WRITE|DELETE`) dener; yine acilamayan dosya atlanir ve
  ADIYLA sayilir.
* **Eksik kopya.** En yeni aramalar henuz `.ldb`ye sikistirilmamis yazma
  gunlugunde (`*.log`) durur. Atlanan dosyalar arasinda `.log`, `MANIFEST`
  ya da `CURRENT` varsa kopya EKSIK sayilir ve once **canli klasor** okunur
  (ccl salt okuma yapar). Canli okuma da patlarsa kopyadan okunan sonuc
  kullanilir ama `warning` ile birlikte doner.
* **ccl yalnizca burada.** IndexedDB okuyucusu `app/vendor/` icinde tasinir
  ve **cagri aninda** `ensure_path()` ile yola eklenir; modul import'u da
  fonksiyonun icindedir. Linux paketinde hic dokunulmaz.
* **Yapi kaynagi.** Store ve alan adlari acik kaynak adli analiz araclarinin
  belgeledigi duzendir; bu dosya o duzeni okur, tahmin etmez ve eksik alanda
  kaydi dusurmez.
"""

from __future__ import annotations

import shutil
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .. import vendor
from .source import (
    SOURCE_COPY,
    SOURCE_LIVE,
    CalendarRecord,
    CallRecord,
    CallsError,
    blob_dir_for,
    clean_text,
    default_cache_path,
    empty_diagnostics,
    temp_root,
)

# Veritabani adlarinda aranan isaretler.
CALL_DB_HINT = "call-history-manager"
CALENDAR_DB_HINT = "calendar"
# `replychain-calendar` mesaj zinciridir, takvim degil: adi kandiriyor.
CALENDAR_DB_SKIP = "replychain"

CALL_STORE = "call-history"
CALENDAR_STORE = "calendar"

# Kimlik -> ad cozumunun beslendigi store'lar.
NAME_STORES: tuple[str, ...] = ("profiles", "internal-data", "capiv3-contacts")

# Kayitta kimligi tasiyabilecek alan adlari (sirayla denenir).
ID_KEYS: tuple[str, ...] = ("mri", "id", "objectId", "userId", "personId", "skypeId")
# Kayitta adi tasiyabilecek alan adlari.
NAME_KEYS: tuple[str, ...] = ("displayName", "imDisplayName", "name", "userPrincipalName")


# --- saf yardimcilar: yapiyi taniyan suzgecler ---------------------------


def is_call_database(name: Any) -> bool:
    return CALL_DB_HINT in clean_text(name).casefold()


def is_calendar_database(name: Any) -> bool:
    marker = clean_text(name).casefold()
    if CALENDAR_DB_SKIP in marker:
        return False
    return CALENDAR_DB_HINT in marker


def is_name_store(name: Any) -> bool:
    marker = clean_text(name).casefold()
    return any(marker == hint or hint in marker for hint in NAME_STORES)


def _pick(value: dict[str, Any], keys: Iterable[str]) -> str:
    for key in keys:
        text = clean_text(value.get(key))
        if text:
            return text
    return ""


def person_of(value: Any) -> tuple[str, str]:
    """`{displayName, id}` benzeri bir sozluk -> (kimlik, ad)."""
    if isinstance(value, str):
        return clean_text(value), ""
    if not isinstance(value, dict):
        return "", ""
    return _pick(value, ID_KEYS), _pick(value, NAME_KEYS)


def participants_of(value: Any) -> list[str]:
    """`participants` alani: sozluk listesi de olabilir, duz kimlik listesi de."""
    if not isinstance(value, (list, tuple)):
        return []
    people: list[str] = []
    for item in value:
        person_id, _ = person_of(item)
        if person_id and person_id not in people:
            people.append(person_id)
    return people


def call_from_value(value: Any) -> CallRecord | None:
    """`call-history` store kaydi -> `CallRecord`. Kimliksiz kayit atlanir."""
    if not isinstance(value, dict):
        return None
    call_id = clean_text(value.get("callId") or value.get("id"))
    if not call_id:
        return None
    originator_id, originator_name = person_of(value.get("originatorParticipant"))
    target_id, target_name = person_of(value.get("targetParticipant"))
    duration = value.get("durationInMs")
    return CallRecord(
        call_id=call_id,
        start_time=clean_text(value.get("startTime")),
        end_time=clean_text(value.get("endTime")),
        connect_time=clean_text(value.get("connectTime")),
        duration_ms=duration if isinstance(duration, (int, float)) else None,
        direction=clean_text(value.get("callDirection")),
        state=clean_text(value.get("callState")),
        call_type=clean_text(value.get("callType")),
        originator_id=originator_id,
        originator_name=originator_name,
        target_id=target_id,
        target_name=target_name,
        forwarded=clean_text(value.get("forwardedTargetType")),
        thread_id=clean_text(value.get("threadId")),
        subject=clean_text(value.get("subject")),
        participants=participants_of(value.get("participants")),
        raw=value,
    )


def calendar_from_value(value: Any) -> CalendarRecord | None:
    """`calendar` store kaydi -> `CalendarRecord`. Saatsiz kayit atlanir."""
    if not isinstance(value, dict):
        return None
    start = clean_text(value.get("startTime"))
    if not start:
        return None
    attendees = value.get("attendees")
    names: list[str] = []
    if isinstance(attendees, (list, tuple)):
        for item in attendees:
            _, name = person_of(item)
            if not name and isinstance(item, dict):
                name = _pick(item, ("emailAddress", "address", "upn"))
            if name and name not in names:
                names.append(name)
    organizer_id, organizer_name = person_of(value.get("organizer"))
    return CalendarRecord(
        event_id=clean_text(value.get("id") or value.get("iCalUid") or value.get("objectId")),
        start_time=start,
        end_time=clean_text(value.get("endTime")),
        subject=clean_text(value.get("subject")),
        organizer_name=clean_text(value.get("organizerName")) or organizer_name or organizer_id,
        my_response=clean_text(value.get("myResponseType")),
        is_online_meeting=bool(value.get("isOnlineMeeting")),
        location=clean_text(value.get("location")),
        event_type=clean_text(value.get("eventType")),
        show_as=clean_text(value.get("showAs")),
        attendees=names,
    )


def names_from_value(value: Any) -> dict[str, str]:
    """Profil/kisi kaydi -> {kimlik: ad}. Ikisinden biri yoksa bos doner."""
    if not isinstance(value, dict):
        return {}
    person_id, name = person_of(value)
    if person_id and name:
        return {person_id: name}
    # Bazi store'lar kaydi bir kabugun icinde tasir (`{"value": {...}}`).
    inner = value.get("value") or value.get("profile") or value.get("person")
    if isinstance(inner, dict):
        return names_from_value(inner)
    return {}


# --- kopyalama -----------------------------------------------------------
#
# LevelDB'nin dosya turleri esit degildir: `.ldb` sikistirilmis eski veriyi,
# `*.log` HENUZ SIKISTIRILMAMIS yeni veriyi tasir. Teams acikken kilitli
# kalan genellikle tam da o yazma gunlugudur; atlanirsa "son bir hafta yok"
# sikayeti cikar. Bu yuzden atlanan dosyalar turleriyle sayilir.

# Tek seferde okunan parca (kilitli dosyayi parca parca cekiyoruz).
COPY_CHUNK = 1024 * 1024

# Atlanmasi kopyayi EKSIK yapan dosya turleri.
CRITICAL_KINDS: tuple[str, ...] = ("log", "manifest", "current")


def file_kind(name: Any) -> str:
    """Dosya adindan LevelDB dosya turu: log / ldb / manifest / current / lock."""
    marker = Path(str(name or "")).name.upper()
    if marker.endswith(".LOG"):
        return "log"
    if marker.endswith(".LDB") or marker.endswith(".SST"):
        return "ldb"
    if marker.startswith("MANIFEST"):
        return "manifest"
    if marker == "CURRENT":
        return "current"
    if marker == "LOCK":
        return "lock"
    return "other"


def is_critical(name: Any) -> bool:
    """Bu dosya atlanirsa kopya eksik mi sayilir?"""
    return file_kind(name) in CRITICAL_KINDS


@dataclass
class CopyReport:
    """Kopyalamanin sonucu: nereye, kac dosya, neler atlandi."""

    target: Path
    copied: int = 0
    skipped: int = 0
    skipped_names: list[str] = field(default_factory=list)
    reasons: Counter = field(default_factory=Counter)

    def kinds(self) -> dict[str, int]:
        """Atlanan dosyalarin tur dokumu (`{"log": 1, "lock": 1}`)."""
        return dict(Counter(file_kind(name) for name in self.skipped_names))

    @property
    def critical(self) -> list[str]:
        """Atlanmasi veriyi eksik birakan dosyalar."""
        return [name for name in self.skipped_names if is_critical(name)]

    @property
    def is_incomplete(self) -> bool:
        """Kopya eksik mi? (yazma gunlugu, MANIFEST ya da CURRENT atlandiysa)"""
        return bool(self.critical)

    def merge(self, other: "CopyReport") -> None:
        self.copied += other.copied
        self.skipped += other.skipped
        self.skipped_names.extend(other.skipped_names)
        self.reasons.update(other.reasons)


def copy_stream(src: Any, dst: Any) -> None:
    """Normal okuma: dosya paylasimli acilabiliyorsa bu yeter."""
    with open(src, "rb") as source, open(dst, "wb") as target:
        shutil.copyfileobj(source, target, COPY_CHUNK)


def copy_shared(src: Any, dst: Any) -> None:
    """Kilitli dosyayi Windows'un paylasimli acma bayraklariyla okur.

    `open()` Windows'ta dosyayi paylasimsiz acar ve Teams'in tuttugu
    `*.log` / `LOCK` dosyalarinda `PermissionError` alir. `CreateFile`
    `FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE` ile ayni
    dosyayi okumaya izin verir; cogu kilitli dosya boyle kopyalanabiliyor.
    """
    if sys.platform != "win32":
        raise OSError("paylaşımlı okuma yalnız Windows'ta var")
    import win32file  # noqa: PLC0415 - yerel ice aktarim: yalnizca Windows

    handle = win32file.CreateFile(
        str(src),
        win32file.GENERIC_READ,
        win32file.FILE_SHARE_READ | win32file.FILE_SHARE_WRITE | win32file.FILE_SHARE_DELETE,
        None,
        win32file.OPEN_EXISTING,
        0,
        None,
    )
    try:
        with open(dst, "wb") as target:
            while True:
                _, chunk = win32file.ReadFile(handle, COPY_CHUNK)
                if not chunk:
                    break
                target.write(chunk)
    finally:
        try:
            handle.Close()
        except Exception:  # pragma: no cover - tanitici zaten kapanmis olabilir
            pass


def copy_tree(source: Path, target: Path) -> CopyReport:
    """Klasoru kopyalar; acilamayan dosyayi atlar, adiyla ve turuyle sayar."""
    report = CopyReport(target=target)
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)
    target.mkdir(parents=True, exist_ok=True)

    def copy_file(src: str, dst: str, *, follow_symlinks: bool = True) -> Any:
        """Once normal, olmazsa paylasimli okuma; ikisi de olmazsa atla."""
        try:
            copy_stream(src, dst)
        except OSError:
            try:
                copy_shared(src, dst)
            except Exception as exc:
                report.skipped += 1
                report.skipped_names.append(Path(src).name)
                report.reasons[type(exc).__name__] += 1
                return dst
        report.copied += 1
        return dst

    try:
        shutil.copytree(source, target, copy_function=copy_file, dirs_exist_ok=True)
    except shutil.Error as exc:  # pragma: no cover - klasor duzeyinde hata
        report.skipped += len(getattr(exc, "args", [[]])[0] or [])
        report.reasons["Error"] += 1
    except OSError as exc:  # pragma: no cover - kok klasor okunamadi
        report.reasons[type(exc).__name__] += 1
    return report


def missing_warning(report: CopyReport) -> str:
    """Eksik kopyanin kullaniciya gosterilen cumlesi."""
    return (
        f"Teams açıkken {len(report.critical)} dosya kopyalanamadı; "
        "en yeni aramalar eksik olabilir. Teams'i kapatıp tekrar deneyin."
    )


# --- kaynak --------------------------------------------------------------


def load_reader() -> Any:
    """Vendor'lanan IndexedDB okuyucusunu yukler (cagri aninda)."""
    vendor.ensure_path()
    from ccl_chromium_reader import ccl_chromium_indexeddb  # noqa: PLC0415

    return ccl_chromium_indexeddb


class TeamsCacheSource:
    """Yeni Teams'in yerel onbellegini okuyan uretim kaynagi."""

    def __init__(self, cache_path: str = "", copy_first: bool = True) -> None:
        self.cache_path = clean_text(cache_path)
        self.copy_first = copy_first
        self.copy_report: CopyReport | None = None
        self.report: dict[str, Any] = empty_diagnostics()

    # --- sozlesme -----------------------------------------------------

    def diagnostics(self) -> dict[str, Any]:
        """Son taramanin teshisi: kopyalanan/atlanan dosyalar, kaynak, uyari."""
        return dict(self.report)

    def read(self) -> tuple[list[CallRecord], list[CalendarRecord], dict[str, str]]:
        leveldb = Path(self.cache_path) if self.cache_path else default_cache_path()
        if not leveldb.is_dir():
            raise CallsError(
                "calls_cache_missing",
                "Teams önbelleği bulunamadı. Yeni Teams kurulu değilse ya da "
                "klasör başka yerdeyse Ayarlar → Teams'ten yolu yazın.",
                status=404,
            )

        blob = blob_dir_for(leveldb)
        live_blob = blob if blob.is_dir() else None
        self.report = empty_diagnostics()
        self.copy_report = None

        if not self.copy_first:
            result = self._read_or_fail(leveldb, live_blob)
            self.report["source"] = SOURCE_LIVE
            return result

        copy_leveldb, copy_blob, report = self._copy_cache(leveldb, blob)
        self.copy_report = report
        self.report["copied"] = report.copied
        self.report["skipped"] = report.skipped
        self.report["skipped_kinds"] = report.kinds()

        # Kopya eksikse (yazma gunlugu kilitli kaldiysa) en yeni aramalar
        # kopyada YOKTUR; once canli klasor denenir. ccl salt okuma yapar,
        # Teams'in verisine dokunmaz.
        if report.is_incomplete:
            try:
                result = self._read_all(leveldb, live_blob)
            except Exception:
                result = None
            if result is not None:
                self.report["source"] = SOURCE_LIVE
                return result

        try:
            result = self._read_all(copy_leveldb, copy_blob)
        except Exception as exc:
            if report.is_incomplete:
                # Hem canli hem kopya okunamadi: soyleyecek bir sey kalmadi.
                raise CallsError(
                    "calls_read_failed",
                    f"Teams önbelleği okunamadı ({type(exc).__name__}). "
                    "Teams'i kapatıp yeniden deneyin.",
                ) from exc
            # Kopya tamdi ama bozuk cikti: canli klasore bir sans daha.
            result = self._read_or_fail(leveldb, live_blob)
            self.report["source"] = SOURCE_LIVE
            return result

        self.report["source"] = SOURCE_COPY
        if report.is_incomplete:
            self.report["warning"] = missing_warning(report)
        return result

    # --- ic islem -----------------------------------------------------

    def _copy_cache(self, leveldb: Path, blob: Path) -> tuple[Path, Path | None, CopyReport]:
        """Onbellegi `%TEMP%` altina kopyalar; rapor iki klasoru birlestirir."""
        root = temp_root()
        report = copy_tree(leveldb, root / leveldb.name)
        copy_blob: Path | None = None
        if blob.is_dir():
            blob_report = copy_tree(blob, root / blob.name)
            report.merge(blob_report)
            copy_blob = blob_report.target
        return report.target, copy_blob, report

    def _read_or_fail(
        self, leveldb: Path, blob: Path | None
    ) -> tuple[list[CallRecord], list[CalendarRecord], dict[str, str]]:
        try:
            return self._read_all(leveldb, blob)
        except Exception as exc:
            raise CallsError(
                "calls_read_failed",
                f"Teams önbelleği okunamadı ({type(exc).__name__}). "
                "Teams'i kapatıp yeniden deneyin.",
            ) from exc

    def _read_all(
        self, leveldb: Path, blob: Path | None
    ) -> tuple[list[CallRecord], list[CalendarRecord], dict[str, str]]:
        reader = load_reader()
        wrapper = reader.WrappedIndexDB(str(leveldb), str(blob) if blob else None)
        calls: list[CallRecord] = []
        calendar: list[CalendarRecord] = []
        names: dict[str, str] = {}
        try:
            for db_id in wrapper.database_ids:
                name = clean_text(getattr(db_id, "name", ""))
                try:
                    database = wrapper[db_id.dbid_no]
                except Exception:  # pragma: no cover - bozuk ust veri
                    continue
                for store in database:
                    self._read_store(name, store, calls, calendar, names)
        finally:
            try:
                wrapper.close()
            except Exception:  # pragma: no cover
                pass
        return calls, calendar, names

    def _read_store(
        self,
        database_name: str,
        store: Any,
        calls: list[CallRecord],
        calendar: list[CalendarRecord],
        names: dict[str, str],
    ) -> None:
        """Tek store; hangi store patlarsa patlasin digerleri okunur."""
        store_name = clean_text(getattr(store, "name", ""))
        wants_calls = is_call_database(database_name) and store_name == CALL_STORE
        wants_calendar = is_calendar_database(database_name) and store_name == CALENDAR_STORE
        wants_names = is_name_store(store_name)
        if not (wants_calls or wants_calendar or wants_names):
            return
        try:
            for record in store.iterate_records():
                value = getattr(record, "value", None)
                if wants_calls:
                    parsed = call_from_value(value)
                    if parsed is not None:
                        calls.append(parsed)
                elif wants_calendar:
                    event = calendar_from_value(value)
                    if event is not None:
                        calendar.append(event)
                else:
                    names.update(names_from_value(value))
        except Exception:  # pragma: no cover - bozuk kayit tum taramayi dusurmesin
            return

