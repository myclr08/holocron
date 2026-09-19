"""Bellek ici Copilot calistiricisi: hicbir test gercek Copilot CLI'yi kosturmaz.

`app.copilot.CopilotCalistirici` ile ayni sozlesme: `sor(klasor, model, istem)`
bir `CopilotCikti` dondurur. Kip secimi hangi saha durumunu taklit ettigini
soyler:

* `dosya`  : model cevabi kendi yazdigi dosyaya koyar (mutlu yol),
* `stdout` : dosya yok, JSON stdout'ta kod blogu ve ANSI icinde,
* `bos`    : hicbir JSON yok, yalnizca ozur metni,
* `izin`   : arac izni reddedildi,
* `sessiz` : surec doner ama hicbir sey yazmaz ve hicbir sey basmaz.

Metin duzeltme ucu JSON degil DUZ METIN ister; onun kipleri ayri:

* `metin`        : model duzeltilmis metni istemdeki cikti dosyasina yazar,
* `metin-stdout` : dosya yok, metin stdout'ta kod blogu ve ANSI icinde.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Iterable

from app.copilot import CopilotCikti, cikti_yolu

# Sinama isteginin bekledigi cevap.
ORNEK_CEVAP: dict[str, Any] = {"hazir": True}

# Duzeltme kipinde modelin yazdigini varsaydigimiz metin.
ORNEK_DUZELTME = "Geçiş 15 Ekim'de yapılacak."

# Istemde gecen cikti dosyasinin yolu: gercek model de onu oradan okur.
_CIKTI_SATIRI = re.compile(r"Çıktı dosyası:\s*(.+)")


class SahteCopilot:
    """Testlerin kullandigi calistirici."""

    def __init__(
        self,
        kip: str = "dosya",
        cevap: dict[str, Any] | None = None,
        reddedilen: Iterable[str] = (),
        son_yol: str = "",
        duzeltme: str = ORNEK_DUZELTME,
    ) -> None:
        self.kip = kip
        self.cevap = dict(cevap or ORNEK_CEVAP)
        # Duzeltme kiplerinde modelin urettigi metin (testler degistirir).
        self.duzeltme = duzeltme
        self.reddedilen = set(reddedilen)
        self.son_yol = son_yol
        # Gorulen istemler ve modeller: testler sirayi ve icerigi denetler.
        self.istemler: list[str] = []
        self.modeller: list[str] = []

    def sor(self, klasor: Path, model: str, istem: str) -> CopilotCikti:
        self.istemler.append(istem)
        self.modeller.append(model)
        if model in self.reddedilen:
            return CopilotCikti(
                kod=1,
                metin="",
                hata=f'Model "{model}" from --model flag is not available',
            )
        if self.kip == "metin":
            hedef = _cikti_dosyasi(istem)
            if hedef is not None:
                hedef.write_text(self.duzeltme, encoding="utf-8")
            return CopilotCikti(kod=0, metin="● Wrote copilot-duzelt-cikti.txt")
        if self.kip == "metin-stdout":
            return CopilotCikti(
                kod=0,
                metin=(
                    "\x1b[1mGitHub Copilot CLI\x1b[0m\n● Reading…\n"
                    f"```\n{self.duzeltme}\n```\n"
                ),
            )
        if self.kip == "sessiz":
            return CopilotCikti(kod=0, metin="", hata="")
        if self.kip == "bos":
            return CopilotCikti(kod=0, metin="\x1b[33mÜzgünüm, dosyayı bulamadım.\x1b[0m")
        if self.kip == "izin":
            return CopilotCikti(
                kod=0, metin="", hata="Error: permission to write file was denied"
            )
        govde = _json_metni(self.cevap)
        if self.kip == "stdout":
            return CopilotCikti(
                kod=0, metin=f"\x1b[1mGitHub Copilot CLI\x1b[0m\n```json\n{govde}\n```\n"
            )
        # Varsayilan: model dosyayi gercekten yazar, cagiran taraf okur.
        cikti_yolu(klasor).write_text(govde, encoding="utf-8")
        return CopilotCikti(kod=0, metin="● Done", dosya=govde)


def _cikti_dosyasi(istem: str) -> Path | None:
    """Istemdeki "Çıktı dosyası: ..." satirindan yolu okur."""
    eslesme = _CIKTI_SATIRI.search(str(istem or ""))
    return Path(eslesme.group(1).strip()) if eslesme else None


def _json_metni(veri: dict[str, Any]) -> str:
    import json

    return json.dumps(veri, ensure_ascii=False)
