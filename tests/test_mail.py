"""Asama 7: Outlook e-postalarindan gorev uretme.

Test edilen sorular:

* Bu posta beni ilgilendiriyor mu? (gonderen/alici/CC, joker, X500 vs SMTP)
* Takvim daveti gorev uretiyor mu? (uretmemeli)
* Ayni konusma ikinci kez gorev uretiyor mu? (uretmemeli -- gorev silinmis,
  bitmis ya da duruyor olsun)
* Windows olmayan makinede uclar ne diyor? (`feature_unavailable`)

Gercek COM hicbir testte calismaz: `app/mail/fake.py` bellek ici kaynak,
`FakeItem` ise duck typing ile sahte bir COM ogesidir.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app import db, repository as repo
from app.mail import MailError, default_source, intake, outlook
from app.mail.fake import FakeMailSource, message
from app.mail.source import (
    MailMessage,
    address_matches,
    clean_body,
    is_mail_class,
    normalize_topic,
    parse_addresses,
)

ME = "ornek@example.com"
NOW = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)


def moment(hours_ago: float = 0) -> datetime:
    return NOW - timedelta(hours=hours_ago)


def config(**extra) -> intake.MailConfig:
    values = {"enabled": True, "addresses": (ME,), "folders": ("Gelen Kutusu",), "days": 30}
    values.update(extra)
    return intake.MailConfig(**values)


def enable_mail(api_client, addresses: str = ME, **extra) -> dict:
    payload = {
        "mail.enabled": "1",
        "mail.addresses": addresses,
        "mail.folders": '["Gelen Kutusu"]',
    }
    payload.update(extra)
    response = api_client.put("/api/settings", json=payload)
    assert response.status_code == 200, response.text
    return response.json()["settings"]


def scan(conn, source, cfg=None, now=NOW) -> dict:
    return intake.scan(conn, source, cfg or config(), now=now)


def quiet_jira(context, conn) -> None:
    """Guncelle'nin Jira ayagi sessizce basarili olsun: adres tanimli, katalog dolu."""
    context.settings.apply(
        {"jira.base_url": "https://jira.example.com", "jira.secret": "ornek-pat"}
    )
    repo.store_fields(conn, [{"id": "summary", "name": "Özet", "schema": {"type": "string"}}])


# --- goc ----------------------------------------------------------------


def test_migration_creates_the_mail_tables(conn):
    names = db.table_names(conn)
    assert "mail_conversations" in names
    assert "mail_messages" in names


def test_tasks_gain_the_mail_columns(conn):
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    for name in (
        "source",
        "mail_conversation_id",
        "mail_sender",
        "mail_received_at",
        "mail_count",
        "mail_last_at",
        "mail_entry_id",
        "mail_store_id",
    ):
        assert name in columns, name


def test_existing_tasks_stay_manual(conn):
    task = repo.create_task(conn, "Elle yazdim")
    assert task["source"] == repo.TASK_SOURCE_MANUAL
    assert task["mail_count"] == 0


def test_conversation_state_is_constrained(conn):
    repo.upsert_mail_conversation(conn, "c1", task_id=None, state="active")
    with pytest.raises(Exception):
        conn.execute(
            "INSERT INTO mail_conversations (conversation_id, state) VALUES ('c2', 'belki')"
        )


# --- adres eslestirme (saf) ---------------------------------------------


def test_addresses_are_parsed_and_folded():
    assert parse_addresses(" Ornek@Example.COM , ikinci@example.com ") == [
        "ornek@example.com",
        "ikinci@example.com",
    ]
    assert parse_addresses("<ornek@example.com>") == ["ornek@example.com"]
    assert parse_addresses("SMTP:ornek@example.com") == ["ornek@example.com"]
    assert parse_addresses("") == []


def test_wildcard_matches_a_whole_domain():
    patterns = ["*@example.com"]
    assert address_matches("biri@example.com", patterns)
    assert address_matches("BIRI@Example.com", patterns)
    assert not address_matches("biri@baska.com", patterns)


def test_sender_recipient_or_cc_is_enough():
    addresses = [ME]
    assert intake.message_matches(message("<1>", sender=ME), addresses)
    assert intake.message_matches(message("<2>", to=[ME]), addresses)
    assert intake.message_matches(message("<3>", cc=[ME]), addresses)
    assert not intake.message_matches(message("<4>", to=["baska@example.com"]), addresses)


def test_no_address_means_no_match():
    assert not intake.message_matches(message("<1>", sender=ME), [])


# --- takvim / sistem ogeleri --------------------------------------------


@pytest.mark.parametrize(
    "message_class",
    [
        "IPM.Schedule.Meeting.Request",
        "IPM.Schedule.Meeting.Resp.Pos",
        "IPM.Appointment",
        "IPM.TaskRequest",
        "REPORT.IPM.Note.NDR",
        "IPM.Note.Rules.OofTemplate.Microsoft",
        "",
    ],
)
def test_calendar_and_system_items_are_not_mail(message_class):
    assert not is_mail_class(message_class)


@pytest.mark.parametrize("message_class", ["IPM.Note", "IPM.Note.SMIME", "ipm.note"])
def test_plain_mail_is_mail(message_class):
    assert is_mail_class(message_class)


def test_calendar_items_never_become_tasks(conn):
    source = FakeMailSource(
        [
            message("<davet>", sender=ME, message_class="IPM.Schedule.Meeting.Request",
                    received_at=moment(1)),
            message("<posta>", sender=ME, subject="Gerçek iş", received_at=moment(2)),
        ]
    )
    summary = scan(conn, source)
    assert summary["created"] == 1
    assert summary["skipped_calendar"] == 1
    assert [task["title"] for task in repo.list_tasks(conn)] == ["Gerçek iş"]


# --- konusma anahtari ---------------------------------------------------


def test_reply_prefixes_collapse_into_one_topic():
    assert normalize_topic("RE: Teklif") == normalize_topic("Teklif")
    assert normalize_topic("YNT: Teklif") == normalize_topic("Teklif")
    assert normalize_topic("FW: RE: Teklif") == normalize_topic("teklif")


def test_conversation_falls_back_to_the_topic():
    first = message("<1>", subject="Teklif", conversation_id="")
    second = message("<2>", subject="RE: Teklif", conversation_id="")
    assert first.conversation() == second.conversation()


def test_conversation_id_wins_over_the_topic():
    item = message("<1>", subject="Teklif", conversation_id="CONV-1")
    assert item.conversation() == "CONV-1"


# --- gorev uretimi ------------------------------------------------------


def test_a_matching_mail_becomes_a_todo_task(conn):
    source = FakeMailSource(
        [message("<1>", subject="Sunucu raporu", sender="patron@example.com", to=[ME],
                 body="İlk satır\r\n\r\n\r\nİkinci satır", received_at=moment(1))]
    )
    summary = scan(conn, source)

    assert summary == {
        "created": 1,
        "appended": 0,
        "skipped_calendar": 0,
        "skipped_seen": 0,
        "scanned": 1,
        "errors": [],
    }
    task = repo.list_tasks(conn)[0]
    assert task["status"] == repo.TASK_TODO
    assert task["title"] == "Sunucu raporu"
    assert task["description"] == "İlk satır\n\nİkinci satır"
    assert task["note"].startswith("Kimden: patron@example.com · Alındı: ")
    assert task["source"] == repo.TASK_SOURCE_MAIL
    assert task["mail_sender"] == "patron@example.com"
    assert task["mail_count"] == 1
    assert task["mail_entry_id"] == "ENTRY-<1>"


def test_a_mail_task_lands_on_top_of_the_column(conn):
    repo.create_task(conn, "Elle yazdim")
    scan(conn, FakeMailSource([message("<1>", subject="Postadan", sender=ME, received_at=moment(1))]))
    titles = [task["title"] for task in repo.list_tasks(conn, repo.TASK_TODO)]
    assert titles == ["Postadan", "Elle yazdim"]


def test_an_empty_subject_still_gets_a_name(conn):
    scan(conn, FakeMailSource([message("<1>", subject="   ", sender=ME, received_at=moment(1))]))
    assert repo.list_tasks(conn)[0]["title"] == intake.NO_SUBJECT


def test_a_long_subject_is_cut_to_the_task_limit(conn):
    scan(conn, FakeMailSource([message("<1>", subject="A" * 500, sender=ME, received_at=moment(1))]))
    title = repo.list_tasks(conn)[0]["title"]
    assert len(title) == repo.TASK_TITLE_LIMIT
    assert title.endswith("…")


def test_the_body_is_cut_at_the_configured_limit(conn):
    source = FakeMailSource([message("<1>", sender=ME, body="x" * 9000, received_at=moment(1))])
    scan(conn, source, config(body_limit=500))
    description = repo.list_tasks(conn)[0]["description"]
    assert len(description) == 501  # 500 karakter + kesme isareti
    assert description.endswith("…")


def test_mails_are_processed_oldest_first(conn):
    source = FakeMailSource(
        [
            message("<yeni>", subject="Yeni", sender=ME, received_at=moment(1)),
            message("<eski>", subject="Eski", sender=ME, received_at=moment(5)),
        ]
    )
    scan(conn, source)
    # En yeni posta en son islendigi icin sutunun en ustunde durur.
    assert [task["title"] for task in repo.list_tasks(conn, repo.TASK_TODO)] == ["Yeni", "Eski"]


def test_mails_outside_the_window_are_ignored(conn):
    source = FakeMailSource(
        [message("<eski>", sender=ME, received_at=NOW - timedelta(days=40))]
    )
    summary = scan(conn, source, config(days=30))
    assert summary["created"] == 0
    assert repo.list_tasks(conn) == []


def test_a_mail_that_does_not_match_is_skipped(conn):
    source = FakeMailSource(
        [message("<1>", sender="biri@baska.com", to=["digeri@baska.com"], received_at=moment(1))]
    )
    summary = scan(conn, source)
    assert summary["scanned"] == 1
    assert summary["created"] == 0


# --- tekillestirme ------------------------------------------------------


def test_one_task_per_conversation(conn):
    source = FakeMailSource(
        [
            message("<1>", subject="Teklif", sender=ME, conversation_id="C1", received_at=moment(5)),
            message("<2>", subject="RE: Teklif", sender=ME, conversation_id="C1",
                    received_at=moment(1)),
        ]
    )
    summary = scan(conn, source)

    assert summary["created"] == 1
    assert summary["appended"] == 1
    tasks = repo.list_tasks(conn)
    assert len(tasks) == 1
    assert tasks[0]["mail_count"] == 2
    assert tasks[0]["mail_last_at"] > tasks[0]["mail_received_at"]


def test_scanning_twice_creates_nothing_new(conn):
    source = FakeMailSource([message("<1>", sender=ME, received_at=moment(1))])
    first = scan(conn, source)
    second = scan(conn, source)

    assert first["created"] == 1
    assert second["created"] == 0
    assert second["skipped_seen"] == 1
    assert len(repo.list_tasks(conn)) == 1


def test_a_deleted_task_never_comes_back(conn):
    source = FakeMailSource(
        [message("<1>", subject="Teklif", sender=ME, conversation_id="C1", received_at=moment(5))]
    )
    scan(conn, source)
    task = repo.list_tasks(conn)[0]
    repo.delete_task(conn, task["id"])

    known = repo.get_mail_conversation(conn, "C1")
    assert known["state"] == repo.MAIL_TASK_DELETED
    assert known["task_id"] is None
    assert repo.list_mail_messages(conn, "C1")[0]["task_id"] is None

    # Ayni konusmadan yeni bir mesaj gelse bile gorev uretilmez.
    source.add(message("<2>", subject="RE: Teklif", sender=ME, conversation_id="C1",
                       received_at=moment(1)))
    summary = scan(conn, source)
    assert summary["created"] == 0
    assert summary["appended"] == 1
    assert repo.list_tasks(conn) == []


def test_a_finished_task_only_collects_the_message(conn):
    source = FakeMailSource(
        [message("<1>", subject="Teklif", sender=ME, conversation_id="C1", received_at=moment(5))]
    )
    scan(conn, source)
    task = repo.list_tasks(conn)[0]
    repo.move_task(conn, task["id"], status=repo.TASK_DONE)

    source.add(message("<2>", subject="RE: Teklif", sender=ME, conversation_id="C1",
                       received_at=moment(1)))
    summary = scan(conn, source)

    assert summary["appended"] == 1
    done = repo.get_task(conn, task["id"])
    assert done["status"] == repo.TASK_DONE
    assert done["mail_count"] == 1  # sayac bozulmaz
    assert len(repo.list_mail_messages(conn, "C1")) == 2


def test_an_ignored_conversation_produces_nothing(conn):
    repo.upsert_mail_conversation(conn, "C1", task_id=None, state=repo.MAIL_IGNORED)
    source = FakeMailSource(
        [message("<1>", sender=ME, conversation_id="C1", received_at=moment(1))]
    )
    summary = scan(conn, source)
    assert summary["created"] == 0
    assert repo.list_tasks(conn) == []


# --- klasorler ve hatalar -----------------------------------------------


def test_one_broken_folder_does_not_stop_the_others(conn):
    source = FakeMailSource(
        [message("<1>", sender=ME, folder_path="Gelen Kutusu", received_at=moment(1))],
        failing_folders=["Arşiv"],
    )
    summary = scan(conn, source, config(folders=("Arşiv", "Gelen Kutusu")))

    assert summary["created"] == 1
    assert summary["errors"][0]["folder"] == "Arşiv"
    assert summary["errors"][0]["code"] == "folder_not_found"


def test_the_same_mail_in_two_folders_is_counted_once(conn):
    shared = dict(sender=ME, subject="Tek", received_at=moment(1))
    source = FakeMailSource(
        [
            message("<1>", folder_path="Gelen Kutusu", **shared),
            message("<1>", folder_path="Arşiv", **shared),
        ]
    )
    summary = scan(conn, source, config(folders=("Gelen Kutusu", "Arşiv")))
    assert summary["created"] == 1


def test_a_disabled_config_scans_nothing(conn):
    source = FakeMailSource([message("<1>", sender=ME, received_at=moment(1))])
    assert scan(conn, source, config(enabled=False))["scanned"] == 0
    assert scan(conn, source, config(addresses=()))["scanned"] == 0


# --- yapilandirma -------------------------------------------------------


def test_config_comes_from_the_settings_table(store):
    store.apply(
        {
            "mail.enabled": "1",
            "mail.addresses": "ornek@example.com, *@example.com",
            "mail.folders": '["Gelen Kutusu", "Arşiv\\\\2026"]',
            "mail.days": "7",
            "mail.body_limit": "1200",
            "mail.scan_on_refresh": "0",
        }
    )
    cfg = intake.load_config(store)
    assert cfg.enabled is True
    assert cfg.addresses == ("ornek@example.com", "*@example.com")
    assert cfg.folders == ("Gelen Kutusu", "Arşiv\\2026")
    assert cfg.days == 7
    assert cfg.body_limit == 1200
    assert cfg.scan_on_refresh is False
    assert cfg.ready is True


def test_broken_settings_fall_back_to_the_defaults(store):
    store.apply({"mail.enabled": "1", "mail.addresses": ME, "mail.days": "sallama",
                 "mail.folders": "[bozuk"})
    cfg = intake.load_config(store)
    assert cfg.days == intake.DEFAULT_DAYS
    assert cfg.folders == intake.DEFAULT_FOLDERS


def test_the_defaults_are_conservative(store):
    cfg = intake.load_config(store)
    assert cfg.enabled is False
    assert cfg.ready is False
    assert cfg.days == 30
    assert cfg.body_limit == 4000


# --- API ----------------------------------------------------------------


def test_scan_endpoint_reports_what_it_did(api_client, fake_mail):
    enable_mail(api_client)
    fake_mail.add(message("<1>", subject="Sunucu raporu", sender="patron@example.com", to=[ME]))

    response = api_client.post("/api/mail/scan")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["summary"]["created"] == 1
    todo = next(item for item in body["board"]["columns"] if item["status"] == "todo")
    assert todo["tasks"][0]["title"] == "Sunucu raporu"
    assert todo["tasks"][0]["source"] == "mail"


def test_scan_is_refused_when_the_feature_is_off(api_client):
    response = api_client.post("/api/mail/scan")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "mail_disabled"


def test_scan_is_refused_without_an_address(api_client):
    api_client.put("/api/settings", json={"mail.enabled": "1", "mail.addresses": ""})
    response = api_client.post("/api/mail/scan")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "mail_no_address"


def test_the_test_endpoint_reports_the_account(api_client, fake_mail):
    enable_mail(api_client)
    result = api_client.post("/api/mail/test").json()["result"]
    assert result["ok"] is True
    assert result["account"] == "ornek@example.com"
    assert fake_mail.probes == 1


def test_the_folder_endpoint_returns_a_tree(api_client, fake_mail):
    enable_mail(api_client)
    fake_mail.add(message("<1>", sender=ME, folder_path="Gelen Kutusu"))
    folders = api_client.get("/api/mail/folders").json()["folders"]
    assert folders[0]["path"] == "Gelen Kutusu"
    assert folders[0]["count"] == 1
    assert "children" in folders[0]


def test_open_mail_asks_outlook_for_the_item(api_client, fake_mail):
    enable_mail(api_client)
    fake_mail.add(message("<1>", subject="Aç beni", sender=ME))
    api_client.post("/api/mail/scan")
    task_id = api_client.get("/api/tasks").json()["columns"][0]["tasks"][0]["id"]

    response = api_client.post(f"/api/tasks/{task_id}/open-mail")
    assert response.status_code == 200
    assert fake_mail.opened == [("ENTRY-<1>", "STORE")]


def test_open_mail_on_a_manual_task_is_a_clean_404(api_client):
    task = api_client.post("/api/tasks", json={"title": "Elle"}).json()["task"]
    response = api_client.post(f"/api/tasks/{task['id']}/open-mail")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "mail_not_found"


def test_settings_expose_the_mail_keys(api_client):
    settings = api_client.get("/api/settings").json()["settings"]
    assert settings["mail.enabled"] == "0"
    assert settings["mail.folders"] == '["Gelen Kutusu"]'
    assert settings["mail.days"] == "30"
    assert "mail_supported" in settings


def test_mail_fields_are_read_only_on_a_task(api_client, fake_mail):
    enable_mail(api_client)
    fake_mail.add(message("<1>", subject="Kaynak", sender="patron@example.com", to=[ME]))
    api_client.post("/api/mail/scan")
    task = api_client.get("/api/tasks").json()["columns"][0]["tasks"][0]

    api_client.put(
        f"/api/tasks/{task['id']}",
        json={"source": "manual", "mail_sender": "sahte@example.com", "title": "Yeni ad"},
    )
    after = api_client.get("/api/tasks").json()["columns"][0]["tasks"][0]
    assert after["title"] == "Yeni ad"
    assert after["source"] == "mail"
    assert after["mail_sender"] == "patron@example.com"


# --- Windows disi ortam --------------------------------------------------


def test_the_default_source_refuses_outside_windows(monkeypatch):
    import app.mail as mail_package

    monkeypatch.setattr(mail_package, "is_supported", lambda: False)
    with pytest.raises(MailError) as error:
        default_source()
    assert error.value.code == "feature_unavailable"
    assert "Windows" in error.value.message


def test_endpoints_answer_feature_unavailable_outside_windows(context, monkeypatch):
    """Gercek fabrika bagli: Windows disinda uc 400 ve anlasilir mesaj doner."""
    import app.mail as mail_package
    from fastapi.testclient import TestClient

    from app.server import create_app

    monkeypatch.setattr(mail_package, "is_supported", lambda: False)
    context.mail_factory = lambda body_limit=None: default_source(body_limit)
    context.settings.apply({"mail.enabled": "1", "mail.addresses": ME})

    with TestClient(create_app(context)) as client:
        for path in ("/api/mail/test", "/api/mail/scan"):
            response = client.post(path)
            assert response.status_code == 400, path
            assert response.json()["error"]["code"] == "feature_unavailable", path
        response = client.get("/api/mail/folders")
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "feature_unavailable"


# --- Guncelle entegrasyonu ----------------------------------------------


def test_refresh_scans_the_mail_as_its_last_stage(context, conn, fake_mail):
    context.settings.apply({"mail.enabled": "1", "mail.addresses": ME})
    quiet_jira(context, conn)
    fake_mail.add(message("<1>", subject="Güncelle sırasında", sender=ME))

    status = context.refresh.run_blocking(context)

    assert status["state"] == "done"
    assert status["summary"]["mail"]["created"] == 1
    assert repo.list_tasks(conn)[0]["title"] == "Güncelle sırasında"


def test_refresh_does_not_scan_when_the_switch_is_off(context, conn, fake_mail):
    context.settings.apply(
        {"mail.enabled": "1", "mail.addresses": ME, "mail.scan_on_refresh": "0"}
    )
    fake_mail.add(message("<1>", sender=ME))

    status = context.refresh.run_blocking(context)

    assert status["summary"]["mail"] is None
    assert repo.list_tasks(conn) == []


def test_a_jira_failure_does_not_block_the_mail_scan(context, conn, fake_mail):
    """Sahte sunucuda hicbir yol tanimli degil: Jira patlar, posta yine taranir."""
    context.settings.apply(
        {
            "jira.base_url": "https://jira.example.com",
            "jira.secret": "ornek-pat",
            "mail.enabled": "1",
            "mail.addresses": ME,
        }
    )
    group = repo.create_group(conn, "Takip", repo.KIND_MANUAL)
    repo.add_items(conn, group["id"], ["DEMO-1"])
    fake_mail.add(message("<1>", subject="Yine de geldim", sender=ME))

    status = context.refresh.run_blocking(context)

    assert status["state"] == "error"
    assert status["summary"]["mail"]["created"] == 1
    assert repo.list_tasks(conn)[0]["title"] == "Yine de geldim"


def test_a_mail_failure_does_not_break_the_refresh(context, conn):
    context.settings.apply({"mail.enabled": "1", "mail.addresses": ME})
    quiet_jira(context, conn)

    def explode(body_limit=None):
        raise MailError("outlook_unavailable", "Outlook'a bağlanılamadı.")

    context.mail_factory = explode
    status = context.refresh.run_blocking(context)

    assert status["state"] == "done"
    assert status["summary"]["mail"]["errors"][0]["code"] == "outlook_unavailable"


# --- COM sarmalayicisi (sahte COM nesneleriyle) --------------------------


class FakeUser:
    def __init__(self, address: str) -> None:
        self.PrimarySmtpAddress = address


class FakeEntry:
    """`AddressEntry`: Exchange kullanicisi olabilir de olmayabilir de."""

    def __init__(self, address: str = "", exchange: str | None = None) -> None:
        self.Address = address
        self._exchange = exchange

    def GetExchangeUser(self):  # noqa: N802 - COM adi
        return FakeUser(self._exchange) if self._exchange else None


class FakeAccessor:
    def __init__(self, values: dict[str, str]) -> None:
        self._values = values

    def GetProperty(self, tag: str) -> str:  # noqa: N802 - COM adi
        if tag not in self._values:
            raise RuntimeError("property not found")
        return self._values[tag]


class FakeRecipient:
    def __init__(self, kind: int, entry: FakeEntry | None = None, properties=None,
                 address: str = "") -> None:
        self.Type = kind
        self.AddressEntry = entry
        self.Address = address
        self.PropertyAccessor = FakeAccessor(properties or {})


class FakeItem:
    """COM `MailItem`in duck typing karsiligi."""

    def __init__(self, **values) -> None:
        self.MessageClass = values.get("message_class", "IPM.Note")
        self.Subject = values.get("subject", "Konu")
        self.Body = values.get("body", "Gövde")
        self.EntryID = values.get("entry_id", "ENTRY")
        self.ConversationID = values.get("conversation_id", "")
        self.ReceivedTime = values.get("received_at", NOW)
        self.SenderEmailType = values.get("sender_type", "SMTP")
        self.SenderEmailAddress = values.get("sender", "biri@example.com")
        self.Sender = values.get("sender_entry")
        self.Recipients = values.get("recipients", [])
        self.PropertyAccessor = FakeAccessor(values.get("properties", {}))
        if "meeting_status" in values:
            self.MeetingStatus = values["meeting_status"]


def test_an_exchange_sender_is_resolved_to_smtp():
    item = FakeItem(
        sender_type="EX",
        sender="/O=KURUM/OU=EXCHANGE/CN=RECIPIENTS/CN=PATRON",
        sender_entry=FakeEntry(exchange="patron@example.com"),
    )
    assert outlook.sender_address(item) == "patron@example.com"


def test_an_exchange_sender_falls_back_to_the_proptag():
    item = FakeItem(
        sender_type="EX",
        sender="/O=KURUM/CN=PATRON",
        sender_entry=FakeEntry(),  # GetExchangeUser None doner
        properties={outlook.PR_SENDER_SMTP_ADDRESS: "patron@example.com"},
    )
    assert outlook.sender_address(item) == "patron@example.com"


def test_an_internet_sender_is_used_as_is():
    item = FakeItem(sender_type="SMTP", sender="Disari@Example.com")
    assert outlook.sender_address(item) == "disari@example.com"


def test_an_x500_address_never_leaks_into_the_match():
    item = FakeItem(sender_type="EX", sender="/O=KURUM/CN=PATRON", sender_entry=FakeEntry())
    assert outlook.sender_address(item) == ""


def test_recipients_split_into_to_and_cc():
    item = FakeItem(
        recipients=[
            FakeRecipient(outlook.RECIPIENT_TO, FakeEntry(exchange="bana@example.com")),
            FakeRecipient(
                outlook.RECIPIENT_CC,
                FakeEntry("/O=KURUM/CN=KOPYA"),
                properties={outlook.PR_SMTP_ADDRESS: "kopya@example.com"},
            ),
            FakeRecipient(outlook.RECIPIENT_BCC, FakeEntry(exchange="gizli@example.com")),
        ]
    )
    to_list, cc_list = outlook.recipient_addresses(item)
    assert to_list == ["bana@example.com"]
    assert cc_list == ["kopya@example.com"]


def test_a_single_broken_recipient_does_not_drop_the_mail():
    class Angry:
        Type = 1

        @property
        def AddressEntry(self):  # noqa: N802 - COM adi
            raise RuntimeError("COM patladi")

    item = FakeItem(
        recipients=[Angry(), FakeRecipient(outlook.RECIPIENT_TO, address="saglam@example.com")]
    )
    to_list, _ = outlook.recipient_addresses(item)
    assert to_list == ["saglam@example.com"]


def test_a_recipient_without_smtp_is_dropped_not_guessed():
    item = FakeItem(
        recipients=[FakeRecipient(outlook.RECIPIENT_TO, address="/O=KURUM/CN=BILINMEZ")]
    )
    assert outlook.recipient_addresses(item) == ([], [])


def test_the_item_becomes_a_message():
    item = FakeItem(
        subject="  Teklif  ",
        body="Satır\r\nİki",
        conversation_id="CONV",
        properties={outlook.PR_INTERNET_MESSAGE_ID: "<abc@example.com>"},
        recipients=[FakeRecipient(outlook.RECIPIENT_TO, FakeEntry(exchange=ME))],
    )
    built = outlook.message_from_item(item, folder_path="Gelen Kutusu", store_id="ST")

    assert isinstance(built, MailMessage)
    assert built.message_id == "<abc@example.com>"
    assert built.conversation_id == "CONV"
    assert built.subject == "Teklif"
    assert built.body_text == "Satır\nİki"
    assert built.to_smtp == [ME]
    assert built.folder_path == "Gelen Kutusu"
    assert built.store_id == "ST"


def test_the_message_id_falls_back_to_the_entry_id():
    built = outlook.message_from_item(FakeItem(entry_id="ENTRY-42"))
    assert built.message_id == "ENTRY-42"


def test_a_calendar_item_is_converted_to_nothing():
    assert outlook.message_from_item(FakeItem(message_class="IPM.Schedule.Meeting.Request")) is None
    assert outlook.message_from_item(FakeItem(meeting_status=1)) is None
    assert outlook.message_from_item(FakeItem(meeting_status=0)) is not None


def test_the_body_limit_is_applied_at_the_source():
    built = outlook.message_from_item(FakeItem(body="y" * 100), body_limit=10)
    assert built.body_text == "y" * 10 + "…"


def test_the_default_inbox_is_recognised_by_name():
    assert outlook.is_default_inbox("Gelen Kutusu")
    assert outlook.is_default_inbox("inbox")
    assert outlook.is_default_inbox("")
    assert not outlook.is_default_inbox("Arşiv")


def test_a_folder_path_is_split_on_backslashes():
    assert outlook.split_path("Inbox\\Alt\\Klasör") == ["Inbox", "Alt", "Klasör"]
    assert outlook.split_path("") == []


def test_outlook_source_refuses_to_start_outside_windows(monkeypatch):
    monkeypatch.setattr(outlook.sys, "platform", "linux")
    with pytest.raises(MailError) as error:
        outlook.OutlookSource()
    assert error.value.code == "feature_unavailable"


# --- govde temizligi ----------------------------------------------------


def test_the_body_normalises_line_endings_and_trailing_space():
    assert clean_body("Bir \r\nİki\r\n\r\n\r\nÜç  ") == "Bir\nİki\n\nÜç"


def test_an_empty_body_stays_empty():
    assert clean_body(None) == ""
