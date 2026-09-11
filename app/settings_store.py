"""Ayarlarin okunmasi/yazilmasi.

Sir iceren anahtarlar sifreli saklanir ve disari asla ham dondurulmez;
arayuz yalnizca "ayarli / ayarsiz" bilgisini gorur.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from .secrets import SecretBox, SecretError

MODE_CLOUD = "cloud"
MODE_SERVER = "server"

AUTH_PAT = "pat"
AUTH_BASIC = "basic"

# Sifrelenerek saklanan anahtarlar.
SECRET_KEYS: frozenset[str] = frozenset({"jira.secret"})

DEFAULTS: dict[str, str] = {
    "jira.mode": MODE_SERVER,  # yaygin kurulum: Server/DC + PAT
    "jira.base_url": "",
    "jira.email": "",
    "jira.username": "",
    "jira.auth_type": AUTH_PAT,
    "net.proxy_http": "",
    "net.proxy_https": "",
    "net.ca_file": "",
    "net.verify_ssl": "1",
}

# Arayuzun gonderebilecegi duz ayarlar; buradaki liste beyaz listedir.
PUBLIC_KEYS: tuple[str, ...] = tuple(DEFAULTS)


@dataclass(frozen=True)
class JiraConfig:
    """Jira istemcisinin ihtiyac duydugu birlesik yapilandirma."""

    mode: str
    base_url: str
    email: str = ""
    username: str = ""
    auth_type: str = AUTH_PAT
    secret: str = ""
    proxy_http: str = ""
    proxy_https: str = ""
    ca_file: str = ""
    verify_ssl: bool = True

    @property
    def has_secret(self) -> bool:
        return bool(self.secret)


class SettingsStore:
    def __init__(self, conn: sqlite3.Connection, box: SecretBox | None = None) -> None:
        self._conn = conn
        self._box = box or SecretBox()

    # --- temel erisim -------------------------------------------------

    def get(self, key: str, default: str | None = None) -> str | None:
        row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        if row is None:
            return DEFAULTS.get(key, default)
        value = row["value"]
        if value is None:
            return DEFAULTS.get(key, default)
        if key in SECRET_KEYS:
            try:
                return self._box.decrypt(value)
            except SecretError:
                # Anahtar degismisse sir kullanilamaz; ayarsiz kabul edilir.
                return default
        return value

    def set(self, key: str, value: str | None) -> None:
        if value is None:
            self.delete(key)
            return
        stored = self._box.encrypt(value) if key in SECRET_KEYS else value
        self._conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, stored),
        )
        self._conn.commit()

    def delete(self, key: str) -> None:
        self._conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        self._conn.commit()

    def has_secret(self, key: str) -> bool:
        row = self._conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
        return bool(row and row["value"])

    # --- arayuz gorunumu ----------------------------------------------

    def public_view(self) -> dict[str, Any]:
        """Arayuze gonderilen ayar goruntusu: sir yok, yalnizca durum bayragi."""
        data: dict[str, Any] = {key: self.get(key, DEFAULTS.get(key, "")) for key in PUBLIC_KEYS}
        data["verify_ssl"] = self.get("net.verify_ssl", "1") == "1"
        data["secret_set"] = self.has_secret("jira.secret")
        return data

    def apply(self, payload: dict[str, Any]) -> None:
        """Beyaz listedeki alanlari yazar; sir alani ayri ele alinir."""
        for key in PUBLIC_KEYS:
            if key not in payload:
                continue
            value = payload[key]
            if key == "net.verify_ssl":
                value = "1" if _as_bool(value) else "0"
            self.set(key, "" if value is None else str(value))

        if "jira.secret" in payload:
            secret = payload["jira.secret"]
            if secret is None or secret == "":
                # Bos gonderim mevcut siri bozmaz; silmek icin acik istek gerekir.
                pass
            else:
                self.set("jira.secret", str(secret))
        if payload.get("clear_secret"):
            self.delete("jira.secret")

    # --- tureyen yapilandirma ------------------------------------------

    def jira_config(self) -> JiraConfig:
        return JiraConfig(
            mode=self.get("jira.mode", MODE_SERVER) or MODE_SERVER,
            base_url=(self.get("jira.base_url", "") or "").strip(),
            email=(self.get("jira.email", "") or "").strip(),
            username=(self.get("jira.username", "") or "").strip(),
            auth_type=self.get("jira.auth_type", AUTH_PAT) or AUTH_PAT,
            secret=self.get("jira.secret", "") or "",
            proxy_http=(self.get("net.proxy_http", "") or "").strip(),
            proxy_https=(self.get("net.proxy_https", "") or "").strip(),
            ca_file=(self.get("net.ca_file", "") or "").strip(),
            verify_ssl=self.get("net.verify_ssl", "1") == "1",
        )


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"1", "true", "yes", "on", "evet"}
