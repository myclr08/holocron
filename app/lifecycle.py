"""Nabiz takibi ve kendini kapatma.

Arayuz her 30 saniyede bir nabiz gonderir. Tarayici sekmesi kapandiginda
surecin arkada asili kalmamasi icin 5 dakika nabiz gelmezse kapanir.
Saat disaridan verilebilir; testler beklemeden zaman ilerletir.
"""

from __future__ import annotations

import threading
import time
from typing import Callable

BEAT_INTERVAL_SECONDS = 30
BEAT_TIMEOUT_SECONDS = 300
WATCHDOG_TICK_SECONDS = 5


class Heartbeat:
    """Son nabzin uzerinden gecen sureyi tutar."""

    def __init__(
        self,
        timeout: float = BEAT_TIMEOUT_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.timeout = float(timeout)
        self._clock = clock
        self._lock = threading.Lock()
        self._last_beat = clock()
        self._stop_requested = False

    def beat(self) -> None:
        with self._lock:
            self._last_beat = self._clock()

    @property
    def last_beat(self) -> float:
        with self._lock:
            return self._last_beat

    def seconds_since_beat(self) -> float:
        return self._clock() - self.last_beat

    def is_expired(self) -> bool:
        if self._stop_requested:
            return True
        return self.seconds_since_beat() >= self.timeout

    def request_stop(self) -> None:
        with self._lock:
            self._stop_requested = True

    @property
    def stop_requested(self) -> bool:
        with self._lock:
            return self._stop_requested

    def status(self) -> dict[str, float | bool | int]:
        return {
            "interval": BEAT_INTERVAL_SECONDS,
            "timeout": self.timeout,
            "since_last_beat": round(self.seconds_since_beat(), 3),
            "expired": self.is_expired(),
        }


class Watchdog:
    """Nabiz olunce verilen kapatma islevini cagiran arka plan izleyicisi."""

    def __init__(
        self,
        heartbeat: Heartbeat,
        on_expire: Callable[[], None],
        tick: float = WATCHDOG_TICK_SECONDS,
    ) -> None:
        self._heartbeat = heartbeat
        self._on_expire = on_expire
        self._tick = tick
        self._thread: threading.Thread | None = None
        self._cancel = threading.Event()

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="holocron-watchdog", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._cancel.set()

    def _run(self) -> None:
        while not self._cancel.wait(self._tick):
            if self._heartbeat.is_expired():
                self._on_expire()
                return
