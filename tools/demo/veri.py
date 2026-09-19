"""Demo veri havuzlari ve sahte Jira kayitlarinin uretimi.

Butun adlar, projeler ve basliklar uydurmadir; hicbir gercek kurum, kisi ya da
adres gecmez. Uretim `random.Random(TOHUM)` ile deterministiktir: ayni surum
ayni kayitlari uretir, ekran goruntusu de tekrar edilebilir olur.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any

# Sabit tohum: demo her calistirmada ayni gorunsun.
TOHUM = 20260920

# Kayit sayisi ve tarih penceresi.
KAYIT_SAYISI = 120
GUN_PENCERESI = 90

# --- projeler -----------------------------------------------------------

PROJELER: tuple[dict[str, str], ...] = (
    {"key": "PRJ", "name": "Ödeme Platformu", "id": "10001"},
    {"key": "OPS", "name": "Operasyon Merkezi", "id": "10002"},
    {"key": "MOB", "name": "Mobil Bankacılık", "id": "10003"},
)

# --- durumlar -----------------------------------------------------------
#
# Holocron `statusCategory.key` degerine bakar: "new" / "indeterminate" /
# "done". Sefer puani ve kanban rengi oradan geliyor.

DURUMLAR: tuple[dict[str, str], ...] = (
    {"name": "Açık", "id": "1", "kategori": "new", "kategori_adi": "Yapılacak"},
    {"name": "Geliştiriliyor", "id": "3", "kategori": "indeterminate", "kategori_adi": "Sürüyor"},
    {"name": "Test", "id": "10100", "kategori": "indeterminate", "kategori_adi": "Sürüyor"},
    {"name": "Kapalı", "id": "6", "kategori": "done", "kategori_adi": "Bitti"},
)

DURUM_AGIRLIKLARI: tuple[int, ...] = (32, 28, 18, 22)

ONCELIKLER: tuple[dict[str, str], ...] = (
    {"name": "Engelleyici", "id": "1"},
    {"name": "Yüksek", "id": "2"},
    {"name": "Orta", "id": "3"},
    {"name": "Düşük", "id": "4"},
)

ONCELIK_AGIRLIKLARI: tuple[int, ...] = (6, 24, 50, 20)

TURLER: tuple[dict[str, Any], ...] = (
    {"name": "Hata", "id": "1", "subtask": False},
    {"name": "Görev", "id": "3", "subtask": False},
    {"name": "İyileştirme", "id": "4", "subtask": False},
    {"name": "Talep", "id": "10001", "subtask": False},
)

# --- kisiler ------------------------------------------------------------
#
# Hepsi uydurma. Kullanici adi `ad.soyad`, adres `@example.com`.

KISILER: tuple[dict[str, str], ...] = (
    {"ad": "Deniz Akgün", "kullanici": "deniz.akgun"},
    {"ad": "Kaan Yılmaz", "kullanici": "kaan.yilmaz"},
    {"ad": "Selin Arslan", "kullanici": "selin.arslan"},
    {"ad": "Mert Kaya", "kullanici": "mert.kaya"},
    {"ad": "Ece Demir", "kullanici": "ece.demir"},
    {"ad": "Burak Şahin", "kullanici": "burak.sahin"},
    {"ad": "Nazlı Erdoğan", "kullanici": "nazli.erdogan"},
    {"ad": "Onur Çetin", "kullanici": "onur.cetin"},
    {"ad": "Gizem Aydın", "kullanici": "gizem.aydin"},
    {"ad": "Serkan Polat", "kullanici": "serkan.polat"},
    {"ad": "Pelin Koç", "kullanici": "pelin.koc"},
    {"ad": "Emre Tunç", "kullanici": "emre.tunc"},
    {"ad": "Zeynep Bulut", "kullanici": "zeynep.bulut"},
    {"ad": "Cem Doğan", "kullanici": "cem.dogan"},
    {"ad": "Aylin Sarı", "kullanici": "aylin.sari"},
)

# Demo kullanicisi: `assignee = currentUser()` bunu cozer.
BEN = {"ad": "Demir Yalçın", "kullanici": "demir.yalcin"}

TAKIMLAR: tuple[str, ...] = ("Ödeme Çekirdek", "Kanal Entegrasyon", "Raporlama", "Mobil", "Altyapı")

MUSTERILER: tuple[str, ...] = (
    "İç Operasyon",
    "Şube Ağı",
    "Çağrı Merkezi",
    "Dijital Kanallar",
    "Hazine",
)

ETIKETLER: tuple[str, ...] = (
    "acil",
    "regresyon",
    "musteri-talebi",
    "teknik-borc",
    "guvenlik",
    "performans",
    "raporlama",
    "entegrasyon",
)

# --- basliklar ----------------------------------------------------------
#
# Uydurma BT/bankacilik isleri. Kalip: "<is> <nesne>" ile 120 farkli baslik
# cikarmak icin iki havuz carpilir.

ISLER: tuple[str, ...] = (
    "Raporlama servisi geçişi",
    "Gecikmeli ödeme ekranı",
    "Toplu virman dosyası doğrulaması",
    "Kart limiti sorgusu zaman aşımı",
    "Mutabakat raporu tutarsızlığı",
    "EFT kuyruğu yeniden deneme mantığı",
    "Kampanya puanı hesaplama hatası",
    "Şube ekranında yavaş açılan liste",
    "Mobil girişte oturum düşmesi",
    "Bildirim servisi sertifika yenileme",
    "Kredi başvuru formu doğrulama",
    "Hesap özeti PDF üretimi",
    "Günsonu toplu iş süresi",
    "Döviz kuru önbelleği",
    "Fatura ödeme kanalı entegrasyonu",
    "Müşteri arama sonuçları sıralaması",
    "Log toplama ajanı sürüm yükseltmesi",
    "Yetki matrisi güncellemesi",
    "Test ortamı veri maskeleme",
    "API geçidi hız sınırı",
    "Ödeme talimatı iptali",
    "IBAN doğrulama kütüphanesi",
    "Mesaj kuyruğu ölü mektup kutusu",
    "Veritabanı indeks bakımı",
    "Sanal POS iade akışı",
    "Kart bloke ekranı uyarıları",
    "Bakiye sorgulama önbelleği",
    "Çağrı merkezi ekran kaydı",
    "Şifre sıfırlama e-postası",
    "Kullanıcı oturum günlüğü",
)

DETAYLAR: tuple[str, ...] = (
    "canlıda tekrar eden vaka",
    "test ortamında doğrulanacak",
    "iş birimi talebi",
    "sürüm öncesi zorunlu",
    "kapasite planı kapsamında",
    "denetim bulgusu",
    "müşteri şikâyeti kaynaklı",
    "teknik borç temizliği",
)

ACIKLAMA_KALIPLARI: tuple[str, ...] = (
    "{is} konusunda {detay}. Son iki haftada {sayi} kez gözlendi.\n\n"
    "Beklenen: işlem tek seferde tamamlanmalı.\n"
    "Görülen: {sayi} denemenin ardından hata dönüyor.\n\n"
    "Not: {takim} ekibiyle birlikte bakılacak.",
    "{is} için {takim} ekibinden analiz istendi. {detay}.\n\n"
    "Adımlar:\n"
    "1. Mevcut akış çıkarılacak\n"
    "2. Etkilenen kanallar listelenecek\n"
    "3. Geçiş planı {musteri} ile paylaşılacak",
    "{musteri} tarafından iletilen talep: {is}.\n\n"
    "Kapsam netleştirildikten sonra tahmin güncellenecek. {detay}.",
)

SPRINTLER: tuple[str, ...] = (
    "Sprint 41",
    "Sprint 42",
    "Sprint 43",
    "Sprint 44",
)

# --- alan katalogu ------------------------------------------------------
#
# `/rest/api/2/field` cevabi. Holocron sutun secicisini ve bicimlendirmeyi
# buradaki `schema.type` degerine gore kuruyor.

ALAN_KATALOGU: tuple[dict[str, Any], ...] = (
    {"id": "summary", "name": "Özet", "custom": False, "navigable": True,
     "schema": {"type": "string", "system": "summary"}},
    {"id": "description", "name": "Açıklama", "custom": False, "navigable": True,
     "schema": {"type": "string", "system": "description"}},
    {"id": "status", "name": "Durum", "custom": False, "navigable": True,
     "schema": {"type": "status", "system": "status"}},
    {"id": "priority", "name": "Öncelik", "custom": False, "navigable": True,
     "schema": {"type": "priority", "system": "priority"}},
    {"id": "assignee", "name": "Atanan", "custom": False, "navigable": True,
     "schema": {"type": "user", "system": "assignee"}},
    {"id": "reporter", "name": "Raportör", "custom": False, "navigable": True,
     "schema": {"type": "user", "system": "reporter"}},
    {"id": "created", "name": "Oluşturma", "custom": False, "navigable": True,
     "schema": {"type": "datetime", "system": "created"}},
    {"id": "updated", "name": "Güncelleme", "custom": False, "navigable": True,
     "schema": {"type": "datetime", "system": "updated"}},
    {"id": "duedate", "name": "Son tarih", "custom": False, "navigable": True,
     "schema": {"type": "date", "system": "duedate"}},
    {"id": "resolutiondate", "name": "Çözüm tarihi", "custom": False, "navigable": True,
     "schema": {"type": "datetime", "system": "resolutiondate"}},
    {"id": "labels", "name": "Etiketler", "custom": False, "navigable": True,
     "schema": {"type": "array", "items": "string", "system": "labels"}},
    {"id": "issuetype", "name": "Tür", "custom": False, "navigable": True,
     "schema": {"type": "issuetype", "system": "issuetype"}},
    {"id": "project", "name": "Proje", "custom": False, "navigable": True,
     "schema": {"type": "project", "system": "project"}},
    {"id": "resolution", "name": "Çözüm", "custom": False, "navigable": True,
     "schema": {"type": "resolution", "system": "resolution"}},
    # Ozel alanlar: kurum Jira'sinda tipik olanlarin uydurma karsiliklari.
    {"id": "customfield_10001", "name": "Sprint", "custom": True, "navigable": True,
     "schema": {"type": "string", "custom": "com.example.demo:sprint", "customId": 10001}},
    {"id": "customfield_10002", "name": "Takım", "custom": True, "navigable": True,
     "schema": {"type": "string", "custom": "com.example.demo:select", "customId": 10002}},
    {"id": "customfield_10003", "name": "İş birimi", "custom": True, "navigable": True,
     "schema": {"type": "string", "custom": "com.example.demo:select", "customId": 10003}},
    {"id": "customfield_10004", "name": "Tahmini efor (gün)", "custom": True, "navigable": True,
     "schema": {"type": "number", "custom": "com.example.demo:float", "customId": 10004}},
    {"id": "customfield_10005", "name": "Hedef sürüm", "custom": True, "navigable": True,
     "schema": {"type": "string", "custom": "com.example.demo:text", "customId": 10005}},
)


def eposta(kullanici: str) -> str:
    """Demo adresi: alan adi her zaman example.com."""
    return f"{kullanici}@example.com"


def kisi_alani(kisi: dict[str, str]) -> dict[str, Any]:
    """Jira Server/DC'nin `user` alani bicimi."""
    return {
        "name": kisi["kullanici"],
        "key": kisi["kullanici"],
        "displayName": kisi["ad"],
        "emailAddress": eposta(kisi["kullanici"]),
        "active": True,
    }


def zaman(an: datetime) -> str:
    """Jira Server/DC damgasi: `2026-09-20T10:15:00.000+0000`."""
    return an.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000+0000")


def _sec(rastgele: random.Random, secenekler, agirliklar=None):
    if agirliklar is None:
        return rastgele.choice(list(secenekler))
    return rastgele.choices(list(secenekler), weights=list(agirliklar), k=1)[0]


def kayitlar_uret(
    simdi: datetime | None = None,
    sayi: int = KAYIT_SAYISI,
    tohum: int = TOHUM,
) -> list[dict[str, Any]]:
    """Sahte Jira kayitlari: uc proje, son `GUN_PENCERESI` gune yayilmis."""
    an = simdi or datetime.now(timezone.utc)
    rastgele = random.Random(tohum)
    sayaclar = {proje["key"]: 1400 for proje in PROJELER}
    kayitlar: list[dict[str, Any]] = []

    for sira in range(sayi):
        proje = PROJELER[sira % len(PROJELER)]
        sayaclar[proje["key"]] += rastgele.randint(1, 4)
        anahtar = f"{proje['key']}-{sayaclar[proje['key']]}"

        durum = _sec(rastgele, DURUMLAR, DURUM_AGIRLIKLARI)
        oncelik = _sec(rastgele, ONCELIKLER, ONCELIK_AGIRLIKLARI)
        tur = _sec(rastgele, TURLER)
        is_adi = ISLER[sira % len(ISLER)]
        detay = _sec(rastgele, DETAYLAR)
        takim = _sec(rastgele, TAKIMLAR)
        musteri = _sec(rastgele, MUSTERILER)

        # Demo kullanicisinin kayitlari gorunur olsun: kayitlarin ucte biri
        # onun. Sira sayisina bakmiyoruz: projeler sirayla dagitildigi icin
        # "her ucuncu kayit" demek butun kayitlarini tek projeye toplardi.
        atanan = BEN if rastgele.random() < 0.33 else _sec(rastgele, KISILER)
        raportor = _sec(rastgele, KISILER)

        olusturma = an - timedelta(
            days=rastgele.randint(1, GUN_PENCERESI),
            hours=rastgele.randint(0, 23),
            minutes=rastgele.randint(0, 59),
        )
        # Guncelleme olusturmadan sonra, bugunden once.
        aralik = max((an - olusturma).days, 1)
        guncelleme = olusturma + timedelta(
            days=rastgele.randint(0, aralik - 1) if aralik > 1 else 0,
            hours=rastgele.randint(1, 23),
        )
        if guncelleme > an:
            guncelleme = an - timedelta(hours=rastgele.randint(1, 20))

        cozum = None
        cozum_tarihi = None
        if durum["kategori"] == "done":
            cozum = {"id": "1", "name": "Tamamlandı"}
            cozum_tarihi = zaman(guncelleme)

        son_tarih = None
        if rastgele.random() < 0.45:
            son_tarih = (an + timedelta(days=rastgele.randint(-12, 25))).date().isoformat()

        etiketler = rastgele.sample(list(ETIKETLER), k=rastgele.randint(0, 3))

        alanlar: dict[str, Any] = {
            "summary": f"{is_adi} — {detay}",
            "description": _sec(rastgele, ACIKLAMA_KALIPLARI).format(
                **{
                    "is": is_adi,
                    "detay": detay,
                    "sayi": rastgele.randint(2, 9),
                    "takim": takim,
                    "musteri": musteri,
                }
            ),
            "status": {
                "id": durum["id"],
                "name": durum["name"],
                "statusCategory": {
                    "key": durum["kategori"],
                    "name": durum["kategori_adi"],
                    "colorName": _kategori_rengi(durum["kategori"]),
                },
            },
            "priority": {"id": oncelik["id"], "name": oncelik["name"]},
            "issuetype": {"id": tur["id"], "name": tur["name"], "subtask": tur["subtask"]},
            "project": {"id": proje["id"], "key": proje["key"], "name": proje["name"]},
            "assignee": kisi_alani(atanan),
            "reporter": kisi_alani(raportor),
            "created": zaman(olusturma),
            "updated": zaman(guncelleme),
            "duedate": son_tarih,
            "resolution": cozum,
            "resolutiondate": cozum_tarihi,
            "labels": etiketler,
            "customfield_10001": _sec(rastgele, SPRINTLER),
            "customfield_10002": takim,
            "customfield_10003": musteri,
            "customfield_10004": round(rastgele.uniform(0.5, 13.0), 1),
            "customfield_10005": f"{rastgele.randint(1, 4)}.{rastgele.randint(0, 9)}.0",
        }

        kayitlar.append(
            {
                "id": str(30000 + sira),
                "key": anahtar,
                "self": f"/rest/api/2/issue/{anahtar}",
                "fields": alanlar,
            }
        )

    return kayitlar


def _kategori_rengi(kategori: str) -> str:
    if kategori == "done":
        return "green"
    if kategori == "indeterminate":
        return "yellow"
    return "blue-gray"
