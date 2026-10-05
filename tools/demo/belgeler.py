"""Demo Arsiv'i: uydurma belgeler (veri kartlari) ve baglari.

Belgeler diskte uretilir, ag ya da gercek dosya kullanilmaz: kucuk ama
gecerli bir PDF, openpyxl ile gercek bir .xlsx, zlib ile cizilmis bir PNG,
duz metin ve CSV. Biri goreve ve kayda birlikte, biri iki goreve bagli;
ikisi baglanmamis kalir, biri de klasore "elle birakilmis" gibi yazilir ve
Arsiv ekraninin ilk taramasinda gelir.

Butun icerik uydurmadir.
"""

from __future__ import annotations

import io
import struct
import zlib
from pathlib import Path
from typing import Any

from app import arsiv, paths, repository


def _pdf(baslik: str, satirlar: list[str]) -> bytes:
    """Tek sayfalik, metinli, gecerli bir PDF (capraz basvuru tablosuyla)."""
    def kacis(metin: str) -> str:
        # Standart Helvetica Turkce harfleri tasimaz; demo icin sadelestirilir.
        tablo = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
        return metin.translate(tablo).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    akis = ["BT /F1 18 Tf 56 780 Td (" + kacis(baslik) + ") Tj ET"]
    y = 750
    for satir in satirlar:
        akis.append(f"BT /F1 11 Tf 56 {y} Td ({kacis(satir)}) Tj ET")
        y -= 18
    icerik = "\n".join(akis).encode("latin-1")
    nesneler = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(icerik)).encode() + b" >>\nstream\n" + icerik + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    cikti = io.BytesIO()
    cikti.write(b"%PDF-1.4\n")
    yerler = []
    for numara, govde in enumerate(nesneler, start=1):
        yerler.append(cikti.tell())
        cikti.write(f"{numara} 0 obj\n".encode() + govde + b"\nendobj\n")
    xref = cikti.tell()
    cikti.write(f"xref\n0 {len(nesneler) + 1}\n0000000000 65535 f \n".encode())
    for yer in yerler:
        cikti.write(f"{yer:010d} 00000 n \n".encode())
    cikti.write(
        f"trailer\n<< /Size {len(nesneler) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return cikti.getvalue()


def _png(genislik: int = 160, yukseklik: int = 96, tohum: int = 0) -> bytes:
    """Lacivert zemin, sari ufuk cizgisi: sahte bir "mimari cizim" kucuk resmi."""
    satirlar = []
    for y in range(yukseklik):
        satir = bytearray([0])
        for x in range(genislik):
            if abs(y - (yukseklik // 2 + ((x + tohum) % 40) // 4 - 5)) < 2:
                satir += bytes((255, 232, 31))
            elif (x // 16 + y // 16) % 2:
                satir += bytes((17, 26, 46))
            else:
                satir += bytes((11, 16, 32))
        satirlar.append(bytes(satir))
    ham = zlib.compress(b"".join(satirlar), 9)

    def parca(tur: bytes, veri: bytes) -> bytes:
        return struct.pack(">I", len(veri)) + tur + veri + struct.pack(">I", zlib.crc32(tur + veri) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", genislik, yukseklik, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + parca(b"IHDR", ihdr) + parca(b"IDAT", ham) + parca(b"IEND", b"")


def _xlsx() -> bytes:
    from openpyxl import Workbook

    kitap = Workbook()
    sayfa = kitap.active
    sayfa.title = "Senaryolar"
    sayfa.append(["No", "Senaryo", "Beklenen", "Durum"])
    for numara, (ad, beklenen, durum) in enumerate(
        [
            ("Kartla ödeme", "Onay kodu döner", "Geçti"),
            ("İade akışı", "Tutar hesaba döner", "Kaldı"),
            ("Gün sonu mutabakatı", "Fark sıfır", "Bekliyor"),
            ("Taksitli ödeme", "Taksit tablosu doğru", "Geçti"),
        ],
        start=1,
    ):
        sayfa.append([numara, ad, beklenen, durum])
    akis = io.BytesIO()
    kitap.save(akis)
    return akis.getvalue()


def _belgeler() -> list[tuple[str, bytes]]:
    return [
        ("Kapsam Dokümanı v2.pdf", _pdf("Kapsam Dokumani v2", [
            "Proje: Odeme altyapisi yenileme (uydurma)",
            "Kapsam: kartli odeme, iade, gun sonu mutabakati",
            "Kapsam disi: yurt disi kartlar",
            "Onay: Mimari kurul, 12.09.2026",
        ])),
        ("Test Senaryoları.xlsx", _xlsx()),
        ("Mimari Çizim.png", _png(tohum=3)),
        ("Toplantı Notları.txt", (
            "Haftalık durum toplantısı (uydurma)\n"
            "- Mutabakat farkı kalem kalem çıkarılacak.\n"
            "- Test ortamında maskeleme kontrolü cuma.\n"
            "- Sürüm notları 4.2.0 için hazırlanacak.\n"
        ).encode("utf-8")),
        ("Sürüm Planı.csv", (
            "surum;tarih;not\n4.2.0;2026-10-15;odeme iade\n4.3.0;2026-11-20;raporlar\n"
        ).encode("utf-8")),
        ("Ekran Görüntüsü 12.png", _png(120, 72, tohum=17)),
        ("Yapılandırma Yedeği.json", b'{"ornek": true, "ortam": "test", "surum": "4.2.0"}\n'),
    ]


def belgeleri_kur(context: Any) -> int:
    """Arsiv bossa uydurma belgeleri kopyalar ve baglar. Kac belge yazildi?"""
    conn = context.connection()
    if arsiv.counts(conn)["total"]:
        return 0
    klasor = paths.archive_dir()
    yazilan: dict[str, dict[str, Any]] = {}
    for ad, icerik in _belgeler()[:-1]:
        belge, _ = arsiv.store_chunks(conn, [icerik], ad, klasor)
        yazilan[ad] = belge
    # Sonuncusu klasore "elle" birakilir: ilk taramada baglanmamis gelir.
    son_ad, son_icerik = _belgeler()[-1]
    (Path(klasor) / son_ad).write_bytes(son_icerik)

    gorevler = {gorev["title"]: gorev for gorev in repository.list_tasks(conn)}
    anahtarlar = [gorev.get("issue_key") for gorev in gorevler.values() if gorev.get("issue_key")]

    def bagla(belge_adi: str, gorev_adi: str | None = None, anahtar: str | None = None) -> None:
        belge = yazilan.get(belge_adi)
        if belge is None:
            return
        if gorev_adi and gorev_adi in gorevler:
            arsiv.link(conn, belge["id"], arsiv.TARGET_TASK, gorevler[gorev_adi]["id"])
        if anahtar:
            arsiv.link(conn, belge["id"], arsiv.TARGET_ISSUE, anahtar)

    ilk_anahtar = anahtarlar[0] if anahtarlar else None
    bagla("Kapsam Dokümanı v2.pdf", "Mutabakat raporu farkını çıkar", ilk_anahtar)
    bagla("Test Senaryoları.xlsx", "Test ortamında veri maskeleme kontrolü")
    bagla("Test Senaryoları.xlsx", "Sürüm notlarını hazırla")
    bagla("Toplantı Notları.txt", "Mutabakat raporu farkını çıkar")
    bagla("Sürüm Planı.csv", "Sürüm notlarını hazırla", anahtarlar[1] if len(anahtarlar) > 1 else None)
    bagla("Mimari Çizim.png", anahtar=ilk_anahtar)
    return len(yazilan)
