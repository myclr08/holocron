"""Demo veritabanini tohumlar.

Kural: ham SQL YOK. Her sey Holocron'un kendi katmanlarindan gecer
(`repository`, `gamify`, `gamify_repo`, `mailsend_repo`, `settings_store`);
sema degisirse bu dosya da onlarla birlikte tasinir.

Tohumlama iki asamada calisir:

  1. `ayarlari_yaz` + `filolari_kur`  -> Jira'ya baglanmadan once
  2. `geri_kalani_kur`               -> "Guncelle" kayitlari cektikten sonra

Ikinci asama kayitlarin veritabaninda olmasini bekler: yerel alan degeri,
gorev-kayit bagi ve kayit kisileri var olmayan anahtara yazilamaz.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app import gamify, gamify_repo, mailsend_repo, repository, settings_store
from app.mail import fake as sahte_posta

from . import veri

# Sahte PAT: gercek bir anahtara benzemesin diye acikca "sahte" yaziyor.
SAHTE_PAT = "demo-sahte-pat-0000000000"

# Filo tanimlari: ikisi JQL filtreli, ikisi elle anahtar listeli.
FILOLAR: tuple[dict[str, Any], ...] = (
    {
        "name": "Bana atananlar",
        "kind": repository.KIND_FILTER,
        "jql": "assignee = currentUser() AND status != Kapalı ORDER BY updated DESC",
        "color": "blue",
        "columns": [
            "issuekey",
            "summary",
            "status",
            "priority",
            "customfield_10001",
            "updated",
        ],
    },
    {
        "name": "Ödeme ekibi açık işler",
        "kind": repository.KIND_FILTER,
        "jql": (
            "project = PRJ AND status in (Açık, Geliştiriliyor, Test) "
            "AND updated >= -45d ORDER BY priority ASC, updated DESC"
        ),
        "color": "purple",
        "columns": [
            "issuekey",
            "summary",
            "status",
            "assignee",
            "customfield_10002",
            "updated",
        ],
    },
    {
        "name": "Haftalık toplantı listesi",
        "kind": repository.KIND_MANUAL,
        "color": "green",
        "columns": ["issuekey", "summary", "status", "assignee", "duedate"],
        "elle": 9,
    },
    {
        "name": "Sürüm öncesi takip",
        "kind": repository.KIND_MANUAL,
        "color": "yellow",
        "columns": ["issuekey", "summary", "status", "priority", "customfield_10005"],
        "elle": 6,
    },
)

# Teams mesaj belirteci (uydurma kiraci/sohbet kimlikleriyle): bir gorevin
# aciklamasinda ve bir kaydin yerel alaninda duruyor. Ekranda tiklanabilir bir
# cipe donusur, Excel'e ve e-postaya HIC girmez (bkz. app/teamslink.py).
TEAMS_KIRACI = "00000000-0000-4000-8000-000000000001"
TEAMS_SOHBET_BELIRTEC = (
    "[[teams: Deniz Akgün · Ödeme ekibi · 16 Eyl 2026 14:14|"
    "https://teams.microsoft.com/l/message/"
    "19:2f1c9a7b4d8e4f0b9c3a5d6e7f801234@unq.gbl.spaces/1789557243396"
    "?groupId=&parentMessageId=1789557243396"
    f"&tenantId={TEAMS_KIRACI}"
    "&context=%7B%22contextType%22%3A%22chat%22%7D]]"
)
TEAMS_KANAL_BELIRTEC = (
    "[[teams: Ayça Yıldırım · Ödemeler / Duyurular · 15 Eyl 2026 09:40|"
    "https://teams.microsoft.com/l/message/"
    "19:7b3d5e9a1c2f4068b8d7e6a5c4b30099@thread.tacv2/1789461612874"
    f"?tenantId={TEAMS_KIRACI}&groupId=00000000-0000-4000-8000-000000000002"
    "&parentMessageId=&teamName=%C3%96demeler&channelName=Duyurular&createdTime=]]"
)

# Yerel ek alanlar: Jira'da olmayan, kullanicinin kendi tuttugu alanlar.
YEREL_ALANLAR: tuple[dict[str, Any], ...] = (
    {"name": "Benim notum", "type": "text", "track_history": True},
    {"name": "İş birimi puanı", "type": "number", "track_history": False},
    {"name": "Takip tarihi", "type": "date", "track_history": True},
    {
        "name": "Bizdeki durum",
        "type": "select",
        "options": ["Beklemede", "Bizde", "Karşı tarafta", "Bitti"],
        "track_history": True,
    },
)

YEREL_NOTLAR: tuple[str, ...] = (
    "İş birimiyle konuşuldu, kapsam daraltıldı.",
    "Test ortamında tekrar edilemedi; canlı log isteniyor.",
    "Sürüm planına alındı, geliştirme bekliyor.",
    "Karşı ekipten dönüş bekleniyor.",
    "Analiz notları paylaşıldı, onay bekliyor.",
    "Geçici çözüm uygulandı, kalıcısı sıradaki sprintte.",
)

# Gorevlerim panosu: uc sutuna dagilmis 12 kart.
GOREVLER: tuple[dict[str, Any], ...] = (
    {
        "title": "Mutabakat raporu farkını çıkar",
        "description": (
            "Günsonu raporu ile kanal toplamı arasındaki farkı kalem kalem ayır.\n"
            f"Kaynak mesaj: {TEAMS_SOHBET_BELIRTEC}"
        ),
        "note": "Önce son üç günün dosyalarını karşılaştır.",
        "status": "todo",
        "kayit_ara": "Mutabakat raporu",
        "son_tarih_gun": 2,
    },
    {
        "title": "Sürüm notlarını hazırla",
        "description": (
            "4.2.0 sürümüne giren kayıtların listesini çıkar.\n"
            "Sürüm takvimi: https://ornek.local/wiki/surum-4-2-0"
        ),
        "status": "todo",
        "son_tarih_gun": 5,
    },
    {
        "title": "Test ortamında veri maskeleme kontrolü",
        "description": "Maskeleme sonrası müşteri adlarının görünmediğini doğrula.",
        "note": "Denetim bulgusuna bağlı, geciktirme.",
        "status": "todo",
        "son_tarih_gun": -3,
    },
    {
        "title": "Kapasite raporunu güncelle",
        "status": "todo",
        "son_tarih_gun": 9,
    },
    {
        "title": "Yeni ekip üyesi için yetki talebi",
        "description": "Test ve canlı okuma yetkileri açılacak.",
        "status": "todo",
    },
    {
        "title": "EFT kuyruğu yeniden deneme mantığını gözden geçir",
        "description": "Üç denemeden sonra ölü mektup kutusuna düşsün.",
        "note": "Tasarım notu yazılacak.",
        "status": "doing",
        "kayit_ara": "EFT kuyruğu",
        "son_tarih_gun": 1,
    },
    {
        "title": "Sanal POS iade akışı analizi",
        "status": "doing",
        "kayit_ara": "Sanal POS iade",
        "son_tarih_gun": 4,
    },
    {
        "title": "Bildirim sertifikası yenileme planı",
        "description": "Sertifika 30 gün içinde doluyor; yenileme adımlarını yaz.",
        "status": "doing",
        "son_tarih_gun": 0,
    },
    {
        "title": "Şube ekranı performans ölçümü",
        "note": "İlk ölçüm alındı, ikinci tur bekliyor.",
        "status": "doing",
    },
    {
        "title": "IBAN doğrulama kütüphanesini yükselt",
        "description": "Yeni sürüme geçildi, regresyon testleri geçti.",
        "status": "done",
        "kayit_ara": "IBAN doğrulama",
    },
    {
        "title": "Haftalık ekip toplantısı notları",
        "status": "done",
    },
    {
        "title": "Döviz kuru önbelleği süresini düşür",
        "description": "TTL 15 dakikadan 3 dakikaya indirildi.",
        "status": "done",
        "kayit_ara": "Döviz kuru önbelleği",
    },
)

# Gorevlerin "son durum" defteri: baslik -> (kac saat once, metin). Bes gorevde
# ikiden dorde girdi var; demo acildiginda kartlarda gri son durum satiri ve
# tiklaninca dolu bir gecmis popover'i gorunsun diye.
SON_DURUMLAR: dict[str, tuple[tuple[int, str], ...]] = {
    "Mutabakat raporu farkını çıkar": (
        (72, "Fark listesi çıkarıldı, 14 kalem var."),
        (30, "Sekiz kalem kur farkından; kalanı inceleniyor."),
        (4, "İş birimine ara özet gönderildi, dönüş bekleniyor."),
    ),
    "Test ortamında veri maskeleme kontrolü": (
        (96, "Maskeleme betiği test ortamında koştu."),
        (20, "İki ekranda ad görünüyor; hata kaydı açıldı."),
    ),
    "EFT kuyruğu yeniden deneme mantığını gözden geçir": (
        (120, "Mevcut mantık çıkarıldı."),
        (60, "Ölü mektup kutusu tasarımı taslak halinde."),
        (26, "Tasarım gözden geçirmede."),
        (3, "Geri bildirimler işlendi, geliştirme başlıyor."),
    ),
    "Şube ekranı performans ölçümü": (
        (48, "İlk ölçüm alındı: açılış 2,4 sn."),
        (8, "İkinci tur için yük üretici hazırlandı."),
    ),
    "IBAN doğrulama kütüphanesini yükselt": (
        (200, "Yükseltme dalı açıldı."),
        (150, "Regresyon testleri geçti."),
        (100, "Canlıya alındı, takip ediliyor."),
    ),
}

# Adres defteri: 15 kisi + 2 dagitim listesi (hepsi uydurma).
EK_KISILER: tuple[tuple[str, str], ...] = (
    ("İş Analizi Ekibi", "is-analizi"),
    ("Sistem Yönetimi", "sistem-yonetimi"),
)

DAGITIM_LISTELERI: tuple[tuple[str, str], ...] = (
    ("Ödeme Platformu Ekibi", "odeme-platformu"),
    ("Sürüm Duyuruları", "surum-duyurulari"),
)

# Teams / kisa mesaj sablonlari (goc zaten ikisini yaziyor, bunlar ustune).
TEAMS_SABLONLARI: tuple[tuple[str, str], ...] = (
    (
        "Test ortamı sorusu",
        "{key} — {summary} kaydı test ortamında hangi aşamada? Durum: {status}. {url}",
    ),
    (
        "Sürüm öncesi kontrol",
        "Merhaba, {key} sürüm öncesi listede. {assignee} tarafında kapanma tarihi netleşti mi? {url}",
    ),
)

# Filo e-posta sablonlari (Excel ekli gonderim).
POSTA_SABLONLARI: tuple[dict[str, Any], ...] = (
    {
        "name": "Haftalık durum özeti",
        "to_addresses": "odeme-platformu@example.com",
        "cc_addresses": "is-analizi@example.com",
        "subject": "Haftalık durum: {grup}",
        "body": (
            "Merhaba,\n\n"
            "Ekte {grup} filosundaki {adet} kaydın güncel durumu var.\n"
            "Sorusu olan bana dönebilir.\n\n"
            "İyi çalışmalar."
        ),
        "attach_excel": True,
        "inline_table": True,
    },
    {
        "name": "Sürüm öncesi hatırlatma",
        "to_addresses": "surum-duyurulari@example.com",
        "subject": "Sürüm öncesi açık kayıtlar: {grup}",
        "body": (
            "Merhaba,\n\n"
            "Sürüm öncesi listede {adet} kayıt açık görünüyor.\n"
            "Bugün içinde durum bilgisi rica ederim.\n\n"
            "Teşekkürler."
        ),
        "attach_excel": True,
        "inline_table": False,
    },
)

# Outlook'tan uretilmis gibi gorunen konusmalar (Linux'ta Outlook yok).
POSTA_KONUSMALARI: tuple[dict[str, Any], ...] = (
    {
        "konu": "Mutabakat farkı hk.",
        "gonderen": "pelin.koc",
        "govde": "Merhaba, dünkü mutabakat raporunda 3 kalem fark görünüyor. Bakabilir misin?",
        "saat_once": 6,
        "mesaj_adedi": 3,
    },
    {
        "konu": "Şube ekranı yavaşlığı",
        "gonderen": "onur.cetin",
        "govde": "Şube ekibinden liste ekranının geç açıldığına dair iki şikâyet geldi.",
        "saat_once": 30,
        "mesaj_adedi": 2,
    },
    {
        "konu": "Sürüm 4.2.0 kapsamı",
        "gonderen": "gizem.aydin",
        "govde": "Sürüm kapsamını bugün kapatmamız gerekiyor; listeyi teyit eder misin?",
        "saat_once": 52,
        "mesaj_adedi": 1,
    },
)


def _simdi(an: datetime | None = None) -> datetime:
    return an or datetime.now(timezone.utc)


def _damga(an: datetime) -> str:
    return an.replace(microsecond=0).isoformat()


# --- 1. asama: ayarlar ve filolar ---------------------------------------


def ayarlari_yaz(context: Any, jira_url: str) -> None:
    """Jira baglantisini sahte sunucuya yonlendirir, acilis ekranini atlar."""
    ayar = context.settings
    ayar.set("jira.mode", settings_store.MODE_SERVER)
    ayar.set("jira.base_url", jira_url)
    ayar.set("jira.auth_type", settings_store.AUTH_PAT)
    ayar.set("jira.username", veri.BEN["kullanici"])
    ayar.set("jira.email", veri.eposta(veri.BEN["kullanici"]))
    ayar.set("jira.secret", SAHTE_PAT)
    # Sahte sunucu 127.0.0.1'de: kurumsal vekil ayari isin icine karismasin.
    ayar.set("net.proxy_mode", settings_store.PROXY_DIRECT)
    ayar.set("net.verify_ssl", "0")
    # Acilis animasyonu demo ekraninda bir kez bile beklemesin.
    ayar.set("ui.crawl_seen", "1")
    # Outlook yok; posta taramasi "Guncelle" isini uzatmasin.
    ayar.set("mail.enabled", "0")
    ayar.set("mail.scan_on_refresh", "0")
    ayar.set("teams.topic_format", "{key} durum")


def filolari_kur(context: Any) -> list[dict[str, Any]]:
    """Dort filo: ikisi JQL filtreli, ikisi elle listeli (uyeler sonra)."""
    conn = context.connection()
    if repository.list_groups(conn):
        return repository.list_groups(conn)
    for tanim in FILOLAR:
        repository.create_group(
            conn,
            name=tanim["name"],
            kind=tanim["kind"],
            jql=tanim.get("jql"),
            color=tanim["color"],
            columns=tanim["columns"],
        )
    return repository.list_groups(conn)


def elle_uyeleri_ekle(context: Any, anahtarlar: list[str]) -> None:
    """Elle listeli filolara kayit anahtari yazar."""
    conn = context.connection()
    gruplar = {grup["name"]: grup for grup in repository.list_groups(conn)}
    baslangic = 0
    for tanim in FILOLAR:
        adet = tanim.get("elle")
        if not adet:
            continue
        grup = gruplar.get(tanim["name"])
        if grup is None or grup["count"]:
            continue
        secilen = anahtarlar[baslangic : baslangic + adet]
        baslangic += adet
        if secilen:
            repository.add_items(conn, grup["id"], secilen)


# --- 2. asama: geri kalan her sey ---------------------------------------


def yerel_alanlari_kur(context: Any, anahtarlar: list[str], an: datetime | None = None) -> None:
    """Yerel ek alanlar ve birkac dolu deger."""
    conn = context.connection()
    if repository.list_local_fields(conn):
        return
    alanlar = [
        repository.create_local_field(
            conn,
            name=tanim["name"],
            type=tanim["type"],
            options=tanim.get("options"),
            track_history=tanim.get("track_history", False),
        )
        for tanim in YEREL_ALANLAR
    ]
    simdi = _simdi(an)
    durumlar = YEREL_ALANLAR[3]["options"]
    for sira, anahtar in enumerate(anahtarlar[:14]):
        repository.set_local_value(conn, anahtar, alanlar[0]["id"], YEREL_NOTLAR[sira % len(YEREL_NOTLAR)])
        if sira % 2 == 0:
            repository.set_local_value(conn, anahtar, alanlar[1]["id"], str(40 + (sira * 7) % 60))
        if sira % 3 == 0:
            hedef = (simdi + timedelta(days=3 + sira)).date().isoformat()
            repository.set_local_value(conn, anahtar, alanlar[2]["id"], hedef)
        repository.set_local_value(conn, anahtar, alanlar[3]["id"], durumlar[sira % len(durumlar)])
    # Bir kaydin yerel metin alaninda Teams mesaj belirteci dursun: cekmecede
    # ve grid hucresinde cip olarak gorunur.
    if anahtarlar:
        repository.set_local_value(
            conn,
            anahtarlar[0],
            alanlar[0]["id"],
            f"Kanal duyurusu: {TEAMS_KANAL_BELIRTEC} sonrasında kapsam netleşti.",
        )
    # Gecmis dolu gorunsun: birkac alanin degeri bir kez daha degissin.
    for anahtar in anahtarlar[:5]:
        repository.set_local_value(conn, anahtar, alanlar[3]["id"], "Bitti")


def gorevleri_kur(context: Any, anahtarlar: list[str], an: datetime | None = None) -> None:
    """Gorevlerim panosu: 12 kart, uc sutuna dagilmis."""
    conn = context.connection()
    if repository.list_tasks(conn):
        return
    simdi = _simdi(an)
    ozetler = _ozetler(conn, anahtarlar)
    for tanim in GOREVLER:
        son_tarih = None
        if "son_tarih_gun" in tanim:
            son_tarih = (simdi + timedelta(days=tanim["son_tarih_gun"])).date().isoformat()
        gorev = repository.create_task(
            conn,
            title=tanim["title"],
            description=tanim.get("description"),
            note=tanim.get("note"),
            due_date=son_tarih,
            status=tanim["status"],
            issue_key=_kayit_bul(ozetler, tanim.get("kayit_ara")),
        )
        for saat_once, metin in SON_DURUMLAR.get(tanim["title"], ()):
            repository.set_task_son_durum(
                conn, gorev["id"], metin, at=_damga(simdi - timedelta(hours=saat_once))
            )


def _ozetler(conn: Any, anahtarlar: list[str]) -> list[tuple[str, str]]:
    """(anahtar, ozet) listesi: gorev-kayit bagi konuya gore kurulsun."""
    kayitlar = repository.get_issues(conn, anahtarlar)
    return [
        (anahtar, str(((kayitlar[anahtar].get("raw") or {}).get("fields") or {}).get("summary") or ""))
        for anahtar in anahtarlar
        if anahtar in kayitlar
    ]


def _kayit_bul(ozetler: list[tuple[str, str]], aranan: str | None) -> str | None:
    """Ozeti arananla baslayan ilk kayit; bulunamazsa bag kurulmaz."""
    if not aranan:
        return None
    for anahtar, ozet in ozetler:
        if aranan.casefold() in ozet.casefold():
            return anahtar
    return None


def kisileri_kur(context: Any, anahtarlar: list[str]) -> None:
    """Adres defteri: 15 kisi + 2 dagitim listesi; birkac kayda kisi bagi."""
    conn = context.connection()
    from app import teams

    if not repository.list_contacts(conn):
        for kisi in veri.KISILER[:13]:
            repository.upsert_contact(
                conn, veri.eposta(kisi["kullanici"]), kisi["ad"], kind=teams.CONTACT_PERSON
            )
        for ad, kullanici in EK_KISILER:
            repository.upsert_contact(
                conn, veri.eposta(kullanici), ad, kind=teams.CONTACT_PERSON
            )
        for ad, kullanici in DAGITIM_LISTELERI:
            repository.upsert_contact(
                conn, veri.eposta(kullanici), ad, kind=teams.CONTACT_LIST, source="gal"
            )
    # Birkac kaydin kendi kisi listesi olsun: "Son durumu sor" dolu gelsin.
    for sira, anahtar in enumerate(anahtarlar[:6]):
        if repository.list_issue_contacts(conn, anahtar):
            continue
        secilen = veri.KISILER[sira : sira + 2] or veri.KISILER[:2]
        repository.set_issue_contacts(
            conn,
            anahtar,
            [
                {"email": veri.eposta(kisi["kullanici"]), "name": kisi["ad"]}
                for kisi in secilen
            ],
        )


def sablonlari_kur(context: Any) -> None:
    """Teams/kisa mesaj ve filo e-posta sablonlari."""
    conn = context.connection()
    mevcut = {sablon["name"] for sablon in repository.list_templates(conn)}
    for ad, govde in TEAMS_SABLONLARI:
        if ad not in mevcut:
            repository.create_template(conn, ad, govde)

    posta_mevcut = {sablon["name"] for sablon in mailsend_repo.list_templates(conn)}
    for tanim in POSTA_SABLONLARI:
        if tanim["name"] not in posta_mevcut:
            mailsend_repo.create_template(conn, dict(tanim))

    # Ilk filoya bir posta sablonu baglansin: "E-posta ile gonder" hazir gelsin.
    gruplar = repository.list_groups(conn)
    sablonlar = mailsend_repo.list_templates(conn)
    if gruplar and sablonlar and mailsend_repo.group_template_id(conn, gruplar[0]["id"]) is None:
        mailsend_repo.set_group_template(conn, gruplar[0]["id"], sablonlar[0]["id"])


def posta_gorevleri_kur(context: Any, an: datetime | None = None) -> None:
    """Outlook'tan gelmis gibi gorunen gorevler ve konusma kayitlari.

    Linux'ta Outlook yok; tablo yine de dolu gorunsun ki "Outlook'tan gorev"
    akisinin ekranda nasil durdugu gorulebilsin.
    """
    conn = context.connection()
    simdi = _simdi(an)
    for sira, tanim in enumerate(POSTA_KONUSMALARI):
        konusma = f"AAQkDEMO{sira:04d}"
        if repository.get_mail_conversation(conn, konusma):
            continue
        geldi = simdi - timedelta(hours=tanim["saat_once"])
        gonderen = veri.eposta(tanim["gonderen"])
        gorev = repository.create_mail_task(
            conn,
            title=tanim["konu"],
            description=tanim["govde"],
            note="Outlook'tan üretildi (demo).",
            conversation_id=konusma,
            sender=gonderen,
            received_at=_damga(geldi),
            entry_id=f"ENTRY-DEMO-{sira}",
            store_id="STORE-DEMO",
        )
        repository.upsert_mail_conversation(
            conn, konusma, task_id=gorev["id"], seen_at=_damga(geldi)
        )
        for mesaj_sira in range(int(tanim["mesaj_adedi"])):
            mesaj = sahte_posta.message(
                message_id=f"<demo-{sira}-{mesaj_sira}@example.com>",
                subject=tanim["konu"] if mesaj_sira == 0 else f"RE: {tanim['konu']}",
                sender=gonderen,
                to=[veri.eposta(veri.BEN["kullanici"])],
                received_at=geldi + timedelta(hours=mesaj_sira),
                body=tanim["govde"],
                conversation_id=konusma,
                entry_id=f"ENTRY-DEMO-{sira}-{mesaj_sira}",
                store_id="STORE-DEMO",
            )
            repository.record_mail_message(conn, mesaj, task_id=gorev["id"])
            if mesaj_sira:
                repository.bump_mail_task(conn, gorev["id"], _damga(mesaj.received_at))


# --- Sefer (oyunlastirma) ------------------------------------------------

# Uc haftalik defter: (gun once, kaynak, tur, baslik).
DEFTER_KALIPLARI: tuple[tuple[str, str, str], ...] = (
    (gamify.SOURCE_TASK, gamify.KIND_DONE, "Görev kapatıldı: {baslik}"),
    (gamify.SOURCE_TASK, gamify.KIND_DONE_BEFORE_DUE, "Son tarihinden önce: {baslik}"),
    (gamify.SOURCE_JIRA, gamify.KIND_STATUS_DONE, "{baslik} tamamlandı"),
    (gamify.SOURCE_JIRA, gamify.KIND_STATUS_PROGRESS, "{baslik} işleme alındı"),
    (gamify.SOURCE_JIRA, gamify.KIND_LEFT_GROUP, "{baslik} filodan düştü"),
    (gamify.SOURCE_TEAMS, gamify.KIND_STATUS_ASKED, "Son durum soruldu: {baslik}"),
    (gamify.SOURCE_MAIL, gamify.KIND_MAIL_FAST, "E-posta görevi hızlı kapandı: {baslik}"),
)

# Her is gununde deftere kac satir yazilir (rutbe ilerlemesi gorunsun).
DEFTER_GUNLUK_SATIR = 4

DEFTER_BASLIKLARI: tuple[str, ...] = (
    "Mutabakat raporu farkı",
    "EFT kuyruğu yeniden deneme",
    "Sanal POS iade akışı",
    "Şube ekranı performansı",
    "Kart limiti sorgusu",
    "Bildirim sertifikası",
    "IBAN doğrulama kütüphanesi",
    "Döviz kuru önbelleği",
    "Hesap özeti PDF üretimi",
    "Yetki matrisi güncellemesi",
)

HAFTALIK_EMIRLER: tuple[tuple[str, str, int, int], ...] = (
    (gamify.QUEST_OVERDUE, "Gecikmiş görevleri kapat", 3, 25),
    (gamify.QUEST_DOING_LIMIT, "\"Yapılıyor\" sütununu 5 kartın altına indir", 1, 25),
    (gamify.QUEST_STALE_ASK, "Bayatlamış 5 kaydın son durumunu sor", 5, 25),
)

# Demoda kazanilmis gorunen rozetler. Katalogda 50'den fazla rozet var; demo
# duvarin nasil doldugunu gostermek icin bir avucunu isaretler, geri kalani
# kilitli ve ilerleme cubuklu kalir ("9/25" gibi) -- ekran hem dolu hem de
# "daha var" duygusu versin.
KAZANILAN_ROZETLER: tuple[str, ...] = (
    gamify.BADGE_FIRST_TASK,
    gamify.BADGE_TASK_10,
    gamify.BADGE_FIRST_ISSUE,
    gamify.BADGE_STREAK_5,
    gamify.BADGE_FIRST_QUEST,
    gamify.BADGE_FIRST_FLEET,
    gamify.BADGE_FIRST_LOCAL_FIELD,
    gamify.BADGE_FIRST_FIX,
    gamify.BADGE_FIRST_EXCEL,
    gamify.BADGE_CARTOGRAPHER,
)

# Ilerlemesi yarida gorunsun diye birakilan is izleri: 12 "Duzelt" (Metin
# Ustasi 12/25) ve 3 Excel dokumu.
DEMO_DUZELTME = 12
DEMO_EXCEL = 3


def seferi_kur(context: Any, an: datetime | None = None) -> dict[str, Any] | None:
    """Yaklasik uc haftalik XP gecmisi, rutbe ilerlemesi, rozet ve emirler."""
    conn = context.connection()
    if gamify_repo.active_campaign(conn) is not None:
        return gamify_repo.active_campaign(conn)

    gamify.ensure_rules(context)
    simdi = _simdi(an)
    baslangic = (simdi - timedelta(days=21)).date()
    bitis = (simdi + timedelta(days=21)).date()
    sefer = gamify_repo.create_campaign(
        conn,
        name="Sonbahar Seferi",
        ends_at=bitis.isoformat(),
        target_xp=1000,
        starts_at=baslangic.isoformat(),
        today=simdi.date().isoformat(),
    )
    kurallar = gamify_repo.rules_map(conn)

    # Defter: her is gunune birkac satir.
    sayac = 0
    gun = baslangic
    isaretli_gunler: list[str] = []
    while gun <= simdi.date():
        if gamify.is_workday(gun):
            for _ in range(DEFTER_GUNLUK_SATIR):
                kaynak, tur, kalip = DEFTER_KALIPLARI[sayac % len(DEFTER_KALIPLARI)]
                baslik = DEFTER_BASLIKLARI[sayac % len(DEFTER_BASLIKLARI)]
                kural = kurallar.get(tur)
                if kural is None:
                    sayac += 1
                    continue
                an_damgasi = _damga(
                    datetime.combine(gun, datetime.min.time(), tzinfo=timezone.utc)
                    + timedelta(hours=9 + (sayac % 7))
                )
                gamify_repo.add_event(
                    conn,
                    sefer["id"],
                    kaynak,
                    tur,
                    int(kural["points"]),
                    ref=f"demo-{sayac}",
                    title=kalip.format(baslik=baslik),
                    note="Demo verisi",
                    at=an_damgasi,
                )
                sayac += 1
            gamify_repo.mark_day(conn, sefer["id"], gun.isoformat())
            isaretli_gunler.append(gun.isoformat())
        gun += timedelta(days=1)

    # Seri gunleri de deftere yazilir: XP defteri "neden bu puan" diye
    # bakildiginda bos kalmasin.
    seri_kurali = kurallar.get(gamify.KIND_STREAK_DAY)
    if seri_kurali:
        for isaretli in isaretli_gunler:
            gamify_repo.add_event(
                conn,
                sefer["id"],
                gamify.SOURCE_STREAK,
                gamify.KIND_STREAK_DAY,
                int(seri_kurali["points"]),
                ref=f"streak-{isaretli}",
                title=f"Etkin iş günü: {isaretli}",
                at=f"{isaretli}T18:00:00+00:00",
            )

    # Is izleri: "Duzelt" ve Excel hicbir tabloda gorunmedigi icin kendi
    # defterlerine yazilir; rozet ilerlemesi bunlardan okunur.
    for sira in range(DEMO_DUZELTME):
        gamify_repo.log_activity(
            conn, gamify_repo.ACTIVITY_FIX, f"demo-{sira}",
            at=_damga(simdi - timedelta(days=18 - sira)),
        )
    for sira in range(DEMO_EXCEL):
        gamify_repo.log_activity(
            conn, gamify_repo.ACTIVITY_EXCEL, f"demo-{sira}",
            at=_damga(simdi - timedelta(days=14 - sira * 5)),
        )

    # Rozetler: kosullari yeniden hesaplamadan dogrudan yazilir (demo).
    rozet_kurali = kurallar.get(gamify.KIND_BADGE_EARNED)
    for sira, kod in enumerate(KAZANILAN_ROZETLER):
        kazanildi = _damga(simdi - timedelta(days=18 - sira))
        gamify_repo.earn_badge(conn, sefer["id"], kod, at=kazanildi)
        if rozet_kurali:
            gamify_repo.add_event(
                conn,
                sefer["id"],
                gamify.SOURCE_BADGE,
                gamify.KIND_BADGE_EARNED,
                int(rozet_kurali["points"])
                * gamify.RARITY_MULTIPLIERS.get(gamify.BADGES_BY_CODE[kod]["rarity"], 1),
                ref=f"badge:{kod}",
                title=f"Rozet kazanıldı: {gamify.BADGES_BY_CODE[kod]['label']}",
                at=kazanildi,
            )

    # Haftalik emirler: iki gecmis hafta bitmis, bu hafta suruyor.
    for hafta in range(2, -1, -1):
        hafta_basi = gamify.week_start_of((simdi - timedelta(weeks=hafta)).date())
        for kod, baslik, hedef, puan in HAFTALIK_EMIRLER:
            emir = gamify_repo.add_quest(
                conn, sefer["id"], hafta_basi, kod, baslik, hedef, puan
            )
            if emir is None:
                continue
            if hafta > 0:
                bitti = _damga(simdi - timedelta(days=hafta * 7 - 2))
                gamify_repo.set_quest_progress(conn, emir["id"], hedef, done_at=bitti)
                emir_kurali = kurallar.get(gamify.KIND_QUEST_DONE)
                if emir_kurali:
                    gamify_repo.add_event(
                        conn,
                        sefer["id"],
                        gamify.SOURCE_QUEST,
                        gamify.KIND_QUEST_DONE,
                        int(emir_kurali["points"]),
                        ref=f"quest-{emir['id']}",
                        title=f"Haftalık emir tamam: {baslik}",
                        at=bitti,
                    )
            else:
                gamify_repo.set_quest_progress(conn, emir["id"], max(hedef - 2, 0))

    return gamify_repo.get_campaign(conn, sefer["id"])


# --- dis kapi ------------------------------------------------------------


def geri_kalani_kur(context: Any, anahtarlar: list[str], an: datetime | None = None) -> None:
    """Kayitlar cekildikten SONRA calisan tohumlama.

    `elle_uyeleri_ekle` buradan cagrilmaz: elle uyeler "Guncelle"den ONCE
    yazilir ki ayni iste onlarin kayitlari da Jira'dan cekilsin.
    """
    yerel_alanlari_kur(context, anahtarlar, an)
    gorevleri_kur(context, anahtarlar, an)
    kisileri_kur(context, anahtarlar)
    sablonlari_kur(context)
    posta_gorevleri_kur(context, an)
    seferi_kur(context, an)
