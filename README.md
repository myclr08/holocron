# Holocron

Jira kayıtlarını yerelde takip etmek için küçük bir masaüstü aracı. Sunucu
yalnızca `127.0.0.1` üzerinde çalışır, arayüz tarayıcıda açılır; veriler
uygulamanın yanındaki `holocron.db` dosyasında kalır, hiçbir yere gönderilmez.

Amaç tek bir şey: peşinde olduğunuz kayıtları, kendi notlarınızı ve kime ne
sorduğunuzu **tek masada** toplamak. Jira'nın kendi ekranlarının yerine geçmez;
sizin takip listenizi tutar, yanına Jira'nın bilmediği bilgileri ekler ve o
listeyi Excel'e, Teams'e ya da e-postaya bir tıkla taşır.

> Bu depo yalnızca aracın kendisini içerir. Kurum bilgisi, iç adres, gerçek
> proje anahtarı veya kayıt örneği bulunmaz; belgelerdeki tüm örnekler
> uydurmadır (`https://jira.example.com`, `DEMO-1`, `project = DEMO`,
> `ornek@example.com`).

Güncel sürüm: **v0.10.7** (bkz. [Sürüm notları](#sürüm-notları)).

## Ne yapar

| Özellik | Kısaca |
| --- | --- |
| **Gruplar ve JQL filtreleri** | Kayıtları elle topladığınız *manuel* gruplar ya da üyeliği her Güncelle'de sorgudan gelen *JQL filtresi* grupları. Filtre grubunda iğnelenen kayıt sonuçtan düşse bile listede kalır. |
| **Güncelle** | Arka planda çalışan tazeleme işi: ilerleme, aşama ve iptal üst çubukta; değişen hücreler beş saniye vurgulanır. |
| **Sütunlar ve arama** | Sütun seçici, sıralama, bütün seçili sütunlarda süzen arama kutusu (`/`). Türkçe `İ/ı` ayrımı gözetilmez. |
| **Yerel alanlar** | Jira'ya asla gitmeyen kendi alanlarınız (metin, sayı, tarih, evet/hayır, liste) — istenirse **alan başına değişim geçmişiyle**, iki türetilmiş sütunla (son değişim, kaç kez değişti). |
| **Görevlerim** | Jira'dan bağımsız kişisel kanban: üç sütun, sürükle-bırak, son tarih rozetleri, Jira kaydına bağlanabilen kartlar. |
| **Outlook e-postasından görev** (Windows) | Kimden / Kime / CC listelerine uyan postalar **Yapılacak** sütununa düşer; bir konuşmadan tek görev çıkar. |
| **Teams'e mesaj** | Kayda iliştirilen kişilere tek tıkla mesaj: Graph API ve IT izni yok, `msteams:` derin bağlantısı. Gönder'e siz basarsınız. |
| **E-posta ile gönder** (Windows) | Grubun kayıtlarını seçili sütunlarla Excel'e çevirip şablonlu bir postaya ekler; Kime/CC şablondan gelir, posta Outlook'ta açılır ya da gönderilir. |
| **Copilot** | Makinenizde kurulu **Copilot CLI**'yi bulup çalıştırabilen köprü: yol, vekil sunucu ve model sırası Ayarlar'dan yönetilir, **Copilot'u sına** gerçekten çalışıp çalışmadığını söyler. |
| **Sefer** | Bitiş tarihi olan XP hedefi: görev kapatmak, kaydın filodan düşmesi ve durum geçişleri puan verir; rütbe, rozetler, haftalık emirler ve "ne için puan aldım" defteri (yanlış satır silinebilir). |
| **Excel'e aktarma** | Gruplar, görevler ve XP defteri için gerçek tarih/sayı hücreli, köprülü `.xlsx` dosyaları. |
| **Tema** | Koyu Star Wars atmosferi: yıldız alanı, ışın kılıcı renkleri, açılış akışı — hepsi kapatılabilir. Dış kaynak, CDN, izleme yok. |

## Gereksinimler

- Python 3.11 veya üstü (Windows tam paketi kendi Python'ını getirir, lite paket getirmez)
- Jira Server / Data Center (kişisel erişim anahtarı destekleyen sürümler) veya Jira Cloud
- Outlook ve Teams özellikleri yalnız **Windows**'ta çalışır; diğer her şey her yerde çalışır
- Ayarlar'daki **Copilot** kartı kurulu bir **Copilot CLI** arar; yoksa kart
  "bulunamadı" der, başka hiçbir şeyi etkilemez

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

**Yükseltme.** Yeni sürümü eski klasörün üstüne açtığınızda başlatıcılar
`requirements.txt`in özetini yanlarında tuttukları özetle (`.venv/holocron-req.sha`)
karşılaştırır; liste değiştiyse "Bagimliliklar guncelleniyor..." der ve eksik
paketleri kurar — önce yanınızdaki `wheels/`
klasöründen, olmazsa ağdan. Kurulum düşse bile uygulama yine açılır: eksik
paket yalnızca kendi özelliğini kapatır ve bir uyarı satırı yazılır.

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

### Çalıştırma seçenekleri

Uygulama boş bir port bulur (tercihen 8765), varsayılan tarayıcıyı açar ve
adresi konsola yazar. Açık sekme her 30 saniyede bir nabız gönderir; **on iki
saat** hiç nabız gelmezse süreç kendini kapatır. Bu süre "sekmeyi kapatınca
hemen kapansın" için değil, "unutulmuş bir süreç gece boyu ayakta kalmasın"
içindir — işiniz bitince arayüzdeki **Kapat** düğmesi süreci anında bitirir.
Başka bir süre isterseniz `--timeout` saniye cinsinden verilir.

Başlatıcıyı ikinci kez çalıştırırsanız yeni bir süreç açılmaz: çalışan örnek
`holocron.port` dosyasından bulunur, sağlığı yoklanır ve yalnızca tarayıcı o
adrese getirilir ("Zaten calisiyor: http://127.0.0.1:8765/").

```bash
./holocron.sh --port 8765     # sabit port
./holocron.sh --no-browser    # tarayıcıyı açma
./holocron.sh --console       # ayrıntılı log, hata ekranda kalır
```

## İlk ayar sırası

Hepsi **Ayarlar** ekranındadır ve sırayla yapılması en kolayıdır. Yalnızca ilk
adım zorunludur; geri kalanı kullanmadığınız özellikleri kapalı bırakır.

### 1. Jira bağlantısı

Kimlik bilgileri `settings` tablosunda şifreli durur; şifreleme anahtarı
yanındaki `holocron.key` dosyasındadır (yalnız sahibine okunur izinle
oluşturulur). Kaydedilen token bir daha ekranda gösterilmez, yalnızca
"ayarlı / ayarsız" bilgisi görünür.

**Jira Server / Data Center (varsayılan)**

1. Jira'da profilinizden bir **kişisel erişim anahtarı (PAT)** oluşturun.
2. Ayarlar ekranında mod olarak *Jira Server / Data Center* seçin.
3. Adres: `https://jira.example.com`
4. Kimlik türü *Kişisel erişim anahtarı*, alana anahtarı yapıştırın.
5. **Bağlantıyı sına** ile ad-soyad ve sunucu başlığını doğrulayın.

Kullanıcı adı + parola ile Basic kimlik de desteklenir, ancak PAT önerilir.

**Jira Cloud**

1. Atlassian hesabınızdan bir **API token** oluşturun.
2. Mod olarak *Jira Cloud*, adres `https://demo.atlassian.net`.
3. E-posta adresinizi ve token'ı girin.

Bağlantı kurulduktan sonra **Alan kataloğunu çek** deyin: sütun başlıkları ham
alan kimliği yerine gerçek adlarıyla görünsün.

### 2. Ağ (yalnız kurum ağındaysanız)

**Ayarlar → Ağ** altında vekil sunucu kipi, muaf adresler, özel CA dosyası,
SSL doğrulaması ve IPv4 önceliği bulunur.

Vekil sunucu kipi üç seçenektir:

* **Sistem ayarını kullan** (varsayılan): alanlar doluysa onlar, boşsa
  işletim sisteminin bildirdiği değer. Windows'ta bu, ortam değişkenlerinin
  yanında kayıt defterindeki `ProxyServer` kaydını da kapsar — `requests` tek
  başına oraya bakmaz.
* **Aşağıdaki alanları kullan**: yalnız elle yazdığınız adresler; alanlar
  boşsa vekil sunucu kullanılmaz.
* **Doğrudan bağlan**: vekil sunucu hiç kullanılmaz, `https_proxy` gibi ortam
  değişkenleri yok sayılır (`trust_env` kapatılır). Kurum makinesinde ortam
  değişkeni kurumsal vekile bakıyor ama Jira iç ağdaysa doğru seçenek budur.

**Vekil sunucudan muaf adresler** (`no_proxy`) virgülle ayrılır; `.kurum.local`
gibi bir son ek yazabilirsiniz. Sistem ve elle kiplerinde geçerlidir.

**Önce IPv4 dene** varsayılan olarak açıktır. Sunucunun AAAA (IPv6) kaydı olup
IPv6 yolu kapalıysa Python önce IPv6'yı deneyip zaman aşımına düşer, tarayıcı
ise IPv4'e inip çalışır — "tarayıcıda açılıyor, uygulamada açılmıyor" şikâyeti
çoğu zaman budur.

SSL doğrulamayı kapatmak güvenliği düşürür; mümkünse kurumun kök sertifikasını
CA dosyası olarak verin. Takılırsanız **Teşhis** düğmesi hangi adımda
durduğunuzu söyler (bkz. [Teşhis](#teşhis)).

### 3. E-posta (Outlook'tan görev üretme)

**Ayarlar → E-posta** kartında taramayı açın, **Kimden / Kime / CC**
listelerinden en az birine adres yazın, klasörleri seçin. Ayrıntı:
[E-posta (görev üretme)](#e-posta-görev-üretme).

### 4. Teams (kişiler ve mesaj şablonları)

**Ayarlar → Teams** altında mesaj şablonlarını yazar, adres defterini
yönetirsiniz. Windows'ta **Rehberi Outlook'tan yenile** kurum adres listesini
tek geçişte içeri alır. Ayrıntı: [Teams'e mesaj](#teamse-mesaj-kayıttan-tek-tıkla).

### 5. Copilot (isteğe bağlı)

**Ayarlar → Copilot** kartı makinenizdeki Copilot CLI'yi yapılandırır. Kurulu
değilse burayı atlayın; hiçbir şey bozulmaz. Ayrıntı:
[Copilot](#copilot-isteğe-bağlı-1).

### 6. E-posta şablonları (gönderim)

**Ayarlar → E-posta şablonları** kartında Kime/CC/konu/gövde birlikte saklanır
ve gönderim kipi seçilir. Ayrıntı: [E-posta ile gönder](#e-posta-ile-gönder-yalnız-windows).

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

Detay çekmecesindeki **Alanları seç** düğmesi Jira ve yerel alanları arayıp
seçmenizi sağlar. Seçim filo bazında saklanır; **Hiçbiri** bu iki alan grubunu
gizler, **Varsayılana dön** hepsini yeniden gösterir. Değerler yalnız gizlenir,
silinmez; Teams bölümü bu seçimden etkilenmez.

### Satır seçimi

Grid'in ilk sütunu onay kutusudur; başlıktaki kutu **görünen** (süzülmüş)
satırların hepsini işaretler. Seçili sayısı araç çubuğunda küçük bir rozet
olarak durur ("3 seçili"). Seçim **grup değişince** sıfırlanır, süzgeç ya da
sıralama değişince **durur**: arayıp seçtikten sonra aramayı temizlemek
seçimi bozmaz.

Seçim iki yerde kullanılır: **Excel'e aktar** penceresindeki "Yalnız seçili N
kayıt" kutusu ve **E-posta ile gönder** penceresi.

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
eklersin; **görünen süzgeç ve sıralama** varsayılan olarak uygulanır. Grid'de
seçim varsa **Yalnız seçili N kayıt** kutusu da çıkar ve varsayılan olarak
işaretlidir. Dosya adı `<grup-adı>-<YYYY-AA-GG>.xlsx` olur, boş grupta düğme
pasiftir.

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
Yalnız belirli anahtarlar için:
`GET /api/groups/<id>/export-selected.xlsx?keys=DEMO-1,DEMO-2&columns=issuekey,summary`

### E-posta ile gönder (yalnız Windows)

Araç çubuğundaki **E-posta ile gönder**, grubun kayıtlarını seçili sütunlarla
Excel'e çevirir, şablonlu bir postanın ekine koyar ve Outlook'ta açar. "Her hafta
aynı kişilere aynı başlıkla listeyi yolla" işi tek pencereye iner: Kime, CC,
konu ve gövde şablonda birlikte durur.

> **Varsayılan davranış: posta açılır, gönderilmez.** Gönder'e siz basarsınız.
> Kipi **Ayarlar → E-posta şablonları → Gönderim kipi** ile *Doğrudan gönder*
> yapabilirsiniz; o zaman pencerede ikinci bir **Gönder** düğmesi çıkar.

Pencerede ne var:

- **Şablon** açılır listesi. Grubun varsayılanı seçili gelir; **Bu şablonu bu
  gruba bağla** düğmesi seçimi gruba yazar (bir daha aynı grupta o şablonla
  açılır).
- **Kime** ve **CC** çip kutuları. Şablondan dolu gelir, elle eklenip
  çıkarılabilir; iki harften sonra **adres defterinden** öneri gelir (Teams
  kişileriyle aynı defter, kurum rehberi de oradadır). Öneriden seçilen kişi
  **tek çip** olur: çipte adı görünür, üstüne gelince adresi yazar, postaya
  giden değer adrestir.
- **Konu** ve **Gövde**. Metin **ham** tutulur: kutuda `{tablo}` yazar,
  **Önizleme** sekmesi onu tabloya çevirip HTML'i bir çerçevede gösterir.
  Gönderilen posta da aynı kaynaktan üretilir.
- **Sütunlar** listesi (varsayılan grubun sütunları): hem gövdedeki tabloya hem
  Excel'e bu sütunlar girer.
- Kutular: **Yalnız seçili N kayıt** (grid'de seçim varsa), **Görünen süzgeç ve
  sıralamayı uygula**, **Excel ekle**, **Gövdeye tablo ekle**.
- Altta kayıt sayısı ve üretilecek dosya adı; onun altında **Gönderilenler**
  listesi (tarih, konu, alıcı sayısı, kayıt adedi, açıldı mı gönderildi mi).

Yer tutucular:

| Yer tutucu | Karşılığı |
| --- | --- |
| `{grup}` | Grubun adı |
| `{tarih}` | Bugün (`GG.AA.YYYY`) |
| `{adet}` | Gönderilen kayıt sayısı |
| `{jql}` | Grubun JQL sorgusu (varsa) |
| `{tablo}` | Seçili sütunlarla kayıt tablosu — **yalnız gövdede** |

Bilinmeyen yer tutucu **boş kalır**. Konuda `{tablo}` yazarsanız sessizce düşer.

**Adres yazma kuralları** (hem bu pencerede hem Ayarlar'daki şablon kartında
aynıdır):

- Ayırıcı yalnızca `,` `;` ve satır sonudur. **Boşluk ayırıcı değildir**:
  `Mustafa Erhan` tek parçadır, iki alıcı değil.
- `Ad Soyad <ornek@example.com>` biçimi ayrıştırılır: ad çipte görünür, postaya
  adres gider. Düz adres de yazılabilir.
- `@` içermeyen (ya da içinde boşluk kalan) bir parça çip olmaz; kutunun altında
  kırmızı "geçersiz adres" uyarısı çıkar.
- `Enter`, `Tab`, `,` ve `;` çip oluşturur; boş kutuda `Backspace` son çipi siler.
- Şablonda saklanan biçim `;` ile ayrılmış `Ad <adres>` ya da düz adrestir;
  sunucu ikisini de okur, eski kayıtlar kırılmaz.

Nasıl çalışır:

- **Gövde HTML'i Outlook'a göre üretilir.** `<style>` bloğu ve dış CSS yoktur;
  her kural etiketin `style` özniteliğindedir. Outlook'un HTML motoru Word'dür,
  sınıf seçicilerini yok sayar. Tabloda başlık satırı koyu ve zeminli, hücreler
  ince kenarlıklı, anahtar hücresi Jira kaydına köprülüdür.
- **Gövdeyi düz metin yazabilirsiniz**: satır sonları `<br>` olur, `&` ve `<`
  kaçışlanır. İçinde HTML etiketi varsa metne dokunulmaz, yazdığınız gibi gider.
- **İmzanız korunur.** Outlook imzayı öge *görüntülenirken* ekler; bu yüzden
  posta önce görünmez pencerede açılır, sonra gövde imzanın **önüne** yazılır.
- **Ek dosyası silinmez.** Excel `%TEMP%\holocron-mail\<grup>-<tarih>.xlsx`
  olarak (ASCII güvenli adla) yazılır ve orada kalır: Outlook eklemeyi kendi
  zamanlamasıyla yapabiliyor, hemen silinen dosya bazen boş ek olarak gidiyordu.
  Klasörde en fazla **yirmi** dosya tutulur, eskiler yeni gönderimde düşer.
- **Şablonlar** **Ayarlar → E-posta şablonları** altında yazılır: ad, Kime, CC,
  konu, gövde, "Excel ekle" ve "Gövdeye tablo ekle" anahtarları, sıra. Uygulama
  **Haftalık liste** şablonuyla gelir.

Windows dışında düğme pasiftir ve uç `feature_unavailable` döner; şablonlar yine
yazılabilir ve **önizleme her yerde çalışır**.

### Görevlerim

Sol kenarda, grupların üstünde duran **Görevlerim** kendi işlerinizin panosudur.
Jira'dan bağımsızdır: üç sütun (**Yapılacak**, **Yapılıyor**, **Yapıldı**), her
kartta ad, açıklama, not ve son tarih vardır. Rozet açık görev sayısını gösterir,
gecikmiş görev varsa kırmızıya döner.

- **Yeni görev** düğmesi pencereyi açar; `Ctrl+Enter` kaydeder, `Esc` kapatır.
  Karta tıklamak aynı pencereyi düzenleme kipinde açar, **Sil** onay ister.
- Kart sürükleyip bırakılarak hem sütun hem sıra değiştirir; klavye ve fare
  kullanmadan da olsun diye sağ üstteki `←` / `→` düğmeleri kartı komşu sütuna
  taşır. Sütun **Yapıldı** olunca tamamlanma zamanı damgalanır, geri alınınca
  silinir.
- Son tarih rozeti geciken kartta kırmızı, bugün bitmesi gerekende sarı, üç gün
  içinde gelende turuncu, uzaktakinde nötrdür.
- Arama kutusu (`/` kısayolu burada da çalışır) ad, açıklama, not, bağlı kayıt
  anahtarı ve bağlı kaydın özeti üzerinde süzer.
- Biten kartlardan tamamlanması **30 günden eski** olanlar panoyu doldurmasın
  diye gizlenir; kaç tane olduğu yazar, **Eskileri göster** hepsini geri getirir.

#### Bir görevi Jira kaydına bağlamak

Pencerede **Jira kaydı** alanına anahtarı yazın (`DEMO-1`) ya da kaydın
bağlantısını yapıştırın; kutunun altında bilinen anahtarlardan öneri gelir.
Bağ zorunlu değildir ve **henüz çekilmemiş** bir anahtar da bağlanabilir —
o durumda kart "henüz çekilmedi" der, kayıt ilk Güncelle'de özetiyle gelir.
Kartın üstündeki anahtara tıklamak sağdaki detay çekmecesini açar. Ters yön de
var: grid'de satırın sonundaki görev düğmesi, adı Jira özetiyle önden dolu bir
görev penceresi açar.

Görevler **Excel'e aktar** ile tek sayfalık bir dosyaya yazılır: Durum, Ad,
Açıklama, Not, Son tarih (gerçek tarih hücresi), Jira kaydı (köprü), Jira özeti,
Jira durumu, Oluşturma, Tamamlanma. Dosya eski biten kartları da kapsar.
Doğrudan da indirilebilir: `GET /api/tasks/export.xlsx?status=all|todo|doing|done`

### Teams'e mesaj (kayıttan tek tıkla)

Bir kaydın peşine düşmek için Jira'yı, Teams'i ve kime soracağınızı ayrı ayrı
hatırlamanız gerekmesin diye: kayda **Teams kişileri** iliştirirsiniz, satırdaki
sohbet balonu düğmesi ("Son durumu sor") mesajı Teams'te hazır açar.

> **Gönder'e siz basarsınız.** Holocron mesajı yalnızca Teams'in yazma kutusuna
> koyar; gönderildiğini göremez, doğrulayamaz. "Gönderilenler" listesi bu yüzden
> *gönderildi* değil **açıldı** kaydıdır.

Nasıl çalışır:

- **Kurulum gerekmez.** Graph API, uygulama kaydı, IT izni yok. Kullanılan tek
  şey derin bağlantıdır: `msteams:/l/chat/0/0?users=...&message=...`
  Hedef yalnızca **kişilerdir**: kanala yazma denendi ve kaldırıldı, çünkü Teams
  kanal bağlantıları mesaj ön doldurmayı kabul etmiyor.
- **Sekme açılmaz.** Adresi tarayıcı değil **Holocron'un kendisi** açar
  (Windows'ta `os.startfile`, macOS'ta `open`, Linux'ta `xdg-open`): Windows adresi
  kayıtlı işleyiciye verdiği için kurulu Teams doğrudan öne gelir, arkada boş bir
  sekme kalmaz. Açılamazsa uç `opened: false` der ve arayüz eski yola döner:
  `https://teams.microsoft.com/l/chat/0/0?...` adresini yeni sekmede açar. İki
  adresin parametreleri ve kırpma sınırı birebir aynıdır.
- **Kişiler.** Detay çekmecesindeki **Teams** bölümünde kişi eklersiniz (e-posta ya
  da adres defterinden ad). Tek kişi varsa doğrudan sohbet, birden fazlaysa **grup
  sohbeti** açılır; grup sohbetinin adı **Ayarlar → Teams → Konu adı biçimi** ile
  belirlenir (varsayılan `{key}`).
- **Adres defteri.** Kayda girdiğiniz her kişi defterinize düşer ve bir dahaki
  yazışta tamamlanır. Arama ad **ve** e-posta üzerinde çalışır, büyük/küçük harf ve
  Türkçe `İ/ı` ayrımı gözetilmez, önce baştan eşleşenler gelir. Defter
  **Ayarlar → Teams** altında listelenir: ad ve adres düzenlenir, silinen kişi
  bütün kayıtlardan da çıkar. Aynı defteri **E-posta ile gönder** penceresi de
  kullanır.
- **Kurum rehberi (yalnız Windows).** **Ayarlar → Teams → Rehberi Outlook'tan
  yenile**, Outlook'un Genel Adres Listesi'ni (GAL) tek geçişte okuyup adres
  defterine yazar: parola sorulmaz, açık Outlook oturumu kullanılır. Kaç kişi
  eklendiği, kaçının güncellendiği, kaç dağıtım listesi geldiği ve süre durum
  satırında yazar; son yenileme zamanı düğmenin yanında durur.
  - **Elle girdiğiniz kişiler korunur:** aynı adres rehberde de geçiyorsa
    yalnızca adı tazelenir, kaynağı "manual" kalır.
  - **Dağıtım listeleri** (`ekip@example.com`) ayrı işaretlenir ve hem defterde hem
    çekmecedeki çipte küçük **liste** rozetiyle görünür — bir listeye yazmak
    bir kişiye yazmakla aynı şey değildir.
  - Adresi çözülemeyen girişler (toplantı odaları, X500'de kalmış eski kayıtlar)
    sessizce atlanır. Windows dışında düğme pasiftir, uç `feature_unavailable` döner.
- **Şablonlar.** İki şablonla gelir — *Son durum* ve *Güncelleme rica*. Yenisini
  **Ayarlar → Teams** altında yazarsınız. Yer tutucular:

  | Yer tutucu | Karşılığı |
  | --- | --- |
  | `{key}` `{summary}` `{status}` `{assignee}` `{priority}` | Kaydın alanları |
  | `{url}` | Jira bağlantısı |
  | `{field:customfield_10016}` | Herhangi bir alan kimliği |
  | `{local:3}` | Yerel alan (kimliğiyle) |

  Bilinmeyen yer tutucu **boş kalır**: yanlış yazılmış bir ad mesajın içinde
  `{musteri}` diye görünmez. Şablon seçince metin kutusu çözülmüş haliyle dolar
  ve göndermeden önce elle düzenlenebilir.
- **Uzun mesaj.** Adres uzunluğu 2.000 karakteri aşarsa mesaj kırpılır ve tamamı
  panoya kopyalanır; balon bunu söyler. Panoya kopyalama yalnızca bu durumda
  devreye girer.
- **Sütun.** Sütun seçicide **Teams kişileri** sanal sütunu vardır: kayda bağlı
  adları virgülle gösterir, Excel'e de aynı şekilde girer.
- **Kanban.** Bir görev Jira kaydına bağlıysa kartta da aynı düğme durur.

Kayıtta kişi yoksa düğme mesaj açmaz; çekmeceyi açıp önce kişi eklemenizi ister.
Adres defteri de boşsa kutunun altında "Rehber boş — Ayarlar → Teams → Rehberi
Outlook'tan yenile" ipucu çıkar.

### E-posta (görev üretme)

**Ayarlar → E-posta**, Outlook'taki postalarınızdan görev üretir. Sizin
tanımladığınız adres listelerinden birine uyan her e-posta **Görevlerim →
Yapılacak** sütununun en üstüne düşer: konu görevin adı, gövde açıklaması,
altına da "Kimden / Alındı" satırı yazılır. Kart zarf ikonu ve **e-posta**
rozetiyle gelir, penceresinde **Outlook'ta aç** düğmesi durur.

Nasıl çalışır:

- **Bağlantı**: masaüstündeki klasik Outlook'a COM ile bağlanılır (`pywin32`).
  Açık olan oturumunuz kullanılır: **parola sorulmaz, hiçbir yere yazılmaz**,
  sunucu adresi girilmez. Outlook kapalıysa "Bağlantıyı sına" bunu söyler.
- **Adresler**: üç ayrı liste vardır ve her biri yalnızca kendi başlığına bakar.

  | Liste | Ne zaman tutar |
  | --- | --- |
  | **Kimden** | Gönderen bu adreslerden biriyse |
  | **Kime** | Alıcılar arasında bu adreslerden biri varsa |
  | **CC** | CC'de bu adreslerden biri varsa |

  Üçünden birinin tutması yeter (VEYA). Listeler çapraz tutmaz: **Kimden**
  listesindeki bir adres postanın Kime alanında geçiyor diye eşleşme saymaz.
  Böylece "bana gelenler" ile "patronun yazdıkları" ayrı ayrı seçilebilir.
  Her liste virgülle ayrılır, `*@example.com` alan adının tamamını kapsar,
  büyük/küçük harf ayrımı yoktur. **Üçü de boşsa** tarama "adres tanımlı
  değil" der ve hiçbir posta eşleşmez.

  Kurum içi adresler Exchange'de `/O=.../CN=...` biçiminde gelir; Holocron
  bunları birincil SMTP adresine çevirir, çeviremezse o alıcıyı yok sayar
  (postayı düşürmez).
- **Klasörler**: varsayılan Gelen Kutusu'dur. **Klasörleri getir** Outlook'tan
  ağacı çeker, işaretlediğiniz klasörler `Gelen Kutusu\Alt\Klasör` yoluyla
  saklanır. Outlook'un **Arama Klasörleri** (Search Folders) listesi normal
  klasör ağacında yer almaz; ayrıca toplanıp `Arama Klasörleri\<ad>` yoluyla
  ağacın altına eklenir ve normal klasör gibi taranabilir (başlığın kendisi
  gerçek bir klasör olmadığı için işaretlenemez).
- **Pencere**: varsayılan son 30 gün. Klasör yeniden eskiye taranır ve
  pencerenin dışına çıkılınca durulur.
- **Takvim yok**: toplantı davetleri ve yanıtları, görev istekleri, teslim
  raporları ve "ofiste değilim" şablonları atlanır. Yalnızca normal postalar
  (`IPM.Note`) görev üretir.
- **Tek görev kuralı**: bir konuşmadan yalnızca bir görev çıkar. Aynı
  konuşmanın sonraki mesajları karta "N mesaj · son: GG.AA SS:dd" satırı olarak
  düşer. Görevi **silerseniz, arşivlerseniz ya da Yapıldı'ya taşırsanız o
  konuşma bir daha görev üretmez** — konuşma kaydı silinen görevden sonra da
  durur.
- **Ne zaman taranır**: **Şimdi tara** düğmesiyle elle, ya da **Güncelle**
  işinin son aşaması olarak (kapatılabilir). Jira hatası e-posta taramasını
  engellemez, tersi de geçerlidir; özet balonunda "N görev e-postadan" yazar.

Daha önceki sürümde tek bir adres listesi vardı (hem gönderende hem alıcıda
aranıyordu). Yükseltmede o değer üç listeye de kopyalanır, davranış birebir
aynı kalır; sonra istediğiniz listeyi boşaltabilirsiniz.

Windows dışında kart "Bu özellik yalnız Windows'ta Outlook ile çalışır" der ve
düğmeler pasiftir; uçlar `feature_unavailable` döner. `pywin32` yalnızca Windows
paketine girer (`requirements.txt` içinde `sys_platform == "win32"` işaretçisi
vardır), Linux zip'ine alınmaz. Ayrı bir `pywin32_postinstall` adımı
**gerekmez**: `win32com` site-packages'tan olduğu gibi çalışır.

### Copilot (isteğe bağlı)

Holocron, makinenizde zaten kurulu olan **Copilot CLI**'yi bulup
çalıştırabilir. Aracın kendisini Holocron kurmaz, güncellemez ve hesabınıza
dokunmaz; yalnızca alt süreç olarak çağırır.

**Copilot nerede?** Alan boşken sırayla `PATH`, Windows'un bilinen yerleri
(`%APPDATA%\npm\copilot.cmd` başta olmak üzere npm, WinGet, Program Files,
`%USERPROFILE%\.local\bin`) ve **kayıt defterinden taze okunan** kullanıcı/makine
PATH'i denenir. `holocron.bat` uygulamayı `pythonw.exe` ile açtığı için süreç,
terminalinizin güncel PATH'ini görmeyebilir: terminalde `where copilot` yazıp
çıkan **tam yolu** (`.cmd` ya da `.exe` uzantısıyla) **Copilot yolu** alanına
yapıştırın. Bulunan yol loga yazılır ve kartta "Son bulunan: ..." diye durur.

npm'in bıraktığı `copilot.cmd` dosyasını Windows doğrudan çalıştıramadığı için
(`CreateProcess` `.cmd` açamaz) bu dosyalar `cmd.exe /c` ile çağrılır; cmd
komut satırını yeniden ayrıştırdığından istem metni **argüman olarak geçmez**,
çalışma klasörüne dosya olarak yazılıp modele okutulur.

**Vekil sunucu.** Kurumda Copilot vekil sunucudan çıkarken Jira doğrudan
görülebiliyor. Vekil adresi **yalnızca Copilot alt sürecinin ortamına** yazılır
(`HTTPS_PROXY`, `HTTP_PROXY` ve küçük harfli eşleri), Jira sunucusunun adı da o
sürecin `NO_PROXY` listesine eklenir. Holocron'un kendi Jira bağlantısı
"doğrudan bağlan" kipinde kalır, `os.environ` hiç değişmez.

**Model sırası.** Copilot CLI hesap başına farklı modelleri açar. Modeller
sırayla denenir; biri reddedilirse ("Model ... from --model flag is not
available") sıradakine geçilir, çalışan model "son çalışan" olarak saklanır ve
bir dahaki sefere başa alınır. Hepsi reddedilirse hata tek satırda hangi
modellerin kapalı olduğunu söyler.

**Copilot'u sına.** Küçük bir istek atar: modelden bir çalışma dosyasına
`{"hazir": true}` yazması istenir ve cevap **o dosyadan** okunur. Yani
"çalışıyor" yazısı aynı zamanda `--allow-tool=read --allow-tool=write`
izinlerinin verildiğini de kanıtlar. Sınamanın zaman aşımı 300 saniyedir.
Başarısız olursa Copilot'un ham çıktısının son 400 karakteri ekranda,
2000 karakteri `holocron.log`'da durur; parola/anahtar benzeri diziler
maskelenir.

### Sağ çekmeceleri genişletme

Bütün sağ çekmecelerin sol kenarında ince bir
tutamaç vardır: sürükleyerek genişliği değiştirirsiniz. En az 360 piksel, en
çok ekranın %90'ı (ya da 1400 piksel). Ölçü tarayıcıda saklanır ve bir sonraki
açılışta uygulanır; **çift tıklamak** varsayılan 440 piksele döner. Tutamaç
klavyeyle de odaklanır: ok tuşları 20'şer piksel kaydırır.

### Sefer (XP, rütbe, rozetler)

Sol kenarda, Görevlerim'in üstünde duran **Sefer** bir kampanyadır: bir adı, bir
**bitiş tarihi** ve bir **hedef XP**'si vardır. Aynı anda tek sefer sürer. Sefer
yokken panel "Sefer başlat" formunu gösterir ve **hiç puan toplanmaz**; tarih
geçtiğinde sefer kendiliğinden kapanır, özeti geçmişe düşer ve yeni sefer
sıfırdan başlar. Kapanan seferin defteri silinmez, o sefere bağlı kalır.

Kontrol uygulama açılışında ve her Güncelle'de yapılır: uygulamayı günlerce
açmasanız da sefer doğru günde bitmiş görünür.

**Puan veren olaylar** (Ayarlar → Sefer altında hepsi değiştirilebilir, kapatılabilir):

| Olay | Varsayılan |
| --- | --- |
| Görev kapatıldı | 10 |
| Son tarihinden önce kapatıldı (ek) | +5 |
| Gecikmiş görev kapatıldı (10 yerine) | 5 |
| E-posta görevi 24 saat içinde ele alındı | 5 |
| **Kayıt bir filtre filosundan düştü** | 15 |
| Jira kaydı tamamlandı (`statusCategory` → done) | 20 |
| Jira kaydı işleme alındı (new → indeterminate) | 5 |
| Son durum soruldu (günde en çok 3) | 2 |
| Seri: etkin iş günü | 3 |
| Haftalık emir tamamlandı | 25 |
| Rozet kazanıldı | 10 |

Filtre filosundan düşme puanı yalnız **Güncelle'nin JQL sonucundan** gelir;
manuel gruptan kaydı elinizle çıkarmak puan vermez. Bir JQL'i daralttığınızda
onlarca kayıt birden düşebilir — bu "iş bitti" demek olmadığı için tek
Güncelle'de filo başına en çok **20 düşüş** puanlanır. Üstü sonraya
ertelenmez, hiç puanlanmaz: yanlış puandan çok az puan yeğdir. Aynı olay iki kez puan
vermez: veritabanı (sefer, tür, referans) üçlüsünü tekil tutar, görevi
"Yapıldı"ya ikinci kez taşımak yeni puan üretmez (gecikmiş kapanan bir görev
sonradan son tarihi düzeltilip yeniden kapatılsa da). Grup başına ayrı puan
gerekiyorsa kuralın `params_json` alanına `{"group_points": {"3": 40}}` yazılır.

**Rütbeler** hedefe göre oranlıdır: Padawan %0, Şövalye %25, Usta %60, Konsey
Üyesi %100, Efsane %150. Böylece 400 XP'lik kısa bir sefer de tırmanma duygusu
verir. Rütbe atlayınca panel kısaca parlar.

**Seri** iş günü bazlıdır: o gün en az bir XP olayı ya da **sizin** yaptığınız bir
görev hareketi varsa gün işaretlenir, hafta sonu atlanır (seriyi kırmaz). Posta
taramasının ya da Güncelle'nin dokunduğu kayıtlar günü işaretlemez. Ayda bir
**Güç koruması** kaçırılan tek bir iş gününü affeder; harcandığı ay Ayarlar'da
yazar.

**Haftalık görev emirleri** her pazartesi (ya da haftanın ilk açılışında) gerçek
veriden üretilir: gecikmiş görevleri kapatmak, Yapılıyor'u beşin altına indirmek,
14 gündür güncellenmemiş kayıtların son durumunu sormak, bekleyen e-posta
görevlerini ele almak. İlerleme ayrı bir sayaçtan değil, panonun anlık
durumundan ölçülür; biten emir 25 XP verir.

**Bu hafta** şeridi puan vermez, yalnız sayar: bu hafta kapanan görev, düşen
kayıt ve toplanan XP.

**Rozetler** kazanıldığında renklenir, kazanılmayan gri hologram olarak durur ve
üzerine gelince koşulu yazar: Temiz Masa, Hızlı Yanıt, Kapatıcı, Bitirici, Beş /
Yirmi / Altmış Gün, Haritacı, Arşivci, Elçi, Sefer Tamam.

**XP defteri** son 50 olayı tarih, kaynak, açıklama ve puanla listeler; kaynak
süzgeci vardır ve **Tümünü Excel'e** defterin tamamını `.xlsx` yazar
(`GET /api/campaign/ledger.xlsx?source=task|jira|mail|teams|streak|quest|badge`).
Pazartesi ilk açılışta on saniye duran bir **Holocron kaydı** kartı geçen haftayı
özetler.

**Yanlış puanı silmek.** Defterdeki her satırın sonunda küçük bir `×` vardır:
satır silinir, toplam XP düşer, rütbe geriye gidebilir ve koşulu artık
sağlanmayan rozetler puanlarıyla birlikte geri alınır. Seri günü silinirse o gün
seriden de düşer. Silmek "bu olay hiç olmadı" demektir: aynı olay ileride
yeniden gerçekleşirse (kayıt tekrar filodan düşer, görev yeniden kapanır) puan
normal şekilde yeniden yazılır. (`DELETE /api/campaign/ledger/{id}`)

**Geçmiş seferi silmek.** Geçmiş sefer kartındaki `×` seferi defteri, rozetleri,
emirleri ve serisiyle birlikte siler; geri alınamaz. Süren sefer buradan
silinmez, onun yolu **Seferi bitir**. (`DELETE /api/campaign/history/{id}`)

Rütbe ve rozet görselleri `app/static/img/gamify/` altında beklenir; dosya adları
sabittir. Rütbeler: `rank-padawan.png`, `rank-knight.png`, `rank-master.png`,
`rank-council.png`, `rank-legend.png`. Rozetler `badge-<kod>.png`, kodlar:
`clean_desk`, `fast_reply`, `closer`, `finisher`, `streak_5`, `streak_20`,
`streak_60`, `cartographer`, `archivist`, `envoy`, `campaign_complete`.
Dosya yoksa arayüz kendi çizdiği SVG hologramı gösterir; ekran hiçbir zaman boş
kalmaz, eksik görsel sonradan eklenebilir.

### Güncelle

**Güncelle** bütün grupları, **Güncelle (bu grup)** yalnız açık olanı tazeler.
Aynı anda tek iş çalışır; üst çubukta ilerleme ve aşama görünür, **İptal**
paketler arasında durur ve o ana kadar çekilenler kalır. İş bitince kaç kayıt
çekildiği, kaçının yeni/değişmiş olduğu ve bulunamayan anahtarlar özetlenir;
değişen hücreler beş saniye vurgulanır. Bir filtre grubunun JQL'i hatalıysa
yalnız o grup hata listesine düşer, iş sürer.

Kayıtlar `*navigable` (Jira Cloud'da `*all`) ile çekilir: sonradan hangi sütunu
seçerseniz seçin yeniden çekmeye gerek kalmaz.

### Görünüm

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

## Veri ve gizlilik

Holocron bir istemcidir; kendi sunucusu, hesabı, bulutu yoktur.

- **Her şey yerelde.** Sunucu yalnız `127.0.0.1` dinler. Dışarıya çıkan tek
  trafik sizin tanımladığınız **Jira** adresidir. Teams ve Outlook özellikleri
  ağa hiç çıkmaz: biri işletim sistemine bir adres verir, diğeri makinenizdeki
  COM oturumunu okur.
- **`holocron.db`** bütün verinizi taşır: gruplar, kayıtların ham JSON'u, yerel
  alanlar ve geçmişleri, görevler, kişiler, şablonlar. Yedeği
  dosyayı kopyalamaktır.
- **`holocron.key`** Jira token'ınızı şifreleyen anahtardır, yalnız sahibine
  okunur izinle oluşturulur. **Kaybederseniz kayıtlı token çözülemez**; Ayarlar
  ekranından yeniden girmeniz gerekir. `holocron.db` ile birlikte yedekleyin.
- **`holocron.log` paylaşılabilir.** `urllib3` ve `asyncio` günlükçüleri
  WARNING'e sabitlenmiştir; eskiden DEBUG satırları `Starting new HTTPS
  connection (1): jira.kurum.local:443` diye **sunucu adını** yazıyordu. Kendi
  satırlarımız INFO'da kalır ve hata metinlerinde sunucu adı yerine "Jira
  sunucusu" geçer. Teşhis çıktısı da kullanıcı adı ve token taşımaz.
- **Parola saklanmaz.** Outlook ve Teams için açık oturumunuz kullanılır; hiçbir
  kimlik bilgisi sorulmaz.
- **Yerel alanlar Jira'ya gitmez.** Yazma yönü tek yönlüdür: Holocron Jira'dan
  okur, Jira'ya yazmaz.
- Paketi yazma izni olmayan bir klasöre açtıysanız veri ve log
  `%LOCALAPPDATA%\Holocron` altına düşer.

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

### Uygulama bir süre sonra kendiliğinden kapandı

Sekmeye dönünce sayfanın tepesinde "Holocron kapanmış görünüyor" şeridi
varsa süreç gerçekten kapanmıştır: `holocron.bat` ile yeniden başlatıp sayfayı
yenileyin. Sebebi `holocron.log` dosyasında yazar:

- `nabiz ... sn'dir yok, zaman asimi ... sn: kapaniliyor` — on iki saat boyunca
  hiçbir sekme nabız göndermemiş. Sekmeyi kapattıysanız beklenen davranış.
- `Kapat dugmesi: kapaniliyor` — arayüzdeki **Kapat** düğmesine basılmış.

v0.8.3'ten önce zaman aşımı beş dakikaydı ve tarayıcı arka plandaki sekmeyi
dondurduğunda (Edge'in uyuyan sekmeleri, ekran kilidi, uzun süre başka
sekmede çalışmak) nabız kesildiği için uygulama siz kullanırken kapanıyordu.
Şimdi süre on iki saat ve sekme her görünür olduğunda anında bir nabız gider.

### Tarayıcı açılmıyor ama uygulama çalışıyor

Log dosyasındaki adresi (`http://127.0.0.1:<port>/`) elle açın. Windows'ta
önce `os.startfile`, olmazsa `webbrowser` denenir; ikisi de olmazsa uygulama
yine de ayakta kalır, sadece log'a uyarı düşer.

### "Bağlantıyı sına" on saniye sonra hata veriyor

Bağlantı zaman aşımı on saniyedir ve bağlantı kurulamadığında **yeniden
deneme yapılmaz**: hata hemen döner, sebebini de yazar. (Eskiden üç deneme
otuzar saniye bekliyordu; kullanıcı doksan saniye sonunda yine aynı cümleyi
görüyordu.) Okuma zaman aşımı ayrıdır: sunucu bağlandıktan sonra yanıtı otuz
saniye bekler ve yalnız o durumda yeniden denenir.

Hata mesajı sebebi söyler: adı çözülemedi (DNS), kapı reddedildi, TCP
kurulamadı, vekil sunucuya ulaşılamadı, sertifika doğrulanamadı. Daha
ayrıntısı için **Ayarlar → Ağ → Teşhis**.

### Gönderilen postada imza kayboldu ya da tablo bozuk görünüyor

Holocron postayı önce görünmez bir pencerede açar (imzayı Outlook orada
ekler), sonra gövdeyi imzanın önüne yazar. İmza hiç çıkmıyorsa Outlook'ta
**yeni ileti** için bir imza tanımlı olmayabilir.

Tablo bozuksa sebep neredeyse her zaman gövdeye elle yapıştırılan HTML'dir:
Outlook Word ile çizer, `<style>` bloklarını ve sınıf seçicilerini yok sayar.
Holocron'un ürettiği tablo satır içi stil kullanır; kendi HTML'inizi yazarken
aynısını yapın.

### Ek gitmedi ya da boş gitti

Excel `%TEMP%\holocron-mail` altında kalır ve **silinmez**: Outlook eklemeyi
kendi zamanlamasıyla yapabiliyor. O klasörde dosyayı göremiyorsanız
"Excel ekle" kutusu kapalı demektir. Klasörde en fazla yirmi dosya tutulur.

### Teşhis

**Bağlantıyı sına** düğmesinin yanındaki **Teşhis**, zinciri parçalara ayırıp
her halkayı süresiyle gösterir:

| Adım | Ne yapar |
| --- | --- |
| Adres ayrıştırma | `https://…` biçimi, sunucu adı, kapı numarası |
| Vekil sunucu | Hangi kip, hangi vekil sunucu, sistemde PAC var mı |
| Ad çözümleme | `getaddrinfo`: dönen bütün IPv4 ve IPv6 adresleri |
| TCP (doğrudan) | Her adrese beş saniye; hangisi açıldı |
| TCP (vekil sunucu üzerinden) | Vekil sunucuya bağlanır, `CONNECT` tüneli dener |
| TLS | El sıkışma, sertifikanın sahibi ve vereni |
| HTTP | Kimliksiz `GET /rest/api/2/serverInfo` (401 de olumlu sayılır) |
| Kimlik | Token ile `myself` |

Her adım yeşil/kırmızı/atlandı ve milisaniye olarak yazılır; kırmızı adımın
altında ne yapmanız gerektiği durur. Çıktıda kullanıcı adınız ve token'ınız
**yer almaz**, yalnız sunucu adı ve IP adresleri görünür.

Sık çıkan üç sonuç:

* **Doğrudan TCP açıldı, vekil sunucu tüneli zaman aşımına uğradı.** Jira iç
  ağda, kurumsal vekil sunucu onu görmüyor. Ayarlar → Ağ → **Doğrudan bağlan**.
* **PAC tanımlı, vekil sunucu yok.** Windows'ta `AutoConfigURL` var demektir.
  Holocron PAC (JavaScript) dosyasını çözmez; Jira için geçerli vekil sunucuyu
  ağ yöneticinizden öğrenip Ayarlar → Ağ'a yazın, ya da iç ağ ise Doğrudan
  bağlan seçin.
* **IPv6 adresi zaman aşımına uğradı, IPv4 açıldı.** "Önce IPv4 dene" zaten
  varsayılan olarak açıktır; kapatmayın.

### Kurum kök sertifikası

Kurum trafiği kendi kök sertifikasıyla açıyorsa TLS adımı "sertifika
doğrulanamadı" der. Kurumun kök sertifikasını `.pem` olarak alıp
**Ayarlar → Ağ → Özel CA dosyası** alanına yolunu yazın. SSL doğrulamayı
kapatmak son çaredir ve güvenliği düşürür.

## Geliştirme

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest -q
```

Testler gerçek Jira'ya ya da gerçek Outlook'a **çıkmaz**: `requests` oturumu
sahte bir sunucuyla değiştirilir (`tests/fake_jira.py`), posta kaynağı ve
göndericisi bellek içi sahtelerle (`app/mail/fake.py`) takılır. Her test kendi
geçici veri klasöründe çalışır.

### Paketleme

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
# pywin32'nin isaretcisi (sys_platform == "win32") pip'in CALISTIGI yoruma gore
# degerlendirilir: Linux'tan indirirken sessizce atlanir, ayrica istenir.
pip download --only-binary=:all: --python-version 3.13 \
  --platform win_amd64 --implementation cp --no-deps pywin32 -d wheels
```

Linux paketinde `pywin32` tekerlekleri `wheels/` klasörüne kopyalanmaz.

### Sürüm çıkarma

Sürüm numarası iki dosyada durur ve **aynı olmak zorundadır**:
`app/__init__.py` içindeki `__version__` (arayüzdeki `s<sürüm>` rozeti ve
`/api/health`) ve `pyproject.toml` içindeki `version`. Bir test bu ikisini
kilitler. Üçüncüsü etikettir: `v<sürüm>` biçiminde itilir ve release iş akışı
`publish` adımında etiketi koddaki sürümle karşılaştırır — tutmuyorsa iş düşer,
yanlış numaralı bir release çıkmaz.

```bash
git tag v0.7.3 && git push origin v0.7.3
```

## Dosya düzeni

| Yol | İçerik |
| --- | --- |
| `holocron_run.py` | Çalışma dizininden bağımsız giriş dosyası (başlatıcılar bunu çağırır) |
| `app/` | Uygulama kodu (API, veritabanı, Jira istemcisi, arayüz) |
| `app/runlog.py` | Dosya logu, konsolsuz (pythonw) dayanıklılık, hata yakalama |
| `app/net.py` | Zaman aşımları, IPv4 önceliği, vekil sunucu kipleri, hata ayrımı |
| `app/diagnose.py` | Adım adım ağ teşhisi (DNS → TCP → TLS → HTTP → kimlik) |
| `app/repository.py` | Gruplar, üyelikler, kayıtlar ve alan kataloğu (tüm SQL) |
| `app/fields.py` | Alan değerlerini metne çeviren saf formatlayıcı |
| `app/grid.py` | Grid satırlarının kurulması (ekran ve Excel ortak kaynağı) |
| `app/tasks.py` | Görev panosunun kurulması (sütunlar, son tarih durumu) |
| `app/teams.py` | Teams derin bağlantısı ve şablon çözümü (saf mantık) |
| `app/desktop.py` | Adresi işletim sistemine açtırır (`msteams:` protokolü dahil) |
| `app/export.py` | Grup / görev / XP defteri → Excel (.xlsx) dosyası |
| `app/refresh.py` | Arka planda çalışan Güncelle işi |
| `app/mail/` | Outlook'tan görev üretme (kaynak sözleşmesi, COM sarmalayıcı, iş mantığı) |
| `app/mail/intake.py` | Eşleştirme, tekilleştirme, görev üretimi (COM'dan bağımsız) |
| `app/mail/outlook.py` | Outlook COM sarmalayıcısı, postaları **okur** (yalnız Windows) |
| `app/mail/send.py` | Outlook COM sarmalayıcısı, posta **gönderir** (yalnız Windows) |
| `app/mailsend.py` | E-posta şablonu çözümü ve Outlook uyumlu HTML tablo (saf mantık) |
| `app/mailsend_repo.py` | E-posta şablonları, grup bağı, gönderim kayıtları (SQL) |
| `app/api_mailsend.py` | "E-posta ile gönder" uçları (ayrı router) |
| `app/gamify.py` | Sefer motoru: XP kuralları, rütbe, rozet, emir, seri (saf mantık) |
| `app/gamify_repo.py` | Seferler, XP defteri, kurallar, rozetler, emirler, seri (SQL) |
| `app/api_gamify.py` | Sefer uçları (ayrı router) |
| `app/copilot.py` | Copilot CLI'yi bulma, çağırma, sınama (genel yardımcı) |
| `app/static/` | Vanilla HTML/CSS/JS arayüz, dış bağımlılık yok |
| `app/static/fonts/` | Gömülü OFL yazı tipleri ve lisans metinleri |
| `app/static/js/starfield.js` | Arka plandaki yıldız alanı (canvas) |
| `app/static/js/addressbox.js` | Ortak adres kutusu: çipler, tamamlama, ayrıştırma |
| `app/static/js/mailsend.js` | "E-posta ile gönder" penceresi |
| `app/static/js/mailsend-settings.js` | Ayarlar → E-posta şablonları kartı |
| `app/static/js/campaign.js` | Sefer paneli (kahraman şeridi, emirler, rozetler, defter) |
| `app/static/js/campaign-settings.js` | Ayarlar → Sefer kartı (XP kuralları, koruma durumu) |
| `app/static/img/gamify/` | Rütbe ve rozet görselleri (yoksa SVG hologram yedeği) |
| `tests/` | pytest testleri, sahte Jira sunucusu |
| `tools/build_portable.py` | Taşınabilir Windows/Linux paketlerini üretir |
| `holocron.db` | Yerel veritabanı (depoya girmez) |
| `holocron.key` | Şifreleme anahtarı (depoya girmez, yedekleyin) |
| `holocron.log` | Çalışma günlüğü (1 MB × 3, depoya girmez) |
| `holocron.port` | Çalışırken seçilen port; kapanışta silinir |

## Sürüm notları

| Sürüm | Tarih | Ne geldi |
| --- | --- | --- |
| **v0.10.7** | 19 Eylül 2026 | Sahadan gelen yedinci hata: görüşme notu özet adımı "Özet alınamadı: Model JSON döndürmedi." diyordu — Copilot CLI bulunuyor, model (`claude-sonnet-5`) kabul ediliyor, süreç dönüyor ama `stdout`'tan JSON çıkmıyordu. Sebep programatik kipin (`-p`) çıktısı: banner, ilerleme satırları, araç kullanım dökümü ve ANSI renk kodları aynı akışa karışıyor, cevap markdown içinde ya da `stderr`'de kalabiliyor. Çözüm TDD Beyin'in kanıtlanmış kalıbı: cevap artık modelin **yazdığı dosyadan** alınıyor. Şablon (`app/gorusme/sablon.txt`) modelden JSON'u transkriptin yanındaki `ozet.json` dosyasına yazmasını istiyor, Copilot `--allow-tool=read --allow-tool=write` ile çağrılıyor ve dosya UTF-8/BOM toleranslı okunuyor; önceki koşudan kalan dosya çağrıdan önce, okunan dosya hemen sonra siliniyor. Dosya oluşmazsa yedek yol devrede: `stdout`+`stderr` birleşiyor, ANSI kaçış dizileri temizleniyor, kod bloğundan ya da düz metinden en dıştaki JSON nesnesi çekiliyor (ilerleme satırındaki küçük `{...}` parçacığı asıl nesnenin önüne geçmiyor). JSON yine yoksa kullanıcı ekranda "Model JSON döndürmedi. Ham çıktı (son 400 karakter): …" görüyor, `holocron.log`'a WARNING ile stdout ve stderr'in son 2000 karakteri düşüyor (parola/anahtar benzeri diziler maskeli); araç izni reddedilmişse metin bunu söylüyor ("Copilot dosyaya yazma izni vermedi; bayraklar: …"). **Copilot'u sına** da aynı dosya yolunu yürüyor (modelden `{"hazir": true}` yazmasını ister), yani "çalışıyor" yazısı yazma izninin de verildiğini kanıtlıyor; sınamanın zaman aşımı 300 sn, özetinki 900 sn ve aşılırsa hata "Copilot 900 sn'de bitmedi" diyor. Alt süreçlerin konsolsuz çalışması (v0.10.6) aynen duruyor |
| **v0.10.6** | 19 Eylül 2026 | Sahadan gelen altıncı hata: Copilot CLI çağrısı (ve pip ile paket kurulumu) sırasında ekranda kısa süreliğine boş bir konsol penceresi açılıyordu — `holocron.bat` uygulamayı konsolsuz `pythonw.exe` ile açtığı için Windows, alt süreç başlatıldığında kendiliğinden bir pencere yaratıyordu. Ortak bir yardımcı (`app/gorusme/altsurec.py::sessiz_calistir_ayarlari`) artık her alt süreç çağrısına Windows'ta `CREATE_NO_WINDOW` bayrağını ve gizli `STARTUPINFO`yu ekliyor (diğer platformlarda hiçbir şey değişmiyor); ayrıca konsolsuz süreçte hiç var olmayan `stdin` artık `DEVNULL` veriliyor, girdi bekleyen bir CLI takılı kalmıyor |
| **v0.10.5** | 19 Eylül 2026 | Sahadan gelen beşinci hata: özet modelleri `gpt-5`, `claude-sonnet-4.5`, `gpt-4.1` Copilot CLI tarafından "Model "gpt-4.1" from --model flag is not available" diyerek reddedildi, oysa aynı hesapta `claude-sonnet-5` ve `gpt-5-mini` (yedeği `claude-haiku-4.5`) çalışıyordu. Varsayılan özet modeli sırası `claude-sonnet-5, gpt-5-mini, claude-haiku-4.5, claude-sonnet-4.5, gpt-5` oldu (yalnızca ayar hiç yazılmamış kurulumlarda geçerli, kaydedilmiş ayarlar değişmez). Model reddi tespiti bu yeni hata metnini de tanıyor; reddedilen bütün modeller artık tek satırda listeleniyor ("Copilot modelleri reddetti: ... — Ayarlar'dan hesabında olan bir model seçin"). Ayarlar'daki Özet modelleri alanının ipucu güncel model adlarını gösteriyor |
| **v0.10.4** | 19 Eylül 2026 | Sahadan gelen dördüncü hata: VDI'da özet adımı "Özet alınamadı: Copilot CLI bulunamadı (copilot)" diyordu, oysa kullanıcı aynı makinede terminalde `copilot` çalıştırabiliyordu. Sebep PATH: `holocron.bat` uygulamayı `start "" pythonw.exe` ile açar, o süreç kullanıcının **güncel** PATH'ini görmez (npm'in global klasörü çoğu kez yalnızca kullanıcı PATH'indedir ve o PATH oturum açıldıktan sonra değişmiştir). Copilot artık sırayla aranıyor: **Ayarlar → Görüşme notları → Copilot yolu** alanı, `PATH` (`PATHEXT` ile `.cmd`/`.exe` çözülür), Windows'un bilinen yerleri (`%APPDATA%\npm\copilot.cmd` başta olmak üzere npm, WinGet, Program Files, `%USERPROFILE%\.local\bin`) ve **kayıt defterinden taze okunan** kullanıcı/makine PATH'i. Bulunan tam yol loga yazılır, **Copilot'u sına** onu ekranda gösterir ("çalışıyor · `<yol>` · model X · N sn") ve ayara "son bulunan" olarak saklar; hiçbiri yoksa hata metni **denenen yerleri tek tek sayar** ve ne yapılacağını söyler. npm kurulumunun bıraktığı `copilot.cmd` dosyasını Windows doğrudan çalıştıramadığı için (`CreateProcess` `.cmd` açamaz) bu dosyalar `cmd.exe /c` ile çağrılıyor; cmd komut satırını yeniden ayrıştırdığından istem metni **argüman olarak geçmiyor**, transkriptin yanına dosya olarak yazılıp modele okutuluyor (tırnak, `%` ve `&` komutu bölemez). Vekil ve `NO_PROXY` mantığı aynen duruyor |
| **v0.10.3** | 19 Eylül 2026 | Sahadan gelen üçüncü hata: VDI'da model zip'i açılıp yolu **Model klasörü** alanına yazılınca yazıya dökme `ConnectTimeout ... cannot find the appropriate snapshot folder` diye düşüyordu — klasör Hugging Face **önbellek kökü** sanılıp `download_root`a veriliyordu, oysa faster-whisper açılmış model klasörünü DOĞRUDAN kabul eder. Klasör artık sırayla çözümleniyor: klasörün kendisi (`model.bin` içeriyorsa), `<klasör>/<model adı>` ya da `<klasör>/faster-whisper-<model adı>` alt klasörü, ya da HF önbellek düzeni (`models--Systran--faster-whisper-<ad>/snapshots/*`, `local_files_only` ile). **Klasör yazılıysa ağa hiç çıkılmıyor**: model bulunamazsa dakikalarca zaman aşımı yerine anında "Model klasöründe model.bin bulunamadı: `<yol>`; beklenen düzen ..." deniyor. Model yükleme hataları tek satırlık Türkçeye iniyor ("Model yüklenemedi: ...", ilk 200 karakter), satır kesin olarak **hata** durumuna geçiyor ve takip şeridindeki "işleniyor" sayacı sıfırlanıyor; yarım kalan satırlar açılışta kuyruğa dönüyor. Ayarlar'a **Modeli sına** düğmesi geldi: modeli yüklemeyi dener, `hazır: <yol>, N sn` ya da temiz hata yazar, kuyruğu bloklamaz |
| **v0.10.2** | 19 Eylül 2026 | Sahadan gelen ikinci hata: VDI'da **Deneme kaydı** HTTP 500 veriyordu — döngü aygıtı 10 saniye boyunca tek çerçeve vermemiş, WAV 0 bayt kalmış, okuma `EOFError` atmıştı. Kayıt artık bloklayan `read` yerine **geri çağrı (callback)** kipinde çalışıyor: susan bir aygıt kayıt iş parçacığını kilitleyemiyor, dosya her durumda geçerli başlıkla kapanıyor. Deneme kaydı hiçbir durumda 500 dönmüyor; kanal başına aygıt adı, açıldı mı, kaç çerçeve geldi, ses var mı ve hata metni gösteriliyor, altında öneri duruyor. Döngü kanalını beslemek için deneme boyunca hoparlöre duyulmayan (-60 dB) bir sinyal çalınıyor. Akış açılamazsa 44100/2 ile yeniden deneniyor; döngü aygıtı kendi kanal sayısı ve hızıyla açılıyor. Gerçek kayıtta veri gelmeyen parça "boş" işaretlenip birleştirmede atlanıyor, not "hata: Ses alınamadı (mikrofon: veri gelmedi)" diye düşüyor ve ses klasörü silinmiyor. Boş, eksik ya da bozuk WAV artık istisna yerine 0 çerçeve dönüyor |
| **v0.10.1** | 19 Eylül 2026 | Sahadan gelen "faster-whisper kurulu değil" hatası: paket `requirements.txt`e girdi (Windows işaretiyle), yani artık ilk kurulumda gelir. Başlatıcılar (`holocron.bat`, `holocron.sh`) her açılışta `requirements.txt`in SHA256 özetini `.venv` içinde tuttukları özetle karşılaştırıyor; liste değiştiyse eksik paketleri kuruyor (önce `wheels/` ve `whisper-wheels/`, olmazsa ağdan) — eski sürümün üstüne açılan kurulumlarda sonradan eklenen bağımlılık hiç kurulmuyordu. Kurulum düşse bile uygulama açılıyor. Ayarlar → Görüşme notları'na **Yazıya dökme paketini kur** düğmesi eklendi (pip alt süreçte, vekil yalnızca o sürecin ortamında, çıktı maskelenir); Görüşme notları sekmesinde paket yokken kapatılabilir uyarı şeridi duruyor. Paket gelince kuyrukta "hata: faster-whisper yok" diye bekleyen satırlar hem açılışta hem kurulumdan sonra kendiliğinden yeniden deneniyor. `holocron-windows-whisper.zip` artık `whisper-wheels/` klasörü olarak açılıyor |
| **v0.10.0** | 19 Eylül 2026 | **Görüşme notları**: Teams görüşmesi başlayınca mikrofon ve duyulan ses iki ayrı kanal olarak kaydedilir, görüşme bitince kuyrukta sırayla birleştirilir, faster-whisper ile yazıya dökülür ("Sen" / "Karşı taraf"), Copilot CLI ile özetlenir; not hazır olunca ses ve transkript silinir. Aramalar filosuna "Görüşme notları" sekmesi ve takip şeridi (duraklat/sürdür, canlı kayıt rozeti, işleniyor/kuyrukta sayaçları), not çekmecesi (Özet, Kararlar, Aksiyonlar, Açık sorular), aksiyondan tek tıkla görev, tek Jira kaydına bağ, Jira detayında ve Kişiler çekmecesinde "Görüşme notları (n)", Excel'e yeni sayfa. Ayarlar'da aygıt seçimi ve 10 saniyelik deneme kaydı, asgari süre, Whisper modeli/klasörü, özet modeli yedek sırası, özet şablonu, saklama ve bildirim seçenekleri. Copilot CLI'nin vekil sunucusu ayrı bir ayarda durur ve yalnızca alt sürecin ortamına yazılır: Jira "doğrudan bağlan" kipinde kalır |
| **v0.9.0** | 17 Eylül 2026 | **Teams Aramalar sadeleşti: toplantı kavramı tümden kaldırıldı** (takvim eşleşmesi, toplantı sohbetinden katılım türetme, toplantı satırları/sütunları, "Eşleşmeyenler" ve "Katılım teşhisi" ekranları; göç eski toplantı satırlarını siler). Geriye **birebir** ve **grup** aramaları kaldı. Grup artık **katılımcı kümesidir** (sohbet kimliği değil): aynı kişilerle yapılan bütün aramalar tek satırda toplanır, etiket katılımcı adlarından türer. Yeni **Gruplar** sekmesi (arama sayısı, toplam süre, son arama, katılımcılar; tıklayınca liste süzülür, Excel'de ayrı sayfa). İstatistik şeridi dört kutu oldu: birebir / grupta / toplamda en çok görüşülenler ve birebir-grup dağılımı ("Toplam Teams" ile "iş günü" kutuları kalktı). Önbellekte artık yalnız iki veritabanı açılıyor |
| **v0.8.3** | 17 Eylül 2026 | Uygulama bir süre sonra kendiliğinden kapanıyordu: tarayıcı arka plandaki sekmenin zamanlayıcılarını dondurunca (Edge uyuyan sekmeler, ekran kilidi) nabız kesiliyor, beş dakikalık zaman aşımı dolup süreç kapanıyordu. Zaman aşımı 12 saat oldu, nabız `setInterval` yerine zincirleme `setTimeout` ile atılıyor ve sekme görünür olunca / pencere odaklanınca anında bir nabız gidiyor. Sunucuya iki kez ulaşılamazsa sayfanın tepesinde kapatılabilir bir şerit çıkar, sunucu dönünce kendiliğinden kalkar. Başlatıcılar çalışan örneği bulup yalnızca tarayıcıyı açar; kapanma sebebi loga ayırt edilebilir yazılır |
| **v0.8.2** | 16 Eylül 2026 | Yerel alan geçmişi popover'ı "Okunuyor..." yazısında takılı kalıyordu: `campaign.js` ile `app.js` aynı sayfada iki ayrı `renderHistory` tanımlıyordu, sonra yüklenen sefer sürümü diğerini eziyordu. Sefer sürümü `renderCampaignHistory` oldu; aynı sayfadaki betiklerde ad çakışmasını yasaklayan test eklendi |
| **v0.8.1** | 12 Eylül 2026 | Sefer: geçmiş sefer silme, XP satırı silme (yeniden değerlendirme, iptal işareti yok), güç dengesi ve Denge rozeti kaldırıldı, rütbe ve rozet görselleri dairesel çerçevede |
| **v0.8.0** | 12 Eylül 2026 | **Sefer**: bitiş tarihli XP kampanyası, ayarlanabilir kural motoru, hedefe oranlı rütbeler, 11 rozet, haftalık görev emirleri, iş günü serisi ve aylık Güç koruması, XP defteri (+ Excel, satır silme ve yeniden değerlendirme), geçmiş seferler (silinebilir), pazartesi "Holocron kaydı" |
| **v0.7.3** | 12 Eylül 2026 | Toplantı katılımı: birleştirme anahtarı toplantı kimliği + gün (farklı gün/thread asla birleşmez), ad alanlı XML etiketleri, yeniden taramada eski sohbet kayıtlarının temizlenmesi |
| **v0.7.2** | 12 Eylül 2026 | Teams Aramalar: tek `view` isteği, SQL pencere ve indeks (1.000 kayıtta < 30 ms), önbellek yalnız çekim ve teşhiste; katılım kuralı sertleşti (kendi süre yoksa kayıt yok, GUID eşleşme, oturum toplama); "Katılım teşhisi" çekmecesi |
| **v0.7.1** | 12 Eylül 2026 | Toplantı katılımı: yeni partlist biçimi (calleventtype, ended, meetingdetails), iCalUid ile takvim eşleşmesi, kendi kimlik veritabanı adından; sonda iskelet çıktısı |
| **v0.7.0** | 12 Eylül 2026 | Teams Aramalar: toplantı sohbetlerindeki katılım kayıtlarından (partlist) gerçek katılım süresiyle toplantılar; "sohbetten" rozeti; Kendi Teams kimliğim ayarı; sonda `--meetings` |
| **v0.6.3** | 12 Eylül 2026 | Teams Aramalar: takvim saatleri UTC olarak okunur (3 saatlik kayma düzeltildi); grup sohbeti aramaları teşhiste ayrı sayılır, rozet yalnız şüphelileri gösterir |
| **v0.6.2** | 12 Eylül 2026 | Teams Aramalar: tekrarlayan toplantı serileri (RecurringMaster) kimlikle eşleşir, iptal edilmiş seriler dahil; "Eşleşmeyenler" teşhis çekmecesi; taramada tekrarlayan sayısı |
| **v0.6.1** | 12 Eylül 2026 | Kime/CC adres kutusu: rehberden seçim tek çip, boşlukta bölme yok, `Ad Soyad <adres>` ayrıştırma, geçersiz adres uyarısı |
| **v0.6.0** | 12 Eylül 2026 | **E-posta ile gönder**: grid'de onay kutusu sütunu, Kime/CC taşıyan e-posta şablonları, çipli adres kutusu (`Ad Soyad <adres>`), Outlook uyumlu HTML tablo, Excel eki, "Outlook'ta aç" / "Doğrudan gönder" kipleri, gönderim geçmişi |
| **v0.5.0** | 12 Eylül 2026 | Teams'e mesaj (derin bağlantı, `msteams:` protokolü, kurum rehberi içe aktarma) ve **Teams Aramalar**: yerel önbellekten arama geçmişi, kişi kırılımı, istatistik şeridi, üç sayfalık Excel |
| **v0.4.0** | 11 Eylül 2026 | Outlook e-postalarından görev üretme (COM, üç ayrı adres listesi, klasör ağacı, arama klasörleri, konuşma başına tek görev) |
| **v0.3.0** | 11 Eylül 2026 | Görevlerim kanban panosu; ağ katmanı (vekil sunucu kipleri, IPv4 önceliği, adım adım teşhis); Star Wars görsel katmanı, taşınabilir paketler ve lite Windows zip'i |
| **v0.2.0** | 11 Eylül 2026 | Yerel alanlar, alan başına değişim geçmişi, türetilmiş sütunlar, satır içi düzenleme; Excel'e aktarma ve grid'in tek kaynağa taşınması |
| **v0.1.0** | 11 Eylül 2026 | İskelet: SQLite şeması, Jira istemcisi (Server/DC + Cloud), ayarlar ekranı, gruplar, arka planda Güncelle, grid, sütun seçici, arama, detay çekmecesi |

## Lisans

MIT. Ayrıntı için `LICENSE`.
