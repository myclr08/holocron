# Holocron

Jira kayıtlarını yerelde takip etmek için küçük bir masaüstü aracı. Sunucu
yalnızca `127.0.0.1` üzerinde çalışır, arayüz tarayıcıda açılır; veriler
uygulamanın yanındaki `holocron.db` dosyasında kalır, hiçbir yere gönderilmez.

> Bu depo yalnızca aracın kendisini içerir. Kurum bilgisi, iç adres, gerçek
> proje anahtarı veya kayıt örneği bulunmaz; belgelerdeki tüm örnekler
> uydurmadır (`https://jira.example.com`, `DEMO-1`, `project = DEMO`).

## Durum

Gruplar (manuel ve JQL filtresi), kayıt listesi, sütun seçici, arama, arka
planda çalışan **Güncelle** işi, kendi tanımladığınız **yerel alanlar** (alan
başına değişim geçmişiyle), **Excel'e aktarma** ve tam tema kullanılabilir
durumda. Taşınabilir Windows/Linux paketleri etiket itildiğinde üretilir.

## Gereksinimler

- Python 3.11 veya üstü (Windows tam paketi kendi Python'ını getirir, lite paket getirmez)
- Jira Server / Data Center (kişisel erişim anahtarı destekleyen sürümler) veya Jira Cloud

## Kurulum

Üç yol var; en kolayından en zoruna.

### 1. Hazır paket (önerilen)

[Releases](https://github.com/myclr08/holocron/releases) sayfasından
işletim sisteminize uygun zip'i indirin:

| Durumunuz | Dosya | Boyut | İçerik |
| --- | --- | --- | --- |
| **Windows, Python kuruluysa** (önerilen) | `holocron-windows-x64-lite.zip` | ~7 MB, ~60 dosya | `app/` + `wheels/`; mevcut Python'unuzla `.venv` kurar |
| **Windows, Python yoksa** | `holocron-windows-x64.zip` | ~29 MB, ~1600 dosya | `python-embed/` ile gelir, hiçbir kurulum gerekmez |
| **Linux** | `holocron-linux-x64.zip` | ~8 MB, ~60 dosya | `wheels/` ile gelir, ilk çalıştırmada çevrimdışı kurulum yapar |

Fark asıl dosya sayısında: `python-embed` standart kitaplığı bin altı yüz ayrı
dosya olarak taşır, bu yüzden tam paketin zip'ten çıkması dakikalar sürebilir.
Lite paket altmış dosyadır, saniyeler içinde açılır. Makinenizde Python 3.11+
varsa lite paketi seçin.

Zip'i boş bir klasöre açın, sonra:

- **Windows**: `holocron.bat` dosyasına çift tıklayın. Lite pakette ilk
  çalıştırmada `.venv` kurulur ve bağımlılıklar yanınızdaki `wheels/`
  klasöründen çevrimdışı yüklenir; birkaç saniye sürer. Uygulama açıldıktan
  sonra konsol penceresi kendiliğinden kapanır — **kapanmazsa** bir sorun var
  demektir, pencerede son log satırları yazar (bkz. [Sorun giderme](#sorun-giderme)).
- **Linux**: `./holocron.sh` çalıştırın. İlk seferde `.venv` yanınızdaki
  `wheels/` klasöründen kurulur; tekerlekler sizin Python sürümünüze uymazsa
  betik ağdan indirmeyi dener.

Veritabanı (`holocron.db`) ve şifreleme anahtarı (`holocron.key`) bu klasörde
oluşur. Klasörü taşırsanız verileriniz de gelir.

### 2. Kaynaktan, Python kuruluysa

```bash
git clone https://github.com/myclr08/holocron.git
cd holocron
./holocron.sh          # Windows'ta: holocron.bat
```

İlk çalıştırmada `.venv` kurulur ve bağımlılıklar PyPI'dan indirilir. Ağa
çıkışın kapalı olduğu ortamlarda paketleri `wheels/` klasörüne koyun; betikler
onları görürse kurulumu çevrimdışı yapar.

### 3. Geliştirici

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest -q
.venv/bin/python -m app --no-browser --port 8765
```

Taşınabilir paketi elle üretmek için:

```bash
# Windows tam paketi (embed dağıtımını önce indirin)
python tools/build_portable.py --target windows --embed-zip python-embed.zip --wheels wheels
# Windows lite paketi (python-embed yok, ~7 MB)
python tools/build_portable.py --target windows --no-embed --wheels wheels
# Linux paketi (zaten embed'siz)
python tools/build_portable.py --target linux --wheels wheels
# Ne yapacağını yazsın, dosyaya dokunmasın
python tools/build_portable.py --target linux --wheels wheels --dry-run
```

`--no-embed` ile `--variant lite` aynı şeydir. Her iki varyantta da `tests/`,
`tools/`, `.github/` ve `__pycache__` pakete girmez.

Tekerlekler şöyle toplanır:

```bash
pip download --only-binary=:all: --python-version 3.13 \
  --platform win_amd64 --implementation cp -r requirements.txt -d wheels
```

### Çalıştırma seçenekleri

Uygulama boş bir port bulur (tercihen 8765), varsayılan tarayıcıyı açar ve
adresi konsola yazar. Sekme kapatılıp beş dakika nabız gelmezse süreç kendini
kapatır; arayüzdeki **Kapat** düğmesi de aynı işi anında yapar.

```bash
./holocron.sh --port 8765     # sabit port
./holocron.sh --no-browser    # tarayıcıyı açma
./holocron.sh --console       # ayrıntılı log, hata ekranda kalır
```

## Sorun giderme

### Konsol açılıp hemen kapanıyor, tarayıcı gelmiyor

Windows'ta uygulama `pythonw.exe` ile açılır: konsol penceresi yoktur, bu
yüzden bir hata çıkarsa görünmez. İki aracınız var.

**1. Konsol kipi.** Komut isteminde paketin klasöründe:

```bat
holocron.bat --console
```

Uygulama ön planda çalışır, hata mesajı pencerede kalır, kapanırken `pause`
bekler. Çift tıklamayla da çalışır: `holocron.bat` kısayolu oluşturup hedefin
sonuna ` --console` ekleyin.

**2. Log dosyası.** Her çalıştırma `holocron.log` dosyasına yazar; dosya
`holocron.db` ile aynı klasördedir (paketi açtığınız yer). İçinde ilk satırda
sürüm, Python sürümü, işletim sistemi, port ve veri klasörü bulunur;
yakalanmamış her hata tam yığın izi ile düşer. Dosya 1 MB'ı geçince döner,
üç yedek tutulur.

Normal başlatmada (çift tıklama) betik altı saniye bekleyip `/api/health`
adresini yoklar. Cevap gelmezse pencere kapanmaz: "Uygulama acilmadi" der ve
logun son kırk satırını basar.

Paketi yazma izni olmayan bir klasöre (`Program Files`, doğrudan zip
görüntüleyicisinin içi) açtıysanız veri ve log `%LOCALAPPDATA%\Holocron`
altına düşer. En temizi paketi `Belgeler` gibi yazılabilir bir klasöre taşımak.

### "No module named app"

`python -m app` yalnızca doğru klasörden çalıştırılırsa iş görür ve
`PYTHONSAFEPATH`/`-P` açıkken hiç çalışmaz. Bunun yerine giriş dosyasını tam
yoluyla çağırın; nereden çalıştırdığınız önemli olmaz:

```bat
.venv\Scripts\python.exe "C:\yol\holocron\holocron_run.py" --console
```

Başlatıcılar (`holocron.bat`, `holocron.sh`) zaten bunu yapar.

### Port 8765 dolu

Uygulama kendisi boş bir port seçer ve seçtiğini `holocron.port` dosyasına
yazar; başlatıcı sağlık yoklamasında o dosyayı okur. Sabit port isterseniz
`--port` verin. Çalışan adres log dosyasının ilk satırında da yazılıdır.

### Tarayıcı açılmıyor ama uygulama çalışıyor

Log dosyasındaki adresi (`http://127.0.0.1:<port>/`) elle açın. Windows'ta
önce `os.startfile`, olmazsa `webbrowser` denenir; ikisi de olmazsa uygulama
yine de ayakta kalır, sadece log'a uyarı düşer.

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

## Görünüm

Arayüz koyu temalıdır ve açık tema seçeneği yoktur. Arka plandaki yıldız alanı
tek bir canvas'tır; **Ayarlar → Görünüm** altından kapatılabilir. "Hareketler"
kapatıldığında yıldızlar durur, geçiş animasyonları kaybolur ve açılış
gösterilmez. İşletim sisteminizde "hareketi azalt" açıksa aynısı kendiliğinden
olur.

İlk açılışta kısa bir tanıtım akar; tıklayarak ya da `Esc` ile geçilir.
"Bir daha gösterme" işaretliyken geçerseniz bir daha çıkmaz;
**Ayarlar → Görünüm → Açılışı tekrar göster** onu geri getirir.

Yazı tipleri (Inter ve Pathway Gothic One) uygulamanın içinde gelir, SIL Open
Font License ile dağıtılır ve lisans metinleri `app/static/fonts/` altındadır.
Arayüz hiçbir CDN'e, hiçbir dış adrese istek atmaz.

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
| `holocron_run.py` | Çalışma dizininden bağımsız giriş dosyası (başlatıcılar bunu çağırır) |
| `app/` | Uygulama kodu (API, veritabanı, Jira istemcisi, arayüz) |
| `app/runlog.py` | Dosya logu, konsolsuz (pythonw) dayanıklılık, hata yakalama |
| `app/repository.py` | Gruplar, üyelikler, kayıtlar ve alan kataloğu (tüm SQL) |
| `app/fields.py` | Alan değerlerini metne çeviren saf formatlayıcı |
| `app/grid.py` | Grid satırlarının kurulması (ekran ve Excel ortak kaynağı) |
| `app/export.py` | Grup → Excel (.xlsx) dosyası |
| `app/refresh.py` | Arka planda çalışan Güncelle işi |
| `app/static/` | Vanilla HTML/CSS/JS arayüz, dış bağımlılık yok |
| `app/static/fonts/` | Gömülü OFL yazı tipleri ve lisans metinleri |
| `app/static/js/starfield.js` | Arka plandaki yıldız alanı (canvas) |
| `tests/` | pytest testleri ve sahte Jira sunucusu |
| `tools/build_portable.py` | Taşınabilir Windows/Linux paketlerini üretir |
| `holocron.db` | Yerel veritabanı (depoya girmez) |
| `holocron.key` | Şifreleme anahtarı (depoya girmez, yedekleyin) |
| `holocron.log` | Çalışma günlüğü (1 MB × 3, depoya girmez) |
| `holocron.port` | Çalışırken seçilen port; kapanışta silinir |

`holocron.key` dosyasını kaybederseniz kayıtlı token çözülemez; Ayarlar
ekranından yeniden girmeniz gerekir.

## Lisans

MIT. Ayrıntı için `LICENSE`.
