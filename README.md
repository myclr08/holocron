# Holocron

Jira kayıtlarını yerelde takip etmek için küçük bir masaüstü aracı. Sunucu
yalnızca `127.0.0.1` üzerinde çalışır, arayüz tarayıcıda açılır; veriler
uygulamanın yanındaki `holocron.db` dosyasında kalır, hiçbir yere gönderilmez.

> Bu depo yalnızca aracın kendisini içerir. Kurum bilgisi, iç adres, gerçek
> proje anahtarı veya kayıt örneği bulunmaz; belgelerdeki tüm örnekler
> uydurmadır (`https://jira.example.com`, `DEMO-1`, `project = DEMO`).

## Durum

Aşama 1: iskelet, veritabanı, Jira istemcisi ve ayarlar ekranı hazır.
Gruplar, sütun seçimi, Excel'e aktarım ve tema sonraki aşamalarda gelecek.

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
| `app/static/` | Vanilla HTML/CSS/JS arayüz, dış bağımlılık yok |
| `tests/` | pytest testleri ve sahte Jira sunucusu |
| `tools/build_portable.py` | Windows taşınabilir paketini üretir |
| `holocron.db` | Yerel veritabanı (depoya girmez) |
| `holocron.key` | Şifreleme anahtarı (depoya girmez, yedekleyin) |

`holocron.key` dosyasını kaybederseniz kayıtlı token çözülemez; Ayarlar
ekranından yeniden girmeniz gerekir.

## Lisans

MIT. Ayrıntı için `LICENSE`.
