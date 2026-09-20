"""Teams mesaj belirteci: uygulama disina cikarken metinden silinir.

Teams'te bir mesajin baglantisi kopyalandiginda panoya bir on satir ve bir
derin baglanti duser. Arayuz (`app/static/js/teamslink.js`) bunu tek satirlik
bir belirtece cevirir ve alanda oyle saklar:

    [[teams: Deniz Akgün · Ödeme ekibi · 16 Eyl 2026 14:14|https://teams…]]

Belirtec YALNIZCA uygulamanin icinde anlamlidir: orada tiklanabilir bir cipe
donusur. Excel dokumune, e-posta govdesine ve Teams mesaj sablonlarina HIC
girmez; ne adres ne cip ne de yer tutucu olarak. Bu modul onu metinden siler
ve cevresindeki boslugu duzgun birakir.

`temizle` JavaScript esi `teamsLinkTemizle` ile ayni sonucu verir; ikisi de
`tests/test_teamslink.py` icinde ayni orneklerle sinaniyor.
"""

from __future__ import annotations

import re
from typing import Any

# Etiket ve adres `|` ile ayrilir; ikisi de `[`, `]` ve `|` icermez.
BELIRTEC = re.compile(r"\[\[teams:([^\[\]|]*)\|([^\[\]|]*)\]\]")

# Yatay bosluk: belirtecin iki yanindan silinen karakterler.
_BOSLUK = (" ", "\t")


def var_mi(metin: Any) -> bool:
    """Metinde en az bir Teams belirteci var mi?"""
    return bool(BELIRTEC.search(str(metin or "")))


def temizle(metin: Any) -> str:
    """Belirtecleri siler, cevresindeki boslugu duzgun birakir.

    "önce [[teams:…]] sonra" -> "önce sonra" (tek bosluk). Kendi satirinda
    duran belirtec satiriyla birlikte gider. Metinde belirtec yoksa metin
    oldugu gibi doner.
    """
    ham = "" if metin is None else str(metin)
    if not ham:
        return ""

    parcalar: list[str] = []
    imlec = 0
    bulundu = False
    for eslesme in BELIRTEC.finditer(ham):
        bulundu = True
        bas, son = eslesme.span()
        while bas > 0 and ham[bas - 1] in _BOSLUK:
            bas -= 1
        while son < len(ham) and ham[son] in _BOSLUK:
            son += 1
        sol = ham[bas - 1] if bas > 0 else ""
        sag = ham[son] if son < len(ham) else ""
        if sol == "\n" and sag == "\n":
            # Belirtec kendi satirindaydi: satir tamamen gider.
            son += 1
            ayirici = ""
        elif sol in ("", "\n") or sag in ("", "\n"):
            ayirici = ""
        else:
            ayirici = " "
        if bas <= imlec:
            # Iki belirtec arasinda metin kalmadi: ayirici bir kez konur.
            ayirici = ""
        parcalar.append(ham[imlec:bas] if bas > imlec else "")
        parcalar.append(ayirici)
        imlec = son
    if not bulundu:
        return ham
    parcalar.append(ham[imlec:])
    return "".join(parcalar)


def etiket(metin: Any) -> str:
    """Ilk belirtecin etiketi (test ve teshis icin); yoksa bos metin."""
    eslesme = BELIRTEC.search(str(metin or ""))
    return eslesme.group(1).strip() if eslesme else ""
