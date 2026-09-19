"""Sessiz alt surec ayarlari: konsolsuz calisan Holocron'un actigi pencere.

Saha hatasi (19 Eylul 2026, Windows): `holocron.bat` uygulamayi
`start "" pythonw.exe` ile konsolsuz actiriyor. Boyle bir surec Copilot CLI'yi
(`ozet.py`) ya da pip'i (`yaziyadok.py::kur`) `subprocess` ile cagirdiginda
Windows kendiliginden BOS bir konsol penceresi aciyor -- kullanicinin gordugu
tek sey bu yaniltici pencere. `sessiz_calistir_ayarlari()` bu pencereyi
bastiran ayarlari dondurur, cagiran taraf `subprocess.run`/`Popen`e
`**ayarlar` ile yayar.

Windows disinda bu bayraklar (CREATE_NO_WINDOW, STARTUPINFO) hic yok --
`subprocess` modulunde bile tanimli degiller -- bu yuzden diger platformlarda
sozluk bos doner (yalnizca `stdin` kalir, o her platformda gecerli).

`stdin=DEVNULL` HER platformda eklenir: konsolsuz surecte okunacak bir stdin
zaten yok, ama bazi CLI'lar (Copilot dahil) girdi beklerse asilip kalabiliyor;
DEVNULL vermek onlari hemen EOF ile karsilar.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Any


def sessiz_calistir_ayarlari() -> dict[str, Any]:
    """`subprocess.run`/`Popen`e `**ayarlar` ile verilecek sozluk.

    Windows disinda yalnizca `stdin` icerir. Windows'ta ayrica konsol
    penceresini bastiran `creationflags` ve `startupinfo` eklenir.
    `getattr` ile okunur: testler `sys.platform`i Linux'ta "win32"ye
    cevirdiginde gercek `subprocess` modulunde bu ozellikler yoktur, hata
    vermek yerine sessizce atlanir (gercek Windows'ta ikisi de her zaman var).
    """
    ayarlar: dict[str, Any] = {"stdin": subprocess.DEVNULL}
    if not sys.platform.startswith("win"):
        return ayarlar

    ayarlar["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    baslangic_sinifi = getattr(subprocess, "STARTUPINFO", None)
    if baslangic_sinifi is not None:
        baslangic = baslangic_sinifi()
        baslangic.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 1)
        baslangic.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
        ayarlar["startupinfo"] = baslangic
    return ayarlar
