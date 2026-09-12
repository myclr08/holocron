# Teams yapı sondası

Yeni Teams sohbet, arama ve takvim verisini bilgisayarınızda bir Chromium
IndexedDB'sinde tutar. Bu küçük betik o veritabanının **yapısını** çıkarır:
hangi veritabanı, hangi tablo (object store), kaç kayıt, hangi alan adları,
hangi tipler, tarih alanlarının en eskisi ve en yenisi.

Amaç, bir sonraki adımda "kiminle ne kadar görüştüm" tablosunu kurabilmek
için verinin nerede durduğunu görmek. Sonda hiçbir şey değiştirmez, hiçbir
yere bağlanmaz, hiçbir şey göndermez.

## Çalıştırma

Kurulum gerekmez; bağımlılıklar `vendor/` içinde gelir.

```
python probe.py
```

Yol kendiliğinden `%LOCALAPPDATA%` altından kurulur. Başka bir yerdeyse:

```
python probe.py --path "D:\kopya\https_teams.microsoft.com_0.indexeddb.leveldb"
```

Holocron'un sanal ortamıyla da çalışır:

```
.venv\Scripts\python.exe tools\teams_probe\probe.py
```

Seçenekler:

| Seçenek | Anlamı |
| --- | --- |
| `--path` | IndexedDB klasörü (varsayılan: Teams'inki) |
| `--output` | Çıktı dosyası (varsayılan `teams-probe.txt`) |
| `--no-copy` | Kopyalamadan doğrudan oku (Teams kapalıyken) |
| `--limit` | Örneklenecek kayıt sayısı (varsayılan 200) |

## Teams açıkken

LevelDB dosyaları Teams çalışırken kilitli olabilir. Sonda bu yüzden klasörü
önce `%TEMP%\holocron-teams-probe\` altına **kopyalar** ve kopyayı okur.
Kopyalanamayan dosya atlanır, sayılır ve raporda görünür; tek bir kilitli
dosya sondayı düşürmez. Kopya bozuk çıkarsa canlı klasör bir kez denenir.

Teams'i kapatmak gerekmez, ama kapalıyken çalıştırmak en temiz sonucu verir.

## Çıktıda ne var

`teams-probe.txt` içinde:

* her veritabanının adı, kaynağı ve tablo listesi
* her tablo için kayıt sayısı
* ilk 200 kaydın **alan adları** (`a.b.c` biçiminde, en fazla dört derinlik),
  alan tipleri (`str`, `int`, `list`, `dict`, …)
* tarih gibi görünen alanların en eski / en yeni değeri
* dizge alanları için yalnızca **uzunluk** istatistiği (ortalama / en büyük)
* `.blob` klasörünün varlığı ve dosya sayısı
* okunamayan tablo ve kayıt sayıları, istisna **türleri**
* "aday tablolar" bölümü: adında `conversation`, `message`, `thread`, `call`,
  `people`, `contact`, `reply` geçenler
* "hedef veritabanları" bölümü: arama geçmişi, takvim ve kişi çözümü

## Çıktıda ne YOK

Hiçbir dizge değeri. Mesaj metni, kişi adı, e-posta adresi, konu başlığı,
sohbet kimliği: hiçbiri yazılmaz. `content`, `text`, `body`, `displayName`,
`mri`, `email`, `imdisplayname`, `subject` gibi alanlardan çıktıya giren tek
şey alanın **adı** ve değerin **uzunluğudur**.

Kural alan adına bakarak değil, tipe bakarak uygulanır: dizge olan her değer
uzunluğa iner. Çıktıya yalnızca alan adları, sayılar ve tarihler girer. Bu
yüzden dosya olduğu gibi paylaşılabilir.

Sondanın gizlilik süzgeci (`redact_value`, `summarize_records`) birim
testleriyle korunur: `tests/test_teams_probe.py`.

## Bağımlılıklar

`vendor/` klasöründe saf Python olarak gelir; `pip` gerekmez. Kaynak, sürüm
ve lisanslar için `vendor/README.md`.

## Paket

Sonda ayrı bir zip olarak da üretilir, uygulama paketlerine girmez:

```
python tools/build_portable.py --probe
# -> dist/holocron-teams-probe.zip
```
