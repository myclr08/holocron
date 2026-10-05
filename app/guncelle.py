"""Yazilim guncelleme: GitHub surumunu denetle, yedekle, indir, dogrula, kur.

Akis (Ayarlar -> Guncelleme -> "Guncelle"):

1. **Yedek.** `holocron.db` sqlite'in yedekleme API'siyle (WAL'daki yazilar
   dahil, tutarli bir anlik goruntu), `holocron.key` dosya olarak
   `<Belgeler>/holocron/holocron-yedek/<YYYY-MM-DD_HHMMSS>_v<eski>/` altina
   alinir. Son 10 yedek tutulur. Yedek alinamazsa guncelleme BASLAMAZ.
2. **Indir + dogrula.** Kurulumla ayni turdeki zip (Windows'ta Python kurulu
   makine icin lite, embed'li kurulum icin tam paket, Linux'ta Linux paketi)
   ve surumun `SHA256SUMS.txt` dosyasi indirilir. Ozet tutmazsa ya da ozet
   dosyasi yoksa zip KURULMAZ.
3. **Hazirla.** Zip kurulum klasorundeki `.holocron-guncelleme/` altina
   acilir (ayni disk: yer degistirme tasimayla, kopyasiz olur), yeni surum
   numarasi denetlenir.
4. **Devret.** `guncelleyici.py` hazirlama klasorune kopyalanir ve konsolsuz,
   bagimsiz bir surec olarak baslatilir; uygulama kendini kapatir. Dosyalari
   o degistirir, Holocron'u yeniden baslatir, olmazsa eskiye doner.

Ag istekleri Jira ve Ambar ile ayni ag ayarlarindan gecer (vekil sunucu,
ozel CA, IPv4 onceligi). Testler sahte oturum ve sahte baslatici verir.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import requests

from . import __version__, net, paths
from .settings_store import PROXY_DIRECT

log = logging.getLogger("holocron.guncelle")

REPO = "myclr08/holocron"
DEFAULT_API = "https://api.github.com"
# Testler ve demo sahte bir surum sunucusuna yonlendirir.
ENV_API = "HOLOCRON_UPDATE_API"
CHECKSUMS_NAME = "SHA256SUMS.txt"

ASSET_LITE = "holocron-windows-x64-lite.zip"
ASSET_FULL = "holocron-windows-x64.zip"
ASSET_LINUX = "holocron-linux-x64.zip"

KEEP_BACKUPS = 10
STAGING_DIR = ".holocron-guncelleme"
HELPER_NAME = "guncelleyici.py"
LOG_NAME = "guncelleme.log"

# Arka plandaki rozet denetimi en fazla bu siklikla aga cikar.
CHECK_INTERVAL_SECONDS = 12 * 60 * 60
SETTING_LAST_CHECK = "guncelleme.son_denetim"
SETTING_LAST_INFO = "guncelleme.son_bilgi"

# Bir surum zip'i bundan buyuk olamaz (tam paket ~30 MB).
MAX_DOWNLOAD = 300 * 1024 * 1024

STEP_BACKUP = "backup"
STEP_DOWNLOAD = "download"
STEP_VERIFY = "verify"
STEP_EXTRACT = "extract"
STEP_RESTART = "restart"
STEPS: tuple[tuple[str, str], ...] = (
    (STEP_BACKUP, "Veriler yedekleniyor"),
    (STEP_DOWNLOAD, "Yeni sürüm indiriliyor"),
    (STEP_VERIFY, "SHA-256 özeti doğrulanıyor"),
    (STEP_EXTRACT, "Paket hazırlanıyor"),
    (STEP_RESTART, "Holocron yeniden başlatılıyor"),
)

OFFLINE_MESSAGE = (
    "GitHub'a ulaşılamadı: ağ bağlantısı yok ya da vekil sunucu geçirmiyor. "
    "Ayarlar → Ağ bölümünü kontrol edip yeniden deneyin."
)


class UpdateError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


# --- surum karsilastirma ------------------------------------------------


_VERSION = re.compile(r"^\s*v?(\d+(?:\.\d+)*)(?:[-+.]?([0-9A-Za-z.\-]+))?\s*$")


def parse_version(text: Any) -> tuple[tuple[int, ...], bool]:
    """`"v0.16.0"` -> `((0, 16, 0), False)`; ikinci deger "on surum mu".

    Bilinmeyen bicim `((), True)` doner ve hicbir surumden yeni sayilmaz.
    """
    match = _VERSION.match(str(text or ""))
    if not match:
        return (), True
    numbers = tuple(int(part) for part in match.group(1).split("."))
    while len(numbers) > 1 and numbers[-1] == 0:
        numbers = numbers[:-1]
    return numbers, bool(match.group(2))


def is_newer(candidate: Any, current: Any) -> bool:
    """`candidate` `current`tan yeni mi? On surum (`-rc1`) ayni numarali
    kararli surumden eskidir; bicimi okunamayan surum asla yeni degildir."""
    new_numbers, new_pre = parse_version(candidate)
    old_numbers, old_pre = parse_version(current)
    if not new_numbers:
        return False
    if not old_numbers:
        return True
    if new_numbers != old_numbers:
        return new_numbers > old_numbers
    return old_pre and not new_pre


def clean_version(text: Any) -> str:
    return str(text or "").strip().lstrip("vV")


# --- kurulum turu ----------------------------------------------------------


def asset_for_install(install: Path, platform: str | None = None) -> str:
    """Kurulumla ayni turdeki zip: lite, tam (embed'li) ya da Linux."""
    name = platform or sys.platform
    if name.startswith("win"):
        return ASSET_FULL if (install / "python-embed").is_dir() else ASSET_LITE
    return ASSET_LINUX


# --- ag ------------------------------------------------------------------


def api_base() -> str:
    return (os.environ.get(ENV_API) or DEFAULT_API).rstrip("/")


def network_for(settings: Any, url: str) -> tuple[requests.Session, dict[str, str] | None, bool | str]:
    """Ambar'in GitHub istemcisiyle ayni karar: Ambar vekili > Ag ayarlari."""
    from urllib.parse import urlparse

    from .ambar import SETTING_PROXY

    config = settings.jira_config()
    own_proxy = (settings.get(SETTING_PROXY, "") or "").strip()
    if own_proxy:
        proxies: dict[str, str] | None = {"http": own_proxy, "https": own_proxy}
        trust_env = True
    else:
        mapped, _source = net.effective_proxies(
            urlparse(url).hostname or "",
            mode=config.proxy_mode,
            proxy_http=config.proxy_http,
            proxy_https=config.proxy_https,
            no_proxy=config.no_proxy,
        )
        proxies = mapped or None
        trust_env = config.proxy_mode != PROXY_DIRECT
    verify: bool | str = False if not config.verify_ssl else (config.ca_file or True)
    session = net.build_session(ipv4_first=config.ipv4_first, trust_env=trust_env)
    return session, proxies, verify


@dataclass
class Fetcher:
    """GET istekleri; testler `session` olarak sahte bir oturum verir."""

    session: Any
    proxies: dict[str, str] | None = None
    verify: bool | str = True
    timeout: Any = net.DEFAULT_TIMEOUT

    def get(self, url: str, *, stream: bool = False, accept: str = "") -> Any:
        headers = {"User-Agent": f"Holocron/{__version__}"}
        if accept:
            headers["Accept"] = accept
        try:
            response = self.session.get(
                url, headers=headers, stream=stream, timeout=self.timeout,
                proxies=self.proxies, verify=self.verify,
            )
        except requests.exceptions.SSLError as exc:
            raise UpdateError(
                "ssl_error",
                "GitHub'a SSL doğrulaması başarısız: kurum kök sertifikası eksik olabilir "
                "(Ayarlar → Ağ → Özel CA dosyası).",
            ) from exc
        except requests.exceptions.RequestException as exc:
            raise UpdateError("offline", OFFLINE_MESSAGE, 503) from exc
        if response.status_code == 404:
            raise UpdateError("no_release", "GitHub'da yayımlanmış bir sürüm bulunamadı.", 404)
        if response.status_code == 403 and response.headers.get("X-RateLimit-Remaining") == "0":
            raise UpdateError("rate_limited", "GitHub istek sınırı doldu; biraz sonra yeniden deneyin.", 429)
        if response.status_code >= 400:
            raise UpdateError("http_error", f"GitHub {response.status_code} döndürdü.", 502)
        return response


def fetcher_for(settings: Any) -> Fetcher:
    session, proxies, verify = network_for(settings, api_base())
    return Fetcher(session=session, proxies=proxies, verify=verify)


# --- denetim -------------------------------------------------------------


def latest_release(fetcher: Fetcher) -> dict[str, Any]:
    response = fetcher.get(f"{api_base()}/repos/{REPO}/releases/latest", accept="application/vnd.github+json")
    try:
        data = response.json()
    except ValueError as exc:
        raise UpdateError("bad_response", "GitHub'dan okunamayan bir cevap geldi.", 502) from exc
    assets = {
        str(item.get("name") or ""): {
            "url": str(item.get("browser_download_url") or ""),
            "size": int(item.get("size") or 0),
        }
        for item in (data.get("assets") or [])
        if isinstance(item, dict)
    }
    return {
        "tag": str(data.get("tag_name") or ""),
        "version": clean_version(data.get("tag_name")),
        "name": str(data.get("name") or data.get("tag_name") or ""),
        "notes": str(data.get("body") or ""),
        "url": str(data.get("html_url") or ""),
        "published_at": str(data.get("published_at") or ""),
        "assets": assets,
    }


def check(settings: Any, fetcher: Fetcher | None = None, install: Path | None = None) -> dict[str, Any]:
    """Son surumu sorar, sonucu ayarlara yazar (rozet icin)."""
    release = latest_release(fetcher or fetcher_for(settings))
    asset = asset_for_install(install or paths.base_dir())
    info = {
        "current": __version__,
        "latest": release["version"],
        "newer": is_newer(release["version"], __version__),
        "notes": release["notes"],
        "url": release["url"],
        "name": release["name"],
        "published_at": release["published_at"],
        "asset": asset,
        "asset_ready": asset in release["assets"],
        "checksums_ready": CHECKSUMS_NAME in release["assets"],
        "checked_at": now_iso(),
    }
    try:
        settings.set(SETTING_LAST_CHECK, info["checked_at"])
        settings.set(SETTING_LAST_INFO, json.dumps({k: info[k] for k in ("latest", "name", "url")}))
    except Exception:  # noqa: BLE001 - onbellek ikincil
        log.warning("guncelleme: denetim sonucu yazilamadi", exc_info=True)
    return info


def cached(settings: Any) -> dict[str, Any]:
    """Son denetimin ozeti; aga cikmaz."""
    try:
        info = json.loads(settings.get(SETTING_LAST_INFO, "") or "{}")
    except ValueError:
        info = {}
    latest = str(info.get("latest") or "")
    return {
        "current": __version__,
        "latest": latest,
        "newer": bool(latest) and is_newer(latest, __version__),
        "checked_at": settings.get(SETTING_LAST_CHECK, "") or "",
        "url": str(info.get("url") or ""),
    }


def check_due(settings: Any, now: datetime | None = None) -> bool:
    stamp = settings.get(SETTING_LAST_CHECK, "") or ""
    if not stamp:
        return True
    try:
        last = datetime.fromisoformat(stamp)
    except ValueError:
        return True
    moment = now or datetime.now(timezone.utc)
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)
    return (moment - last).total_seconds() >= CHECK_INTERVAL_SECONDS


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# --- yedek -----------------------------------------------------------------


def backup_name(old_version: str, moment: datetime | None = None) -> str:
    stamp = (moment or datetime.now()).strftime("%Y-%m-%d_%H%M%S")
    return f"{stamp}_v{clean_version(old_version)}"


def backup_data(
    db_file: Path,
    key_file: Path,
    old_version: str,
    root: Path | None = None,
    moment: datetime | None = None,
    keep: int = KEEP_BACKUPS,
) -> Path:
    """Veritabani + anahtar yedegi. Herhangi bir sorun `UpdateError` olur.

    Veritabani `sqlite3.Connection.backup` ile kopyalanir: WAL kipinde ham
    dosya kopyasi son yazilari kaciribilir ya da yarim sayfa tasiyabilir.
    Yedek bittiginde `PRAGMA integrity_check` ile okunur.
    """
    target_root = root or paths.backup_dir()
    target = target_root / backup_name(old_version, moment)
    try:
        target.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        target = target_root / (target.name + f"_{int(time.time() * 1000) % 1000:03d}")
        try:
            target.mkdir(parents=True, exist_ok=False)
        except OSError as exc:
            raise UpdateError("backup_failed", f"Yedek klasörü açılamadı: {exc}", 500) from exc
    except OSError as exc:
        raise UpdateError("backup_failed", f"Yedek klasörü açılamadı: {exc}", 500) from exc

    try:
        if not db_file.exists():
            raise UpdateError("backup_failed", "Veritabanı dosyası bulunamadı, yedek alınamadı.", 500)
        # URI kullanilmaz: yoldaki bosluk, "#" ya da "%" URI'yi bozar.
        source = sqlite3.connect(str(db_file))
        try:
            dest = sqlite3.connect(str(target / db_file.name))
            try:
                source.backup(dest)
                row = dest.execute("PRAGMA integrity_check").fetchone()
                if not row or row[0] != "ok":
                    raise UpdateError("backup_failed", "Yedek veritabanı doğrulanamadı.", 500)
            finally:
                dest.close()
        finally:
            source.close()
        if key_file.exists():
            shutil.copy2(key_file, target / key_file.name)
    except UpdateError:
        shutil.rmtree(target, ignore_errors=True)
        raise
    except (OSError, sqlite3.Error) as exc:
        shutil.rmtree(target, ignore_errors=True)
        raise UpdateError("backup_failed", f"Yedek alınamadı: {exc}", 500) from exc

    rotate_backups(target_root, keep)
    return target


_BACKUP_DIR = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}_v")


def rotate_backups(root: Path, keep: int = KEEP_BACKUPS) -> list[Path]:
    """En yeni `keep` yedek kalir; yalnizca bizim adlandirdigimiz klasorler silinir."""
    if not root.exists():
        return []
    ours = sorted(
        (entry for entry in root.iterdir() if entry.is_dir() and _BACKUP_DIR.match(entry.name)),
        key=lambda entry: entry.name,
    )
    removed: list[Path] = []
    for old in ours[: max(0, len(ours) - keep)]:
        shutil.rmtree(old, ignore_errors=True)
        removed.append(old)
    return removed


# --- indirme ve dogrulama --------------------------------------------------


def parse_checksums(text: str) -> dict[str, str]:
    """`sha256sum` ciktisi: `<64 hex>  <ad>` (ikili kipte ad `*` ile baslar)."""
    result: dict[str, str] = {}
    for line in text.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2 or not re.fullmatch(r"[0-9a-fA-F]{64}", parts[0]):
            continue
        name = parts[1].strip().lstrip("*").split("/")[-1]
        result[name] = parts[0].lower()
    return result


def download(
    fetcher: Fetcher,
    url: str,
    target: Path,
    progress: Callable[[int, int], None] | None = None,
    max_size: int = MAX_DOWNLOAD,
) -> str:
    """Dosyayi indirir, SHA-256 ozetini dondurur."""
    target.parent.mkdir(parents=True, exist_ok=True)
    response = fetcher.get(url, stream=True)
    total = int(response.headers.get("Content-Length") or 0)
    digest = hashlib.sha256()
    done = 0
    try:
        with open(target, "wb") as handle:
            for chunk in response.iter_content(chunk_size=256 * 1024):
                if not chunk:
                    continue
                done += len(chunk)
                if done > max_size:
                    raise UpdateError("too_large", "İndirilen dosya beklenenden büyük; durduruldu.", 502)
                digest.update(chunk)
                handle.write(chunk)
                if progress:
                    progress(done, total)
    except requests.exceptions.RequestException as exc:
        raise UpdateError("offline", "İndirme yarıda kesildi: " + OFFLINE_MESSAGE, 503) from exc
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()
    return digest.hexdigest()


def verify_checksum(name: str, actual: str, checksums: dict[str, str]) -> None:
    expected = checksums.get(name)
    if not expected:
        raise UpdateError(
            "checksum_missing",
            f"{CHECKSUMS_NAME} içinde {name} yok: doğrulanamayan paket kurulmaz.",
            502,
        )
    if expected.lower() != actual.lower():
        raise UpdateError(
            "checksum_mismatch",
            "İndirilen paketin SHA-256 özeti tutmuyor: dosya bozuk ya da değiştirilmiş. "
            "Kurulmadı.",
            502,
        )


def extract(zip_path: Path, target: Path) -> Path:
    """Zip'i acar, paket kokunu (`holocron/`) dondurur. Yol kacirma reddedilir."""
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    root = target.resolve()
    try:
        with zipfile.ZipFile(zip_path) as archive:
            for member in archive.infolist():
                name = member.filename.replace("\\", "/")
                destination = (root / name).resolve()
                if name.startswith("/") or ".." in name.split("/") or (
                    destination != root and root not in destination.parents
                ):
                    raise UpdateError("bad_package", f"Pakette güvensiz yol: {member.filename}", 502)
            archive.extractall(root)
    except zipfile.BadZipFile as exc:
        raise UpdateError("bad_package", "İndirilen dosya geçerli bir zip değil.", 502) from exc
    package = root / "holocron"
    if not package.is_dir():
        entries = [entry for entry in root.iterdir() if entry.is_dir()]
        package = entries[0] if len(entries) == 1 else root
    if not (package / "app" / "__init__.py").exists() or not (package / "holocron_run.py").exists():
        raise UpdateError("bad_package", "Paket beklenen Holocron dosyalarını içermiyor.", 502)
    # Yardimci kendi .venv'ini getirmeyen pakete guvenir; olur da gelirse
    # o .venv CI makinesinin yollarini tasir, kullanilmaz.
    return package


def package_version(package: Path) -> str:
    text = (package / "app" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    return match.group(1) if match else ""


# --- yardimci sureci ----------------------------------------------------------


def staging_root(install: Path) -> Path:
    return install / STAGING_DIR


def helper_python(install: Path, staging: Path, executable: str | None = None) -> str:
    """Yardimciyi calistiracak yorumlayici.

    Kurulum `python-embed/` ile geldiyse yorumlayicinin kendisi degisecek
    klasordedir ve Windows calisan dosyanin klasorunu tasitmaz: embed bir
    kez hazirlama klasorune kopyalanir, yardimci oradan calisir. `.venv`
    hic degismedigi icin dogrudan kullanilir.
    """
    exe = Path(executable or sys.executable)
    embed = install / "python-embed"
    try:
        inside = embed.is_dir() and embed.resolve() in exe.resolve().parents
    except OSError:
        inside = False
    if not inside:
        return str(exe)
    runtime = staging / "runtime"
    if runtime.exists():
        shutil.rmtree(runtime)
    shutil.copytree(embed, runtime)
    return str(runtime / exe.name)


def spawn_helper(python: str, helper: Path, plan_file: Path) -> None:
    """Konsolsuz, uygulamadan bagimsiz surec. Uygulama kapaninca yasamaya devam eder."""
    command = [python, "-I", str(helper), "--plan", str(plan_file)]
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "cwd": str(helper.parent),
        "close_fds": True,
    }
    if sys.platform.startswith("win"):
        base = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        breakaway = 0x01000000  # CREATE_BREAKAWAY_FROM_JOB
        try:
            subprocess.Popen(command, creationflags=base | breakaway, **kwargs)  # noqa: S603
            return
        except OSError:
            # Is nesnesi ayrilmaya izin vermiyorsa ayrilmadan baslatilir.
            pass
        subprocess.Popen(command, creationflags=base, **kwargs)  # noqa: S603
        return
    subprocess.Popen(command, start_new_session=True, **kwargs)  # noqa: S603


# --- durum ve ana akis ---------------------------------------------------


@dataclass
class Updater:
    """Tek uygulama icin guncelleme durumu; ayni anda tek is."""

    settings: Any
    shutdown: Callable[[], None]
    fetcher_factory: Callable[[], Fetcher] | None = None
    install: Path = field(default_factory=paths.base_dir)
    home: Path = field(default_factory=paths.home_dir)
    spawner: Callable[[str, Path, Path], None] = spawn_helper
    backup_root: Path | None = None
    shutdown_delay: float = 1.5
    state: str = "idle"
    step: str = ""
    message: str = ""
    error_code: str = ""
    progress: dict[str, int] = field(default_factory=dict)
    target_version: str = ""
    backup_path: str = ""
    done_steps: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    # --- durum ------------------------------------------------------

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "state": self.state,
                "step": self.step,
                "message": self.message,
                "error_code": self.error_code,
                "progress": dict(self.progress),
                "target_version": self.target_version,
                "current": __version__,
                "backup_path": self.backup_path,
                "steps": [
                    {
                        "id": key,
                        "label": label,
                        "done": key in self.done_steps,
                        "active": key == self.step and self.state == "running",
                    }
                    for key, label in STEPS
                ],
                "log": str(self.home / LOG_NAME),
            }

    def _set(self, **values: Any) -> None:
        with self._lock:
            for key, value in values.items():
                setattr(self, key, value)

    def _enter(self, step: str) -> None:
        with self._lock:
            if self.step and self.step not in self.done_steps:
                self.done_steps.append(self.step)
            self.step = step
        log.info("guncelleme: %s", step)

    def _fetcher(self) -> Fetcher:
        return self.fetcher_factory() if self.fetcher_factory else fetcher_for(self.settings)

    # --- on kosullar ------------------------------------------------

    def preflight(self) -> None:
        if (self.install / ".git").exists():
            raise UpdateError(
                "dev_checkout",
                "Bu bir geliştirme kopyası (git deposu): güncellemek için git pull kullanın.",
                409,
            )
        if not paths._is_writable(self.install):  # noqa: SLF001 - ayni paket
            raise UpdateError(
                "not_writable",
                "Holocron klasörüne yazılamıyor; güncelleme kurulamaz. Paketi yazılabilir bir "
                "klasöre taşıyın ya da yeni zip'i elle açın.",
                409,
            )

    # --- baslat -----------------------------------------------------

    def start(self, background: bool = True) -> dict[str, Any]:
        with self._lock:
            if self.state == "running" or self.state == "restarting":
                raise UpdateError("busy", "Güncelleme zaten sürüyor.", 409)
            self.state = "running"
            self.step = ""
            self.message = ""
            self.error_code = ""
            self.progress = {}
            self.done_steps = []
            self.backup_path = ""
        if background:
            self._thread = threading.Thread(target=self._run_safe, name="holocron-guncelle", daemon=True)
            self._thread.start()
        else:
            self._run_safe()
        return self.status()

    def _run_safe(self) -> None:
        try:
            self.run()
        except UpdateError as exc:
            log.warning("guncelleme durdu: %s", exc.code)
            self._set(state="error", message=exc.message, error_code=exc.code)
        except Exception as exc:  # noqa: BLE001 - arayuz takili kalmasin
            log.exception("guncelleme: beklenmeyen hata")
            self._set(state="error", message=f"Beklenmeyen hata: {exc}", error_code="unexpected")

    def run(self) -> None:
        self.preflight()
        fetcher = self._fetcher()
        release = latest_release(fetcher)
        version = release["version"]
        if not is_newer(version, __version__):
            raise UpdateError("up_to_date", f"Zaten en güncel sürüm: {__version__}.", 409)
        asset = asset_for_install(self.install)
        if asset not in release["assets"]:
            raise UpdateError("asset_missing", f"Sürümde {asset} yok; elle indirin.", 502)
        if CHECKSUMS_NAME not in release["assets"]:
            raise UpdateError(
                "checksum_missing",
                f"Sürümde {CHECKSUMS_NAME} yok: doğrulanamayan paket kurulmaz.",
                502,
            )
        self._set(target_version=version)

        # 1) Yedek: olmazsa hicbir sey indirilmez, hicbir dosyaya dokunulmaz.
        self._enter(STEP_BACKUP)
        backup = backup_data(
            self.home / paths.DB_FILENAME,
            self.home / paths.KEY_FILENAME,
            __version__,
            root=self.backup_root,
        )
        self._set(backup_path=str(backup))

        staging = staging_root(self.install)
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        staging.mkdir(parents=True, exist_ok=True)

        # 2) Indirme: once ozet dosyasi (kucuk), sonra zip.
        self._enter(STEP_DOWNLOAD)
        sums_response = fetcher.get(release["assets"][CHECKSUMS_NAME]["url"])
        checksums = parse_checksums(getattr(sums_response, "text", "") or "")
        zip_path = staging / "indirme" / asset

        def progress(done: int, total: int) -> None:
            self._set(progress={"done": done, "total": total or release["assets"][asset]["size"]})

        actual = download(fetcher, release["assets"][asset]["url"], zip_path, progress)

        # 3) Dogrulama: tutmazsa zip silinir, kurulum hic baslamaz.
        self._enter(STEP_VERIFY)
        try:
            verify_checksum(asset, actual, checksums)
        except UpdateError:
            shutil.rmtree(staging, ignore_errors=True)
            raise

        # 4) Hazirlik
        self._enter(STEP_EXTRACT)
        package = extract(zip_path, staging / "yeni")
        found = package_version(package)
        if clean_version(found) != clean_version(version):
            shutil.rmtree(staging, ignore_errors=True)
            raise UpdateError(
                "bad_package", f"Paketteki sürüm ({found}) yayımlanan sürümle ({version}) aynı değil.", 502
            )
        helper = staging / HELPER_NAME
        shutil.copy2(Path(__file__).with_name(HELPER_NAME), helper)
        python = helper_python(self.install, staging)
        plan = {
            "pid": os.getpid(),
            "install": str(self.install),
            "new_root": str(package),
            "old_dir": str(staging / "eski"),
            "staging": str(staging),
            "home": str(self.home),
            "log": str(self.home / LOG_NAME),
            "old_version": __version__,
            "expected_version": clean_version(version),
            "wait_timeout": 90,
            "health_timeout": 300,
        }
        plan_file = staging / "plan.json"
        plan_file.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")

        # 5) Devir: yardimci baslar, uygulama kapanir.
        self._enter(STEP_RESTART)
        self.spawner(python, helper, plan_file)
        self._set(state="restarting", message="Holocron yeniden başlatılıyor; sayfa kendini yenileyecek.")
        log.info("guncelleme: yardimci baslatildi, %s -> %s", __version__, version)
        timer = threading.Timer(self.shutdown_delay, self.shutdown)
        timer.daemon = True
        timer.start()
