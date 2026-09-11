# Holocron

Jira kayıtlarını yerelde takip etmek için küçük bir masaüstü aracı. Sunucu
yalnızca `127.0.0.1` üzerinde çalışır, arayüz tarayıcıda açılır; veriler
uygulamanın yanındaki `holocron.db` dosyasında kalır, hiçbir yere gönderilmez.

> Bu depo yalnızca aracın kendisini içerir. Kurum bilgisi, iç adres, gerçek
> proje anahtarı veya kayıt örneği bulunmaz; belgelerdeki tüm örnekler
> uydurmadır (`https://jira.example.com`, `DEMO-1`, `project = DEMO`).

## Durum

Aşama 2 tamam: gruplar (manuel ve JQL filtresi), kayıt listesi, sütun seçici,
arama ve arka planda çalışan **Güncelle** işi kullanılabilir durumda. Yerel
alanlar, Excel'e aktarım ve tam tema sonraki aşamalarda gelecek.

## Gereksinimler

- Python 3.11 veya üstü (Windows taşınabilir paketi kendi Python'ını getirir)
- Jira Server / Data Center (kişisel erişim anahtarı destekleyen sürümler) veya Jira Cloud

## Çalıştırma

### Linux / macOS

```bash
./holocron.sh
```

İlk çalıştırmada `.venv` kurulur ve bağımlılıklar yüklenir. Ağa çıkışın kapalı
olduğu ortamlarda paketleri `wheels/` klasörüne koyun; betik onları görürse
kurulumu çevrimdışı yapar.

### Windows

`holocron.bat` dosyasına çift tıklayın. Taşınabilir pakette `python-embed`
klasörü hazır gelir, hiçbir kurulum gerekmez.

Uygulama boş bir port bulur (tercihen 8765), varsayılan tarayıcıyı açar ve
adresi konsola yazar. Sekme kapatılıp beş dakika nabız gelmezse süreç kendini
kapatır; arayüzdeki **Kapat** düğmesi de aynı işi anında yapar.

Faydalı seçenekler:

```bash
./holocron.sh --port 8765     # sabit port
./holocron.sh --no-browser    # tarayıcıyı açma
```

## Kullanım

### Gruplar (Hangar)

Sol kenardaki **+ Yeni Filo** ile grup açılır. İki tür vardır:

- **Manuel**: kayıt anahtarlarını elle eklersiniz. **Kayıt ekle** kutusuna
  satır, virgül veya boşlukla ayrılmış anahtarları yapıştırın; Jira
  bağlantılarının içinden de anahtar okunur (`.../browse/DEMO-1`). Sonuç özeti
  kaç tanesinin eklendiğini, kaçının zaten var olduğunu ve anlaşılmayanları söyler.
- **JQL filtresi**: üyelik her Güncelle'de sorgudan gelir. Filtre grubuna elle
  eklenen kayıt *iğnelenir*: JQL sonucundan düşse bile grupta kalır. İğneyi
  satırdaki 📌 düğmesiyle açıp kapatabilirsiniz.

Grupların sırası yukarı/aşağı oklarıyla değişir, renk şeridi ışın kılıcı
paletinden seçilir (`blue`, `green`, `purple`, `red`, `yellow`, `white`).

### Sütunlar ve arama

**Sütunlar** düğmesi seçili sütunları sıralı gösterir; alan arama kutusundan
yenisini ekler, ✕ ile çıkarır, ok tuşlarıyla sırasını değiştirirsiniz.
**Genel varsayılan yap** seçimi sütunu olmayan bütün gruplara uygular.
Başlığa tıklamak sıralar, arama kutusu (`/` kısayolu) seçili bütün sütunlarda
büyük/küçük harf ayırmadan süzer; Türkçe `İ/ı` ayrımı gözetilmez.

Satıra tıklamak sağdan detay çekmecesini açar (`Esc` kapatır); anahtar sütunu
kaydı Jira'da yeni sekmede açar.

### Güncelle

**Güncelle** bütün grupları, **Güncelle (bu grup)** yalnız açık olanı tazeler.
Aynı anda tek iş çalışır; üst çubukta ilerleme ve aşama görünür, **İptal**
paketler arasında durur ve o ana kadar çekilenler kalır. İş bitince kaç kayıt
çekildiği, kaçının yeni/değişmiş olduğu ve bulunamayan anahtarlar özetlenir;
değişen hücreler beş saniye vurgulanır. Bir filtre grubunun JQL'i hatalıysa
yalnız o grup hata listesine düşer, iş sürer.

Kayıtlar `*navigable` (Jira Cloud'da `*all`) ile çekilir: sonradan hangi sütunu
seçerseniz seçin yeniden çekmeye gerek kalmaz.

## Jira bağlantısı

**Ayarlar** ekranından tanımlanır. Kimlik bilgileri `settings` tablosunda
şifreli durur; şifreleme anahtarı yanındaki `holocron.key` dosyasındadır
(yalnız sahibine okunur izinle oluşturulur). Kaydedilen token bir daha ekranda
gösterilmez, yalnızca "ayarlı / ayarsız" bilgisi görünür.

### Jira Server / Data Center (varsayılan)

1. Jira'da profilinizden bir **kişisel erişim anahtarı (PAT)** oluşturun.
2. Ayarlar ekranında mod olarak *Jira Server / Data Center* seçin.
3. Adres: `https://jira.example.com`
4. Kimlik türü *Kişisel erişim anahtarı*, alana anahtarı yapıştırın.
5. **Bağlantıyı sına** ile ad-soyad ve sunucu başlığını doğrulayın.

Kullanıcı adı + parola ile Basic kimlik de desteklenir, ancak PAT önerilir.

### Jira Cloud

1. Atlassian hesabınızdan bir **API token** oluşturun.
2. Mod olarak *Jira Cloud*, adres `https://demo.atlassian.net`.
3. E-posta adresinizi ve token'ı girin.

### Kurum ağı

Vekil sunucu, özel CA sertifikası ve SSL doğrulamayı kapatma seçenekleri
Ayarlar ekranındadır. SSL doğrulamayı kapatmak güvenliği düşürür; mümkünse
kurumun kök sertifikasını CA dosyası olarak verin.

## Geliştirme

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest -q
```

Testler gerçek Jira'ya çıkmaz; `requests` oturumu sahte bir sunucuyla
değiştirilir (`tests/fake_jira.py`).

## Dosya düzeni

| Yol | İçerik |
| --- | --- |
| `app/` | Uygulama kodu (API, veritabanı, Jira istemcisi, arayüz) |
| `app/repository.py` | Gruplar, üyelikler, kayıtlar ve alan kataloğu (tüm SQL) |
| `app/fields.py` | Alan değerlerini metne çeviren saf formatlayıcı |
| `app/refresh.py` | Arka planda çalışan Güncelle işi |
| `app/static/` | Vanilla HTML/CSS/JS arayüz, dış bağımlılık yok |
| `tests/` | pytest testleri ve sahte Jira sunucusu |
| `tools/build_portable.py` | Windows taşınabilir paketini üretir |
| `holocron.db` | Yerel veritabanı (depoya girmez) |
| `holocron.key` | Şifreleme anahtarı (depoya girmez, yedekleyin) |

`holocron.key` dosyasını kaybederseniz kayıtlı token çözülemez; Ayarlar
ekranından yeniden girmeniz gerekir.

## Lisans

MIT. Ayrıntı için `LICENSE`.
