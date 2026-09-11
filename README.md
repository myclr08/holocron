# Holocron

Jira kayıtlarını yerelde takip etmek için küçük bir masaüstü aracı. Sunucu
yalnızca `127.0.0.1` üzerinde çalışır, arayüz tarayıcıda açılır; veriler
uygulamanın yanındaki `holocron.db` dosyasında kalır, hiçbir yere gönderilmez.

> Bu depo yalnızca aracın kendisini içerir. Kurum bilgisi, iç adres, gerçek
> proje anahtarı veya kayıt örneği bulunmaz; belgelerdeki tüm örnekler
> uydurmadır (`https://jira.example.com`, `DEMO-1`, `project = DEMO`).

## Durum

Aşama 4 tamam: gruplar (manuel ve JQL filtresi), kayıt listesi, sütun seçici,
arama, arka planda çalışan **Güncelle** işi, kendi tanımladığınız **yerel
alanlar** (alan başına değişim geçmişiyle) ve **Excel'e aktarma** kullanılabilir
durumda. Tam tema sonraki aşamada gelecek.

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
büyük/küçük harf ayırmadan süzer; Türkçe `İ/ı` ayrımı gözetilmez. Yerel alan
değerleri, sütunu seçili olmasa bile aramaya dahildir.

Satıra tıklamak sağdan detay çekmecesini açar (`Esc` kapatır); anahtar sütunu
kaydı Jira'da yeni sekmede açar. Yerel alan hücresine tıklamak çekmeceyi açmaz,
hücreyi düzenlemeye alır.

### Yerel alanlar

Üst çubuktaki **Alanlar** düğmesi kendi bilginizi tutacağınız alanları yönetir.
Bu alanlar Jira'ya **asla gönderilmez**, **Güncelle** bunların üzerine yazmaz.

- Alanlar geneldir (bütün gruplarda ortak), değer kayıt başına tektir. Bir kayıt
  iki grupta geçse bile değeri tektir.
- Tipler: metin, sayı, tarih, evet/hayır, liste. Değer yazılırken doğrulanır ve
  normalleştirilir: sayı ondalık noktalı düz metne (`3,5` → `3.5`), tarih ISO
  biçimine (`11.09.2026` → `2026-09-11`) çevrilir, liste değeri seçeneklerden
  biri olmak zorundadır. Hücreyi boşaltmak değeri siler.
- Alanın tipi sonradan değiştirilemez; adı, seçenekleri ve geçmiş anahtarı
  değişebilir. Ad büyük/küçük harf ve Türkçe `İ/ı` ayrımı gözetilmeden benzersizdir.
- Alanı silmek o alandaki bütün değerleri ve geçmişi siler, sütunu kullanan
  grupların sütun listesinden de düşürür. Arayüz kaç kayıt etkileneceğini sorar.

Grid'de yerel hücreye tıklayın: tipe göre metin kutusu, tarih seçici, onay kutusu
ya da açılır liste açılır. `Enter` kaydeder, `Esc` vazgeçer, odak kaybı kaydeder.
Hatalı değer kırmızı kenarlıkla ve kısa bir mesajla geri döner. Aynı alanlar
detay çekmecesinde de ayrı bir bölümde düzenlenebilir.

#### Geçmiş

Alan tanımında **geçmişi tut** işaretliyse, değer her *gerçekten* değiştiğinde
bir satır düşülür (aynı değeri tekrar yazmak satır eklemez). Hücrenin kenarındaki
saat simgesi değişim sayısını gösterir; tıklayınca zaman çizelgesi açılır
(`11.09.2026 09:05  bekliyor → müşteriye soruldu`). Satırlar tek tek silinebilir,
**Geçmişi temizle** hepsini kaldırır — değerin kendisi durur.

Geçmişi sonradan açarsanız kayıt o andan itibaren tutulur; kapatırsanız o ana
kadar biriken geçmiş **silinmez**, yalnızca yeni satır eklenmez.

Geçmişi tutulan her alan için sütun seçicide iki türetilmiş sütun daha çıkar:
**son değişim** (tarih-saat) ve **kaç kez değişti** (sayı). İkisi de normal sütun
gibi sıralanır.

Kayıt gruptan çıkarılsa bile yerel değer ve geçmiş durur (kayıt geri gelirse
bilgi kaybolmasın).

### Excel'e aktarma

Araç çubuğundaki **Excel'e aktar** ekranda görüneni bir `.xlsx` dosyasına yazar.
Küçük pencerede hangi sütunların gideceğini seçer, istersen **Geçmiş sayfasını**
eklersin; **görünen süzgeç ve sıralama** varsayılan olarak uygulanır. Dosya adı
`<grup-adı>-<YYYY-AA-GG>.xlsx` olur, boş grupta düğme pasiftir.

- Satırlar grid ile birebir aynıdır; tek fark uzun metinlerin **kırpılmamasıdır**.
- Tarih ve tarih-saat alanları gerçek tarih hücresi olur (`GG.AA.YYYY`,
  `GG.AA.YYYY SS:DD`, yerel saat), sayılar sayı hücresi; anahtar sütunu Jira
  kaydına köprü taşır.
- İlk sayfanın adı grup adıdır (Excel'in 31 karakter ve yasak karakter kuralına
  göre kısaltılır), başlık satırı kalın ve donuktur, otomatik filtre açıktır.
- **Geçmiş** sayfası yalnız dışa aktarılan kayıtların, geçmişi açık yerel
  alanlardaki değişimlerini yeniden eskiye listeler.
- **Bilgi** sayfası grup adını, türünü, varsa JQL'i, dışa aktarma zamanını, kayıt
  sayısını ve uygulama sürümünü taşır.

Aynı dosyayı doğrudan da indirebilirsin:
`GET /api/groups/<id>/export.xlsx?columns=issuekey,summary&history=1&q=&sort=&dir=`

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
| `app/grid.py` | Grid satırlarının kurulması (ekran ve Excel ortak kaynağı) |
| `app/export.py` | Grup → Excel (.xlsx) dosyası |
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
