# vendor/

Kurum vekil sunucusu `pip`i engelleyebildigi icin sondanin bagimliliklari
burada duruyor. Kurulum gerekmez; `probe.py` bu klasoru `sys.path`e ekler.

| Paket | Surum | Kaynak | Lisans |
| --- | --- | --- | --- |
| `ccl_chromium_reader` | 0.3.18 | <https://github.com/cclgroupltd/ccl_chromium_reader> | MIT (`ccl_chromium_reader/LICENSE`) |
| `ccl_simplesnappy` | 0.4 | <https://github.com/cclgroupltd/ccl_simplesnappy> | MIT (`ccl_simplesnappy/LICENSE`) |

Ikisi de saf Python; ikili eklenti yok. Python 3.11+ ile calisir
(ust akis `requires-python = ">=3.10"`).

## Ne alindi, ne alinmadi

`ccl_chromium_reader` paketinin yalnizca IndexedDB'yi okumak icin gereken
modulleri kopyalandi:

```
ccl_chromium_indexeddb.py
common.py
structures.py
profile_folder_protocols.py
serialization_formats/{ccl_v8_value_deserializer,ccl_blink_value_deserializer}.py
storage_formats/ccl_leveldb.py
```

Gecmis, onbellek, localStorage, sessionStorage, SNSS ve indirme modulleri
alinmadi: `brotli` ve `zstd` ikili eklentilerini istiyorlar, sondanin isine
yaramiyorlar.

## Tek degisiklik

`ccl_chromium_reader/__init__.py` bosaltildi. Ust akistaki hali
`ChromiumProfileFolder` iceri aktariyor, o da alinmayan (ve ikili eklenti
isteyen) modulleri cekiyordu. Dosyalarin geri kalani birebir ust akis.
