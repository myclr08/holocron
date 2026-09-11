from __future__ import annotations

import pytest

from app import paths
from app.secrets import SecretBox, SecretError, ensure_key
from app.settings_store import AUTH_BASIC, AUTH_PAT, MODE_CLOUD, MODE_SERVER

TOKEN = "ornek-pat-degeri-1234"


def test_defaults_prefer_server_and_pat(store):
    view = store.public_view()
    assert view["jira.mode"] == MODE_SERVER
    assert view["jira.auth_type"] == AUTH_PAT
    assert view["verify_ssl"] is True
    assert view["secret_set"] is False


def test_secret_is_encrypted_at_rest(store, conn):
    store.set("jira.secret", TOKEN)
    raw = conn.execute("SELECT value FROM settings WHERE key = 'jira.secret'").fetchone()["value"]
    assert TOKEN not in raw
    assert raw != TOKEN
    assert store.get("jira.secret") == TOKEN


def test_public_view_never_leaks_secret(store):
    store.set("jira.secret", TOKEN)
    view = store.public_view()
    assert view["secret_set"] is True
    assert "jira.secret" not in view
    assert TOKEN not in str(view)


def test_apply_ignores_unknown_keys(store):
    store.apply({"jira.base_url": "https://jira.example.com", "kotu.anahtar": "x"})
    assert store.get("jira.base_url") == "https://jira.example.com"
    assert store.get("kotu.anahtar") is None


def test_empty_secret_does_not_wipe_existing(store):
    store.set("jira.secret", TOKEN)
    store.apply({"jira.secret": ""})
    assert store.get("jira.secret") == TOKEN


def test_clear_secret_removes_it(store):
    store.set("jira.secret", TOKEN)
    store.apply({"clear_secret": True})
    assert store.public_view()["secret_set"] is False
    assert store.get("jira.secret", "") == ""


def test_verify_ssl_round_trip(store):
    store.apply({"net.verify_ssl": False})
    assert store.public_view()["verify_ssl"] is False
    assert store.jira_config().verify_ssl is False
    store.apply({"net.verify_ssl": True})
    assert store.jira_config().verify_ssl is True


def test_jira_config_is_built_from_settings(store):
    store.apply(
        {
            "jira.mode": MODE_SERVER,
            "jira.base_url": "  https://jira.example.com/  ",
            "jira.auth_type": AUTH_BASIC,
            "jira.username": "demo.kullanici",
            "jira.secret": TOKEN,
            "net.proxy_https": "http://proxy.example.com:8080",
            "net.ca_file": "/tmp/ornek-ca.pem",
        }
    )
    config = store.jira_config()
    assert config.mode == MODE_SERVER
    assert config.base_url == "https://jira.example.com/"
    assert config.auth_type == AUTH_BASIC
    assert config.username == "demo.kullanici"
    assert config.secret == TOKEN
    assert config.has_secret is True
    assert config.proxy_https == "http://proxy.example.com:8080"
    assert config.ca_file == "/tmp/ornek-ca.pem"


def test_cloud_mode_can_be_selected(store):
    store.apply({"jira.mode": MODE_CLOUD, "jira.email": "kullanici@example.com"})
    config = store.jira_config()
    assert config.mode == MODE_CLOUD
    assert config.email == "kullanici@example.com"


def test_unreadable_secret_is_treated_as_missing(conn, box):
    from app.settings_store import SettingsStore

    SettingsStore(conn, box).set("jira.secret", TOKEN)

    from cryptography.fernet import Fernet

    other = SettingsStore(conn, SecretBox(key=Fernet.generate_key()))
    assert other.get("jira.secret", "") == ""
    # Deger duruyor ama cozulemiyor: arayuze "ayarli" gorunur, istemci hata verir.
    assert other.has_secret("jira.secret") is True


def test_key_file_is_created_with_owner_only_permissions(tmp_path):
    key_file = tmp_path / "holocron.key"
    key = ensure_key(key_file)
    assert key_file.exists()
    assert key_file.stat().st_mode & 0o777 == 0o600
    # Ikinci cagri ayni anahtari dondurur, yenisini uretmez.
    assert ensure_key(key_file) == key


def test_key_lives_in_home_dir(isolated_home):
    assert paths.key_path() == isolated_home / "holocron.key"
    assert paths.db_path() == isolated_home / "holocron.db"


def test_broken_ciphertext_raises(box):
    with pytest.raises(SecretError):
        box.decrypt("bu-bir-fernet-degeri-degil")
