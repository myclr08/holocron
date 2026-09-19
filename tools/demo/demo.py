#!/usr/bin/env python3
"""Holocron demo ortami: tek komutla, gercek Jira olmadan dolu bir Holocron.

    python tools/demo/demo.py [--port 8765] [--data-dir <yol>] [--reset]
                              [--no-browser] [--copilot-sahte]

Iki sunucu kalkar:

  * sahte Jira Data Center  -> http://127.0.0.1:8090
  * Holocron                -> http://127.0.0.1:8765

Veri `~/.local/share/holocron-demo` altinda durur (Windows'ta
`%LOCALAPPDATA%\\Holocron-Demo`); ikinci calistirmada oradan devam eder,
`--reset` sifirdan tohumlar. Ctrl+C her iki sunucuyu da kapatir.

Butun veri uydurmadir: kisi adlari, projeler ve adresler gercek degildir.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

# Betik dogrudan calistirildiginda (`python tools/demo/demo.py`) ne depo koku
# ne de `tools/` sys.path'te olur; ikisini de elle ekliyoruz.
KOK = Path(__file__).resolve().parent.parent.parent
for _yol in (str(KOK), str(KOK / "tools")):
    if _yol not in sys.path:
        sys.path.insert(0, _yol)

VARSAYILAN_PORT = 8765
VARSAYILAN_JIRA_PORTU = 8090


def veri_klasoru() -> Path:
    """Demo verisinin varsayilan yeri (gercek Holocron kurulumuna dokunmaz)."""
    if sys.platform.startswith("win"):
        taban = os.environ.get("LOCALAPPDATA") or str(Path.home())
        return Path(taban) / "Holocron-Demo"
    taban = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(taban) / "holocron-demo"


def argumanlari_oku(argv: list[str] | None = None) -> argparse.Namespace:
    ayristirici = argparse.ArgumentParser(
        prog="demo.py",
        description="Sahte Jira'ya bagli, dolu bir Holocron ornegi baslatir.",
    )
    ayristirici.add_argument(
        "--port", type=int, default=VARSAYILAN_PORT, help="Holocron portu (varsayilan 8765)"
    )
    ayristirici.add_argument(
        "--jira-port",
        type=int,
        default=VARSAYILAN_JIRA_PORTU,
        help="Sahte Jira portu (varsayilan 8090)",
    )
    ayristirici.add_argument(
        "--data-dir", type=Path, default=None, help="Veri klasoru (varsayilan: demo klasoru)"
    )
    ayristirici.add_argument(
        "--reset", action="store_true", help="Veritabanini silip sifirdan tohumla"
    )
    ayristirici.add_argument("--no-browser", action="store_true", help="Tarayiciyi acma")
    ayristirici.add_argument(
        "--copilot-sahte",
        action="store_true",
        help="PATH'e sahte bir `copilot` betigi koy (Copilot'u sına ve Düzelt çalışsın)",
    )
    ayristirici.add_argument(
        "--kayit-sayisi",
        type=int,
        default=None,
        help="Sahte Jira'daki kayit sayisi (varsayilan 120)",
    )
    return ayristirici.parse_args(argv)


def klasoru_hazirla(klasor: Path, sifirla: bool) -> Path:
    """Veri klasorunu kurar; `--reset` verildiyse eski veriyi siler."""
    klasor.mkdir(parents=True, exist_ok=True)
    if sifirla:
        for ad in ("holocron.db", "holocron.key", "holocron.log", "holocron.port"):
            hedef = klasor / ad
            try:
                hedef.unlink()
            except FileNotFoundError:
                pass
    return klasor


def tohumla(context, sahte, an: datetime | None = None) -> dict[str, int]:
    """Bos veritabanini doldurur; dolu veritabaninda eksikleri tamamlar."""
    from app import repository

    from demo import tohum

    conn = context.connection()
    tohum.ayarlari_yaz(context, f"http://127.0.0.1:{sahte.port}")
    tohum.filolari_kur(context)

    # Elle listeli filolarin uyeleri "Guncelle"den ONCE yazilir: ayni iste
    # onlarin kayitlari da cekilsin.
    havuz = sorted(sahte.durum.anahtarlar())
    tohum.elle_uyeleri_ekle(context, havuz[:20])

    durum = context.refresh.run_blocking(context)
    if durum.get("error"):
        raise RuntimeError(f"Kayitlar cekilemedi: {durum['error']}")

    anahtarlar = repository.all_member_keys(conn)
    tohum.geri_kalani_kur(context, anahtarlar, an)
    return {
        "kayit": repository.issue_count(conn),
        "filo": len(repository.list_groups(conn)),
        "gorev": len(repository.list_tasks(conn)),
        "kisi": len(repository.list_contacts(conn)),
    }


class _SahteSunucu:
    """Sahte Jira sunucusu + durumu tek nesnede (kapatmasi kolay olsun)."""

    def __init__(self, port: int, kayit_sayisi: int | None = None) -> None:
        from demo import sahte_jira, veri

        self.port = port
        kayitlar = veri.kayitlar_uret(sayi=kayit_sayisi) if kayit_sayisi else None
        self.durum = sahte_jira.SahteJira(kayitlar=kayitlar)
        self._sunucu, self._is_parcacigi, _ = sahte_jira.baslat(self.durum, port=port)

    def kapat(self) -> None:
        self._sunucu.shutdown()
        self._sunucu.server_close()


def main(argv: list[str] | None = None) -> int:
    args = argumanlari_oku(argv)
    klasor = klasoru_hazirla(args.data_dir or veri_klasoru(), args.reset)

    # Holocron'un veri yolunu demo klasorune cek; gercek kurulum bozulmasin.
    os.environ["HOLOCRON_HOME"] = str(klasor)

    # `app` ve `demo` ancak HOLOCRON_HOME kurulduktan sonra ice aktarilir.
    import uvicorn

    from app import paths
    from app.context import build_context
    from app.server import create_app

    paths.forget_home()
    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    sahte = _SahteSunucu(args.jira_port, args.kayit_sayisi)
    context = build_context()

    if args.copilot_sahte:
        from demo import sahte_copilot

        betik = sahte_copilot.kur(klasor / "bin")
        sahte_copilot.patha_ekle(betik.parent)
        context.settings.set("copilot.yolu", str(betik))
        print(f"[demo] Sahte Copilot: {betik}", flush=True)

    print(f"[demo] Veri klasörü : {klasor}", flush=True)
    print(f"[demo] Sahte Jira   : http://127.0.0.1:{sahte.port}", flush=True)
    try:
        ozet = tohumla(context, sahte, datetime.now(timezone.utc))
    except Exception as hata:  # noqa: BLE001 - tohumlama hatasi acikca gorunsun
        sahte.kapat()
        context.close()
        print(f"[demo] Tohumlama başarısız: {hata}", file=sys.stderr, flush=True)
        return 1
    print(
        "[demo] Tohum        : "
        f"{ozet['kayit']} kayıt · {ozet['filo']} filo · "
        f"{ozet['gorev']} görev · {ozet['kisi']} kişi",
        flush=True,
    )

    adres = f"http://127.0.0.1:{args.port}/"
    app = create_app(context)
    yapilandirma = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=args.port,
        log_level="warning",
        access_log=False,
        log_config=None,
        use_colors=False,
    )
    sunucu = uvicorn.Server(yapilandirma)

    print(f"[demo] Holocron     : {adres}", flush=True)
    print("[demo] Kapatmak için Ctrl+C.", flush=True)

    if not args.no_browser:
        threading.Timer(1.0, lambda: webbrowser.open(adres)).start()

    try:
        sunucu.run()
    except KeyboardInterrupt:  # pragma: no cover - elle kapatma
        pass
    finally:
        sahte.kapat()
        context.close()
        print("[demo] Kapandı.", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
