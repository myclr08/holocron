"""Nabiz takibi ve kendini kapatma.

Arayuz her 30 saniyede bir nabiz gonderir. Zaman asimi bir is gunudur
(12 saat): amac "sekme kapaninca hemen kapan" degil, "unutulmus surec gece
boyu ayakta kalmasin".

Once 5 dakikaydi ve kullaniciyi vuruyordu: Edge'in uyuyan sekmeleri, ekran
kilidi ve arka plan sekme kisitlamasi zamanlayicilari tamamen durduruyor,
uygulama kullanici baska bir ise bakarken kendini kapatiyordu. Sekme
kapatildiginda hizli kapanmayi arayuzdeki "Kapat" dugmesi saglar.

Saat disaridan verilebilir; testler beklemeden zaman ilerletir.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

BEAT_INTERVAL_SECONDS = 30
# Bir is gunu. --timeout ile degistirilebilir.
BEAT_TIMEOUT_SECONDS = 12 * 60 * 60
WATCHDOG_TICK_SECONDS = 5

log = logging.getLogger("holocron.lifecycle")


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
            if not self._heartbeat.is_expired():
                continue
            self._log_reason()
            self._on_expire()
            return

    def _log_reason(self) -> None:
        """Kapanma sebebi loga ayirt edilebilir yazilsin.

        Kullanici "uygulama kendi kendine kapandi" dediginde logdan hangisi
        oldugu anlasilmali: "Kapat" dugmesi mi, yoksa nabiz kesilmesi mi.
        """
        if self._heartbeat.stop_requested:
            log.info("Kapat dugmesi: kapaniliyor")
            return
        log.warning(
            "nabiz %.0f sn'dir yok, zaman asimi %.0f sn: kapaniliyor",
            self._heartbeat.seconds_since_beat(),
            self._heartbeat.timeout,
        )
