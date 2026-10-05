"""Yazilim guncelleme: surum karsilastirma, ozet, yedek, sira, yardimci surec.

Hicbir test aga cikmaz ya da gercek surec baslatmaz: GitHub sahte bir oturum,
yardimci baslaticisi sahte bir islevdir. Yardimcinin dosya isleri (yerine
koyma, geri alma) gecici klasorde kurulan sahte bir kurulum agacinda denenir.
"""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import subprocess
import sys
import time
import zipfile
from datetime import datetime
from pathlib import Path

import pytest

from app import __version__, guncelle, guncelleyici, paths

NEW = "0.99.0"
API = "https://api.example.test"


# --- surum ------------------------------------------------------------------


@pytest.mark.parametrize(
    "candidate, current, newer",
    [
        ("v0.16.0", "0.15.0", True),
        ("0.16.0", "0.16.0", False),
        ("v0.15.9", "0.16.0", False),
        ("v0.16.1", "0.16.0", True),
        ("v1.0", "0.99.9", True),
        ("v0.16", "0.16.0", False),
        ("v0.10.0", "0.9.0", True),  # metin degil sayi karsilastirmasi
        ("v0.16.0-rc1", "0.15.0", True),
        ("v0.16.0-rc1", "0.16.0", False),
        ("v0.16.0", "0.16.0-rc1", True),
        ("bozuk", "0.15.0", False),
        ("", "0.15.0", False),
    ],
)
def test_version_compare(candidate, current, newer):
    assert guncelle.is_newer(candidate, current) is newer


def test_parse_checksums_reads_sha256sum_output():
    digest = "a" * 64
    text = f"{digest}  holocron-windows-x64-lite.zip\n{'b' * 64} *holocron-linux-x64.zip\nbozuk satir\n"
    assert guncelle.parse_checksums(text) == {
        "holocron-windows-x64-lite.zip": digest,
        "holocron-linux-x64.zip": "b" * 64,
    }


def test_the_asset_matches_the_install(tmp_path):
    assert guncelle.asset_for_install(tmp_path, "win32") == guncelle.ASSET_LITE
    (tmp_path / "python-embed").mkdir()
    assert guncelle.asset_for_install(tmp_path, "win32") == guncelle.ASSET_FULL
    assert guncelle.asset_for_install(tmp_path, "linux") == guncelle.ASSET_LINUX


def test_checksum_mismatch_and_missing_entry_are_refused():
    with pytest.raises(guncelle.UpdateError) as mismatch:
        guncelle.verify_checksum("a.zip", "0" * 64, {"a.zip": "1" * 64})
    assert mismatch.value.code == "checksum_mismatch"
    with pytest.raises(guncelle.UpdateError) as missing:
        guncelle.verify_checksum("a.zip", "0" * 64, {})
    assert missing.value.code == "checksum_missing"
    guncelle.verify_checksum("a.zip", "AB" * 32, {"a.zip": "ab" * 32})


# --- yedek ------------------------------------------------------------------


def _make_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("CREATE TABLE t (v TEXT)")
    conn.execute("INSERT INTO t VALUES ('kayit')")
    conn.commit()
    # Baglanti acik kalir: WAL'daki yazi da yedege girmeli.
    return conn


def test_backup_copies_db_and_key_with_the_backup_api(tmp_path):
    db_file = tmp_path / "holocron.db"
    keep_open = _make_db(db_file)
    (tmp_path / "holocron.key").write_bytes(b"anahtar")
    root = tmp_path / "yedek"
    target = guncelle.backup_data(
        db_file, tmp_path / "holocron.key", "0.15.0", root=root, moment=datetime(2026, 10, 6, 9, 5, 3)
    )
    keep_open.close()
    assert target.name == "2026-10-06_090503_v0.15.0"
    copy = sqlite3.connect(target / "holocron.db")
    assert copy.execute("SELECT v FROM t").fetchall() == [("kayit",)]
    copy.close()
    assert (target / "holocron.key").read_bytes() == b"anahtar"


def test_backup_fails_loudly_when_the_db_is_missing(tmp_path):
    root = tmp_path / "yedek"
    with pytest.raises(guncelle.UpdateError) as caught:
        guncelle.backup_data(tmp_path / "yok.db", tmp_path / "yok.key", "0.15.0", root=root)
    assert caught.value.code == "backup_failed"
    assert not any(root.iterdir())


def test_backup_fails_when_the_folder_cannot_be_made(tmp_path):
    db_file = tmp_path / "holocron.db"
    _make_db(db_file).close()
    blocker = tmp_path / "dosya"
    blocker.write_text("klasor degil", encoding="utf-8")
    with pytest.raises(guncelle.UpdateError) as caught:
        guncelle.backup_data(db_file, tmp_path / "holocron.key", "0.15.0", root=blocker / "yedek")
    assert caught.value.code == "backup_failed"


def test_rotation_keeps_the_last_ten_and_ignores_foreign_folders(tmp_path):
    for day in range(1, 13):
        (tmp_path / f"2026-09-{day:02d}_120000_v0.15.0").mkdir()
    (tmp_path / "benim-klasorum").mkdir()
    removed = guncelle.rotate_backups(tmp_path, keep=10)
    assert [p.name for p in removed] == ["2026-09-01_120000_v0.15.0", "2026-09-02_120000_v0.15.0"]
    left = sorted(p.name for p in tmp_path.iterdir())
    assert "benim-klasorum" in left
    assert len(left) == 11


def test_backup_rotates_after_writing(tmp_path):
    db_file = tmp_path / "holocron.db"
    _make_db(db_file).close()
    root = tmp_path / "yedek"
    root.mkdir()
    for day in range(1, 11):
        (root / f"2026-09-{day:02d}_120000_v0.14.0").mkdir()
    guncelle.backup_data(db_file, tmp_path / "holocron.key", "0.15.0", root=root,
                         moment=datetime(2026, 10, 6, 12, 0, 0))
    names = sorted(p.name for p in root.iterdir())
    assert len(names) == 10
    assert names[-1] == "2026-10-06_120000_v0.15.0"
    assert "2026-09-01_120000_v0.14.0" not in names


# --- sahte GitHub ve sahte kurulum ------------------------------------------


def package_zip(version: str = NEW, extra: dict[str, bytes] | None = None) -> bytes:
    buffer = io.BytesIO()
    files = {
        "holocron/app/__init__.py": f'__version__ = "{version}"\n'.encode(),
        "holocron/app/yeni_modul.py": b"# yeni\n",
        "holocron/holocron_run.py": b"# giris\n",
        "holocron/holocron.bat": b"rem yeni\n",
        "holocron/holocron.sh": b"# yeni\n",
        "holocron/requirements.txt": b"fastapi\nyeni-paket\n",
        "holocron/wheels/yeni-1.0-py3-none-any.whl": b"tekerlek",
    }
    files.update(extra or {})
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, data in files.items():
            archive.writestr(name, data)
    return buffer.getvalue()


class FakeResponse:
    def __init__(self, status: int = 200, payload=None, body: bytes = b"", headers=None):
        self.status_code = status
        self._payload = payload
        self.content = body
        self.text = body.decode("utf-8", "replace")
        self.headers = headers or {"Content-Length": str(len(body))}

    def json(self):
        if self._payload is None:
            raise ValueError("json yok")
        return self._payload

    def iter_content(self, chunk_size=1):
        for start in range(0, len(self.content), chunk_size):
            yield self.content[start : start + chunk_size]

    def close(self):
        pass


class FakeGitHub:
    def __init__(self, zip_bytes: bytes, asset: str, checksum: str | None = None, sums: bool = True,
                 version: str = NEW):
        self.zip_bytes = zip_bytes
        self.asset = asset
        self.version = version
        self.sums = sums
        self.checksum = checksum or hashlib.sha256(zip_bytes).hexdigest()
        self.urls: list[str] = []
        self.offline = False

    def get(self, url, **kwargs):
        import requests

        self.urls.append(url)
        if self.offline:
            raise requests.exceptions.ConnectionError("ag yok")
        if url.endswith("/releases/latest"):
            assets = [{"name": self.asset, "browser_download_url": f"{API}/dl/{self.asset}",
                       "size": len(self.zip_bytes)}]
            if self.sums:
                assets.append({"name": guncelle.CHECKSUMS_NAME,
                               "browser_download_url": f"{API}/dl/{guncelle.CHECKSUMS_NAME}", "size": 100})
            return FakeResponse(payload={
                "tag_name": f"v{self.version}", "name": f"v{self.version}",
                "body": "## Yenilikler\n- Arşiv", "html_url": "https://github.com/x/y/releases/v",
                "assets": assets,
            })
        if url.endswith("/" + guncelle.CHECKSUMS_NAME):
            return FakeResponse(body=f"{self.checksum}  {self.asset}\n".encode())
        if url.endswith("/" + self.asset):
            return FakeResponse(body=self.zip_bytes)
        return FakeResponse(status=404)


class FakeSettings:
    def __init__(self):
        self.values: dict[str, str] = {}

    def get(self, key, default=None):
        return self.values.get(key, default)

    def set(self, key, value):
        self.values[key] = value


def fake_install(root: Path) -> Path:
    install = root / "kurulum"
    (install / "app").mkdir(parents=True)
    (install / "app" / "__init__.py").write_text('__version__ = "0.15.0"\n', encoding="utf-8")
    (install / "app" / "eski_modul.py").write_text("# eski\n", encoding="utf-8")
    (install / "holocron_run.py").write_text("# eski giris\n", encoding="utf-8")
    (install / "holocron.bat").write_text("rem eski\n", encoding="utf-8")
    (install / "requirements.txt").write_text("fastapi\n", encoding="utf-8")
    (install / "wheels").mkdir()
    (install / "wheels" / "eski-1.0-py3-none-any.whl").write_bytes(b"eski")
    (install / ".venv" / "Scripts").mkdir(parents=True)
    (install / ".venv" / "holocron-req.sha").write_text("ESKIOZET\n", encoding="utf-8")
    conn = sqlite3.connect(install / "holocron.db")
    conn.execute("CREATE TABLE t (v TEXT)")
    conn.execute("INSERT INTO t VALUES ('kullanici verisi')")
    conn.commit()
    conn.close()
    (install / "holocron.key").write_bytes(b"gizli-anahtar")
    (install / "holocron.log").write_text("log\n", encoding="utf-8")
    (install / "holocron.port").write_text("8765\n", encoding="utf-8")
    (install / "notlarim.txt").write_text("kendi dosyam\n", encoding="utf-8")
    return install


def data_snapshot(install: Path) -> dict[str, bytes]:
    names = ["holocron.db", "holocron.key", "holocron.log", "holocron.port", "notlarim.txt",
             ".venv/holocron-req.sha"]
    return {name: (install / name).read_bytes() for name in names}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv(guncelle.ENV_API, API)
    install = fake_install(tmp_path)

    def make(github: FakeGitHub):
        spawned: list[tuple[str, Path, Path]] = []
        stops: list[bool] = []
        updater = guncelle.Updater(
            settings=FakeSettings(),
            shutdown=lambda: stops.append(True),
            fetcher_factory=lambda: guncelle.Fetcher(session=github),
            install=install,
            home=install,
            spawner=lambda python, helper, plan: spawned.append((python, helper, plan)),
            backup_root=tmp_path / "yedek",
            shutdown_delay=0.0,
        )
        return updater, spawned, stops

    return install, make


def asset_name() -> str:
    return guncelle.asset_for_install(Path("/yok"))


def test_check_reports_a_newer_release(setup):
    install, make = setup
    github = FakeGitHub(package_zip(), asset_name())
    updater, _, _ = make(github)
    info = guncelle.check(updater.settings, updater._fetcher(), install)
    assert info["newer"] is True
    assert info["latest"] == NEW
    assert info["current"] == __version__
    assert "Arşiv" in info["notes"]
    assert info["checksums_ready"] is True
    assert guncelle.cached(updater.settings)["latest"] == NEW
    assert guncelle.check_due(updater.settings) is False


def test_check_without_network_says_so(setup):
    install, make = setup
    github = FakeGitHub(package_zip(), asset_name())
    github.offline = True
    updater, _, _ = make(github)
    with pytest.raises(guncelle.UpdateError) as caught:
        guncelle.check(updater.settings, updater._fetcher(), install)
    assert caught.value.code == "offline"
    assert "ulaşılamadı" in caught.value.message


def test_a_full_update_backs_up_verifies_and_hands_over(setup, tmp_path):
    install, make = setup
    before = data_snapshot(install)
    updater, spawned, stops = make(FakeGitHub(package_zip(), asset_name()))
    updater.start(background=False)
    status = updater.status()
    assert status["state"] == "restarting", status["message"]
    assert [step["done"] for step in status["steps"]][:4] == [True, True, True, True]

    backups = list((tmp_path / "yedek").iterdir())
    assert len(backups) == 1 and backups[0].name.endswith(f"_v{__version__}")
    assert (backups[0] / "holocron.key").read_bytes() == b"gizli-anahtar"

    (python, helper, plan_file), = spawned
    plan = json.loads(plan_file.read_text(encoding="utf-8"))
    assert plan["expected_version"] == NEW
    assert plan["pid"] > 0
    assert Path(plan["new_root"]).joinpath("app", "yeni_modul.py").exists()
    assert helper.read_text(encoding="utf-8") == Path(guncelleyici.__file__).read_text(encoding="utf-8")
    assert Path(helper).parent == install / guncelle.STAGING_DIR
    # Uygulama henuz hicbir program dosyasini degistirmedi; o yardimcinin isi.
    assert (install / "app" / "eski_modul.py").exists()
    assert data_snapshot(install) == before
    for _ in range(50):
        if stops:
            break
        time.sleep(0.02)
    assert stops == [True]


def test_a_checksum_mismatch_installs_nothing(setup):
    install, make = setup
    updater, spawned, stops = make(FakeGitHub(package_zip(), asset_name(), checksum="0" * 64))
    updater.start(background=False)
    status = updater.status()
    assert status["state"] == "error"
    assert status["error_code"] == "checksum_mismatch"
    assert spawned == [] and stops == []
    assert not (install / guncelle.STAGING_DIR).exists()


def test_a_release_without_checksums_is_not_installed(setup):
    install, make = setup
    github = FakeGitHub(package_zip(), asset_name(), sums=False)
    updater, spawned, _ = make(github)
    updater.start(background=False)
    assert updater.status()["error_code"] == "checksum_missing"
    assert spawned == []
    assert not any(url.endswith(".zip") for url in github.urls)


def test_a_failed_backup_aborts_before_downloading(setup, monkeypatch):
    install, make = setup
    github = FakeGitHub(package_zip(), asset_name())
    updater, spawned, stops = make(github)
    (install / "holocron.db").unlink()  # yedeklenecek veritabani yok
    updater.start(background=False)
    status = updater.status()
    assert status["state"] == "error"
    assert status["error_code"] == "backup_failed"
    assert not any(url.endswith(".zip") for url in github.urls)
    assert spawned == [] and stops == []


def test_a_package_with_the_wrong_version_is_refused(setup):
    install, make = setup
    updater, spawned, _ = make(FakeGitHub(package_zip(version="0.98.0"), asset_name()))
    updater.start(background=False)
    assert updater.status()["error_code"] == "bad_package"
    assert spawned == []


def test_a_package_with_path_escape_is_refused(setup):
    install, make = setup
    evil = package_zip(extra={"holocron/../../kacak.txt": b"x"})
    updater, spawned, _ = make(FakeGitHub(evil, asset_name()))
    updater.start(background=False)
    assert updater.status()["error_code"] == "bad_package"
    assert not (install.parent / "kacak.txt").exists()
    assert spawned == []


def test_nothing_to_do_when_already_current(setup):
    install, make = setup
    updater, spawned, _ = make(FakeGitHub(package_zip(__version__), asset_name(), version=__version__))
    updater.start(background=False)
    assert updater.status()["error_code"] == "up_to_date"


def test_a_git_checkout_is_never_overwritten(setup):
    install, make = setup
    (install / ".git").mkdir()
    updater, spawned, _ = make(FakeGitHub(package_zip(), asset_name()))
    with pytest.raises(guncelle.UpdateError) as caught:
        updater.preflight()
    assert caught.value.code == "dev_checkout"


def test_embedded_python_runs_the_helper_from_a_copy(tmp_path):
    install = tmp_path / "kurulum"
    embed = install / "python-embed"
    embed.mkdir(parents=True)
    (embed / "pythonw.exe").write_bytes(b"exe")
    staging = install / guncelle.STAGING_DIR
    staging.mkdir()
    chosen = guncelle.helper_python(install, staging, str(embed / "pythonw.exe"))
    assert Path(chosen) == staging / "runtime" / "pythonw.exe"
    assert (staging / "runtime" / "pythonw.exe").exists()
    venv = install / ".venv" / "Scripts" / "pythonw.exe"
    assert guncelle.helper_python(install, staging, str(venv)) == str(venv)


# --- yardimci: yerine koyma ve geri alma ------------------------------------


def staged_package(tmp_path: Path) -> Path:
    staging = tmp_path / "hazirlik"
    with zipfile.ZipFile(io.BytesIO(package_zip(extra={
        "holocron/holocron.db": b"PAKETTEN GELEN VERITABANI",
        "holocron/holocron.key": b"PAKETTEN GELEN ANAHTAR",
        "holocron/.venv/pyvenv.cfg": b"home = C:\\ci",
    }))) as archive:
        archive.extractall(staging)
    return staging / "holocron"


def test_apply_replaces_program_files_and_never_touches_user_data(tmp_path):
    install = fake_install(tmp_path)
    before = data_snapshot(install)
    new_root = staged_package(tmp_path)
    old_dir = tmp_path / "eski"
    replaced = guncelleyici.apply_update(install, new_root, old_dir, log=lambda _m: None)
    assert "app" in replaced and "wheels" in replaced
    assert "holocron.db" not in replaced and ".venv" not in replaced
    assert (install / "app" / "yeni_modul.py").exists()
    assert not (install / "app" / "eski_modul.py").exists()
    assert (install / "holocron.bat").read_text(encoding="utf-8") == "rem yeni\n"
    assert (install / "wheels" / "yeni-1.0-py3-none-any.whl").exists()
    assert data_snapshot(install) == before
    assert not (install / ".venv" / "pyvenv.cfg").exists()
    assert (old_dir / "app" / "eski_modul.py").exists()


def test_a_failure_midway_rolls_everything_back(tmp_path):
    install = fake_install(tmp_path)
    before_bat = (install / "holocron.bat").read_text(encoding="utf-8")
    new_root = staged_package(tmp_path)
    old_dir = tmp_path / "eski"
    import os

    def flaky_move(source: Path, target: Path) -> None:
        if source.name == "wheels" and source.parent == new_root:
            raise PermissionError("dosya kilitli")
        os.replace(source, target)

    with pytest.raises(PermissionError):
        guncelleyici.apply_update(install, new_root, old_dir, log=lambda _m: None, move=flaky_move)
    assert (install / "app" / "eski_modul.py").exists()
    assert not (install / "app" / "yeni_modul.py").exists()
    assert (install / "holocron.bat").read_text(encoding="utf-8") == before_bat
    assert (install / "wheels" / "eski-1.0-py3-none-any.whl").exists()
    assert not (install / "holocron.sh").exists()  # eski surumde yoktu, kaldirildi


def test_rollback_after_a_successful_swap_restores_the_old_version(tmp_path):
    install = fake_install(tmp_path)
    before = data_snapshot(install)
    new_root = staged_package(tmp_path)
    old_dir = tmp_path / "eski"
    replaced = guncelleyici.apply_update(install, new_root, old_dir, log=lambda _m: None)
    assert guncelleyici.rollback(install, old_dir, replaced, log=lambda _m: None) is True
    assert (install / "app" / "eski_modul.py").exists()
    assert not (install / "app" / "yeni_modul.py").exists()
    assert not (install / "holocron.sh").exists()
    assert data_snapshot(install) == before


def _plan(tmp_path: Path, install: Path, new_root: Path) -> dict:
    return {
        "pid": 0, "install": str(install), "new_root": str(new_root),
        "old_dir": str(tmp_path / "eski"), "home": str(install),
        "log": str(tmp_path / "guncelleme.log"), "old_version": "0.15.0",
        "expected_version": NEW, "restart": ["baslat"], "health_timeout": 1, "settle": 0,
    }


def test_run_restarts_the_new_version_and_cleans_up(tmp_path, monkeypatch):
    install = fake_install(tmp_path)
    new_root = staged_package(tmp_path)
    restarts: list[list[str]] = []
    monkeypatch.setattr(guncelleyici, "restart", lambda inst, cmd, lp, log: restarts.append(cmd) or True)
    monkeypatch.setattr(guncelleyici, "wait_healthy", lambda *a, **k: True)
    plan = _plan(tmp_path, install, new_root)
    assert guncelleyici.run(plan, guncelleyici.Logger(Path(plan["log"]))) == 0
    assert restarts == [["baslat"]]
    assert (install / "app" / "yeni_modul.py").exists()
    assert not (tmp_path / "eski").exists()
    assert "SONUC" in Path(plan["log"]).read_text(encoding="utf-8")


def test_run_rolls_back_when_the_new_version_does_not_come_up(tmp_path, monkeypatch):
    install = fake_install(tmp_path)
    before = data_snapshot(install)
    new_root = staged_package(tmp_path)
    restarts: list[list[str]] = []
    monkeypatch.setattr(guncelleyici, "restart", lambda inst, cmd, lp, log: restarts.append(cmd) or True)
    monkeypatch.setattr(guncelleyici, "wait_healthy", lambda *a, **k: False)
    monkeypatch.setattr(guncelleyici, "request_shutdown", lambda home: None)
    plan = _plan(tmp_path, install, new_root)
    assert guncelleyici.run(plan, guncelleyici.Logger(Path(plan["log"]))) == 1
    assert restarts == [["baslat"], ["baslat"]]  # yeni denendi, eski geri baslatildi
    assert (install / "app" / "eski_modul.py").exists()
    assert data_snapshot(install) == before
    assert "eski surume donuldu" in Path(plan["log"]).read_text(encoding="utf-8")


def test_run_touches_nothing_if_the_app_never_exits(tmp_path, monkeypatch):
    install = fake_install(tmp_path)
    new_root = staged_package(tmp_path)
    monkeypatch.setattr(guncelleyici, "wait_for_exit", lambda pid, timeout: False)
    restarts: list[list[str]] = []
    monkeypatch.setattr(guncelleyici, "restart", lambda *a: restarts.append(a) or True)
    plan = _plan(tmp_path, install, new_root)
    plan["pid"] = 12345
    assert guncelleyici.run(plan, lambda _m: None) == 2
    assert (install / "app" / "eski_modul.py").exists()
    assert restarts == []


def test_wait_for_exit_sees_a_finished_process():
    finished = subprocess.run([sys.executable, "-c", "pass"], check=True)
    assert finished.returncode == 0
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    assert guncelleyici.wait_for_exit(process.pid, timeout=5) is True


def test_wait_for_exit_times_out_on_a_live_process():
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        started = time.monotonic()
        assert guncelleyici.wait_for_exit(process.pid, timeout=0.5, poll=0.05) is False
        assert time.monotonic() - started < 5
    finally:
        process.kill()
        process.wait()


def test_wait_healthy_waits_for_the_expected_version(tmp_path):
    (tmp_path / "holocron.port").write_text("9999\n", encoding="ascii")
    answers = iter(["0.15.0", "0.15.0", NEW])
    assert guncelleyici.wait_healthy(
        tmp_path, NEW, timeout=5, log=lambda _m: None, poll=0.01, probe=lambda port: next(answers)
    ) is True
    assert guncelleyici.wait_healthy(
        tmp_path, NEW, timeout=0.05, log=lambda _m: None, poll=0.01, probe=lambda port: None
    ) is False


def test_restart_uses_the_same_launcher_as_the_user(tmp_path):
    windows = guncelleyici.restart_command(tmp_path, "win32")
    assert windows[-1] == str(tmp_path / "holocron.bat")
    assert windows[1] == "/c"
    linux = guncelleyici.restart_command(tmp_path, "linux")
    assert linux[-1] == str(tmp_path / "holocron.sh")


def test_the_helper_is_standard_library_only():
    """Yardimci `app` paketini ice aktarmamali: o sirada `app/` degisiyor."""
    text = Path(guncelleyici.__file__).read_text(encoding="utf-8")
    assert "from app" not in text and "from ." not in text and "import app" not in text


# --- API --------------------------------------------------------------------


def test_the_api_reports_state_and_refuses_in_a_git_checkout(api_client, monkeypatch):
    data = api_client.get("/api/guncelleme").json()
    assert data["status"]["state"] == "idle"
    assert data["status"]["current"] == __version__
    assert [step["id"] for step in data["status"]["steps"]] == [key for key, _ in guncelle.STEPS]
    updater = api_client.app.state.updater
    monkeypatch.setattr(updater, "install", Path(guncelle.__file__).resolve().parent.parent)
    if (updater.install / ".git").exists():
        response = api_client.post("/api/guncelleme/baslat")
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "dev_checkout"


def test_the_api_check_without_network_is_a_clear_error(api_client, monkeypatch):
    monkeypatch.setenv(guncelle.ENV_API, API)
    github = FakeGitHub(package_zip(), asset_name())
    github.offline = True
    api_client.get("/api/guncelleme")
    api_client.app.state.updater.fetcher_factory = lambda: guncelle.Fetcher(session=github)
    response = api_client.post("/api/guncelleme/denetle")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "offline"
    # Rozet sessizdir: hata vermez, "yeni yok" der.
    assert api_client.get("/api/guncelleme/rozet").json()["newer"] is False


def test_the_api_badge_uses_the_cached_check(api_client, monkeypatch):
    monkeypatch.setenv(guncelle.ENV_API, API)
    github = FakeGitHub(package_zip(), asset_name())
    api_client.get("/api/guncelleme")
    api_client.app.state.updater.fetcher_factory = lambda: guncelle.Fetcher(session=github)
    first = api_client.get("/api/guncelleme/rozet").json()
    assert first["newer"] is True and first["latest"] == NEW
    calls = len(github.urls)
    api_client.get("/api/guncelleme/rozet")
    assert len(github.urls) == calls  # 12 saat dolmadan aga cikilmaz
