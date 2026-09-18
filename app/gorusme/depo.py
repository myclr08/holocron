"""Gorusme notlarinin SQL katmani.

`gamify_repo.py` / `mailsend_repo.py` ile ayni desen: is mantigi SQL yazmaz,
buradaki fonksiyonlari cagirir. Butun yazmalar tek bir islemde toplanir ki
yarim kalmis bir asama tabloda karisik bir not birakmasin.
"""

from __future__ import annotations

import sqlite3
from typing import Any, Iterable, Sequence

from .. import repository
from .source import (
    ARA_DURUMLAR,
    BOLUM_TURLERI,
    DURUM_HATA,
    DURUM_KAYDEDILIYOR,
    DURUM_KUYRUKTA,
    DURUMLAR,
)

NOT_SUTUNLARI: tuple[str, ...] = (
    "id",
    "call_id",
    "baslangic",
    "bitis",
    "sure_sn",
    "tur",
    "baslik",
    "durum",
    "hata",
    "model",
    "isleme_sn_birlestirme",
    "isleme_sn_yaziya_dokme",
    "isleme_sn_ozet",
    "klasor",
    "olusturma",
)

# Elle guncellenebilen alanlar: beyaz liste, cagiran taraf sutun adi uyduramaz.
YAZILABILIR: frozenset[str] = frozenset(
    {
        "call_id",
        "baslangic",
        "bitis",
        "sure_sn",
        "tur",
        "baslik",
        "durum",
        "hata",
        "model",
        "isleme_sn_birlestirme",
        "isleme_sn_yaziya_dokme",
        "isleme_sn_ozet",
        "klasor",
    }
)


def fts_var(conn: sqlite3.Connection) -> bool:
    """FTS5 tablosu kuruldu mu? Kurulmadiysa arama LIKE'a duser."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='gorusme_fts'"
    ).fetchone()
    return row is not None


def _not_dict(row: sqlite3.Row) -> dict[str, Any]:
    veri = {ad: row[ad] for ad in NOT_SUTUNLARI}
    for ad in ("sure_sn", "isleme_sn_birlestirme", "isleme_sn_yaziya_dokme", "isleme_sn_ozet"):
        veri[ad] = int(veri.get(ad) or 0)
    veri["id"] = int(veri["id"])
    for ad in NOT_SUTUNLARI:
        if veri[ad] is None:
            veri[ad] = ""
    return veri


# --- not -----------------------------------------------------------------


def not_olustur(
    conn: sqlite3.Connection,
    baslangic: str,
    klasor: str = "",
    tur: str = "",
    durum: str = DURUM_KAYDEDILIYOR,
) -> int:
    with conn:
        imlec = conn.execute(
            "INSERT INTO gorusme_notu (baslangic, klasor, tur, durum, olusturma) "
            "VALUES (?, ?, ?, ?, ?)",
            (baslangic, klasor, tur, _durum(durum), repository.now_iso()),
        )
    return int(imlec.lastrowid or 0)


def _durum(deger: Any) -> str:
    metin = str(deger or "").strip()
    return metin if metin in DURUMLAR else DURUM_KAYDEDILIYOR


def not_guncelle(conn: sqlite3.Connection, not_id: int, **alanlar: Any) -> dict[str, Any] | None:
    temiz = {ad: deger for ad, deger in alanlar.items() if ad in YAZILABILIR}
    if not temiz:
        return get_not(conn, not_id)
    if "durum" in temiz:
        temiz["durum"] = _durum(temiz["durum"])
    atama = ", ".join(f"{ad} = ?" for ad in temiz)
    with conn:
        conn.execute(
            f"UPDATE gorusme_notu SET {atama} WHERE id = ?",
            (*temiz.values(), int(not_id)),
        )
    return get_not(conn, not_id)


def durum_yaz(conn: sqlite3.Connection, not_id: int, durum: str, hata: str = "") -> None:
    """Durum gecisi tabloya yazilir: arayuz hattin neresinde oldugunu anlik gorur."""
    not_guncelle(conn, not_id, durum=durum, hata=hata)


def get_not(conn: sqlite3.Connection, not_id: Any) -> dict[str, Any] | None:
    row = conn.execute(
        f"SELECT {', '.join(NOT_SUTUNLARI)} FROM gorusme_notu WHERE id = ?", (int(not_id or 0),)
    ).fetchone()
    return _not_dict(row) if row is not None else None


def require_not(conn: sqlite3.Connection, not_id: Any) -> dict[str, Any]:
    veri = get_not(conn, not_id)
    if veri is None:
        raise repository.RepositoryError("not_found", "Görüşme notu bulunamadı.", status=404)
    return veri


def list_notlar(
    conn: sqlite3.Connection, since: str = "", durum: str = "", ids: Sequence[int] | None = None
) -> list[dict[str, Any]]:
    """Notlar (yeniden eskiye). Pencere suzgeci SQL tarafinda uygulanir."""
    kosullar: list[str] = []
    degerler: list[Any] = []
    isaret = str(since or "").strip()
    if isaret:
        kosullar.append("baslangic >= ?")
        degerler.append(isaret)
    secili = str(durum or "").strip()
    if secili:
        kosullar.append("durum = ?")
        degerler.append(secili)
    if ids is not None:
        if not ids:
            return []
        kosullar.append("id IN ({})".format(",".join("?" for _ in ids)))
        degerler.extend(int(deger) for deger in ids)
    nerede = (" WHERE " + " AND ".join(kosullar)) if kosullar else ""
    rows = conn.execute(
        f"SELECT {', '.join(NOT_SUTUNLARI)} FROM gorusme_notu{nerede} "
        "ORDER BY baslangic DESC, id DESC",
        degerler,
    ).fetchall()
    return [_not_dict(row) for row in rows]


def sil(conn: sqlite3.Connection, not_id: Any) -> bool:
    kimlik = int(not_id or 0)
    with conn:
        conn.execute("DELETE FROM gorusme_notu WHERE id = ?", (kimlik,))
        # FTS5 sanal tablosu yabanci anahtar tanimaz: elle silinir.
        if fts_var(conn):
            conn.execute("DELETE FROM gorusme_fts WHERE not_id = ?", (kimlik,))
    return True


def sayim(conn: sqlite3.Connection) -> dict[str, int]:
    """Durum bazinda not sayilari (takip seridi bunu gosterir)."""
    rows = conn.execute("SELECT durum, COUNT(*) AS n FROM gorusme_notu GROUP BY durum").fetchall()
    sonuc = {durum: 0 for durum in DURUMLAR}
    for row in rows:
        sonuc[str(row["durum"])] = int(row["n"])
    sonuc["toplam"] = sum(int(row["n"]) for row in rows)
    return sonuc


# --- kuyruk --------------------------------------------------------------


def sonraki_kuyruk(conn: sqlite3.Connection) -> dict[str, Any] | None:
    """Sirada bekleyen en eski not; aynı anda tek is islenir."""
    row = conn.execute(
        f"SELECT {', '.join(NOT_SUTUNLARI)} FROM gorusme_notu WHERE durum = ? ORDER BY id LIMIT 1",
        (DURUM_KUYRUKTA,),
    ).fetchone()
    return _not_dict(row) if row is not None else None


def yarim_isleri_kuyruga_al(conn: sqlite3.Connection) -> int:
    """Acilista yarim kalmis isleri kuyruga geri koyar.

    Uygulama isleme ortasinda kapandiysa satir "yaziya_dokuluyor" gibi bir
    durumda kalir; kimse onu surdurmezse not sonsuza kadar orada takilir.
    Kaydedilirken kapanmis bir gorusme de kuyruga alinir: ses parcalari
    diskte duruyor, isin kalanini yapabiliriz.
    """
    isaretler = (*ARA_DURUMLAR, DURUM_KAYDEDILIYOR)
    with conn:
        imlec = conn.execute(
            "UPDATE gorusme_notu SET durum = ? WHERE durum IN ({})".format(
                ",".join("?" for _ in isaretler)
            ),
            (DURUM_KUYRUKTA, *isaretler),
        )
    return int(imlec.rowcount or 0)


def hataya_dus(conn: sqlite3.Connection, not_id: int, mesaj: str) -> None:
    durum_yaz(conn, not_id, DURUM_HATA, mesaj)


# --- bolumler ------------------------------------------------------------


def bolumleri_yaz(conn: sqlite3.Connection, not_id: int, satirlar: Iterable[dict[str, Any]]) -> int:
    """Notun butun bolumlerini yeniden yazar (yeniden ozetleme de buradan gecer)."""
    kimlik = int(not_id)
    temiz = [
        (
            kimlik,
            str(satir.get("tur") or ""),
            int(satir.get("sira") or 0),
            str(satir.get("metin") or "").strip(),
            str(satir.get("kisi") or ""),
            str(satir.get("son_tarih") or ""),
        )
        for satir in satirlar
        if str(satir.get("tur") or "") in BOLUM_TURLERI and str(satir.get("metin") or "").strip()
    ]
    with conn:
        conn.execute("DELETE FROM gorusme_bolum WHERE not_id = ?", (kimlik,))
        if temiz:
            conn.executemany(
                "INSERT INTO gorusme_bolum (not_id, tur, sira, metin, kisi, son_tarih) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                temiz,
            )
    return len(temiz)


def bolumler(conn: sqlite3.Connection, not_id: Any) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, tur, sira, metin, kisi, son_tarih FROM gorusme_bolum "
        "WHERE not_id = ? ORDER BY tur, sira, id",
        (int(not_id or 0),),
    ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "tur": str(row["tur"]),
            "sira": int(row["sira"] or 0),
            "metin": str(row["metin"] or ""),
            "kisi": str(row["kisi"] or ""),
            "son_tarih": str(row["son_tarih"] or ""),
        }
        for row in rows
    ]


# --- katilimcilar --------------------------------------------------------


def katilimcilari_yaz(conn: sqlite3.Connection, not_id: int, kisiler: Iterable[Any]) -> int:
    kimlik = int(not_id)
    temiz: list[tuple[int, str, str]] = []
    for kisi in kisiler:
        if isinstance(kisi, dict):
            anahtar = str(kisi.get("kimlik") or "").strip()
            ad = str(kisi.get("ad") or "").strip()
        else:
            anahtar, ad = str(kisi or "").strip(), ""
        if anahtar:
            temiz.append((kimlik, anahtar, ad))
    with conn:
        conn.execute("DELETE FROM gorusme_katilimci WHERE not_id = ?", (kimlik,))
        if temiz:
            conn.executemany(
                "INSERT OR REPLACE INTO gorusme_katilimci (not_id, kimlik, ad) VALUES (?, ?, ?)",
                temiz,
            )
    return len(temiz)


def katilimcilar(conn: sqlite3.Connection, not_id: Any) -> list[dict[str, str]]:
    rows = conn.execute(
        "SELECT kimlik, ad FROM gorusme_katilimci WHERE not_id = ? ORDER BY ad, kimlik",
        (int(not_id or 0),),
    ).fetchall()
    return [{"kimlik": str(row["kimlik"]), "ad": str(row["ad"] or "")} for row in rows]


def kisi_notlari(conn: sqlite3.Connection, kimlik: str) -> list[int]:
    """Bir kisinin gectigi notlarin kimlikleri (Kisiler cekmecesi icin)."""
    rows = conn.execute(
        "SELECT not_id FROM gorusme_katilimci WHERE kimlik = ? ORDER BY not_id DESC",
        (str(kimlik or ""),),
    ).fetchall()
    return [int(row["not_id"]) for row in rows]


# --- baglar --------------------------------------------------------------


def jira_bagla(conn: sqlite3.Connection, not_id: int, jira_key: str) -> None:
    """Not tek bir Jira kaydina baglidir: once eskisi kalkar."""
    kimlik = int(not_id)
    anahtar = str(jira_key or "").strip().upper()
    with conn:
        conn.execute(
            "DELETE FROM gorusme_bag WHERE not_id = ? AND jira_key IS NOT NULL", (kimlik,)
        )
        if anahtar:
            conn.execute(
                "INSERT INTO gorusme_bag (not_id, jira_key) VALUES (?, ?)", (kimlik, anahtar)
            )


def gorev_bagla(conn: sqlite3.Connection, not_id: int, gorev_id: int) -> None:
    with conn:
        conn.execute(
            "INSERT INTO gorusme_bag (not_id, gorev_id) VALUES (?, ?)",
            (int(not_id), int(gorev_id)),
        )


def gorev_bagini_kaldir(conn: sqlite3.Connection, gorev_id: int) -> None:
    with conn:
        conn.execute("DELETE FROM gorusme_bag WHERE gorev_id = ?", (int(gorev_id),))


def jira_key(conn: sqlite3.Connection, not_id: Any) -> str:
    row = conn.execute(
        "SELECT jira_key FROM gorusme_bag WHERE not_id = ? AND jira_key IS NOT NULL LIMIT 1",
        (int(not_id or 0),),
    ).fetchone()
    return str(row["jira_key"] or "") if row is not None else ""


def gorev_kimlikleri(conn: sqlite3.Connection, not_id: Any) -> list[int]:
    rows = conn.execute(
        "SELECT gorev_id FROM gorusme_bag WHERE not_id = ? AND gorev_id IS NOT NULL "
        "ORDER BY gorev_id",
        (int(not_id or 0),),
    ).fetchall()
    return [int(row["gorev_id"]) for row in rows]


def kayit_notlari(conn: sqlite3.Connection, jira_key_degeri: str) -> list[int]:
    """Bir Jira kaydina bagli notlarin kimlikleri (kayit detayi icin)."""
    rows = conn.execute(
        "SELECT not_id FROM gorusme_bag WHERE jira_key = ? ORDER BY not_id DESC",
        (str(jira_key_degeri or "").strip().upper(),),
    ).fetchall()
    return [int(row["not_id"]) for row in rows]


def gorev_sayilari(conn: sqlite3.Connection) -> dict[int, int]:
    """Not basina bagli gorev sayisi (liste sutunu)."""
    rows = conn.execute(
        "SELECT not_id, COUNT(*) AS n FROM gorusme_bag WHERE gorev_id IS NOT NULL GROUP BY not_id"
    ).fetchall()
    return {int(row["not_id"]): int(row["n"]) for row in rows}


def jira_anahtarlari(conn: sqlite3.Connection) -> dict[int, str]:
    rows = conn.execute(
        "SELECT not_id, jira_key FROM gorusme_bag WHERE jira_key IS NOT NULL"
    ).fetchall()
    return {int(row["not_id"]): str(row["jira_key"] or "") for row in rows}


# --- transkript ve arama -------------------------------------------------


def transkript_yaz(conn: sqlite3.Connection, not_id: int, metin: str) -> None:
    with conn:
        conn.execute(
            "INSERT INTO gorusme_transkript (not_id, metin) VALUES (?, ?) "
            "ON CONFLICT(not_id) DO UPDATE SET metin = excluded.metin",
            (int(not_id), str(metin or "")),
        )


def transkript(conn: sqlite3.Connection, not_id: Any) -> str:
    row = conn.execute(
        "SELECT metin FROM gorusme_transkript WHERE not_id = ?", (int(not_id or 0),)
    ).fetchone()
    return str(row["metin"] or "") if row is not None else ""


def transkript_sil(conn: sqlite3.Connection, not_id: Any) -> None:
    with conn:
        conn.execute("DELETE FROM gorusme_transkript WHERE not_id = ?", (int(not_id or 0),))


def fts_yaz(conn: sqlite3.Connection, not_id: int, metin: str) -> bool:
    """Aranabilir metni tazeler (baslik + butun bolumler)."""
    if not fts_var(conn):
        return False
    kimlik = int(not_id)
    with conn:
        conn.execute("DELETE FROM gorusme_fts WHERE not_id = ?", (kimlik,))
        conn.execute(
            "INSERT INTO gorusme_fts (metin, not_id) VALUES (?, ?)", (str(metin or ""), kimlik)
        )
    return True


def ara(conn: sqlite3.Connection, sorgu: str) -> list[int] | None:
    """FTS ile eslesen not kimlikleri; FTS yoksa `None` (cagiran LIKE'a duser)."""
    metin = str(sorgu or "").strip()
    if not metin or not fts_var(conn):
        return None
    try:
        rows = conn.execute(
            "SELECT not_id FROM gorusme_fts WHERE gorusme_fts MATCH ?", (_fts_sorgusu(metin),)
        ).fetchall()
    except sqlite3.OperationalError:
        # Kullanicinin yazdigi metin FTS sozdizimini bozabilir; LIKE'a duser.
        return None
    return [int(row["not_id"]) for row in rows]


def _fts_sorgusu(metin: str) -> str:
    """Kullanici metnini guvenli bir FTS sorgusuna cevirir (her kelime onek)."""
    kelimeler = [parca for parca in metin.replace('"', " ").split() if parca]
    return " ".join(f'"{kelime}"*' for kelime in kelimeler)
