"""Gercek surec testi: uygulama nereden calistirilirsa calistirilsin acilir mi?

Saha hatasi: kullanici paket klasorunde `.venv\\Scripts\\python.exe -m app`
yazdiginda "No module named app" aldi. `-m app` calisma dizinine bagimli ve
`PYTHONSAFEPATH`/`-P` acikken calisma dizini `sys.path`'e hic eklenmiyor.
Burada surec gercekten baslatiliyor: baska bir calisma dizininden,
`PYTHONSAFEPATH=1` ile ve icinde Turkce harf/bosluk olan bir klasorden.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from app import __version__ as app_version

ROOT = Path(__file__).resolve().parent.parent

# Windows'ta OneDrive/Masaustu yollari boyle gorunuyor; ayni tuzak burada.
AWKWARD_FOLDER = "Yeni Klasör ğüş (1)"


def _copy_package(destination: Path) -> Path:
    package = destination / AWKWARD_FOLDER
    package.mkdir(parents=True)
    shutil.copytree(
        ROOT / "app", package / "app", ignore=shutil.ignore_patterns("__pycache__", "*.pyc")
    )
    shutil.copy2(ROOT / "holocron_run.py", package / "holocron_run.py")
    return package


def _wait_for_health(url: str, process: subprocess.Popen, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"Surec beklenmedik sekilde kapandi: {process.returncode}")
        try:
            with urllib.request.urlopen(url, timeout=2) as response:
                return json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, OSError) as error:  # henuz dinlemiyor
            last_error = error
            time.sleep(0.25)
    raise AssertionError(f"Nabiz alinamadi: {last_error}")


@pytest.mark.slow
def test_app_starts_from_a_foreign_working_directory(tmp_path, monkeypatch):
    package = _copy_package(tmp_path)
    entry = package / "holocron_run.py"

    port = 8791
    env = dict(os.environ)
    # Kurumsal kurulumlarda gorulen ayar: calisma dizini sys.path'e girmez.
    env["PYTHONSAFEPATH"] = "1"
    env["HOLOCRON_NO_BROWSER"] = "1"
    env.pop("HOLOCRON_HOME", None)
    env.pop("PYTHONPATH", None)

    elsewhere = tmp_path / "baska-yer"
    elsewhere.mkdir()

    process = subprocess.Popen(
        [sys.executable, str(entry), "--no-browser", "--port", str(port)],
        cwd=str(elsewhere),  # bilerek yanlis klasor
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        health = _wait_for_health(f"http://127.0.0.1:{port}/api/health", process)
        assert health["status"] == "ok"
        assert health["app"] == "holocron"

        # Veri klasoru __file__'a gore: cwd baska yerdeyken bile paketin yaninda.
        assert (package / "holocron.db").exists()
        assert not (elsewhere / "holocron.db").exists()

        log_file = package / "holocron.log"
        assert log_file.exists()
        text = log_file.read_text(encoding="utf-8")
        assert f"Holocron {app_version} basliyor" in text
        assert f"port {port}" in text
        assert str(package) in text

        port_file = package / "holocron.port"
        assert port_file.read_text(encoding="ascii").strip() == str(port)

        urllib.request.urlopen(
            urllib.request.Request(f"http://127.0.0.1:{port}/api/shutdown", method="POST"),
            timeout=5,
        )
        process.wait(timeout=20)
        assert process.returncode == 0
        assert not port_file.exists(), "kapanista port dosyasi silinmeli"
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)


# --- pythonw taklidi ----------------------------------------------------
#
# Saha dogrulamasi: ayni klasorde `.venv\\Scripts\\python.exe -m app` sorunsuz
# aciliyor, `start "" pythonw -m app` sessizce oluyor. Tek fark konsol:
# pythonw altinda `sys.stdout`/`sys.stderr` None'dir. Asagidaki iki test bu
# farki gercek bir surecte kuruyor.

PYTHONW_PROLOGUE = (
    "import runpy, sys; "
    "sys.stdout = None; sys.stderr = None; "
    "sys.argv = [{entry!r}, '--no-browser', '--port', {port!r}]; "
    "runpy.run_path({entry!r}, run_name='__main__')"
)


def _clean_env() -> dict:
    env = dict(os.environ)
    env["HOLOCRON_NO_BROWSER"] = "1"
    env.pop("HOLOCRON_HOME", None)
    env.pop("PYTHONPATH", None)
    return env


@pytest.mark.slow
def test_app_starts_without_stdout_or_stderr(tmp_path):
    """pythonw.exe kosulu: her iki akis da None. Onceki surum burada oluyordu.

    uvicorn 0.34 varsayilan log yapilandirmasinda `sys.stdout.isatty()`
    cagiriyor; konsolsuz surecte bu `ValueError: Unable to configure
    formatter 'default'` veriyor ve surec sunucu kalkmadan kapaniyordu.
    """
    package = _copy_package(tmp_path)
    entry = package / "holocron_run.py"
    port = 8792

    process = subprocess.Popen(
        [sys.executable, "-c", PYTHONW_PROLOGUE.format(entry=str(entry), port=str(port))],
        cwd=str(tmp_path),
        env=_clean_env(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        health = _wait_for_health(f"http://127.0.0.1:{port}/api/health", process)
        assert health["status"] == "ok"

        # Konsol yokken de tek satir bile kaybolmuyor.
        text = (package / "holocron.log").read_text(encoding="utf-8")
        assert "Konsol durumu: stdout=yok stderr=yok" in text
        assert f"Holocron {app_version} basliyor" in text
        assert f"port {port}" in text

        urllib.request.urlopen(
            urllib.request.Request(f"http://127.0.0.1:{port}/api/shutdown", method="POST"),
            timeout=5,
        )
        process.wait(timeout=20)
        assert process.returncode == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)


@pytest.mark.slow
def test_app_starts_with_every_stream_sent_to_devnull(tmp_path):
    """Akislar kapali/yonlendirilmis: yazma denemeleri uygulamayi durdurmamali."""
    package = _copy_package(tmp_path)
    port = 8793

    process = subprocess.Popen(
        [sys.executable, str(package / "holocron_run.py"), "--no-browser", "--port", str(port)],
        cwd=str(tmp_path),
        env=_clean_env(),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        assert _wait_for_health(f"http://127.0.0.1:{port}/api/health", process)["status"] == "ok"
        assert (package / "holocron.log").exists()
        urllib.request.urlopen(
            urllib.request.Request(f"http://127.0.0.1:{port}/api/shutdown", method="POST"),
            timeout=5,
        )
        process.wait(timeout=20)
        assert process.returncode == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=10)
