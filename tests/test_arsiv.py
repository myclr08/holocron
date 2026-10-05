"""Arsiv: belge kopyalama, ozetle tekillestirme, adlar, baglar, tarama, Sefer eki.

Belgeler klasoru `HOLOCRON_DOCUMENTS` ile testin gecici klasorune alinir
(conftest); hicbir test gercek Belgeler'e yazmaz ve gercek dosya acicisi
calistirmaz (sahte `arsiv_launcher`).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from urllib.parse import quote

import pytest

from app import arsiv, gamify, paths
from app import gamify_repo as store
from app import repository as repo
from app.repository import RepositoryError

MONDAY = datetime(2026, 9, 14, 10, 0)


def upload(client, name: str, data: bytes, **target):
    query = "&".join(f"{key}={quote(str(value))}" for key, value in target.items())
    return client.post(
        "/api/arsiv/upload" + (f"?{query}" if query else ""),
        content=data,
        headers={"X-File-Name": quote(name), "Content-Type": "application/octet-stream"},
    )


@pytest.fixture
def launched(api_client):
    calls: list[list[str]] = []
    api_client.app.state.arsiv_launcher = calls.append
    return calls


# --- yollar -----------------------------------------------------------------


def test_the_archive_lives_under_documents_holocron(isolated_home):
    folder = paths.archive_dir()
    assert folder == isolated_home / "Belgeler" / "holocron" / "holocron-belgeler"
    assert folder.is_dir()
    assert paths.backup_dir() == isolated_home / "Belgeler" / "holocron" / "holocron-yedek"


def test_documents_falls_back_to_home_documents(monkeypatch, tmp_path):
    monkeypatch.delenv("HOLOCRON_DOCUMENTS", raising=False)
    monkeypatch.setattr(paths, "_windows_documents", lambda: None)
    monkeypatch.setattr(paths, "_xdg_documents", lambda: None)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert paths.documents_dir() == tmp_path / "Documents"


def test_documents_prefers_the_known_folder(monkeypatch, tmp_path):
    """Yonlendirilmis (OneDrive) Belgeler: bilinen klasor API'sinin cevabi kazanir."""
    monkeypatch.delenv("HOLOCRON_DOCUMENTS", raising=False)
    redirected = tmp_path / "OneDrive - Kurum" / "Belgeler"
    monkeypatch.setattr(paths, "_windows_documents", lambda: redirected)
    assert paths.documents_dir() == redirected


def test_xdg_documents_is_read_from_user_dirs(monkeypatch, tmp_path):
    config = tmp_path / "cfg"
    config.mkdir()
    (config / "user-dirs.dirs").write_text('XDG_DOCUMENTS_DIR="$HOME/Belgelerim"\n', encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    assert paths._xdg_documents() == tmp_path / "Belgelerim"


# --- ad ve tur --------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("rapor.pdf", "rapor.pdf"),
        ("..\\..\\Windows\\system32\\x.dll", "x.dll"),
        ("../../etc/passwd", "passwd"),
        ('a<b>c:d"e|f?g*h.txt', "a_b_c_d_e_f_g_h.txt"),
        ("CON.txt", "_CON.txt"),
        ("nul", "_nul"),
        ("rapor.  ", "rapor"),
        ("", "belge"),
        ("...", "belge"),
        ("Gözden Geçirme Notları.docx", "Gözden Geçirme Notları.docx"),
    ],
)
def test_names_are_sanitized(raw, expected):
    assert arsiv.sanitize_name(raw) == expected


def test_long_names_keep_their_extension():
    name = arsiv.sanitize_name("x" * 400 + ".xlsx")
    assert name.endswith(".xlsx")
    assert len(name) <= arsiv.MAX_NAME


@pytest.mark.parametrize(
    "name, kind",
    [
        ("a.PDF", "pdf"), ("a.docx", "doc"), ("a.xlsx", "xls"), ("a.csv", "xls"),
        ("a.png", "img"), ("a.JPEG", "img"), ("a.zip", "other"), ("README", "other"),
    ],
)
def test_kind_follows_the_extension(name, kind):
    assert arsiv.kind_of(name) == kind


# --- yukleme ----------------------------------------------------------------


def test_upload_copies_the_file_into_the_archive(api_client):
    response = upload(api_client, "Teklif.pdf", b"%PDF-1.4 teklif")
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["duplicate"] is False
    document = data["document"]
    assert document["name"] == "Teklif.pdf"
    assert document["kind"] == "pdf"
    assert document["size"] == len(b"%PDF-1.4 teklif")
    stored = paths.archive_dir() / document["file_name"]
    assert stored.read_bytes() == b"%PDF-1.4 teklif"


def test_the_same_content_is_stored_once(api_client):
    first = upload(api_client, "a.txt", b"ayni icerik").json()["document"]
    second = upload(api_client, "baska-ad.txt", b"ayni icerik").json()
    assert second["duplicate"] is True
    assert second["document"]["id"] == first["id"]
    files = [p.name for p in paths.archive_dir().iterdir()]
    assert files == ["a.txt"]


def test_a_name_collision_gets_a_numbered_name(api_client):
    first = upload(api_client, "rapor.pdf", b"bir").json()["document"]
    second = upload(api_client, "rapor.pdf", b"iki").json()["document"]
    third = upload(api_client, "RAPOR.pdf", b"uc").json()["document"]
    assert first["file_name"] == "rapor.pdf"
    assert second["file_name"] == "rapor (2).pdf"
    assert third["file_name"] == "RAPOR (3).pdf"
    assert (paths.archive_dir() / "rapor.pdf").read_bytes() == b"bir"


def test_an_oversized_upload_is_refused_and_leaves_nothing(api_client, monkeypatch):
    monkeypatch.setattr(arsiv, "MAX_SIZE", 10)
    response = upload(api_client, "buyuk.bin", b"x" * 64)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "too_large"
    assert list(paths.archive_dir().iterdir()) == []
    assert api_client.get("/api/arsiv?scan=0").json()["documents"] == []


def test_an_empty_upload_is_refused(api_client):
    response = upload(api_client, "bos.txt", b"")
    assert response.status_code == 400
    assert list(paths.archive_dir().iterdir()) == []


def test_upload_to_a_missing_task_writes_no_file(api_client):
    response = upload(api_client, "x.txt", b"veri", task=999)
    assert response.status_code == 404
    assert list(paths.archive_dir().iterdir()) == []


def test_upload_with_a_path_in_the_name_stays_in_the_archive(api_client):
    document = upload(api_client, "../../kacak.txt", b"veri").json()["document"]
    assert document["file_name"] == "kacak.txt"
    assert (paths.archive_dir() / "kacak.txt").exists()
    assert not (paths.archive_dir().parent.parent / "kacak.txt").exists()


# --- baglar -----------------------------------------------------------------


def test_documents_link_to_tasks_and_records_many_to_many(api_client, conn, fake_jira):
    one = repo.create_task(conn, "Sözleşmeyi incele")
    two = repo.create_task(conn, "Teklifi gönder")
    doc = upload(api_client, "sozlesme.pdf", b"sozlesme", task=one["id"]).json()
    assert doc["linked"] is True
    doc_id = doc["document"]["id"]
    assert api_client.post(f"/api/arsiv/{doc_id}/links", json={"type": "task", "target": two["id"]}).status_code == 200
    assert api_client.post(f"/api/arsiv/{doc_id}/links", json={"type": "issue", "target": "demo-7"}).status_code == 200
    # ayni dosya ikinci kez baska goreve surulurse yine tek belge, yeni bag
    other = upload(api_client, "kopya.pdf", b"sozlesme", issue="DEMO-8").json()
    assert other["duplicate"] is True and other["document"]["id"] == doc_id

    listed = api_client.get("/api/arsiv").json()["documents"]
    assert len(listed) == 1
    links = {(item["type"], item["target"]) for item in listed[0]["links"]}
    assert links == {("task", str(one["id"])), ("task", str(two["id"])), ("issue", "DEMO-7"), ("issue", "DEMO-8")}
    labels = {item["target"]: item["label"] for item in listed[0]["links"]}
    assert labels[str(one["id"])] == "Sözleşmeyi incele"

    for_task = api_client.get(f"/api/arsiv/for/task/{one['id']}").json()["documents"]
    assert [item["id"] for item in for_task] == [doc_id]
    for_issue = api_client.get("/api/arsiv/for/issue/demo-7").json()["documents"]
    assert [item["id"] for item in for_issue] == [doc_id]
    # Bag yalnizca yereldir: Jira'ya tek istek gitmez.
    assert fake_jira.calls == []


def test_unlinking_keeps_the_file_and_other_links(api_client, conn):
    task = repo.create_task(conn, "Görev")
    doc_id = upload(api_client, "not.txt", b"not", task=task["id"]).json()["document"]["id"]
    api_client.post(f"/api/arsiv/{doc_id}/links", json={"type": "issue", "target": "DEMO-1"})
    response = api_client.delete(f"/api/arsiv/{doc_id}/links/task/{task['id']}")
    assert response.json()["removed"] is True
    assert (paths.archive_dir() / "not.txt").exists()
    listed = api_client.get("/api/arsiv").json()["documents"]
    assert [(i["type"], i["target"]) for i in listed[0]["links"]] == [("issue", "DEMO-1")]


def test_invalid_link_targets_are_refused(api_client):
    doc_id = upload(api_client, "x.txt", b"x").json()["document"]["id"]
    assert api_client.post(f"/api/arsiv/{doc_id}/links", json={"type": "task", "target": 404}).status_code == 404
    assert api_client.post(f"/api/arsiv/{doc_id}/links", json={"type": "issue", "target": "yok"}).status_code == 400
    assert api_client.post(f"/api/arsiv/{doc_id}/links", json={"type": "grup", "target": "1"}).status_code == 400


def test_deleting_a_task_drops_its_links_but_not_the_document(api_client, conn):
    task = repo.create_task(conn, "Silinecek")
    doc_id = upload(api_client, "kalir.txt", b"kalir", task=task["id"]).json()["document"]["id"]
    assert api_client.delete(f"/api/tasks/{task['id']}").status_code == 200
    listed = api_client.get("/api/arsiv").json()["documents"]
    assert [item["id"] for item in listed] == [doc_id]
    assert listed[0]["links"] == []
    assert (paths.archive_dir() / "kalir.txt").exists()


def test_delete_removes_the_file_and_all_links(api_client, conn):
    task = repo.create_task(conn, "Görev")
    doc_id = upload(api_client, "gider.txt", b"gider", task=task["id"]).json()["document"]["id"]
    assert api_client.delete(f"/api/arsiv/{doc_id}").status_code == 200
    assert not (paths.archive_dir() / "gider.txt").exists()
    assert conn.execute("SELECT COUNT(*) AS c FROM document_links").fetchone()["c"] == 0
    assert api_client.get("/api/arsiv").json()["documents"] == []
    assert api_client.delete(f"/api/arsiv/{doc_id}").status_code == 404


# --- tarama ve suzme --------------------------------------------------------


def test_a_file_dropped_into_the_folder_shows_up_unlinked(api_client):
    folder = paths.archive_dir()
    (folder / "elle.xlsx").write_bytes(b"tablo")
    (folder / "~$elle.xlsx").write_bytes(b"kilit")  # Office kilit dosyasi
    (folder / ".gizli").write_bytes(b"gizli")
    data = api_client.get("/api/arsiv").json()
    assert [item["name"] for item in data["documents"]] == ["elle.xlsx"]
    assert data["documents"][0]["kind"] == "xls"
    assert data["documents"][0]["links"] == []
    assert data["counts"] == {"total": 1, "linked": 0, "unlinked": 1}
    # ikinci tarama yeni satir yazmaz
    assert api_client.post("/api/arsiv/rescan").json()["added"] == 0
    # ayni icerik yuklenirse elle konan belge kullanilir
    again = upload(api_client, "baska.xlsx", b"tablo").json()
    assert again["duplicate"] is True


def test_a_document_whose_file_vanished_is_marked_missing(api_client, launched):
    doc_id = upload(api_client, "kayip.txt", b"kayip").json()["document"]["id"]
    (paths.archive_dir() / "kayip.txt").unlink()
    listed = api_client.get("/api/arsiv").json()["documents"]
    assert listed[0]["missing"] is True
    assert api_client.post(f"/api/arsiv/{doc_id}/open").status_code == 404
    assert launched == []


def test_filters_by_kind_state_and_search(api_client, conn):
    task = repo.create_task(conn, "Bütçe planı")
    upload(api_client, "butce.xlsx", b"1", task=task["id"])
    upload(api_client, "logo.png", b"2")
    upload(api_client, "Şartname.pdf", b"3", issue="DEMO-3")

    def names(**params):
        query = "&".join(f"{k}={quote(v)}" for k, v in params.items())
        return sorted(item["name"] for item in api_client.get(f"/api/arsiv?{query}").json()["documents"])

    assert names(kind="img") == ["logo.png"]
    assert names(state="unlinked") == ["logo.png"]
    assert names(state="linked") == ["butce.xlsx", "Şartname.pdf"]
    assert names(q="ŞARTNAME") == ["Şartname.pdf"]
    assert names(q="bütçe planı") == ["butce.xlsx"]  # bag etiketinde de aranir
    assert names(q="demo-3") == ["Şartname.pdf"]
    assert names(q="yok-boyle-bir-sey") == []


def test_open_and_reveal_go_to_the_launcher(api_client, launched):
    doc_id = upload(api_client, "ac.pdf", b"ac").json()["document"]["id"]
    assert api_client.post(f"/api/arsiv/{doc_id}/open").status_code == 200
    assert api_client.post(f"/api/arsiv/{doc_id}/reveal").status_code == 200
    assert api_client.post("/api/arsiv/folder").status_code == 200
    target = str(paths.archive_dir() / "ac.pdf")
    assert launched == [["open", target], ["reveal", target], ["folder", str(paths.archive_dir())]]


# --- Sefer --------------------------------------------------------------------


def _campaign(context):
    return gamify.start_campaign(context, "Sefer", "2026-12-31", 1000, now=MONDAY)


def _close(context, task, now=MONDAY):
    conn = context.connection()
    before = repo.get_task(conn, task["id"])
    after = repo.move_task(conn, task["id"], status=repo.TASK_DONE)
    return gamify.on_task_done(context, before, after, now=now)


def _reopen(context, task):
    repo.move_task(context.connection(), task["id"], status=repo.TASK_TODO)


def _kinds(context):
    active = store.active_campaign(context.connection())
    return [event["kind"] for event in store.list_events(context.connection(), active["id"])]


def test_closing_a_task_with_a_document_earns_a_small_bonus(context, conn):
    _campaign(context)
    task = repo.create_task(conn, "Belgeli")
    document, _ = arsiv.store_chunks(conn, [b"veri"], "veri.txt")
    arsiv.link(conn, document["id"], "task", task["id"])
    events = _close(context, task)
    kinds = [event["kind"] for event in events]
    assert gamify.KIND_DONE_WITH_DOCS in kinds
    bonus = next(e for e in events if e["kind"] == gamify.KIND_DONE_WITH_DOCS)
    assert bonus["points"] == 3


def test_a_task_without_documents_gets_no_bonus(context, conn):
    _campaign(context)
    task = repo.create_task(conn, "Belgesiz")
    _close(context, task)
    assert gamify.KIND_DONE_WITH_DOCS not in _kinds(context)


def test_linking_and_unlinking_cannot_farm_the_bonus(context, conn):
    _campaign(context)
    task = repo.create_task(conn, "Kasma")
    document, _ = arsiv.store_chunks(conn, [b"veri"], "veri.txt")
    for _ in range(3):
        arsiv.link(conn, document["id"], "task", task["id"])
        _close(context, task)
        arsiv.unlink(conn, document["id"], "task", task["id"])
        _reopen(context, task)
    assert _kinds(context).count(gamify.KIND_DONE_WITH_DOCS) == 1


def test_a_document_linked_after_closing_scores_nothing(context, conn):
    _campaign(context)
    task = repo.create_task(conn, "Sonradan")
    _close(context, task)
    document, _ = arsiv.store_chunks(conn, [b"veri"], "veri.txt")
    arsiv.link(conn, document["id"], "task", task["id"])
    _reopen(context, task)
    _close(context, task)
    assert gamify.KIND_DONE_WITH_DOCS not in _kinds(context)


def test_the_scribe_badge_needs_ten_closed_tasks_with_documents(context, conn):
    campaign = _campaign(context)
    document, _ = arsiv.store_chunks(conn, [b"ortak"], "ortak.pdf")
    for number in range(10):
        task = repo.create_task(conn, f"Görev {number}")
        arsiv.link(conn, document["id"], "task", task["id"])
        _close(context, task)
        earned = store.earned_badges(conn, campaign["id"])
        assert (gamify.BADGE_SCRIBE in earned) == (number == 9)


def test_the_scribe_badge_is_in_the_catalog_with_an_icon():
    badge = gamify.BADGES_BY_CODE[gamify.BADGE_SCRIBE]
    assert badge["label"] == "Kâtip"
    assert badge["category"] == gamify.CATEGORY_TASK
    icon = Path(paths.static_dir()) / "rozetler" / f"{gamify.BADGE_SCRIBE}.svg"
    assert icon.exists()
    assert "Kâtip" in icon.read_text(encoding="utf-8")


def test_store_chunks_refuses_oversize_without_leftovers(conn, tmp_path):
    folder = tmp_path / "raf"
    with pytest.raises(RepositoryError):
        arsiv.store_chunks(conn, [b"x" * 8, b"y" * 8], "a.bin", folder, max_size=10)
    assert list(folder.iterdir()) == []


# --- arayuz -------------------------------------------------------------------


def test_the_sidebar_has_the_archive_and_the_page_loads_its_script(api_client):
    page = api_client.get("/").text
    assert 'id="arsiv-entry"' in page and 'id="arsiv-view"' in page
    assert "/static/js/arsiv.js" in page
    # Bos arama: Jocasta Nu.
    assert "Arşivlerde yoksa, yok demektir." in page
    script = api_client.get("/static/js/arsiv.js").text
    assert "Kamino" in script and "Arşivden sil" in script
    assert "veriKartlariBolumu" in api_client.get("/static/js/app.js").text


def test_the_settings_page_has_the_update_card(api_client):
    page = api_client.get("/settings").text
    assert 'id="update-card"' in page and "Güncellemeleri denetle" in page
    assert "/static/js/guncelleme-settings.js" in page
    assert api_client.get("/static/js/guncelleme-settings.js").status_code == 200
