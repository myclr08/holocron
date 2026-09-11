"""Baslatici: port secimi ve komut satiri secenekleri."""

from __future__ import annotations

import socket

import pytest

from app.__main__ import HOST, PREFERRED_PORT, parse_args, pick_port
from app.lifecycle import BEAT_TIMEOUT_SECONDS


def test_prefers_the_default_port_when_free():
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind((HOST, PREFERRED_PORT))
    except OSError:
        pytest.skip("Varsayilan port bu makinede kullanimda.")
    finally:
        probe.close()
    assert pick_port() == PREFERRED_PORT


def test_falls_back_to_a_free_port_when_taken():
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    holder.bind((HOST, PREFERRED_PORT))
    holder.listen(1)
    try:
        port = pick_port()
        assert port != PREFERRED_PORT
        assert 1024 < port < 65536
    finally:
        holder.close()


def test_defaults():
    args = parse_args([])
    assert args.port is None
    assert args.no_browser is False
    assert args.timeout == BEAT_TIMEOUT_SECONDS


def test_options_are_parsed():
    args = parse_args(["--port", "9000", "--no-browser", "--timeout", "60"])
    assert args.port == 9000
    assert args.no_browser is True
    assert args.timeout == 60


# --- Windows baslaticisi: metin testleri --------------------------------
#
# holocron.bat burada calistirilamiyor (Linux/CI Ubuntu), bu yuzden mantik
# metin uzerinden korunuyor: dallar, etiketler ve tercih sirasi yerinde mi.

BAT = (ROOT := __import__("pathlib").Path(__file__).resolve().parent.parent) / "holocron.bat"
SH = ROOT / "holocron.sh"


def test_windows_launcher_has_every_goto_target():
    text = BAT.read_text(encoding="utf-8")
    import re

    targets = set(re.findall(r"^:(\w+)", text, re.MULTILINE))
    jumps = set(re.findall(r"goto (\w+)", text)) - {"eof"}
    assert jumps <= targets, jumps - targets
    for label in ("run_embed", "run_venv", "create_venv", "offline_install", "online_install"):
        assert label in targets, label


def test_windows_launcher_prefers_embed_then_venv():
    text = BAT.read_text(encoding="utf-8")
    embed = text.index('if exist "python-embed\\python.exe" goto run_embed')
    venv = text.index('if exist "%VENV_PY%" goto run_venv')
    create = text.index("goto create_venv")
    assert embed < venv < create


def test_windows_launcher_uses_pythonw_to_hide_the_console():
    text = BAT.read_text(encoding="utf-8")
    assert 'python-embed\\pythonw.exe' in text
    assert "%VENV_PYW%" in text
    assert text.count("pythonw.exe") >= 3


def test_windows_launcher_installs_offline_when_wheels_are_present():
    text = BAT.read_text(encoding="utf-8")
    assert 'if exist "wheels\\*" goto offline_install' in text
    assert "pip install --no-index --find-links wheels -r requirements.txt" in text
    # Tekerlekler hedef Python surumune uymazsa agdan denenir.
    assert "if errorlevel 1 goto online_install" in text


def test_windows_launcher_falls_back_to_py_then_python():
    text = BAT.read_text(encoding="utf-8")
    assert 'set "BOOT_PY=py -3"' in text
    assert 'set "BOOT_PY=python"' in text
    assert text.index('BOOT_PY=py -3') < text.index('BOOT_PY=python"')
    assert "goto no_python" in text


def test_shell_launcher_also_falls_back_to_the_network():
    text = SH.read_text(encoding="utf-8")
    assert "--no-index --find-links wheels" in text
    assert "Cevrimdisi kurulum olmadi" in text
