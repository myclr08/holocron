"""Nabiz ve kendini kapatma mantigi. Saat enjekte edilir, test beklemez."""

from __future__ import annotations

import threading

from app.lifecycle import (
    BEAT_INTERVAL_SECONDS,
    BEAT_TIMEOUT_SECONDS,
    Heartbeat,
    Watchdog,
)


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def test_defaults_match_product_rules():
    assert BEAT_INTERVAL_SECONDS == 30
    assert BEAT_TIMEOUT_SECONDS == 300


def test_fresh_heartbeat_is_alive():
    clock = FakeClock()
    beat = Heartbeat(clock=clock)
    assert beat.is_expired() is False
    assert beat.seconds_since_beat() == 0


def test_expires_after_timeout():
    clock = FakeClock()
    beat = Heartbeat(timeout=300, clock=clock)
    clock.advance(299)
    assert beat.is_expired() is False
    clock.advance(1)
    assert beat.is_expired() is True


def test_beat_resets_the_clock():
    clock = FakeClock()
    beat = Heartbeat(timeout=300, clock=clock)
    clock.advance(290)
    beat.beat()
    clock.advance(290)
    assert beat.is_expired() is False
    assert beat.seconds_since_beat() == 290


def test_request_stop_expires_immediately():
    clock = FakeClock()
    beat = Heartbeat(timeout=300, clock=clock)
    beat.request_stop()
    assert beat.stop_requested is True
    assert beat.is_expired() is True


def test_status_reports_remaining_time():
    clock = FakeClock()
    beat = Heartbeat(timeout=300, clock=clock)
    clock.advance(12.5)
    status = beat.status()
    assert status["since_last_beat"] == 12.5
    assert status["timeout"] == 300
    assert status["interval"] == BEAT_INTERVAL_SECONDS
    assert status["expired"] is False


def test_watchdog_fires_when_heartbeat_dies():
    clock = FakeClock()
    beat = Heartbeat(timeout=300, clock=clock)
    fired = threading.Event()

    watchdog = Watchdog(beat, fired.set, tick=0.01)
    watchdog.start()
    try:
        assert fired.wait(0.2) is False  # nabiz taze, kapanmamali
        clock.advance(301)
        assert fired.wait(1.0) is True
    finally:
        watchdog.stop()


def test_watchdog_stops_without_firing():
    clock = FakeClock()
    beat = Heartbeat(timeout=300, clock=clock)
    fired = threading.Event()

    watchdog = Watchdog(beat, fired.set, tick=0.01)
    watchdog.start()
    watchdog.stop()
    clock.advance(999)
    assert fired.wait(0.1) is False
