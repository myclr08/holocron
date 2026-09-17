"""Bellek ici arama gecmisi kaynagi.

`tests/` altinda degil `app/teamscalls/` icinde duruyor: API testleri de ayni
sahte kaynagi baglayabilsin, Windows olmayan makinede uctan uca akis
sinanabilsin diye. Uretimde kimse bunu kullanmaz.

Ornek adlar bilerek uydurmadir (`Örnek Kişi`, `ornek@example.com`).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from .source import (
    DIRECTION_IN,
    DIRECTION_OUT,
    SOURCE_COPY,
    STATE_ACCEPTED,
    TYPE_MULTI_PARTY,
    TYPE_TWO_PARTY,
    CallRecord,
    CallsError,
    empty_diagnostics,
)

# Sahte kimlikler: Teams'in MRI bicimini taklit eder ama kimseye ait degildir.
PERSON_ONE = "8:orgid:00000000-0000-0000-0000-000000000001"
PERSON_TWO = "8:orgid:00000000-0000-0000-0000-000000000002"
ME = "8:orgid:00000000-0000-0000-0000-0000000000aa"

DEFAULT_NAMES: dict[str, str] = {
    PERSON_ONE: "Örnek Kişi",
    PERSON_TWO: "İkinci Örnek",
    ME: "Ben",
}


def utc(text: str) -> str:
    """'2026-09-11 08:30' -> ISO 8601 UTC metni."""
    moment = datetime.fromisoformat(text).replace(tzinfo=timezone.utc)
    return moment.isoformat().replace("+00:00", "Z")


def call(
    call_id: str,
    start: str | datetime,
    minutes: float = 10,
    direction: str = DIRECTION_OUT,
    state: str = STATE_ACCEPTED,
    call_type: str = TYPE_TWO_PARTY,
    other: str = PERSON_ONE,
    other_name: str = "",
    duration_ms: int | None = None,
    participants: Iterable[str] = (),
    subject: str = "",
    forwarded: str = "",
) -> CallRecord:
    """Test verisi uretmeyi kisaltan yardimci."""
    started = start if isinstance(start, datetime) else datetime.fromisoformat(str(start))
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    ended = started + timedelta(minutes=minutes)
    outgoing = direction == DIRECTION_OUT
    return CallRecord(
        call_id=call_id,
        start_time=started.isoformat().replace("+00:00", "Z"),
        end_time=ended.isoformat().replace("+00:00", "Z"),
        connect_time=started.isoformat().replace("+00:00", "Z"),
        duration_ms=int(minutes * 60000) if duration_ms is None else duration_ms,
        direction=direction,
        state=state,
        call_type=call_type,
        originator_id=ME if outgoing else other,
        originator_name="" if outgoing else other_name,
        target_id=other if outgoing else ME,
        target_name=other_name if outgoing else "",
        forwarded=forwarded,
        subject=subject,
        participants=list(participants),
        raw={"callId": call_id},
    )


class FakeCallSource:
    """`CallSource` sozlesmesinin bellek ici karsiligi."""

    def __init__(
        self,
        calls: Iterable[CallRecord] = (),
        names: dict[str, str] | None = None,
        my_mri: str = "",
        error: CallsError | None = None,
        report: dict[str, Any] | None = None,
    ) -> None:
        self.calls: list[CallRecord] = list(calls)
        self.names: dict[str, str] = dict(DEFAULT_NAMES if names is None else names)
        # Gercek kaynak bunu veritabani adindan bulur.
        self.my_mri = my_mri
        self.error = error
        self.reads = 0
        # Teshis: gercek kaynakta kopyalama sonucunu tasir, burada testlerin
        # istedigi degerleri (ornegin bir uyari) tasiyabilir.
        self.report: dict[str, Any] = report or {**empty_diagnostics(), "source": SOURCE_COPY}

    def diagnostics(self) -> dict[str, Any]:
        return dict(self.report)

    def read(self) -> tuple[list[CallRecord], dict[str, str]]:
        self.reads += 1
        if self.error is not None:
            raise self.error
        self.report["my_mri"] = self.my_mri
        return (list(self.calls), dict(self.names))

    # --- kolaylik -----------------------------------------------------

    def add(self, record: CallRecord) -> CallRecord:
        self.calls.append(record)
        return record


def sample_source(**kwargs: Any) -> FakeCallSource:
    """Ekranda bir sey gorunsun diye kucuk bir ornek kume."""
    day = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(days=1)
    base = day.replace(hour=8, minute=0, second=0)
    return FakeCallSource(
        calls=[
            call("ornek-1", base, minutes=12),
            call("ornek-2", base + timedelta(hours=2), minutes=3, direction=DIRECTION_IN),
            call(
                "ornek-3",
                base + timedelta(hours=4),
                minutes=45,
                call_type=TYPE_MULTI_PARTY,
                participants=[PERSON_ONE, PERSON_TWO],
            ),
        ],
        **kwargs,
    )
