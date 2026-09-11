"""Sirlarin sifrelenmesi.

Token/PAT veritabaninda duz metin durmaz. Anahtar ayri bir dosyada (0600)
tutulur; dosya kaybolursa sirlar cozulemez, bu durumda kullanicidan yeniden
girmesi istenir.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from . import paths


class SecretError(RuntimeError):
    """Sifreleme katmani hatasi."""


def ensure_key(path: Path | None = None) -> bytes:
    """Anahtari okur, yoksa uretir. Dosya izni 0600 olarak zorlanir."""
    target = path or paths.key_path()
    if target.exists():
        key = target.read_bytes().strip()
        if not key:
            raise SecretError("Anahtar dosyası boş.")
        _harden(target)
        return key

    key = Fernet.generate_key()
    target.parent.mkdir(parents=True, exist_ok=True)
    # Once dar izinle yarat, sonra yaz: arada baskasinin okumasina firsat kalmasin.
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(key)
    _harden(target)
    return key


def _harden(target: Path) -> None:
    try:
        os.chmod(target, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        # Windows dosya sistemlerinde chmod anlamsiz olabilir; kritik degil.
        pass


class SecretBox:
    """Fernet sarmalayicisi."""

    def __init__(self, key: bytes | None = None, key_file: Path | None = None) -> None:
        self._fernet = Fernet(key or ensure_key(key_file))

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode("utf-8")).decode("ascii")

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode("ascii")).decode("utf-8")
        except (InvalidToken, ValueError) as exc:
            raise SecretError("Sir cozulemedi, anahtar degismis olabilir.") from exc
