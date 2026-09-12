"""Kurulum gerektirmeyen ucuncu parti moduller.

`pip` kurum vekil sunucusunda takilabildigi ve tasinabilir paket cevrimdisi
acildigi icin IndexedDB okuyucusu burada tasiniyor. Yol `sys.path`e yalnizca
gerektiginde eklenir: `app/teamscalls/teams_cache.py` icindeki `ensure_path()`.

Kaynak, surum ve lisanslar icin `README.md`.
"""

from __future__ import annotations

from pathlib import Path

VENDOR_DIR = Path(__file__).resolve().parent


def ensure_path() -> str:
    """Vendor klasorunu `sys.path`e ekler (bir kez) ve yolunu dondurur."""
    import sys

    target = str(VENDOR_DIR)
    if target not in sys.path:
        sys.path.insert(0, target)
    return target
