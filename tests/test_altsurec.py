"""Sessiz alt surec ayarlari: Windows'ta konsol penceresi acilmasin.

Test edilen soru: `sessiz_calistir_ayarlari()` Windows disinda bos (yalnizca
`stdin`) mi doner, Windows'ta `creationflags` ve `startupinfo`yu dogru
bayraklarla mi ekliyor?

Linux'ta gercek `subprocess` modulunde `STARTUPINFO`/`CREATE_NO_WINDOW` hic
yok, bu yuzden Windows dalini tam test edebilmek icin bu ozellikler sahte
degerlerle `monkeypatch` edilir -- boylece kod, gercek Windows'taymis gibi
calisir ve `getattr(..., 0)` gecikmesine dusmez.
"""

from __future__ import annotations

import subprocess
import sys

from app.gorusme.altsurec import sessiz_calistir_ayarlari


def test_non_windows_platforms_get_no_window_flags(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    ayarlar = sessiz_calistir_ayarlari()
    assert ayarlar == {"stdin": subprocess.DEVNULL}


def test_windows_without_startupinfo_still_sets_creationflags(monkeypatch):
    """Bu depodaki gercek `subprocess`ta (Linux) STARTUPINFO yok: cokmemeli."""
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delattr(subprocess, "STARTUPINFO", raising=False)
    ayarlar = sessiz_calistir_ayarlari()
    assert ayarlar["stdin"] == subprocess.DEVNULL
    assert ayarlar["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)
    assert "startupinfo" not in ayarlar


def test_windows_hides_the_window_with_startupinfo(monkeypatch):
    """Gercek Windows'taki davranisi, sahte STARTUPINFO ile Linux'ta da kanitlar."""

    class SahteStartupInfo:
        def __init__(self) -> None:
            self.dwFlags = 0
            self.wShowWindow = None

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(subprocess, "STARTUPINFO", SahteStartupInfo, raising=False)
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr(subprocess, "STARTF_USESHOWWINDOW", 1, raising=False)
    monkeypatch.setattr(subprocess, "SW_HIDE", 0, raising=False)

    ayarlar = sessiz_calistir_ayarlari()

    assert ayarlar["stdin"] == subprocess.DEVNULL
    assert ayarlar["creationflags"] == 0x08000000
    baslangic = ayarlar["startupinfo"]
    assert isinstance(baslangic, SahteStartupInfo)
    assert baslangic.dwFlags & 1
    assert baslangic.wShowWindow == 0
