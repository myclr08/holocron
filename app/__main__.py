"""Giris noktasi: sunucuyu 127.0.0.1'de baslatir, tarayiciyi acar, nabiz olunce kapanir.

Windows'ta bu dosya `pythonw.exe` ile calisir: konsol yoktur, `sys.stdout` ve
`sys.stderr` `None`'dir. Bu yuzden (1) her sey once `holocron.log` dosyasina
yazilir, (2) uvicorn'un kendi log yapilandirmasi devre disi birakilir
(uvicorn 0.34 `sys.stdout.isatty()` cagirdigi icin konsolsuz baslangicta
`AttributeError` verip surec daha sunucu kalkmadan oluyordu), (3) yakalanmamis
her istisna tam traceback ile loga duser.
"""

from __future__ import annotations

import argparse
import logging
import os
import socket
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn

from . import __version__, paths, runlog
from .context import build_context
from .lifecycle import BEAT_TIMEOUT_SECONDS, Heartbeat, Watchdog
from .runlog import announce, safe_print
from .server import create_app

HOST = "127.0.0.1"
PREFERRED_PORT = 8765

log = logging.getLogger("holocron.main")


def pick_port(preferred: int = PREFERRED_PORT, host: str = HOST) -> int:
    """Once tercih edilen portu dener; doluysa isletim sisteminden bos port ister."""
    for candidate in (preferred, 0):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind((host, candidate))
            except OSError:
                continue
            return sock.getsockname()[1]
    raise RuntimeError("Bos port bulunamadi.")


def write_port_file(port: int, path: Path | None = None) -> Path | None:
    """Secilen portu diske yazar: baslatici betik nereye bakacagini bilsin."""
    target = path or paths.port_path()
    try:
        target.write_text(f"{port}\n", encoding="ascii")
    except OSError:
        log.warning("Port dosyasi yazilamadi: %s", target, exc_info=True)
        return None
    return target


def clear_port_file(path: Path | None) -> None:
    if path is None:
        return
    try:
        path.unlink()
    except OSError:
        pass


def open_browser(url: str) -> bool:
    """Varsayilan tarayiciyi acar.

    Windows'ta `pythonw` altinda `webbrowser.open` sessizce basarisiz
    olabiliyor (konsol tutamaci olmayan surecte bazi kayitli tarayici
    komutlari calismiyor); orada once `os.startfile` denenir.
    """
    if sys.platform.startswith("win"):
        starter = getattr(os, "startfile", None)
        if starter is not None:
            try:
                starter(url)
                return True
            except OSError:
                log.warning("os.startfile ile tarayici acilamadi", exc_info=True)
    try:
        if webbrowser.open(url):
            return True
        log.warning("Tarayici acilamadi, adresi elle acin: %s", url)
    except Exception:  # noqa: BLE001 - tarayici yuzunden uygulama olmesin
        log.warning("Tarayici acilirken hata", exc_info=True)
    return False


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="holocron", description="Yerel Jira kayit takip araci")
    parser.add_argument("--port", type=int, default=None, help="Sabit port (varsayilan: 8765 ya da bos port)")
    parser.add_argument("--no-browser", action="store_true", help="Tarayiciyi acma")
    parser.add_argument(
        "--timeout",
        type=float,
        default=BEAT_TIMEOUT_SECONDS,
        help="Nabiz kesilince kapanma suresi (saniye)",
    )
    # holocron.bat --console bunu oldugu gibi gecirir; burada ayrica log
    # ayrintisini artirir, hata ekranda da kalir.
    parser.add_argument(
        "--console",
        "-c",
        action="store_true",
        help="Konsolda calis, ayrintili log bas (sorun giderme)",
    )
    parser.add_argument("--log-file", type=Path, default=None, help="Log dosyasi yolu")
    parser.add_argument("--version", action="version", version=f"Holocron {__version__}")
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> int:
    """Asil is. Butun istisnalar `main` tarafindan loglanir."""
    port = args.port or pick_port()
    url = f"http://{HOST}:{port}/"
    home = paths.home_dir()

    runlog.log_startup(port=port, url=url, home=home)

    heartbeat = Heartbeat(timeout=args.timeout)
    context = build_context(heartbeat=heartbeat)
    app = create_app(context)

    # log_config=None: uvicorn logging'e hic dokunmasin. Varsayilan
    # yapilandirmasi konsolsuz surecte (pythonw) `sys.stdout.isatty()`
    # cagirip patliyor. Kendi gunlukculeri kokteki dosya isleyicisine duser.
    config = uvicorn.Config(
        app,
        host=HOST,
        port=port,
        log_level="warning",
        access_log=False,
        log_config=None,
        use_colors=False,
    )
    server = uvicorn.Server(config)

    # Hem nabiz zaman asimi hem de "Kapat" dugmesi ayni yoldan cikis yapar.
    def request_exit() -> None:
        server.should_exit = True

    context.shutdown_hook = request_exit
    watchdog = Watchdog(heartbeat, request_exit)

    announce(f"Holocron {__version__} calisiyor: {url}")
    announce(f"Veri klasoru: {home}")
    announce(f"Log dosyasi: {paths.log_path()}")

    port_file = write_port_file(port)

    if not args.no_browser and not os.environ.get("HOLOCRON_NO_BROWSER"):
        threading.Timer(0.8, lambda: open_browser(url)).start()

    watchdog.start()
    try:
        server.run()
    finally:
        watchdog.stop()
        context.close()
        clear_port_file(port_file)
        log.info("Holocron kapandi.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    log_file = runlog.setup_logging(
        path=args.log_file,
        level=logging.DEBUG if args.console else logging.INFO,
        console=True,
    )
    runlog.install_excepthook()

    try:
        return run(args)
    except SystemExit:
        raise
    except KeyboardInterrupt:
        log.info("Kullanici kapatti (Ctrl+C).")
        return 0
    except BaseException:
        # Sessiz cokme burada biter: tam traceback dosyaya, ozet konsola.
        log.critical("Holocron baslatilamadi", exc_info=True)
        safe_print(f"[holocron] Hata: ayrinti icin {log_file}", stream=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
