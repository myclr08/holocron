"""Sahte `copilot` betigi: demo makinesinde Copilot CLI kurulu olmasin diye.

`--copilot-sahte` bayragiyla acilir. Yazilan betik gercek Copilot CLI'nin
Holocron'a bakan yuzunu taklit eder:

* `copilot --model <model> -p <istem> --allow-tool=read --allow-tool=write`
* "Copilot'u sina" isteminde `{"hazir": true}` iceren dosyayi yazar,
* "Duzelt" isteminde girdi dosyasini okur, kucuk bir duzeltme uygulayip
  cikti dosyasina yazar.

Hicbir sey aga cikmaz; model adi yalnizca ekrana basilir.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

BETIK_ADI = "copilot"

# Yazilan betigin govdesi. Kendi icinde de ASCII Turkce yorum kullanir.
GOVDE = '''#!{yorumlayici}
"""Sahte Copilot CLI (Holocron demo ortami). Gercek modele hic gitmez."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def istemi_al(argumanlar):
    """`-p <istem>` degeri."""
    for sira, arguman in enumerate(argumanlar):
        if arguman == "-p" and sira + 1 < len(argumanlar):
            return argumanlar[sira + 1]
    return ""


def modeli_al(argumanlar):
    for sira, arguman in enumerate(argumanlar):
        if arguman == "--model" and sira + 1 < len(argumanlar):
            return argumanlar[sira + 1]
    return "demo-model"


def duzelt(metin):
    """Kucuk ve gorunur duzeltmeler: bosluk, noktalama, ilk harf."""
    satirlar = []
    for satir in metin.split("\\n"):
        temiz = re.sub(r"[ \\t]+", " ", satir).strip()
        temiz = re.sub(r"\\s+([,.;:!?])", r"\\1", temiz)
        temiz = re.sub(r"([,.;:!?])(?=[^\\s\\d])", r"\\1 ", temiz)
        if temiz and temiz[0].islower():
            temiz = temiz[0].upper() + temiz[1:]
        if temiz and temiz[-1] not in ".!?:,-" and len(temiz) > 12:
            temiz += "."
        satirlar.append(temiz)
    return "\\n".join(satirlar)


def main():
    argumanlar = sys.argv[1:]
    istem = istemi_al(argumanlar)
    model = modeli_al(argumanlar)

    # 1) "Copilot'u sina" istemi: hedef dosya yolu istemde geciyor.
    if '{{"hazir": true}}' in istem or '"hazir"' in istem:
        eslesme = re.search(r"yaz:\\s*(.+?)\\s+[\\u2014-]", istem) or re.search(
            r"yaz:\\s*(\\S+)", istem
        )
        if eslesme:
            hedef = Path(eslesme.group(1).strip())
            hedef.parent.mkdir(parents=True, exist_ok=True)
            hedef.write_text(json.dumps({{"hazir": True}}), encoding="utf-8")
            print("sahte copilot: {{}} yazildi (model {{}})".format(hedef, model))
            return 0
        print(json.dumps({{"hazir": True}}))
        return 0

    # 2) "Duzelt" istemi: girdi ve cikti dosyalari istemde yaziyor.
    girdi = re.search(r"Girdi dosyas\\u0131:\\s*(.+)", istem)
    cikti = re.search(r"\\u00c7\\u0131kt\\u0131 dosyas\\u0131:\\s*(.+)", istem)
    if girdi and cikti:
        kaynak = Path(girdi.group(1).strip())
        hedef = Path(cikti.group(1).strip())
        try:
            ham = kaynak.read_text(encoding="utf-8")
        except OSError as hata:
            print("sahte copilot: girdi okunamadi: {{}}".format(hata), file=sys.stderr)
            return 1
        hedef.parent.mkdir(parents=True, exist_ok=True)
        hedef.write_text(duzelt(ham), encoding="utf-8")
        print("sahte copilot: metin duzeltildi (model {{}})".format(model))
        return 0

    print("sahte copilot: ne yapacagimi anlamadim", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
'''


def kur(klasor: Path, yorumlayici: str | None = None) -> Path:
    """Sahte `copilot` betigini yazar ve calistirilabilir yapar."""
    hedef_klasor = Path(klasor)
    hedef_klasor.mkdir(parents=True, exist_ok=True)
    hedef = hedef_klasor / BETIK_ADI
    hedef.write_text(
        GOVDE.format(yorumlayici=yorumlayici or sys.executable), encoding="utf-8"
    )
    hedef.chmod(hedef.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return hedef


def patha_ekle(klasor: Path) -> None:
    """Betigin klasorunu PATH'in basina koyar (yalniz bu surec icin)."""
    mevcut = os.environ.get("PATH", "")
    os.environ["PATH"] = f"{Path(klasor)}{os.pathsep}{mevcut}" if mevcut else str(klasor)
