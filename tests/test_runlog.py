"""Konsolsuz calisma (pythonw) ve dosya logu.

Buradaki testlerin hepsi tek bir saha hatasindan dogdu: Windows'ta
`holocron.bat` cift tiklaninca konsol aciliyor, `.venv` kuruluyor, sonra
pencere kapaniyor ve hicbir sey olmuyordu. Sebep `pythonw.exe` altinda
`sys.stdout`/`sys.stderr` degerlerinin `None` olmasi; uvicorn'un varsayilan
log yapilandirmasi `sys.stdout.isatty()` cagirip patliyordu ve gorecek
kimse yoktu.
"""

from __future__ import annotations

import io
import logging
import sys
import threading
from pathlib import Path

import pytest

from app import paths, runlog
from app.__main__ import main, open_browser, parse_args, write_port_file


@pytest.fixture(autouse=True)
def restore_logging():
    """Her test kendi isleyicisini kursun, kokteki eski isleyiciler sizmasin."""
    root = logging.getLogger()
    previous = list(root.handlers), root.level
    previous_hooks = sys.excepthook, threading.excepthook
    yield
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    for handler in previous[0]:
        root.addHandler(handler)
    root.setLevel(previous[1])
    sys.excepthook, threading.excepthook = previous_hooks


# --- safe_print: konsol yokken cokmemeli --------------------------------


def test_safe_print_survives_a_missing_stdout(monkeypatch):
    """pythonw altinda sys.stdout None'dir; print yolu buna dayanmali."""
    monkeypatch.setattr(sys, "stdout", None)
    assert runlog.safe_print("merhaba") is False


def test_safe_print_writes_when_there_is_a_console():
    stream = io.StringIO()
    assert runlog.safe_print("merhaba", stream=stream) is True
    assert stream.getvalue() == "merhaba\n"


def test_safe_print_replaces_characters_the_code_page_cannot_take():
    """cp1254/cp850 konsolunda Turkce harf UnicodeEncodeError veriyordu."""

    class Cp850(io.StringIO):
        encoding = "cp850"

        def write(self, text: str) -> int:
            text.encode(self.encoding)  # gercek konsol gibi patlar
            return super().write(text)

    stream = Cp850()
    assert runlog.safe_print("Guncelleme basarili: ıŞğ", stream=stream) is True
    assert "?" in stream.getvalue()


def test_safe_print_ignores_a_closed_stream():
    stream = io.StringIO()
    stream.close()
    assert runlog.safe_print("merhaba", stream=stream) is False


def test_announce_logs_even_without_a_console(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    log_file = runlog.setup_logging(tmp_path / "holocron.log")
    runlog.announce("konsol yok ama log var")
    logging.shutdown()
    assert "konsol yok ama log var" in log_file.read_text(encoding="utf-8")


# --- Dosya logu ---------------------------------------------------------


def test_setup_logging_creates_the_file_in_the_data_directory(isolated_home):
    log_file = runlog.setup_logging()
    assert log_file == isolated_home / "holocron.log"
    assert log_file.exists()


def test_log_file_rotates_at_one_megabyte_times_three(tmp_path):
    runlog.setup_logging(tmp_path / "holocron.log")
    handlers = [
        h
        for h in logging.getLogger().handlers
        if getattr(h, runlog._MARK, False) and hasattr(h, "maxBytes")
    ]
    assert handlers, "donen dosya isleyicisi yok"
    assert handlers[0].maxBytes == 1_000_000
    assert handlers[0].backupCount == 3


def test_startup_line_names_version_python_and_home(tmp_path, isolated_home):
    log_file = runlog.setup_logging(tmp_path / "holocron.log")
    runlog.log_startup(port=8765, url="http://127.0.0.1:8765/", home=isolated_home)
    logging.shutdown()
    text = log_file.read_text(encoding="utf-8")
    assert "Holocron" in text
    assert sys.version.split()[0] in text
    assert "port 8765" in text
    assert str(isolated_home) in text


def test_setup_logging_is_idempotent(tmp_path):
    runlog.setup_logging(tmp_path / "holocron.log")
    runlog.setup_logging(tmp_path / "holocron.log")
    ours = [h for h in logging.getLogger().handlers if getattr(h, runlog._MARK, False)]
    assert len([h for h in ours if hasattr(h, "maxBytes")]) == 1


def test_setup_logging_adds_no_console_handler_without_a_console(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stderr", None)
    runlog.setup_logging(tmp_path / "holocron.log")
    ours = [h for h in logging.getLogger().handlers if getattr(h, runlog._MARK, False)]
    assert len(ours) == 1
    assert hasattr(ours[0], "maxBytes"), "konsol yokken sadece dosya isleyicisi kalmali"


def test_uvicorn_loggers_are_pinned_to_warning(tmp_path):
    runlog.setup_logging(tmp_path / "holocron.log")
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        assert logging.getLogger(name).level == logging.WARNING
        assert logging.getLogger(name).propagate is True


# --- Yakalanmamis istisnalar -------------------------------------------


def test_excepthook_writes_a_full_traceback(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stderr", None)
    log_file = runlog.setup_logging(tmp_path / "holocron.log")
    runlog.install_excepthook()
    try:
        raise ValueError("sahte cokme")
    except ValueError:
        sys.excepthook(*sys.exc_info())
    logging.shutdown()
    text = log_file.read_text(encoding="utf-8")
    assert "Traceback" in text
    assert "ValueError: sahte cokme" in text
    assert "test_excepthook_writes_a_full_traceback" in text


def test_thread_excepthook_is_captured_too(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "stderr", None)
    log_file = runlog.setup_logging(tmp_path / "holocron.log")
    runlog.install_excepthook()

    def explode() -> None:
        raise RuntimeError("arka planda patladi")

    worker = threading.Thread(target=explode, name="patlayan")
    worker.start()
    worker.join()
    logging.shutdown()
    text = log_file.read_text(encoding="utf-8")
    assert "RuntimeError: arka planda patladi" in text
    assert "patlayan" in text


def test_main_logs_the_traceback_instead_of_dying_silently(tmp_path, monkeypatch):
    """Asil sozumuz: ne olursa olsun bir iz kaliyor."""
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)
    log_file = tmp_path / "holocron.log"

    def explode(_args):
        raise RuntimeError("baslangicta sahte hata")

    monkeypatch.setattr("app.__main__.run", explode)
    code = main(["--no-browser", "--log-file", str(log_file)])
    logging.shutdown()
    assert code == 1
    text = log_file.read_text(encoding="utf-8")
    assert "Holocron baslatilamadi" in text
    assert "RuntimeError: baslangicta sahte hata" in text
    assert "Traceback" in text


def test_uvicorn_config_survives_a_missing_stdout(monkeypatch, isolated_home):
    """Gercek Windows kusuru: uvicorn 0.34 sys.stdout.isatty() cagiriyor.

    Varsayilan log yapilandirmasiyla bu satir pythonw altinda
    `ValueError: Unable to configure formatter 'default'` veriyordu.
    """
    import uvicorn
    from fastapi import FastAPI

    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    with pytest.raises(ValueError):
        uvicorn.Config(FastAPI(), host="127.0.0.1", port=8799)

    # Bizim kurdugumuz yapilandirma ayni kosulda ayakta kalmali.
    uvicorn.Config(
        FastAPI(),
        host="127.0.0.1",
        port=8799,
        log_level="warning",
        access_log=False,
        log_config=None,
        use_colors=False,
    )


# --- Port dosyasi ve tarayici ------------------------------------------


def test_port_file_is_written_next_to_the_database(isolated_home):
    written = write_port_file(8765)
    assert written == isolated_home / "holocron.port"
    assert written.read_text(encoding="ascii").strip() == "8765"


def test_port_file_failure_is_not_fatal(tmp_path):
    """Yazilamayan klasor uygulamayi durdurmaz, sadece uyari duser."""
    unwritable = tmp_path / "olmayan-klasor" / "holocron.port"
    assert write_port_file(8765, unwritable) is None


def test_browser_prefers_startfile_on_windows(monkeypatch):
    calls = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr("os.startfile", calls.append, raising=False)
    monkeypatch.setattr("webbrowser.open", lambda url: calls.append("webbrowser"))
    assert open_browser("http://127.0.0.1:8765/") is True
    assert calls == ["http://127.0.0.1:8765/"]


def test_browser_falls_back_to_webbrowser_when_startfile_fails(monkeypatch, tmp_path):
    runlog.setup_logging(tmp_path / "holocron.log")
    calls = []

    def boom(url):
        raise OSError("kayitli tarayici yok")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr("os.startfile", boom, raising=False)
    monkeypatch.setattr("webbrowser.open", lambda url: calls.append(url) or True)
    assert open_browser("http://127.0.0.1:8765/") is True
    assert calls == ["http://127.0.0.1:8765/"]


def test_browser_error_never_stops_the_app(monkeypatch, tmp_path):
    runlog.setup_logging(tmp_path / "holocron.log")
    monkeypatch.setattr(sys, "platform", "linux")

    def boom(url):
        raise RuntimeError("tarayici yok")

    monkeypatch.setattr("webbrowser.open", boom)
    assert open_browser("http://127.0.0.1:8765/") is False


def test_console_flag_is_accepted():
    """holocron.bat --console argumani oldugu gibi geciyor, argparse bogulmasin."""
    assert parse_args(["--console"]).console is True
    assert parse_args(["-c"]).console is True
    assert parse_args([]).console is False


# --- Veri klasoru -------------------------------------------------------


def test_home_is_based_on_file_not_cwd(monkeypatch, tmp_path):
    """Calisma dizini nereye giderse gitsin veri klasoru paketin yaninda."""
    monkeypatch.delenv("HOLOCRON_HOME", raising=False)
    paths.forget_home()
    monkeypatch.chdir(tmp_path)
    assert paths.home_dir() == Path(paths.__file__).resolve().parent.parent
    paths.forget_home()


def test_home_falls_back_when_the_package_folder_is_read_only(monkeypatch, tmp_path):
    """Program Files ya da zip goruntuleyicisinin gecici klasoru: yazilamaz."""
    monkeypatch.delenv("HOLOCRON_HOME", raising=False)
    paths.forget_home()
    read_only = tmp_path / "salt-okunur"
    read_only.mkdir()
    fallback = tmp_path / "yedek"
    monkeypatch.setattr(paths, "base_dir", lambda: read_only)
    monkeypatch.setattr(paths, "_is_writable", lambda base: False)
    monkeypatch.setattr(paths, "fallback_dir", lambda: fallback)
    assert paths.home_dir() == fallback
    assert fallback.exists()
    paths.forget_home()


def test_log_and_port_files_live_in_the_data_directory(isolated_home):
    assert paths.log_path() == isolated_home / "holocron.log"
    assert paths.port_path() == isolated_home / "holocron.port"
