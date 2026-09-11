"""Dosya logu ve konsolsuz calismaya dayaniklilik.

Windows'ta `pythonw.exe` ile acildiginda konsol yoktur: `sys.stdout` ve
`sys.stderr` `None` olur, cikti gidecek yer kalmaz. Bir istisna cikarsa
surec sessizce olur; kullanici sadece "bir sey olmadi" gorur. Bu modul iki
isi yapar:

1. Her sey `holocron.log` dosyasina yazilir (1 MB x 3, donen dosya).
2. `print` yerine `safe_print`: akis yoksa atlar, kod sayfasi Turkce harfi
   kaldiramazsa `errors="replace"` ile yazar (cp1254/cp850 tuzagi).

`install_excepthook` ana is parcacigini da arka plan is parcaciklarini da
yakalar; yakalanmamis her istisna tam traceback ile loga duser.
"""

from __future__ import annotations

import logging
import logging.handlers
import platform
import sys
import threading
from pathlib import Path

from . import __version__, paths

LOGGER_NAME = "holocron"
MAX_BYTES = 1_000_000
BACKUP_COUNT = 3
FILE_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"

# Bizim taktigimiz isleyiciler isaretlenir: iki kez kurulum yapilirsa
# eskisi temizlensin, log satirlari cift gorunmesin.
_MARK = "_holocron_handler"

# WARNING'in altini hic yazmayan gunlukculer: gurultu ve gizlilik.
QUIET_LOGGERS: tuple[str, ...] = (
    "uvicorn",
    "uvicorn.error",
    "uvicorn.access",
    "urllib3",
    "urllib3.connectionpool",
    "asyncio",
    "charset_normalizer",
)

log = logging.getLogger(LOGGER_NAME)


def usable_stream(stream: object) -> object | None:
    """Yazilabilir bir akis mi? pythonw altinda `None` gelir."""
    if stream is None:
        return None
    if not hasattr(stream, "write"):
        return None
    closed = getattr(stream, "closed", False)
    if closed:
        return None
    return stream


def safe_print(message: str, stream: object | None = None) -> bool:
    """Konsola yazmayi dener; konsol yoksa ya da kodlama tutmazsa cokmez.

    Donus: gercekten yazildi mi. Cagiran taraf ayrica loga yazar, boylece
    mesaj hicbir zaman kaybolmaz.
    """
    target = usable_stream(stream if stream is not None else sys.stdout)
    if target is None:
        return False
    try:
        target.write(message + "\n")
        target.flush()
        return True
    except UnicodeEncodeError:
        encoding = getattr(target, "encoding", None) or "ascii"
        try:
            fallback = message.encode(encoding, errors="replace").decode(encoding, errors="replace")
            target.write(fallback + "\n")
            target.flush()
            return True
        except Exception:  # noqa: BLE001 - konsol yuzunden asla cokmeyelim
            return False
    except Exception:  # noqa: BLE001 - kapali/kirik akis, OSError, ValueError...
        return False


def announce(message: str, level: int = logging.INFO) -> None:
    """Hem loga hem (varsa) konsola: tek satir, iki hedef."""
    log.log(level, message)
    safe_print(message)


def _clear_handlers(target: logging.Logger) -> None:
    for handler in list(target.handlers):
        if getattr(handler, _MARK, False):
            target.removeHandler(handler)
            try:
                handler.close()
            except Exception:  # noqa: BLE001
                pass


def setup_logging(
    path: Path | str | None = None,
    level: int = logging.INFO,
    console: bool = True,
) -> Path:
    """Kok gunlukcuyu donen dosyaya baglar, dosya yolunu dondurur."""
    target = Path(path) if path is not None else paths.log_path()
    target.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    _clear_handlers(root)
    root.setLevel(min(level, logging.WARNING))

    file_handler = logging.handlers.RotatingFileHandler(
        target,
        maxBytes=MAX_BYTES,
        backupCount=BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(logging.Formatter(FILE_FORMAT))
    setattr(file_handler, _MARK, True)
    root.addHandler(file_handler)

    # Konsol varsa yalnizca uyari ve ustu: ekran temiz kalsin, ayrinti dosyada.
    stream = usable_stream(sys.stderr)
    if console and stream is not None:
        console_handler = logging.StreamHandler(stream)
        console_handler.setLevel(logging.WARNING)
        console_handler.setFormatter(logging.Formatter("[holocron] %(levelname)s: %(message)s"))
        setattr(console_handler, _MARK, True)
        root.addHandler(console_handler)

    # uvicorn kendi bicimlendiricisini kurmuyor (log_config=None); kendi
    # gunlukculeri kokten gecsin, gurultu WARNING'de kessin.
    #
    # urllib3 ayni sebeple degil, gizlilik icin kisiliyor: DEBUG seviyesinde
    # "Starting new HTTPS connection (1): jira.kurum.local:443" yaziyor ve
    # kullanici sorun bildirirken log dosyasini oldugu gibi paylasiyor. Kurum
    # adresi oradan disari sizmasin. Kendi gunlukcumuz INFO'da kalir.
    for name in QUIET_LOGGERS:
        logger = logging.getLogger(name)
        logger.setLevel(logging.WARNING)
        logger.propagate = True

    return target


def log_startup(port: int, url: str, home: Path | str) -> None:
    """Ilk satir: hangi surum, hangi Python, hangi makine, nerede."""
    log.info(
        "Holocron %s basliyor | python %s (%s) | %s %s | port %s | url %s | veri %s | frozen %s",
        __version__,
        platform.python_version(),
        sys.executable,
        platform.system(),
        platform.release(),
        port,
        url,
        home,
        bool(getattr(sys, "frozen", False)),
    )
    log.info(
        "Konsol durumu: stdout=%s stderr=%s",
        "var" if usable_stream(sys.stdout) else "yok",
        "var" if usable_stream(sys.stderr) else "yok",
    )


def install_excepthook() -> None:
    """Yakalanmamis istisnalar (ana is parcacigi ve digerleri) loga dussun."""
    previous = sys.excepthook

    def hook(exc_type, exc_value, exc_tb) -> None:  # type: ignore[no-untyped-def]
        if issubclass(exc_type, KeyboardInterrupt):
            previous(exc_type, exc_value, exc_tb)
            return
        log.critical("Yakalanmamis hata", exc_info=(exc_type, exc_value, exc_tb))
        if usable_stream(sys.stderr) is not None:
            previous(exc_type, exc_value, exc_tb)

    sys.excepthook = hook

    previous_thread_hook = threading.excepthook

    def thread_hook(args) -> None:  # type: ignore[no-untyped-def]
        if issubclass(args.exc_type, SystemExit):
            return
        log.critical(
            "Yakalanmamis hata (%s is parcacigi)",
            getattr(args.thread, "name", "?"),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )
        if usable_stream(sys.stderr) is not None:
            previous_thread_hook(args)

    threading.excepthook = thread_hook


def tail(path: Path | str, lines: int = 40) -> str:
    """Log dosyasinin son satirlari (sorun bildirimi kolaylassin diye)."""
    try:
        content = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(content.splitlines()[-lines:])
