"""Giris noktasi: sunucuyu 127.0.0.1'de baslatir, tarayiciyi acar, nabiz olunce kapanir."""

from __future__ import annotations

import argparse
import os
import socket
import threading
import webbrowser

import uvicorn

from . import __version__, paths
from .context import build_context
from .lifecycle import BEAT_TIMEOUT_SECONDS, Heartbeat, Watchdog
from .server import create_app

HOST = "127.0.0.1"
PREFERRED_PORT = 8765


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
    parser.add_argument("--version", action="version", version=f"Holocron {__version__}")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    port = args.port or pick_port()

    heartbeat = Heartbeat(timeout=args.timeout)
    context = build_context(heartbeat=heartbeat)
    app = create_app(context)

    config = uvicorn.Config(app, host=HOST, port=port, log_level="warning", access_log=False)
    server = uvicorn.Server(config)

    # Hem nabiz zaman asimi hem de "Kapat" dugmesi ayni yoldan cikis yapar.
    def request_exit() -> None:
        server.should_exit = True

    context.shutdown_hook = request_exit
    watchdog = Watchdog(heartbeat, request_exit)

    url = f"http://{HOST}:{port}/"
    # pythonw/arka plan kullaniminda cikti tamponlanmasin, adres hemen gorunsun.
    print(f"Holocron {__version__} calisiyor: {url}", flush=True)
    print(f"Veri klasoru: {paths.home_dir()}", flush=True)

    if not args.no_browser and not os.environ.get("HOLOCRON_NO_BROWSER"):
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()

    watchdog.start()
    try:
        server.run()
    finally:
        watchdog.stop()
        context.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
