"""Guncelleme yardimcisi: uygulama kapandiktan sonra program dosyalarini degistirir.

Neden ayri bir surec: Windows calisan uygulamanin kendi dosyalarinin uzerine
yazmasina izin vermez. Uygulama yeni surumu hazirlama klasorune acar, bu
dosyayi oraya kopyalar ve konsolsuz, bagimsiz bir surec olarak baslatir;
sonra kendini kapatir. Bu betik:

1. Uygulama surecinin cikmasini bekler (PID ile).
2. Yeni paketin KOKUNDE bulunan program girdilerini (``app/``, ``wheels/``,
   ``holocron.bat``...) tek tek yerine koyar; eskisini once kenara tasir.
   Paketin getirmedigi hicbir seye dokunulmaz. Kullanici verisi (veritabani,
   anahtar, log, port dosyasi, ``.venv``) pakette olsa bile korunur.
3. Holocron'u ``holocron.bat`` (Linux/macOS'ta ``holocron.sh``) ile yeniden
   baslatir: bagimlilik esitlemesi (``requirements.txt`` ozeti degistiyse
   ``wheels/`` icinden pip kurulumu) baslaticinin kendi isidir.
4. Yeni surum ``/api/health`` uzerinden beklenen surumle cevap vermezse eski
   dosyalari geri koyar ve eski surumu baslatir.

YALNIZCA standart kitaplik kullanir ve ``app`` paketini ice aktarmaz: kendi
kopyasi hazirlama klasorunden calisir, ``app/`` klasoru o sirada degisiyor.
Her adim guncelleme loguna yazilir.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

# Paket icinde gelse bile asla degistirilmeyen adlar: kullanici verisi ve
# makineye ozgu sanal ortam. `.venv` zip'ten gelmez; gelseydi bile CI
# makinesinin mutlak yollarini tasirdi. Bagimliliklar baslatici tarafindan
# yeni `requirements.txt` + `wheels/` ile esitlenir.
PROTECTED = frozenset(
    name.casefold()
    for name in (
        "holocron.db",
        "holocron.db-wal",
        "holocron.db-shm",
        "holocron.db-journal",
        "holocron.key",
        "holocron.log",
        "holocron.port",
        ".venv",
        "venv",
        ".holocron-guncelleme",
        "holocron-belgeler",
        "holocron-yedek",
        "guncelleme.log",
    )
)


def is_protected(name: str) -> bool:
    lowered = name.casefold()
    # Dondurulmus log dosyalari (holocron.log.1 ...) de kullanicinindir.
    return lowered in PROTECTED or lowered.startswith("holocron.log")


class Logger:
    def __init__(self, path: Path | None) -> None:
        self.path = path

    def __call__(self, message: str) -> None:
        line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {message}"
        if self.path is None:
            print(line)
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.path, "a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            pass


# --- surec bekleme ----------------------------------------------------------


def _pid_alive_posix(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _wait_windows(pid: int, timeout: float) -> bool:
    import ctypes

    synchronize = 0x00100000
    kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    handle = kernel32.OpenProcess(synchronize, False, int(pid))
    if not handle:
        return True  # surec yok: cikmis
    try:
        result = kernel32.WaitForSingleObject(handle, int(max(0.0, timeout) * 1000))
        return result == 0  # WAIT_OBJECT_0
    finally:
        kernel32.CloseHandle(handle)


def wait_for_exit(pid: int, timeout: float = 90.0, poll: float = 0.25) -> bool:
    """Surec cikinca `True`; zaman asiminda `False`."""
    if pid <= 0:
        return True
    if sys.platform.startswith("win"):
        return _wait_windows(pid, timeout)
    deadline = time.monotonic() + timeout
    while _pid_alive_posix(pid):
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll)
    return True


# --- degistirme ve geri alma ---------------------------------------------


def _remove(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.exists():
        shutil.rmtree(path)


def _move(source: Path, target: Path, attempts: int = 20, pause: float = 0.5) -> None:
    """`os.replace` + kisa yeniden deneme.

    Windows'ta virus tarayicisi ya da dizin indeksleyici bir dosyayi bir an
    tutabiliyor; "erisim engellendi" cogu zaman yarim saniye sonra kalkar.
    """
    last: OSError | None = None
    for _ in range(max(1, attempts)):
        try:
            os.replace(source, target)
            return
        except OSError as exc:
            last = exc
            time.sleep(pause)
    assert last is not None
    raise last


def plan_entries(new_root: Path) -> list[str]:
    """Yeni paketin kokundeki, degistirilecek girdiler (korunanlar haric)."""
    return sorted(entry.name for entry in new_root.iterdir() if not is_protected(entry.name))


def apply_update(
    install: Path,
    new_root: Path,
    old_dir: Path,
    log: Callable[[str], None] = print,
    move: Callable[[Path, Path], None] = _move,
) -> list[str]:
    """Program girdilerini yeni paketten yerine koyar; dondurdugu liste
    degistirilen adlardir (geri alma icin).

    Bir adim bile basarisiz olursa o ana kadar yapilan her sey geri alinir ve
    istisna yeniden firlatilir: kurulum ya tamamen yeni ya tamamen eski kalir.
    """
    old_dir.mkdir(parents=True, exist_ok=True)
    names = plan_entries(new_root)
    replaced: list[str] = []
    try:
        for name in names:
            target = install / name
            if target.exists() or target.is_symlink():
                move(target, old_dir / name)
            replaced.append(name)
            move(new_root / name, target)
            log(f"yerine kondu: {name}")
    except Exception as exc:
        log(f"HATA: {name} yerine konamadi ({exc}); geri aliniyor")
        rollback(install, old_dir, replaced, log, move)
        raise
    return replaced


def rollback(
    install: Path,
    old_dir: Path,
    replaced: list[str],
    log: Callable[[str], None] = print,
    move: Callable[[Path, Path], None] = _move,
) -> bool:
    """Kenara alinan eski girdileri geri koyar. Hepsi donerse `True`."""
    ok = True
    for name in reversed(replaced):
        target = install / name
        saved = old_dir / name
        try:
            if saved.exists() or saved.is_symlink():
                if target.exists() or target.is_symlink():
                    _remove(target)
                move(saved, target)
                log(f"geri kondu: {name}")
            elif target.exists():
                # Eski surumde olmayan yeni girdi: kaldirilir.
                _remove(target)
                log(f"yeni girdi kaldirildi: {name}")
        except OSError as exc:
            ok = False
            log(f"HATA: {name} geri konamadi ({exc}); eski kopya burada: {saved}")
    return ok


# --- yeniden baslatma ve saglik --------------------------------------------


def restart_command(install: Path, platform: str | None = None) -> list[str]:
    """Holocron'u kullanicinin baslattigi yolla yeniden baslatan komut."""
    name = platform or sys.platform
    if name.startswith("win"):
        comspec = os.environ.get("COMSPEC") or "cmd.exe"
        return [comspec, "/c", str(install / "holocron.bat")]
    # holocron.sh `pipefail` kullanir: dash gibi yalin sh'lar onu tanimaz.
    shell = shutil.which("bash") or "/bin/sh"
    return [shell, str(install / "holocron.sh")]


def restart(install: Path, command: list[str], log_path: Path | None, log: Callable[[str], None]) -> bool:
    env = dict(os.environ)
    # Arayuz zaten acik: yeni surum ikinci bir sekme acmasin, acik sekme
    # kendini yeniler.
    env["HOLOCRON_NO_BROWSER"] = "1"
    env.pop("PYTHONPATH", None)
    kwargs: dict[str, Any] = {"cwd": str(install), "env": env, "stdin": subprocess.DEVNULL}
    sink = None
    if log_path is not None:
        try:
            sink = open(log_path, "a", encoding="utf-8")  # noqa: SIM115 - surece devredilir
            kwargs["stdout"] = sink
            kwargs["stderr"] = subprocess.STDOUT
        except OSError:
            sink = None
    if sink is None:
        kwargs["stdout"] = subprocess.DEVNULL
        kwargs["stderr"] = subprocess.DEVNULL
    if sys.platform.startswith("win"):
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
            subprocess, "CREATE_NEW_PROCESS_GROUP", 0
        )
    else:
        kwargs["start_new_session"] = True
    try:
        subprocess.Popen(command, **kwargs)  # noqa: S603 - sabit baslatici
        log("yeniden baslatildi: " + " ".join(command))
        return True
    except OSError as exc:
        log(f"HATA: baslatilamadi ({exc})")
        return False
    finally:
        if sink is not None:
            sink.close()


def read_port(home: Path) -> int | None:
    try:
        text = (home / "holocron.port").read_text(encoding="ascii").strip()
    except (OSError, UnicodeDecodeError):
        return None
    return int(text) if text.isdigit() else None


def probe_version(port: int, timeout: float = 3.0) -> str | None:
    url = f"http://127.0.0.1:{port}/api/health"
    try:
        # Yerel adres: kurumsal vekil sunucu araya girmesin.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=timeout) as response:  # noqa: S310 - 127.0.0.1
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    return str(data.get("version") or "") or None


def wait_healthy(
    home: Path,
    expected: str,
    timeout: float,
    log: Callable[[str], None],
    poll: float = 1.0,
    probe: Callable[[int], str | None] = probe_version,
) -> bool:
    deadline = time.monotonic() + timeout
    seen = ""
    while time.monotonic() < deadline:
        port = read_port(home)
        if port:
            version = probe(port)
            if version and version != seen:
                seen = version
                log(f"saglik: port {port}, surum {version}")
            if version == expected:
                return True
        time.sleep(poll)
    log(f"HATA: {timeout:.0f} sn icinde {expected} surumu cevap vermedi")
    return False


def request_shutdown(home: Path) -> None:
    port = read_port(home)
    if not port:
        return
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request = urllib.request.Request(f"http://127.0.0.1:{port}/api/shutdown", method="POST", data=b"")
        with opener.open(request, timeout=3):  # noqa: S310 - 127.0.0.1
            pass
    except OSError:
        pass


# --- ana akis ----------------------------------------------------------------


def run(plan: dict[str, Any], log: Callable[[str], None]) -> int:
    install = Path(plan["install"])
    new_root = Path(plan["new_root"])
    old_dir = Path(plan["old_dir"])
    home = Path(plan["home"])
    log_path = Path(plan["log"]) if plan.get("log") else None
    expected = str(plan["expected_version"])
    command = list(plan.get("restart") or restart_command(install))
    log(f"guncelleme basladi: {plan.get('old_version')} -> {expected}")

    if not wait_for_exit(int(plan.get("pid") or 0), float(plan.get("wait_timeout") or 90)):
        log("HATA: uygulama kapanmadi; hicbir dosyaya dokunulmadi")
        return 2

    try:
        replaced = apply_update(install, new_root, old_dir, log)
    except Exception:  # noqa: BLE001 - geri alindi, eski surum acilir
        restart(install, command, log_path, log)
        log("SONUC: guncelleme kurulamadi, eski surum yeniden baslatildi")
        return 1

    restart(install, command, log_path, log)
    if wait_healthy(home, expected, float(plan.get("health_timeout") or 240), log):
        shutil.rmtree(old_dir, ignore_errors=True)
        staging = plan.get("staging")
        if staging:
            shutil.rmtree(Path(staging) / "yeni", ignore_errors=True)
            shutil.rmtree(Path(staging) / "indirme", ignore_errors=True)
        log(f"SONUC: {expected} calisiyor")
        return 0

    # Yeni surum ayaga kalkmadi: kapatilir, eski dosyalar geri konur.
    request_shutdown(home)
    time.sleep(float(plan.get("settle") or 3))
    if rollback(install, old_dir, replaced, log):
        restart(install, command, log_path, log)
        log("SONUC: yeni surum acilmadi, eski surume donuldu")
    else:
        log("SONUC: geri alma eksik kaldi; eski dosyalar " + str(old_dir))
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Holocron guncelleme yardimcisi")
    parser.add_argument("--plan", required=True, type=Path)
    args = parser.parse_args(argv)
    plan = json.loads(args.plan.read_text(encoding="utf-8"))
    log = Logger(Path(plan["log"]) if plan.get("log") else None)
    try:
        return run(plan, log)
    except Exception as exc:  # noqa: BLE001 - logsuz cokme olmasin
        log(f"HATA: beklenmeyen ({exc!r})")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
