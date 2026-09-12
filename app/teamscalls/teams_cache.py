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
import time
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
    ThreadRecord,
    blob_dir_for,
    canonical_direction,
    canonical_state,
    canonical_type,
    clean_text,
    default_cache_path,
    empty_diagnostics,
    jsonable,
    temp_root,
)

# Veritabani adlari `Teams:<ad>:react-web-client:<kiraci>:<kullanici>:<dil>`
# bicimindedir. Ikinci segment TAM esleserek secilir: "call-history-manager"
# yaninda `call-history-sync-state-manager` ve `call-history-voicemail-manager`
# gibi kardesler var, alt dizge aramasi onlari da yakalar ve bosa yuz binlerce
# kayit okutur.
ROLE_CALLS = "call-history-manager"
ROLE_CALENDAR = "calendar"
ROLE_PROFILES = "profiles"
ROLE_THREADS = "conversation-manager"

# Rol -> okunacak object store. Baska hicbir veritabani ACILMAZ.
ROLE_STORES: dict[str, str] = {
    ROLE_CALLS: "call-history",
    ROLE_CALENDAR: "calendar",
    ROLE_PROFILES: "profiles",
    ROLE_THREADS: "conversations",
}

# Kayitta kimligi tasiyabilecek alan adlari (sirayla denenir).
ID_KEYS: tuple[str, ...] = ("mri", "id", "objectId", "userId", "personId", "skypeId")
# Kayitta adi tasiyabilecek alan adlari.
NAME_KEYS: tuple[str, ...] = ("displayName", "imDisplayName", "name", "userPrincipalName")


def database_segment(name: Any) -> str:
    """`Teams:calendar:react-web-client:...` -> `calendar`."""
    text = clean_text(name)
    parts = text.split(":")
    return parts[1] if len(parts) > 1 else text


def database_role(name: Any) -> str:
    """Bu veritabani bizim isimize yariyor mu? Yariyorsa hangi rolde?"""
    segment = database_segment(name).casefold()
    for role in ROLE_STORES:
        if segment == role:
            return role
    return ""


def is_call_database(name: Any) -> bool:
    return database_role(name) == ROLE_CALLS


def is_calendar_database(name: Any) -> bool:
    return database_role(name) == ROLE_CALENDAR


def is_profile_database(name: Any) -> bool:
    return database_role(name) == ROLE_PROFILES


def is_thread_database(name: Any) -> bool:
    return database_role(name) == ROLE_THREADS


def _pick(value: dict[str, Any], keys: Iterable[str]) -> str:
    for key in keys:
        text = clean_text(value.get(key))
        if text:
            return text
    return ""


def person_of(value: Any) -> tuple[str, str]:
    """`{displayName, id}` benzeri bir sozluk -> (kimlik, ad).

    Gercek kayitlarda `displayName` cogu zaman `null`, bazen `bytes`:
    ikisini de `clean_text` hallediyor.
    """
    if isinstance(value, (str, bytes, bytearray)):
        return clean_text(value), ""
    if not isinstance(value, dict):
        return "", ""
    return _pick(value, ID_KEYS), _pick(value, NAME_KEYS)


def participants_of(value: Any) -> list[str]:
    """`participantList` -> katilimci kimlikleri (sira korunur, tekrarsiz).

    Gercek kayitta girisler `{id, type, tenantId}` sozlugudur ve `displayName`
    her zaman `null`; adlar `profiles` store'undan cozulur.
    """
    if not isinstance(value, (list, tuple)):
        return []
    people: list[str] = []
    for item in value:
        person_id, _ = person_of(item)
        if person_id and person_id not in people:
            people.append(person_id)
    return people


def call_from_value(value: Any) -> CallRecord | None:
    """`call-history` store kaydi -> `CallRecord`.

    Kimliksiz ve silinmis (`isDeleted`) kayitlar atlanir. `durationInMs`
    kayitlarin yalnizca bir kisminda var; yedekleri `intake` hesaplar.
    """
    if not isinstance(value, dict):
        return None
    call_id = clean_text(value.get("callId") or value.get("id"))
    if not call_id:
        return None
    if bool(value.get("isDeleted")):
        return None

    originator_id, originator_name = person_of(value.get("originatorParticipant"))
    target_id, target_name = person_of(value.get("targetParticipant"))
    duration = value.get("durationInMs")
    # Baslangic damgasi yoksa gelis zamanina (epoch ms) duselim.
    started = value.get("startTime") or value.get("originalArrivalTime") or ""
    return CallRecord(
        call_id=call_id,
        start_time=started,
        end_time=value.get("endTime") or "",
        connect_time=value.get("connectTime") or "",
        duration_ms=duration if isinstance(duration, (int, float)) else None,
        direction=canonical_direction(value.get("callDirection")),
        state=canonical_state(value.get("callState")),
        call_type=canonical_type(value.get("callType")),
        originator_id=originator_id,
        originator_name=originator_name,
        target_id=target_id,
        target_name=target_name,
        forwarded=clean_text(value.get("forwardedTargetType")),
        thread_id=clean_text(value.get("threadId")),
        group_thread_id=clean_text(value.get("groupChatThreadId")),
        subject=clean_text(value.get("subject")),
        participants=participants_of(value.get("participantList") or value.get("participants")),
        raw=jsonable(value),
    )


def attendee_names(value: Any) -> list[str]:
    """`attendees[]` -> davetli adlari (ad yoksa adres)."""
    names: list[str] = []
    if not isinstance(value, (list, tuple)):
        return names
    for item in value:
        if not isinstance(item, dict):
            text = clean_text(item)
            if text and text not in names:
                names.append(text)
            continue
        name = clean_text(item.get("name")) or clean_text(item.get("address"))
        if name and name not in names:
            names.append(name)
    return names


def calendar_from_value(value: Any) -> CalendarRecord | None:
    """`calendar` store kaydi -> `CalendarRecord`.

    Saat alanlari `datetime` gelir (JS `Date`); metne cevrilmeden oldugu gibi
    tasinir, cevrimi `intake` yapar. `skypeTeamsDataObj.cid` toplanti
    sohbetinin kimligidir ve aramayla KESIN eslesmeyi saglar.
    """
    if not isinstance(value, dict):
        return None
    start = value.get("startTime")
    if start is None or (isinstance(start, (str, bytes)) and not clean_text(start)):
        return None

    data = value.get("skypeTeamsDataObj")
    cid = clean_text(data.get("cid")) if isinstance(data, dict) else ""
    organizer_id, organizer_name = person_of(value.get("organizer"))
    return CalendarRecord(
        event_id=clean_text(value.get("id") or value.get("iCalUid") or value.get("objectId")),
        start_time=start,
        end_time=value.get("endTime") or "",
        subject=clean_text(value.get("subject")),
        organizer_name=clean_text(value.get("organizerName")) or organizer_name or organizer_id,
        organizer_address=clean_text(value.get("organizerAddress")),
        my_response=clean_text(value.get("myResponseType")),
        is_online_meeting=bool(value.get("isOnlineMeeting")),
        is_cancelled=bool(value.get("isCancelled")),
        location=clean_text(value.get("location")),
        event_type=clean_text(value.get("eventType")),
        show_as=clean_text(value.get("showAs")),
        cid=cid,
        meeting_url=clean_text(value.get("skypeTeamsMeetingUrl")),
        attendees=attendee_names(value.get("attendees")),
    )


def thread_from_value(value: Any, key: Any = None) -> ThreadRecord | None:
    """`conversations` store kaydi -> `ThreadRecord` (grup sohbetinin adi).

    Kaydin anahtari thread kimligidir; kayit icinde de `id` olarak gecebilir.
    """
    if not isinstance(value, dict):
        return None
    thread_id = clean_text(value.get("id") or value.get("threadId") or key)
    if not thread_id:
        return None

    properties = value.get("threadProperties")
    topic = clean_text(properties.get("topic")) if isinstance(properties, dict) else ""
    members: list[str] = []
    for item in value.get("members") or ():
        person_id, _ = person_of(item)
        if person_id and person_id not in members:
            members.append(person_id)

    names: list[str] = []
    title = value.get("chatTitle")
    if isinstance(title, dict):
        for item in title.get("avatarUsersInfo") or ():
            _, name = person_of(item)
            if name and name not in names:
                names.append(name)
    return ThreadRecord(thread_id=thread_id, topic=topic, members=members, member_names=names)


def names_from_value(value: Any) -> dict[str, str]:
    """`profiles` kaydi -> {mri: displayName}. Ikisinden biri yoksa bos doner.

    `mri` ile `participantList[].id` ayni bicimdedir (`8:orgid:...`), bu yuzden
    katilimci adlari dogrudan buradan cozulur.
    """
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


@dataclass
class CacheBundle:
    """Bir onbellek acilisindan cikan her sey.

    Dordu birlikte gelir cunku hepsi tek gecişte okunur; `read()` bunu
    sozlesmedeki dortluye acar.
    """

    calls: list[CallRecord] = field(default_factory=list)
    calendar: list[CalendarRecord] = field(default_factory=list)
    names: dict[str, str] = field(default_factory=dict)
    threads: list[ThreadRecord] = field(default_factory=list)
    databases: int = 0
    read_ms: int = 0

    def as_tuple(self) -> tuple[
        list[CallRecord], list[CalendarRecord], dict[str, str], list[ThreadRecord]
    ]:
        return self.calls, self.calendar, self.names, self.threads


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

    def read(self) -> tuple[
        list[CallRecord], list[CalendarRecord], dict[str, str], list[ThreadRecord]
    ]:
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
            return self._finish(self._read_or_fail(leveldb, live_blob), SOURCE_LIVE)

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
                return self._finish(result, SOURCE_LIVE)

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
            return self._finish(self._read_or_fail(leveldb, live_blob), SOURCE_LIVE)

        if report.is_incomplete:
            self.report["warning"] = missing_warning(report)
        return self._finish(result, SOURCE_COPY)

    def _finish(self, bundle: Any, origin: str) -> tuple[
        list[CallRecord], list[CalendarRecord], dict[str, str], list[ThreadRecord]
    ]:
        """Teshisi tamamlar ve sozlesmedeki dortluyu dondurur."""
        self.report["source"] = origin
        self.report["databases"] = getattr(bundle, "databases", 0)
        self.report["read_ms"] = getattr(bundle, "read_ms", 0)
        return bundle.as_tuple() if hasattr(bundle, "as_tuple") else tuple(bundle)

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

    def _read_or_fail(self, leveldb: Path, blob: Path | None) -> "CacheBundle":
        try:
            return self._read_all(leveldb, blob)
        except Exception as exc:
            raise CallsError(
                "calls_read_failed",
                f"Teams önbelleği okunamadı ({type(exc).__name__}). "
                "Teams'i kapatıp yeniden deneyin.",
            ) from exc

    def _read_all(self, leveldb: Path, blob: Path | None) -> "CacheBundle":
        """Yalnizca gereken DORT veritabanini acar.

        Onbellekte yuz kusur veritabani var ve hepsini dolasmak dakikalar
        suruyor (sonda 112 veritabaninda 596 saniye harcadi). Ad suzgeci
        `database_ids` uzerinde calisir: digerleri hic ACILMAZ.
        """
        started = time.monotonic()
        reader = load_reader()
        wrapper = reader.WrappedIndexDB(str(leveldb), str(blob) if blob else None)
        bundle = CacheBundle()
        try:
            for db_id in wrapper.database_ids:
                name = clean_text(getattr(db_id, "name", ""))
                role = database_role(name)
                if not role:
                    continue
                try:
                    database = wrapper[db_id.dbid_no]
                except Exception:  # pragma: no cover - bozuk ust veri
                    continue
                bundle.databases += 1
                wanted = ROLE_STORES[role]
                for store in database:
                    if clean_text(getattr(store, "name", "")) == wanted:
                        self._read_store(role, store, bundle)
        finally:
            try:
                wrapper.close()
            except Exception:  # pragma: no cover
                pass
        bundle.read_ms = int((time.monotonic() - started) * 1000)
        return bundle

    def _read_store(self, role: str, store: Any, bundle: "CacheBundle") -> None:
        """Tek store; hangi store patlarsa patlasin digerleri okunur."""
        try:
            for record in store.iterate_records():
                value = getattr(record, "value", None)
                if role == ROLE_CALLS:
                    parsed = call_from_value(value)
                    if parsed is not None:
                        bundle.calls.append(parsed)
                elif role == ROLE_CALENDAR:
                    event = calendar_from_value(value)
                    if event is not None:
                        bundle.calendar.append(event)
                elif role == ROLE_THREADS:
                    thread = thread_from_value(value, getattr(record, "key", None))
                    if thread is not None:
                        bundle.threads.append(thread)
                else:
                    bundle.names.update(names_from_value(value))
        except Exception:  # pragma: no cover - bozuk kayit tum taramayi dusurmesin
            return
