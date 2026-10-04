"""Demo ortami: Ambar icin uydurma depolar ve sahte GitHub sunucusu."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ROOT / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from app import ambar  # noqa: E402
from demo import sahte_github  # noqa: E402

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git gerekli")


@pytest.fixture(autouse=True)
def git_kimligi(tmp_path, monkeypatch):
    ev = tmp_path / "githome"
    ev.mkdir()
    ayar = ev / ".gitconfig"
    ayar.write_text("[user]\n\tname = Demo Test\n\temail = demo@example.com\n", encoding="utf-8")
    monkeypatch.setenv("HOME", str(ev))
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(ayar))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


@pytest.fixture
def demo(tmp_path, context):
    depolar = sahte_github.depolari_kur(tmp_path / "ambar-depolar")
    durum = sahte_github.SahteGitHub(depolar)
    sunucu, _ = sahte_github.baslat(durum, port=0)
    context.settings.set("net.proxy_mode", "direct")
    sahte_github.ayarlari_yaz(context, depolar, sunucu.server_address[1])
    yield depolar
    sunucu.shutdown()
    sunucu.server_close()


def test_demo_repos_list_ten_prs_each_with_some_already_held(api_client, demo):
    data = api_client.get("/api/ambar", params={"days": 30}).json()
    assert [repo["name"] for repo in data["repos"]] == [d.tam_ad for d in demo]
    for repo in data["repos"]:
        assert repo["error"] is None, repo["error"]
        assert len(repo["prs"]) == 10
        assert all(pr["author"] and pr["files"] for pr in repo["prs"])
    assert [len(repo["held"]) for repo in data["repos"]] == [2, 1, 0]
    assert data["count"] == 3
    # Tarihler son 30 gune yayilmis.
    gunler = {pr["merged_at"][:10] for pr in data["repos"][0]["prs"]}
    assert len(gunler) >= 8


def test_demo_hold_opens_a_pr_and_the_fake_team_merges_it(api_client, demo):
    data = api_client.get("/api/ambar").json()
    repo = data["repos"][2]
    hedef = repo["prs"][0]["number"]
    sonuc = api_client.post(
        "/api/ambar/al", json={"items": [{"repo_id": repo["id"], "number": hedef}]}
    ).json()
    assert sonuc["ok"] is True, sonuc
    assert api_client.get("/api/ambar").json()["count"] == 4

    held = api_client.get("/api/ambar").json()["repos"][0]["held"]
    geri = api_client.post(
        "/api/ambar/cikar",
        json={"items": [{"repo_id": data["repos"][0]["id"], "sha": item["sha"]} for item in held]},
    ).json()
    assert geri["ok"] is True, geri
    assert api_client.get("/api/ambar").json()["count"] == 2


def test_demo_has_a_conflict_to_show(api_client, demo):
    data = api_client.get("/api/ambar").json()
    repo = data["repos"][2]
    eski = next(pr for pr in repo["prs"] if pr["title"] == "Aylık özet raporu")
    sonuc = api_client.post(
        "/api/ambar/al", json={"items": [{"repo_id": repo["id"], "number": eski["number"]}]}
    ).json()
    assert sonuc["results"][0]["error"]["title"] == ambar.CONFLICT_TITLE


def test_demo_repos_are_reused_without_reset(tmp_path):
    ilk = sahte_github.depolari_kur(tmp_path / "d")
    isaret = ilk[0].calisma / "dokunulmadi.txt"
    isaret.write_text("x", encoding="utf-8")
    ikinci = sahte_github.depolari_kur(tmp_path / "d")
    assert [d.calisma for d in ikinci] == [d.calisma for d in ilk] and isaret.exists()
    yeni = sahte_github.depolari_kur(tmp_path / "d", sifirla=True)
    assert all(d.hazir for d in yeni) and not isaret.exists()
