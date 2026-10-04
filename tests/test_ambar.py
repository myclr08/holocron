"""Ambar: liste, ambardakiler, ambara al / ambardan cikar, cakisma, Sefer.

Uzak depo yerel bir ciplak (bare) git deposudur, GitHub API'si 127.0.0.1'de
kucuk bir sahte sunucudur (`tests/fake_github.py`). Hicbir test aga cikmaz.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from datetime import date, timedelta

import pytest

from app import ambar, ambar_git, gamify
from app import gamify_repo as gstore
from tests.fake_github import FakeGitHub, GitWorld, git

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git gerekli")


@pytest.fixture(autouse=True)
def git_identity(tmp_path, monkeypatch):
    """Testler kullanicinin git ayarlarindan bagimsiz calisir."""
    home = tmp_path / "githome"
    home.mkdir()
    config = home / ".gitconfig"
    config.write_text(
        "[user]\n\tname = Test Kisi\n\temail = test@example.com\n"
        "[init]\n\tdefaultBranch = master\n[advice]\n\tdetachedHead = false\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for name in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "all_proxy", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def github():
    server = FakeGitHub()
    yield server
    server.close()


@pytest.fixture
def world(tmp_path, github):
    return GitWorld(tmp_path / "w1", "ornek-org/api", github)


def configure(context, github, *worlds):
    settings = context.settings
    settings.set("net.proxy_mode", "direct")
    view = ambar.save_config(
        settings,
        {
            "api_url": github.api_url,
            "token": github.token,
            "repos": [{"name": item.name, "path": str(item.work)} for item in worlds],
        },
    )
    return {repo["name"]: repo["id"] for repo in view["repos"]}


def start_campaign(context):
    ends = (date.today() + timedelta(days=30)).isoformat()
    gamify.ensure_rules(context)
    gamify.start_campaign(context, "Test Seferi", ends, 1000)


def hold(api_client, repo_id, *numbers, notes=None):
    notes = notes or {}
    items = [{"repo_id": repo_id, "number": n, "note": notes.get(n, "")} for n in numbers]
    answer = api_client.post("/api/ambar/al", json={"items": items})
    assert answer.status_code == 200, answer.text
    return answer.json()


def release(api_client, repo_id, *shas):
    items = [{"repo_id": repo_id, "sha": sha} for sha in shas]
    answer = api_client.post("/api/ambar/cikar", json={"items": items})
    assert answer.status_code == 200, answer.text
    return answer.json()


def overview(api_client, repo_id="", days=30):
    answer = api_client.get("/api/ambar", params={"days": days, "repo": repo_id})
    assert answer.status_code == 200, answer.text
    return answer.json()


def merge_last_ambar_pr(world, github):
    created = github.created(world.name)[-1]
    number = None
    for pr in github.repos[world.name]["pulls"]:
        if pr["head"]["ref"] == created["body"]["head"]:
            number = pr["number"]
    return world.merge_branch(created["body"]["head"], number)


# --- ayarlar -----------------------------------------------------------------


def test_settings_store_tokens_encrypted_and_never_returned(api_client, context, world, github):
    answer = api_client.put(
        "/api/ambar/settings",
        json={
            "token": "ghp_genel_gizli",
            "repos": [{"name": "ornek-org/api", "path": str(world.work), "token": "ghp_depo_gizli"}],
        },
    )
    assert answer.status_code == 200, answer.text
    data = answer.json()["ambar"]
    assert data["token_set"] is True
    assert data["repos"][0]["token_set"] is True
    assert "ghp_" not in answer.text
    raw = context.connection().execute(
        "SELECT key, value FROM settings WHERE key LIKE 'ambar.token%'"
    ).fetchall()
    assert len(raw) == 2
    assert all("ghp_" not in row["value"] for row in raw), "token sifreli saklanmali"
    assert context.settings.get(ambar.token_key(data["repos"][0]["id"])) == "ghp_depo_gizli"
    # Bos token alani mevcut tokeni bozmaz; repo silinince tokeni de gider.
    api_client.put("/api/ambar/settings", json={"token": ""})
    assert context.settings.get(ambar.SETTING_TOKEN) == "ghp_genel_gizli"
    api_client.put("/api/ambar/settings", json={"repos": []})
    assert context.settings.get(ambar.token_key(data["repos"][0]["id"])) in (None, "")


@pytest.mark.parametrize(
    "name", ["org", "org/repo/extra", "../etc", "org/..", "org/re po", "-org/repo", "org/repo;rm"]
)
def test_invalid_repo_names_are_rejected(api_client, world, name):
    answer = api_client.put(
        "/api/ambar/settings", json={"repos": [{"name": name, "path": str(world.work)}]}
    )
    assert answer.status_code == 400
    assert answer.json()["error"]["code"] == "invalid_repo_name"


def test_relative_paths_and_bad_urls_are_rejected(api_client):
    answer = api_client.put(
        "/api/ambar/settings", json={"repos": [{"name": "org/repo", "path": "kod/api"}]}
    )
    assert answer.json()["error"]["code"] == "invalid_repo_path"
    answer = api_client.put("/api/ambar/settings", json={"api_url": "ftp://x"})
    assert answer.json()["error"]["code"] == "invalid_api_url"


def test_default_api_url_and_enterprise_web_url(context):
    assert ambar.config_view(context.settings)["api_url"] == "https://api.github.com"
    assert ambar.web_url(context.settings, "o/r") == "https://github.com/o/r"
    ambar.save_config(context.settings, {"api_url": "https://ghe.example.com/api/v3/"})
    assert ambar.config_view(context.settings)["api_url"] == "https://ghe.example.com/api/v3"
    assert ambar.web_url(context.settings, "o/r") == "https://ghe.example.com/o/r"


def test_repo_check_reports_each_step(api_client, context, world, github):
    ids = configure(context, github, world)
    data = api_client.post(f"/api/ambar/test/{ids[world.name]}").json()
    assert data["ok"] is True
    labels = [step["label"] for step in data["steps"]]
    assert labels == ["git", "Klon", "origin", "Ana dal", "GitHub"]


# --- liste ve ambardakiler ---------------------------------------------------------


def test_listing_shows_recent_merged_prs_with_files(api_client, context, world, github):
    first = world.merge_pr(142, {"a.txt": "a\n"}, title="Ozellik A", author="ayse")
    world.merge_pr(151, {"b/b.txt": "b\n", "c.txt": "c\n"}, title="Ozellik B", squash=True,
                   author="mehmet")
    # Ana dala degil baska dala birlesmis ya da cok eski PR listeye girmez.
    github.add_pull(world.name, number=90, sha=first, at="2020-01-01T00:00:00Z")
    ids = configure(context, github, world)

    data = overview(api_client)
    assert data["count"] == 0
    repo = data["repos"][0]
    assert repo["error"] is None
    assert repo["base"] == "master"
    numbers = [pr["number"] for pr in repo["prs"]]
    assert numbers == [151, 142]
    by_number = {pr["number"]: pr for pr in repo["prs"]}
    assert by_number[142]["files"] == ["a.txt"]
    assert sorted(by_number[151]["files"]) == ["b/b.txt", "c.txt"]
    assert by_number[151]["author"] == "mehmet"
    assert by_number[142]["held"] is False
    # Tek depo sekmesi yalnizca o depoyu dondurur.
    assert [item["id"] for item in overview(api_client, ids[world.name])["repos"]] == [
        ids[world.name]
    ]


def test_listing_survives_a_missing_clone(api_client, context, world, github, tmp_path):
    configure(context, github, world)
    ambar.save_config(
        context.settings,
        {"repos": [
            {"name": world.name, "path": str(world.work)},
            {"name": "ornek-org/yok", "path": str(tmp_path / "yok")},
        ]},
    )
    data = overview(api_client)
    assert data["repos"][0]["error"] is None
    assert data["repos"][1]["error"]["code"] == "path_missing"


def test_held_detection_follows_revert_chain(world, github):
    sha = world.merge_pr(142, {"a.txt": "a\n"})
    git(world.dev, "revert", "--no-edit", "-m", "1", sha)
    git(world.dev, "push", "--quiet", "origin", "master")
    tool = ambar_git.Git(shutil.which("git"), str(world.dev))
    held = ambar_git.held_commits(tool, "refs/heads/master")
    assert [item.sha for item in held] == [sha]
    assert ambar_git.pr_number_of(held[0].commit) == 142
    # Geri almanin geri alinmasi: artik ambarda degil.
    revert_sha = held[0].revert.sha
    git(world.dev, "revert", "--no-edit", revert_sha)
    assert ambar_git.held_commits(tool, "refs/heads/master") == []
    # Yeniden geri alininca yine ambarda.
    git(world.dev, "revert", "--no-edit", "-m", "1", sha)
    again = ambar_git.held_commits(tool, "refs/heads/master")
    assert [item.sha for item in again] == [sha]
    assert again[0].revert.sha != revert_sha


# --- ambara al / ambardan cikar -------------------------------------------------------


def test_hold_and_release_merge_commits(api_client, context, world, github):
    a = world.merge_pr(142, {"a.txt": "a\n"}, title="Ozellik A")
    b = world.merge_pr(151, {"b.txt": "b\n"}, title="Ozellik B")
    ids = configure(context, github, world)
    world.make_dirty()
    before = world.work_state()

    result = hold(api_client, ids[world.name], 142, 151, notes={151: "UAT'ta"})
    assert result["ok"] is True, result
    item = result["results"][0]
    assert item["pr_url"].endswith(f"/pull/{item['pr_number']}")
    assert item["branch"].startswith("ambar/al-")
    created = github.created(world.name)[-1]["body"]
    assert created["title"] == "Ambara alındı: #142, #151"
    assert created["base"] == "master"
    assert "Not: UAT'ta" in created["body"] and "Holocron" in created["body"]
    # Ana dal degismedi; dal uzakta duruyor; klon aynen yerinde.
    assert world.remote_sha("master") == b
    assert item["branch"] in world.remote_branches()
    assert world.work_state() == before
    # Geri alma sirasi yeniden eskiye: once #151, sonra #142 (log en sonu basta verir).
    log = git(world.remote, "log", "--format=%s", "-2", item["branch"]).splitlines()
    assert "#142" in log[0] and "#151" in log[1]

    merge_last_ambar_pr(world, github)
    data = overview(api_client)
    assert data["count"] == 2
    held = data["repos"][0]["held"]
    assert {entry["number"] for entry in held} == {142, 151}
    assert all(pr["held"] for pr in data["repos"][0]["prs"])
    with pytest.raises(AssertionError):
        world.file("a.txt")

    back = release(api_client, ids[world.name], a, b)
    assert back["ok"] is True, back
    assert world.work_state() == before
    assert github.created(world.name)[-1]["body"]["title"] == "Ambardan çıkarıldı: #142, #151"
    merge_last_ambar_pr(world, github)
    assert overview(api_client)["count"] == 0
    assert world.file("a.txt") == "a" and world.file("b.txt") == "b"


def test_hold_and_release_squash_commits(api_client, context, world, github):
    sha = world.merge_pr(160, {"s.txt": "s\n"}, title="Squash", squash=True)
    ids = configure(context, github, world)
    first = hold(api_client, ids[world.name], 160)
    assert first["ok"] is True, first
    merge_last_ambar_pr(world, github)
    held = overview(api_client)["repos"][0]["held"]
    assert [(entry["sha"], entry["number"]) for entry in held] == [(sha, 160)]
    assert release(api_client, ids[world.name], sha)["ok"] is True
    merge_last_ambar_pr(world, github)
    assert overview(api_client)["count"] == 0
    assert world.file("s.txt") == "s"


def test_reverts_go_newest_to_oldest(api_client, context, world, github):
    """#2 ayni satiri #1'in ustune yazar: once #1'i geri almak cakisirdi."""
    world.merge_pr(1, {"app.txt": "satir 1\nBIR\nsatir 3\n"})
    world.merge_pr(2, {"app.txt": "satir 1\nIKI\nsatir 3\n"})
    ids = configure(context, github, world)
    result = hold(api_client, ids[world.name], 1, 2)
    assert result["ok"] is True, result
    merge_last_ambar_pr(world, github)
    assert world.file("app.txt") == "satir 1\nsatir 2\nsatir 3"


def test_conflict_aborts_and_leaves_the_clone_untouched(api_client, context, world, github):
    world.merge_pr(1, {"app.txt": "satir 1\nBIR\nsatir 3\n"}, title="Birinci")
    world.merge_pr(2, {"app.txt": "satir 1\nIKI\nsatir 3\n"}, title="Ikinci")
    ids = configure(context, github, world)
    world.make_dirty()
    before = world.work_state()
    branches_before = world.remote_branches()

    result = hold(api_client, ids[world.name], 1)
    assert result["ok"] is False
    error = result["results"][0]["error"]
    assert error["code"] == "conflict"
    assert error["title"] == "Rota hesaplama hatası"
    assert error["files"] == ["app.txt"]
    assert error["pr"]["number"] == 1
    assert world.work_state() == before
    assert (world.work / "notlarim.txt").read_text(encoding="utf-8") == "izlenmeyen\n"
    assert world.remote_branches() == branches_before
    assert github.created() == []


def test_dirty_working_tree_is_allowed_and_untouched(api_client, context, world, github, tmp_path):
    """Gelistirici klonda calisirken: is gecici worktree'de yapilir, klona dokunulmaz."""
    a = world.merge_pr(1, {"a.txt": "a\n"})
    ids = configure(context, github, world)
    world.make_dirty()
    before = world.work_state()
    assert before["branch"] == "gelistirici-dali" and before["status"]

    result = hold(api_client, ids[world.name], 1)
    assert result["ok"] is True, result
    assert world.work_state() == before
    merge_last_ambar_pr(world, github)
    assert release(api_client, ids[world.name], a)["ok"] is True
    assert world.work_state() == before
    # Gecici agaclar Holocron'un veri klasorunde acilir ve hepsi silinir.
    root = ambar_git.worktree_root()
    assert root.is_relative_to(tmp_path) and not root.is_relative_to(world.work)
    assert list(root.iterdir()) == []
    assert "ambar/" not in git(world.work, "for-each-ref", "refs/heads")
    assert len([b for b in world.remote_branches() if b.startswith("ambar/")]) == 2


def test_locked_worktree_removal_is_retried_then_reported(world, monkeypatch, tmp_path):
    tool = ambar_git.Git(shutil.which("git"), str(world.work))
    path = tmp_path / "kilitli"
    path.mkdir()
    (path / "dosya").write_text("x", encoding="utf-8")
    monkeypatch.setattr(ambar_git, "REMOVE_WAIT", 0)
    monkeypatch.setattr(ambar_git.shutil, "rmtree", lambda *args, **kwargs: None)
    calls = []
    original = ambar_git.Git.run

    def spy(self, *args, **kwargs):
        calls.append(args[:2])
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ambar_git.Git, "run", spy)
    note = ambar_git._remove_worktree(tool, path)
    assert calls.count(("worktree", "remove")) == ambar_git.REMOVE_ATTEMPTS
    assert "silinemedi" in note and str(path) in note


def test_stale_worktrees_from_a_crash_are_cleaned(api_client, context, world, github):
    world.merge_pr(1, {"a.txt": "a\n"})
    ids = configure(context, github, world)
    root = ambar_git.worktree_root()
    root.mkdir(parents=True, exist_ok=True)
    git(world.work, "worktree", "add", "--quiet", "--detach", str(root / "eski"), "origin/master")
    assert hold(api_client, ids[world.name], 1)["ok"] is True
    assert world.work_state()["worktrees"] == 1
    assert list(root.iterdir()) == []


def test_release_conflict_is_reported_the_same_way(api_client, context, world, github):
    a = world.merge_pr(1, {"app.txt": "satir 1\nBIR\nsatir 3\n"})
    ids = configure(context, github, world)
    hold(api_client, ids[world.name], 1)
    merge_last_ambar_pr(world, github)
    world.merge_pr(3, {"app.txt": "satir 1\nUC\nsatir 3\n"})
    world.make_dirty()
    before = world.work_state()
    result = release(api_client, ids[world.name], a)
    error = result["results"][0]["error"]
    assert error["code"] == "conflict" and error["title"] == "Rota hesaplama hatası"
    assert error["files"] == ["app.txt"] and error["pr"]["number"] == 1
    assert world.work_state() == before


def test_many_repos_one_conflict_others_continue(api_client, context, tmp_path, github):
    one = GitWorld(tmp_path / "one", "ornek-org/bir", github)
    two = GitWorld(tmp_path / "two", "ornek-org/iki", github)
    one.merge_pr(1, {"app.txt": "satir 1\nBIR\nsatir 3\n"})
    one.merge_pr(2, {"app.txt": "satir 1\nIKI\nsatir 3\n"})
    two.merge_pr(7, {"x.txt": "x\n"})
    ids = configure(context, github, one, two)
    answer = api_client.post(
        "/api/ambar/al",
        json={"items": [
            {"repo_id": ids[one.name], "number": 1},
            {"repo_id": ids[two.name], "number": 7},
        ]},
    ).json()
    assert answer["ok"] is False
    first, second = answer["results"]
    assert first["name"] == one.name and first["error"]["code"] == "conflict"
    assert second["name"] == two.name and second["ok"] is True and second["pr_url"]
    assert github.created(one.name) == [] and len(github.created(two.name)) == 1


def test_enterprise_base_url_is_used(api_client, context, tmp_path):
    server = FakeGitHub(prefix="/api/v3")
    try:
        world = GitWorld(tmp_path / "ghe", "kurum/servis", server)
        world.merge_pr(5, {"a.txt": "a\n"})
        ids = configure(context, server, world)
        assert ambar.config_view(context.settings)["api_url"].endswith("/api/v3")
        assert overview(api_client)["repos"][0]["prs"][0]["number"] == 5
        assert hold(api_client, ids[world.name], 5)["ok"] is True
        assert server.requests and all(req["path"].startswith("/api/v3/") for req in server.requests)
    finally:
        server.close()


def test_token_never_reaches_logs_or_answers(api_client, context, world, github, caplog):
    world.merge_pr(1, {"a.txt": "a\n"})
    ids = configure(context, github, world)
    caplog.set_level(logging.DEBUG)
    texts = [overview(api_client), hold(api_client, ids[world.name], 1)]
    # Yanlis token: 401 hatasi da tokeni tasimaz.
    context.settings.set(ambar.SETTING_TOKEN, "ghp_yanlis_gizli")
    bad = overview(api_client)
    assert bad["repos"][0]["error"]["code"] == "unauthorized"
    texts.append(bad)
    assert all(req["auth"].startswith("token ") for req in github.requests)
    for token in (github.token, "ghp_yanlis_gizli"):
        assert token not in caplog.text
        assert all(token not in str(item) for item in texts)
    client = ambar.github_for(context.settings, {"id": "r00000000"})
    assert "ghp_" not in repr(client)


def test_git_errors_are_redacted():
    assert ambar.redact("fatal: https://kisi:ghp_x@github.com/o/r.git") == (
        "fatal: https://***@github.com/o/r.git"
    )
    assert ambar.redact("token ghp_abc kullanildi", ["ghp_abc"]) == "token *** kullanildi"


def test_never_writes_to_master_or_forces(api_client, context, world, github, monkeypatch):
    world.merge_pr(1, {"a.txt": "a\n"})
    ids = configure(context, github, world)
    calls: list[list[str]] = []
    original = ambar_git.Git.run

    def spy(self, *args, **kwargs):
        calls.append(list(args))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(ambar_git.Git, "run", spy)
    master = world.remote_sha("master")
    assert hold(api_client, ids[world.name], 1)["ok"] is True
    pushes = [call for call in calls if call and call[0] == "push"]
    assert len(pushes) == 1
    assert not any(flag in pushes[0] for flag in ("--force", "-f", "--force-with-lease"))
    assert pushes[0][-1].startswith("refs/heads/ambar/al-")
    assert pushes[0][-1].split(":")[1].startswith("refs/heads/ambar/")
    assert not any(call and call[0] == "merge" for call in calls)
    assert world.remote_sha("master") == master


def test_already_held_pr_is_refused(api_client, context, world, github):
    world.merge_pr(1, {"a.txt": "a\n"})
    ids = configure(context, github, world)
    hold(api_client, ids[world.name], 1)
    merge_last_ambar_pr(world, github)
    again = hold(api_client, ids[world.name], 1)
    assert again["results"][0]["error"]["code"] == "already_held"


def test_pr_creation_failure_reports_the_pushed_branch(api_client, context, world, github):
    world.merge_pr(1, {"a.txt": "a\n"})
    ids = configure(context, github, world)
    github.fail_create[world.name] = 422
    result = hold(api_client, ids[world.name], 1)
    item = result["results"][0]
    assert item["ok"] is False
    assert item["branch"].startswith("ambar/al-")
    assert "Dal itildi" in item["error"]["message"]


# --- kenar cubugu sayisi -----------------------------------------------------------------


def test_sidebar_count_sums_held_prs_across_repos(api_client, context, tmp_path, github):
    one = GitWorld(tmp_path / "one", "ornek-org/bir", github)
    two = GitWorld(tmp_path / "two", "ornek-org/iki", github)
    one.merge_pr(1, {"a.txt": "a\n"})
    one.merge_pr(2, {"b.txt": "b\n"})
    two.merge_pr(3, {"c.txt": "c\n"}, squash=True)
    ids = configure(context, github, one, two)
    assert api_client.get("/api/ambar/count").json()["count"] == 0
    hold(api_client, ids[one.name], 1, 2)
    hold(api_client, ids[two.name], 3)
    merge_last_ambar_pr(one, github)
    merge_last_ambar_pr(two, github)
    overview(api_client)  # fetch: klonlar origin'in son halini gorur
    data = api_client.get("/api/ambar/count").json()
    assert data["count"] == 3
    assert data["repos"] == {ids[one.name]: 2, ids[two.name]: 1}


def test_sidebar_entry_matches_sefer_and_tasks():
    from pathlib import Path

    html = (Path(__file__).resolve().parent.parent / "app" / "static" / "index.html").read_text(
        encoding="utf-8"
    )
    start = html.index('id="ambar-entry"')
    entry = html[html.rfind("<div", 0, start): html.index("</div>", start)]
    # Sefer ve Gorevlerim ile ayni satir: ayni sinif, serit ve sayac hapi; simge yok.
    assert 'class="tasks-entry"' in html[html.rfind("<div", 0, start): start]
    assert ">Ambar<" in entry
    assert "<svg" not in entry and 'class="color-strip color-yellow"' in entry
    assert 'class="count" id="ambar-count"' in entry
    settings = (Path(__file__).resolve().parent.parent / "app" / "static" / "settings.html").read_text(
        encoding="utf-8"
    )
    assert 'data-group="ambar"' in settings and 'id="grup-ambar"' in settings


# --- Sefer -------------------------------------------------------------------------


def _events(context, kind):
    campaign = gstore.active_campaign(context.connection())
    return gstore.events_of_kind(context.connection(), campaign["id"], kind)


def _earned(context):
    campaign = gstore.active_campaign(context.connection())
    return set(gstore.earned_badges(context.connection(), campaign["id"]))


def test_rules_are_seeded_editable_and_can_be_disabled(api_client, context):
    gamify.ensure_rules(context)
    rules = {rule["kind"]: rule for rule in api_client.get("/api/campaign/rules").json()["rules"]}
    assert rules["ambar_al"]["points"] == 20 and rules["ambar_cikar"]["points"] == 30
    assert rules["ambar_al"]["source_label"] == "Ambar"
    api_client.put("/api/campaign/rules", json={"rules": [{"kind": "ambar_al", "enabled": False}]})
    rules = {rule["kind"]: rule for rule in api_client.get("/api/campaign/rules").json()["rules"]}
    assert rules["ambar_al"]["enabled"] is False


def test_xp_and_first_cargo_badge_without_double_xp(api_client, context, world, github):
    a = world.merge_pr(1, {"a.txt": "a\n"})
    ids = configure(context, github, world)
    start_campaign(context)
    result = hold(api_client, ids[world.name], 1)
    assert result["reward"]["points"] == 20
    assert {badge["code"] for badge in result["reward"]["badges"]} >= {gamify.BADGE_AMBAR_FIRST}
    events = _events(context, gamify.KIND_AMBAR_HOLD)
    assert [(event["points"], event["ref"]) for event in events] == [(20, f"{world.name}:#1")]
    merge_last_ambar_pr(world, github)

    out = release(api_client, ids[world.name], a)
    assert out["reward"]["points"] == 30
    assert gamify.BADGE_AMBAR_CLEAN in _earned(context)
    merge_last_ambar_pr(world, github)

    # Ayni depo + ayni PR kumesi yeniden ambara alinir: ikinci puan yok.
    again = hold(api_client, ids[world.name], 1)
    assert again["ok"] is True
    assert again["reward"]["points"] == 0
    assert len(_events(context, gamify.KIND_AMBAR_HOLD)) == 1


def test_no_xp_when_the_operation_fails(api_client, context, world, github):
    world.merge_pr(1, {"app.txt": "satir 1\nBIR\nsatir 3\n"})
    world.merge_pr(2, {"app.txt": "satir 1\nIKI\nsatir 3\n"})
    ids = configure(context, github, world)
    start_campaign(context)
    result = hold(api_client, ids[world.name], 1)
    assert result["ok"] is False
    assert result["reward"]["points"] == 0
    assert _events(context, gamify.KIND_AMBAR_HOLD) == []
    assert gamify.BADGE_AMBAR_FIRST not in _earned(context)

    # Cakismadan sonra ayni depoda islemi tamamlamak: Rota Ustasi.
    fixed = hold(api_client, ids[world.name], 1, 2)
    assert fixed["ok"] is True
    assert gamify.BADGE_AMBAR_REROUTE in _earned(context)


def test_heavy_load_badge_needs_five_prs_in_one_go(api_client, context, world, github):
    for number in range(1, 6):
        world.merge_pr(number, {f"f{number}.txt": f"{number}\n"})
    ids = configure(context, github, world)
    start_campaign(context)
    hold(api_client, ids[world.name], 1, 2, 3, 4)
    assert gamify.BADGE_AMBAR_HEAVY not in _earned(context)
    merge_last_ambar_pr(world, github)
    hold(api_client, ids[world.name], 5)
    assert gamify.BADGE_AMBAR_HEAVY not in _earned(context), "iki ayri islem sayilmaz"


def test_heavy_load_badge_is_earned(api_client, context, world, github):
    for number in range(1, 6):
        world.merge_pr(number, {f"f{number}.txt": f"{number}\n"})
    ids = configure(context, github, world)
    start_campaign(context)
    result = hold(api_client, ids[world.name], 1, 2, 3, 4, 5)
    assert result["ok"] is True
    assert gamify.BADGE_AMBAR_HEAVY in _earned(context)


def test_restore_with_a_conflict_does_not_earn_clean_takeoff(context):
    start_campaign(context)
    results = [
        {"name": "o/bir", "ok": True, "items": [{"label": "#1"}], "error": None},
        {"name": "o/iki", "ok": False, "items": [{"label": "#2"}], "error": {"code": "conflict"}},
    ]
    gamify.on_ambar(context, "cikar", results, "run-1")
    assert gamify.BADGE_AMBAR_CLEAN not in _earned(context)
    assert [event["ref"] for event in _events(context, gamify.KIND_AMBAR_RELEASE)] == ["o/bir:#1"]


def test_xp_ref_uses_the_sorted_pr_set(context):
    start_campaign(context)
    first = [{"name": "o/r", "ok": True, "items": [{"label": "#151"}, {"label": "#142"}]}]
    second = [{"name": "o/r", "ok": True, "items": [{"label": "#142"}, {"label": "#151"}]}]
    assert gamify.on_ambar(context, "al", first, "a")["points"] == 20
    assert gamify.on_ambar(context, "al", second, "b")["points"] == 0
    assert _events(context, gamify.KIND_AMBAR_HOLD)[0]["ref"] == "o/r:#142,#151"


def test_nothing_happens_without_a_campaign(api_client, context, world, github):
    world.merge_pr(1, {"a.txt": "a\n"})
    ids = configure(context, github, world)
    result = hold(api_client, ids[world.name], 1)
    assert result["ok"] is True
    assert result["reward"] == {"points": 0, "events": [], "badges": []}
    assert gstore.activity_rows(context.connection(), gstore.ACTIVITY_AMBAR) == []
    assert context.connection().execute("SELECT COUNT(*) AS c FROM xp_events").fetchone()["c"] == 0


def test_unknown_revert_targets_are_ignored(world):
    git(world.dev, "commit", "--quiet", "--allow-empty", "-m",
        "Revert \"eski\"\n\nThis reverts commit " + "ab" * 20 + ".")
    tool = ambar_git.Git(shutil.which("git"), str(world.dev))
    assert ambar_git.held_commits(tool, "refs/heads/master") == []


def test_a_wrong_git_path_says_what_to_do(tmp_path):
    with pytest.raises(ambar_git.GitError) as caught:
        ambar_git.find_git(str(tmp_path / "yok" / "git.exe"))
    assert caught.value.code == "git_not_found"
    assert "where git" in caught.value.message


def test_git_runs_without_a_shell_and_without_a_window(monkeypatch):
    seen = {}

    def fake_run(command, **kwargs):
        seen["command"] = command
        seen["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(ambar_git.sys, "platform", "win32")
    monkeypatch.setattr(ambar_git.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)
    monkeypatch.setattr("app.copilot.sys.platform", "win32")
    monkeypatch.setattr(ambar_git.subprocess, "run", fake_run)
    ambar_git.Git("git", "C:\\kod\\api").run("status")
    assert isinstance(seen["command"], list) and seen["command"][:3] == ["git", "-C", "C:\\kod\\api"]
    assert "shell" not in seen["kwargs"]
    assert seen["kwargs"]["creationflags"] == 0x08000000
    assert seen["kwargs"]["stdin"] == subprocess.DEVNULL
    assert seen["kwargs"]["env"]["GIT_TERMINAL_PROMPT"] == "0"
