"""Isletim sistemine bir adres actirir: `https://` ya da `msteams:` gibi bir
protokol baglantisi.

Ayri bir modulde duruyor cunku `app/teams.py` saf tutuluyor (yan etki yok) ve
tarayici acan `app/__main__.open_browser` yalnizca http(s) icindir:
`webbrowser.open` protokol baglantisini Linux/macOS'ta cogu zaman acmaz.
Windows'ta `os.startfile` adresi kayitli isleyiciye verir; Teams kuruluysa
dogrudan uygulama acilir, degilse Windows kendi cozumunu gosterir.

Hicbir hata disari sizmaz: acilamadiysa `False` doner, cagiran taraf adresi
kullaniciya verir.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys

log = logging.getLogger("holocron.desktop")

# Sureci beklemeyiz: acici komut kendi penceresini acip cikar.
_POPEN_OPTIONS = {
    "stdout": subprocess.DEVNULL,
    "stderr": subprocess.DEVNULL,
    "stdin": subprocess.DEVNULL,
}


def open_url(url: str) -> bool:
    """Adresi isletim sisteminin kayitli isleyicisine verir."""
    target = str(url or "").strip()
    if not target:
        return False

    if sys.platform.startswith("win"):
        starter = getattr(os, "startfile", None)
        if starter is None:  # pragma: no cover - Windows disi
            return False
        try:
            starter(target)
            return True
        except OSError:
            log.warning("os.startfile ile adres açılamadı", exc_info=True)
            return False

    command = ["open", target] if sys.platform == "darwin" else ["xdg-open", target]
    try:
        subprocess.Popen(command, **_POPEN_OPTIONS)  # noqa: S603 - sabit komut
        return True
    except (OSError, ValueError):
        log.warning("%s ile adres açılamadı", command[0], exc_info=True)
        return False
