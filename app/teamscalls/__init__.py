"""Teams arama gecmisi: yerel onbellekten tablo ve istatistik.

Katmanlar (posta paketiyle birebir ayni desen):

* `source.py`      -- kaynak-bagimsiz arama modeli ve `CallSource` sozlesmesi
* `teams_cache.py` -- yeni Teams'in IndexedDB onbellegini okur (yalnizca Windows)
* `fake.py`        -- bellek ici kaynak (testler icin)
* `intake.py`      -- normalize, tur karari, istatistik (saf is mantigi)

Veri kullanicinin kendi makinesindeki kendi arama gecmisidir; Teams'in
"Aramalar -> Gecmis" ekraninda zaten gorunur. Hicbir sey disari gitmez.
"""

from __future__ import annotations

from typing import Any

from .source import (
    DIRECTION_IN,
    DIRECTION_OUT,
    STATE_ACCEPTED,
    STATE_DECLINED,
    STATE_MISSED,
    TYPE_MULTI_PARTY,
    TYPE_TWO_PARTY,
    CallRecord,
    CallsError,
    CallSource,
    default_cache_path,
    is_supported,
    unavailable,
)

__all__ = [
    "CallRecord",
    "CallSource",
    "CallsError",
    "DIRECTION_IN",
    "DIRECTION_OUT",
    "STATE_ACCEPTED",
    "STATE_DECLINED",
    "STATE_MISSED",
    "TYPE_MULTI_PARTY",
    "TYPE_TWO_PARTY",
    "default_cache_path",
    "default_source",
    "is_supported",
    "unavailable",
]


def default_source(cache_path: str = "") -> Any:
    """Uretimdeki kaynak: Windows'ta yerel onbellek, baska yerde anlasilir hata.

    `teams_cache` ice aktarimi bu cagrinin icinde kalir; vendor'lanan ccl
    okuyucusu da orada, yalnizca gerektiginde yola eklenir.
    """
    if not is_supported():
        raise unavailable()
    from .teams_cache import TeamsCacheSource  # yerel: Windows disinda dokunulmaz

    return TeamsCacheSource(cache_path=cache_path)
