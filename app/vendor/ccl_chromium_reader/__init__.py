"""Vendor edilmis ccl_chromium_reader -- yalnizca IndexedDB yolu.

Ust akistaki `__init__.py` `ChromiumProfileFolder` iceri aktarir; o da
`brotli` ve `zstd` (ikili eklentiler) isteyen modulleri cekiyor. Sonda
yalnizca IndexedDB okur, bu yuzden burasi bilerek bostur: modulleri
dogrudan iceri aktarin.

    from ccl_chromium_reader import ccl_chromium_indexeddb

Kaynak ve surum icin vendor/README.md.
"""
