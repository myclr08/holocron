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


def test_windows_launcher_explains_a_missing_venv_module():
    """Lite pakette Python kullanicinin: venv yoksa ne yapacagini bilmeli."""
    text = BAT.read_text(encoding="utf-8")
    assert "-m venv --help" in text
    assert "goto no_venv_module" in text
    assert ":no_venv_module" in text
    message = text.split(":no_venv_module", 1)[1].split("exit /b 1", 1)[0]
    assert "venv" in message and "Cozum" in message
    # Mesajlar ASCII kalmali: konsol kod sayfasi Turkce harfleri bozuyor.
    text.encode("ascii")


def test_shell_launcher_also_falls_back_to_the_network():
    text = SH.read_text(encoding="utf-8")
    assert "--no-index --find-links wheels" in text
    assert "Cevrimdisi kurulum olmadi" in text


def test_console_flag_exists_for_troubleshooting():
    args = parse_args(["--console"])
    assert args.console is True
    assert parse_args([]).console is False


# --- Windows baslaticisi: sessiz cokmeyi gorunur kilan dallar -----------


def test_windows_launcher_checks_health_before_giving_up():
    """start sonrasi nabiz yoklanmazsa hata yine sessiz kalirdi."""
    text = BAT.read_text(encoding="utf-8")
    assert "/api/health" in text
    assert "curl.exe" in text
    # Windows 10 1803 oncesinde curl yok; PowerShell yedegi sart.
    assert "Invoke-WebRequest" in text
    assert ":health_check" in text
    assert ":start_failed" in text


def test_windows_launcher_waits_with_ping_not_timeout():
    """timeout /t yonlendirilmis girdide hata veriyor, ping her yerde calisir."""
    text = BAT.read_text(encoding="utf-8")
    assert "ping -n 7 127.0.0.1 >nul" in text
    assert "timeout /t" not in text


def test_windows_launcher_prints_the_log_and_pauses_on_failure():
    text = BAT.read_text(encoding="utf-8")
    failure = text.split(":start_failed", 1)[1]
    assert "Uygulama acilmadi" in failure
    assert "Get-Content -Tail 40" in failure
    assert "type " in failure  # PowerShell yoksa yedek
    assert "pause" in failure
    assert "holocron.log" in text


def test_windows_launcher_has_a_console_mode():
    text = BAT.read_text(encoding="utf-8")
    assert '"%~1"=="--console"' in text
    assert ":launch_console" in text
    console = text.split(":launch_console", 1)[1].split(":health_check", 1)[0]
    # Konsol kipinde pythonw degil python.exe, ve pencere kapanmasin.
    assert "%RUN_PY%" in console and "%RUN_PYW%" not in console
    assert "pause" in console


def test_windows_launcher_reads_the_port_the_app_chose():
    text = BAT.read_text(encoding="utf-8")
    assert "holocron.port" in text
    assert "set /p PORT=" in text
    assert 'set "PORT=8765"' in text  # port dosyasi yoksa varsayilan


def test_windows_launcher_does_not_trust_the_working_directory():
    """'No module named app': cwd sys.path'te olmayabilir (PYTHONSAFEPATH)."""
    text = BAT.read_text(encoding="utf-8")
    assert 'set "PYTHONPATH=%~dp0"' in text
    assert "holocron_run.py" in text
    assert '/d "%~dp0"' in text
    assert "-m app" not in text


def test_shell_launcher_is_also_working_directory_independent():
    text = SH.read_text(encoding="utf-8")
    assert "holocron_run.py" in text
    assert 'export PYTHONPATH="$HERE' in text
    assert "-m app" not in text


def test_run_entrypoint_puts_its_own_folder_on_the_path():
    source = (ROOT / "holocron_run.py").read_text(encoding="utf-8")
    assert "__file__" in source
    assert "sys.path.insert(0, str(ROOT))" in source
    assert "from app.__main__ import main" in source


def test_readme_documents_the_troubleshooting_path():
    """Sessiz cokmenin cevabi belgede olmazsa kimse --console'u bulamaz."""
    text = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "## Sorun giderme" in text
    assert "holocron.bat --console" in text
    assert "holocron.log" in text
    assert "holocron.port" in text
    assert "No module named app" in text
