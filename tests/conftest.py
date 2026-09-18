from __future__ import annotations

import sys
from pathlib import Path

import pytest
from cryptography.fernet import Fernet

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import db  # noqa: E402
from app.context import AppContext  # noqa: E402
from app.jira_client import create_client  # noqa: E402
from app.lifecycle import Heartbeat  # noqa: E402
from app.gorusme import sahte as gorusme_sahte  # noqa: E402
from app.mail.fake import FakeMailSource  # noqa: E402
from app.secrets import SecretBox  # noqa: E402
from app.teamscalls.fake import FakeCallSource  # noqa: E402
from app.settings_store import SettingsStore  # noqa: E402
from tests.fake_jira import FakeJira  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """Her test kendi veri klasorunde calisir; gercek holocron.db'ye dokunulmaz."""
    monkeypatch.setenv("HOLOCRON_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def conn(tmp_path):
    connection = db.open_database(tmp_path / "test.db")
    yield connection
    connection.close()


@pytest.fixture
def box():
    return SecretBox(key=Fernet.generate_key())


@pytest.fixture
def store(conn, box):
    return SettingsStore(conn, box)


@pytest.fixture
def fake_jira():
    return FakeJira()


@pytest.fixture
def sleeps():
    """Yeniden deneme beklemeleri: gercekten uyumadan kaydedilir."""
    return []


@pytest.fixture
def client_factory(fake_jira, sleeps):
    def factory(config):
        return create_client(config, session=fake_jira.session, sleep=sleeps.append)

    return factory


@pytest.fixture
def fake_mail():
    """Bellek ici posta kaynagi; hicbir test gercek Outlook'a dokunmaz."""
    return FakeMailSource()


@pytest.fixture
def fake_calls():
    """Bellek ici arama gecmisi kaynagi; hicbir test gercek onbellege dokunmaz."""
    return FakeCallSource()


@pytest.fixture
def fake_gorusme():
    """Gorusme hattinin sahteleri: algilayici, kayitci, dokucu, ozetleyici.

    Hicbir test gercek ses kartina, faster-whisper'a ya da Copilot CLI'ye
    dokunmaz; butun akis bellek ici sahtelerle uctan uca kosar.
    """
    return {
        "algilayici": gorusme_sahte.SahteAlgilayici(),
        "kayitci": gorusme_sahte.SahteKayitci(),
        "dokucu": gorusme_sahte.SahteYaziyaDokucu(),
        "ozetleyici": gorusme_sahte.SahteOzetleyici(),
        "bildirimci": gorusme_sahte.SahteBildirimci(),
    }


@pytest.fixture
def context(conn, store, client_factory, fake_mail, fake_calls, fake_gorusme):
    return AppContext(
        conn=conn,
        settings=store,
        heartbeat=Heartbeat(),
        client_factory=client_factory,
        mail_factory=lambda body_limit=None: fake_mail,
        calls_factory=lambda cache_path="": fake_calls,
        gorusme_algilayici_factory=lambda *args, **kwargs: fake_gorusme["algilayici"],
        gorusme_kayit_factory=lambda *args, **kwargs: fake_gorusme["kayitci"],
        gorusme_dokucu_factory=lambda ayarlar: fake_gorusme["dokucu"],
        gorusme_ozetleyici_factory=lambda ayarlar: fake_gorusme["ozetleyici"],
        gorusme_bildirimci=fake_gorusme["bildirimci"],
    )


@pytest.fixture
def api_client(context):
    from fastapi.testclient import TestClient

    from app.server import create_app

    with TestClient(create_app(context)) as client:
        yield client
