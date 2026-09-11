#!/usr/bin/env python3
"""Calisma dizininden bagimsiz giris noktasi.

`python -m app` yalnizca `app/` paketinin bulundugu klasor `sys.path` icinde
oldugunda calisir. Bu sart iki yerde bozuluyor:

* Surec baska bir klasorde baslatilmissa (`start ""` ile acilan pencere,
  kisayol, gorev zamanlayici, kullanicinin baska yerde actigi cmd).
* `PYTHONSAFEPATH`/`-P` acikken calisma dizini `sys.path`'e hic eklenmez;
  bazi kurumsal Python kurulumlarinda bu ayar varsayilan geliyor.

Bu dosya kendi konumunu `__file__`'dan bulur ve `sys.path`'in basina koyar;
boylece nereden calistirildigi onemsizlesir. Baslaticilar `-m app` yerine bu
dosyayi tam yoluyla cagirir.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.__main__ import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
