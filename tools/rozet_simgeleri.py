"""Rozet simgelerini (`app/static/rozetler/*.svg`) ureten kucuk arac.

Neden urettik, elle cizmedik: 54 rozet var ve hepsinin AYNI dili konusmasi
gerekiyor -- ayni 24x24 kutu, ayni cizgi kalinligi, kategori basina ayni
cerceve, nadirlige gore ayni halka rengi. Tek tek yazilan SVG'lerde bu duzen
bir iki surumde dagilir; burada kural tek yerde durur.

Calistirma:

    python tools/rozet_simgeleri.py

Uretilen dosyalar DEPOYA GIRER: uygulama calisirken hicbir sey uretmez, sadece
`/static/rozetler/<kod>.svg` okur. Yeni rozet eklenince bu arac yeniden
calistirilir (testler eksik dosyayi yakalar).

Cizim dili: cizgi tabanli (fill yok), tek renk govde + nadirlik halkasi. Emoji
ya da yazi kullanilmaz; simge 22 pikselde de okunabilmeli.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import gamify  # noqa: E402

HEDEF = ROOT / "app" / "static" / "rozetler"

# Govde rengi tek: simge "tek renk + vurgu" kuralina uyar, vurgu halkadir.
CIZGI = "#c9d4e4"
# Nadirlik halkanin rengini belirler: yaygin gri, nadir mavi, efsanevi sari.
HALKA_RENGI = {
    gamify.RARITY_COMMON: "#8a94a6",
    gamify.RARITY_RARE: "#4aa3ff",
    gamify.RARITY_LEGEND: "#ffe81f",
}

# Kategori cercevenin DESENINI belirler: ayni aileden rozetler uzaktan bile
# birbirine benzer.
HALKA_DESENI = {
    gamify.CATEGORY_TASK: "",
    gamify.CATEGORY_JIRA: "3 2",
    gamify.CATEGORY_STREAK: "1 2",
    gamify.CATEGORY_QUEST: "5 2",
    gamify.CATEGORY_RANK: "",
    gamify.CATEGORY_RITUAL: "2 1.6",
    gamify.CATEGORY_CONTACT: "4 1.6 1 1.6",
    gamify.CATEGORY_TOOL: "0.1 2.6",
}


def c(cx: float, cy: float, r: float) -> str:
    return f'<circle cx="{cx}" cy="{cy}" r="{r}"/>'


def l(x1: float, y1: float, x2: float, y2: float) -> str:  # noqa: E743
    return f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}"/>'


def p(d: str) -> str:
    return f'<path d="{d}"/>'


def r(x: float, y: float, w: float, h: float, rx: float = 1) -> str:
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}"/>'


def nokta(cx: float, cy: float) -> str:
    """Dolu kucuk isaret; cizgi kalinligiyla uyumlu olsun diye r=0.7."""
    return f'<circle cx="{cx}" cy="{cy}" r="0.7" fill="{CIZGI}" stroke="none"/>'


TIK = "M8.6 12.1 l2.2 2.2 l4.6-4.9"
ALEV = "M12 6.4 c2.4 2.5 3.8 4.2 3.8 6.1 a3.8 3.8 0 0 1-7.6 0 c0-1.9 1.4-3.6 3.8-6.1z"
ZARF = ["M6.6 8.6 h10.8 v6.8 h-10.8 z", "M6.6 8.9 l5.4 4 l5.4-4"]


def zincir() -> list[str]:
    return [
        p("M10.6 13.4 l-1.6 1.6 a2.2 2.2 0 0 1-3.1-3.1 l1.6-1.6"),
        p("M13.4 10.6 l1.6-1.6 a2.2 2.2 0 0 1 3.1 3.1 l-1.6 1.6"),
        l(10.4, 13.6, 13.6, 10.4),
    ]


def isin(sayi: int, ic: float = 4.6, dis: float = 6.6, bas: float = -90.0,
         yay: float = 360.0) -> list[str]:
    """Merkezden disa dogru isinlar; 'patlama' ve 'gun dogumu' bunu kullanir."""
    import math

    parcalar: list[str] = []
    adim = yay / max(1, sayi - 1 if yay < 360 else sayi)
    for index in range(sayi):
        aci = math.radians(bas + adim * index)
        parcalar.append(
            l(
                round(12 + ic * math.cos(aci), 2), round(12 + ic * math.sin(aci), 2),
                round(12 + dis * math.cos(aci), 2), round(12 + dis * math.sin(aci), 2),
            )
        )
    return parcalar


# Kod -> simgenin govdesi. Her rozetin kendi cizimi var.
GOVDELER: dict[str, list[str]] = {
    # --- Gorev ---
    gamify.BADGE_FIRST_TASK: [p(TIK), l(7.2, 16.4, 16.8, 16.4)],
    gamify.BADGE_TASK_10: [p("M7.4 10.4 l1.8 1.8 l3.6-3.8"), p("M7.4 15.2 l1.8 1.8 l3.6-3.8"),
                           l(14.6, 9.6, 16.8, 9.6), l(14.6, 16.4, 16.8, 16.4)],
    gamify.BADGE_CLOSER: [r(6.8, 7.4, 10.4, 9.2, 1.2), p("M9.4 12.4 l1.8 1.8 l3.4-3.6")],
    gamify.BADGE_TASK_50: [p("M6.9 8.6 l1.6 1.6 l3.2-3.4"), p("M6.9 13 l1.6 1.6 l3.2-3.4"),
                           p("M6.9 17.4 l1.6 1.6 l3.2-3.4"), l(14.4, 8.4, 17, 8.4),
                           l(14.4, 12.8, 17, 12.8), l(14.4, 17.2, 17, 17.2)],
    gamify.BADGE_TASK_200: [c(12, 12, 3.4)] + isin(8),
    gamify.BADGE_TASK_DAY_5: [c(12, 13.6, 3)] + isin(5, ic=5.2, dis=7, bas=-160, yay=140),
    gamify.BADGE_EARLY_10: [c(12, 12, 5.2), l(12, 12, 12, 8.6), l(12, 12, 9.4, 13.6)],
    gamify.BADGE_NOTED_20: [r(7.6, 5.8, 8.8, 12.4, 1.2), l(9.6, 9.2, 14.4, 9.2),
                            l(9.6, 12, 14.4, 12), l(9.6, 14.8, 12.8, 14.8)],
    gamify.BADGE_LINKED_25: zincir(),
    gamify.BADGE_CLEAN_DESK: [l(6.4, 14.4, 17.6, 14.4), l(8.4, 14.4, 8.4, 17.6),
                              l(15.6, 14.4, 15.6, 17.6), p("M12 6.2 l1 2.6 l2.6 1 l-2.6 1 l-1 2.6 "
                                                           "l-1-2.6 l-2.6-1 l2.6-1z")],
    gamify.BADGE_ARCHIVIST: [r(6.2, 9, 11.6, 8.4, 1), l(6.2, 12, 17.8, 12),
                             p("M6.2 9 l1.8-2.4 h8 l1.8 2.4"), l(10.6, 14.4, 13.4, 14.4)],
    # --- Jira akisi ---
    gamify.BADGE_FIRST_ISSUE: [r(6.4, 8, 11.2, 8, 1.4), p("M9 12 l1.8 1.8 l3.6-3.8")],
    gamify.BADGE_FINISHER: [l(8, 6, 8, 18), p("M8 6.8 h7.6 v5.6 h-7.6"),
                            l(11.8, 6.8, 11.8, 12.4), l(8, 9.6, 15.6, 9.6)],
    gamify.BADGE_DROP_25: [p("M12 7 v7.6"), p("M8.8 11.6 l3.2 3.4 l3.2-3.4"),
                           nokta(9, 17.4), nokta(15, 17.4)],
    gamify.BADGE_DROP_100: [p("M8 7.4 v5.4"), p("M6.4 11 l1.6 1.8 l1.6-1.8"),
                            p("M12 7.4 v8"), p("M10.4 13.6 l1.6 1.8 l1.6-1.8"),
                            p("M16 7.4 v5.4"), p("M14.4 11 l1.6 1.8 l1.6-1.8")],
    gamify.BADGE_DROP_500: [p("M7 7 h10"), p("M7.6 10 h8.8"), p("M9 13 h6"),
                            p("M12 15.2 v2.4"), p("M10.4 16.2 l1.6 1.8 l1.6-1.8")],
    gamify.BADGE_WEEK_20: [r(6.2, 7.6, 11.6, 9.6, 1.2), l(6.2, 10.4, 17.8, 10.4),
                           l(9, 6.2, 9, 8.4), l(15, 6.2, 15, 8.4),
                           p("M12.6 11.8 l-2 3.2 h2.2 l-1 2.6")],
    gamify.BADGE_OLD_ISSUE: [p("M8.4 6.6 h7.2 l-3.6 5.4 l3.6 5.4 h-7.2 l3.6-5.4z"),
                             l(8, 6.6, 16, 6.6), l(8, 17.4, 16, 17.4)],
    gamify.BADGE_THREE_PROJECTS: [r(9.4, 5.6, 5.2, 5.2, 1), r(5.6, 13.2, 5.2, 5.2, 1),
                                  r(13.2, 13.2, 5.2, 5.2, 1)],
    gamify.BADGE_LOCAL_50: [r(6.2, 7.4, 11.6, 9.2, 1), l(6.2, 10.4, 17.8, 10.4),
                            l(6.2, 13.6, 17.8, 13.6), l(10.2, 7.4, 10.2, 16.6),
                            l(13.8, 7.4, 13.8, 16.6)],
    # --- Seri ---
    gamify.BADGE_STREAK_5: [p(ALEV)],
    gamify.BADGE_STREAK_20: [p(ALEV), p("M12 11.4 c1.1 1.2 1.7 2 1.7 2.8 a1.7 1.7 0 0 1-3.4 0 "
                                        "c0-.8 .6-1.6 1.7-2.8z")],
    gamify.BADGE_STREAK_30: [p(ALEV), p("M5.4 14.6 a6.8 6.8 0 0 0 1.6 3.6")],
    gamify.BADGE_STREAK_60: [p(ALEV), p("M5.4 14.6 a6.8 6.8 0 0 0 1.6 3.6"),
                             p("M18.6 14.6 a6.8 6.8 0 0 1-1.6 3.6")],
    gamify.BADGE_GRACE_SAVED: [p("M12 5.8 l5 2 v4.2 c0 3-2.1 5.2-5 6.2 c-2.9-1-5-3.2-5-6.2 V7.8z"),
                               p("M9.8 12.2 l1.6 1.6 l3-3.2")],
    gamify.BADGE_STREAK_AGAIN: [p("M17.2 12 a5.2 5.2 0 1 1-1.9-4"),
                                p("M17.4 6.6 v2.8 h-2.8")],
    # --- Haftalik emirler ---
    gamify.BADGE_FIRST_QUEST: [p("M7.6 6.6 h7.2 a1.6 1.6 0 0 1 1.6 1.6 v9.2 h-8.8z"),
                               p("M16.4 17.4 a1.6 1.6 0 0 0 1.6-1.6 h-2.4"),
                               l(9.6, 9.6, 14, 9.6), l(9.6, 12.2, 14, 12.2)],
    gamify.BADGE_QUEST_4_WEEKS: [r(5.6, 9.6, 4, 4, .8), r(10.4, 9.6, 4, 4, .8),
                                 r(5.6, 14.4, 4, 4, .8), r(10.4, 14.4, 4, 4, .8),
                                 p("M15.6 7.4 l1.4 1.4 l2.4-2.6")],
    gamify.BADGE_QUEST_MONDAY: [r(6.2, 7.6, 11.6, 9.6, 1.2), l(6.2, 10.4, 17.8, 10.4),
                                l(9, 6.2, 9, 8.4), l(15, 6.2, 15, 8.4),
                                p("M12.8 11.8 l-2.2 3.4 h2.4 l-1.2 2.6")],
    # --- XP ve rutbe ---
    gamify.BADGE_XP_1000: [p("M7.4 14.4 l4.6-4.8 l4.6 4.8")],
    gamify.BADGE_XP_5000: [p("M7.4 12.4 l4.6-4.8 l4.6 4.8"), p("M7.4 17 l4.6-4.8 l4.6 4.8")],
    gamify.BADGE_XP_20000: [p("M7.4 10.4 l4.6-4.4 l4.6 4.4"), p("M7.4 14.4 l4.6-4.4 l4.6 4.4"),
                            p("M7.4 18.4 l4.6-4.4 l4.6 4.4")],
    gamify.BADGE_RANK_KNIGHT: [l(6.8, 13.6, 17.2, 13.6), p("M12 6.4 l2.4 4 h-4.8z")],
    gamify.BADGE_RANK_MASTER: [l(6.8, 15.4, 17.2, 15.4), l(6.8, 12.6, 17.2, 12.6),
                               p("M12 5.4 l2.4 4 h-4.8z")],
    gamify.BADGE_RANK_COUNCIL: [l(6.8, 17, 17.2, 17), l(6.8, 14.4, 17.2, 14.4),
                                l(6.8, 11.8, 17.2, 11.8), p("M12 4.8 l2.4 4 h-4.8z")],
    gamify.BADGE_RANK_LEGEND: [p("M6.2 16.4 l-.8-7 l3.8 2.8 l2.8-4.6 l2.8 4.6 l3.8-2.8 l-.8 7z"),
                               l(6.2, 18.2, 17.8, 18.2)],
    gamify.BADGE_XP_DAY_300: [c(12, 12, 5.6), p("M13 8.4 l-2.6 4.2 h2.8 l-1.4 3.4")],
    gamify.BADGE_COMPLETE: [c(12, 12, 5.8), c(12, 12, 2.6), nokta(12, 12)],
    # --- Zaman ve ritim ---
    gamify.BADGE_DAWN_10: [p("M8 14.6 a4 4 0 0 1 8 0"), l(5.4, 14.6, 18.6, 14.6)]
    + isin(5, ic=6.2, dis=7.6, bas=-170, yay=160)
    + [l(9, 17.4, 15, 17.4)],
    gamify.BADGE_FRIDAY_5: [p("M8 12.6 a4 4 0 0 0 8 0"), l(5.4, 12.6, 18.6, 12.6),
                            p("M12 18 v-3"), p("M10.4 16.4 l1.6 1.8 l1.6-1.8")],
    gamify.BADGE_FULL_MONTH: [r(6.2, 7.6, 11.6, 9.6, 1.2), l(6.2, 10.4, 17.8, 10.4),
                              nokta(9, 12.6), nokta(12, 12.6), nokta(15, 12.6),
                              nokta(9, 15.2), nokta(12, 15.2), nokta(15, 15.2)],
    gamify.BADGE_YEAR_FIRST: [r(6.2, 7.6, 11.6, 9.6, 1.2), l(6.2, 10.4, 17.8, 10.4),
                              l(9, 6.2, 9, 8.4), l(15, 6.2, 15, 8.4),
                              p("M11 13.4 l1.4-1 v4.4"), l(11, 16.8, 13.8, 16.8)],
    # --- Iletisim ---
    gamify.BADGE_ENVOY: [p("M6.4 8.4 h11.2 v6.8 h-6.4 l-3.2 2.8 v-2.8 h-1.6z"),
                         p("M10.8 11 a1.3 1.3 0 1 1 1.4 1.4 v.8"), nokta(12.2, 14)],
    gamify.BADGE_MAIL_10: [p(ZARF[0]), p(ZARF[1]), nokta(16.6, 16.8)],
    gamify.BADGE_TEAMS_10: [p("M5.4 8 h8.4 v5.4 h-5.2 l-2 1.8 v-1.8 h-1.2z"),
                            p("M10.2 11 h8.4 v5.4 h-1.2 v1.8 l-2-1.8 h-5.2z")],
    gamify.BADGE_CONTACTS_20: [c(10, 9.8, 2.4), p("M5.6 17.4 a4.6 4.6 0 0 1 8.8 0"),
                               p("M14.6 7.8 a2.2 2.2 0 0 1 0 4.2"),
                               p("M15.4 13.8 a4.2 4.2 0 0 1 3 3.6")],
    gamify.BADGE_FAST_REPLY: [p("M6.2 9 h8.2 v6.4 h-8.2z"), p("M6.2 9.2 l4.1 3 l4.1-3"),
                              p("M17.4 7.4 l-2.4 4 h2.2 l-2 3.6")],
    # --- Kesif ve araclar ---
    gamify.BADGE_FIRST_FIX: [l(7.2, 16.8, 14.4, 9.6), p("M13.4 8.6 l2 2"),
                             p("M16.2 5.4 l.7 1.7 l1.7 .7 l-1.7 .7 l-.7 1.7 l-.7-1.7 "
                               "l-1.7-.7 l1.7-.7z")],
    gamify.BADGE_FIX_25: [l(6.6, 17.4, 13.2, 10.8), p("M12.4 10 l2 2"),
                          p("M15.6 5 l.6 1.5 l1.5 .6 l-1.5 .6 l-.6 1.5 l-.6-1.5 l-1.5-.6z"),
                          nokta(18.2, 10.2), nokta(11.4, 6)],
    gamify.BADGE_FIRST_EXCEL: [r(7, 6.2, 10, 11.6, 1.2), l(7, 9.6, 17, 9.6),
                               p("M9.6 12 l4.8 4.4"), p("M14.4 12 l-4.8 4.4")],
    gamify.BADGE_FIRST_FLEET: [p("M12 6.2 l4.6 9.6 h-9.2z"), l(12, 6.2, 12, 15.8),
                               l(7.6, 18.2, 16.4, 18.2)],
    gamify.BADGE_FLEET_5: [p("M12 5.6 l3.2 6.4 h-6.4z"), p("M7 12.6 l2.6 5.2 h-5.2z"),
                           p("M17 12.6 l2.6 5.2 h-5.2z")],
    gamify.BADGE_FIRST_LOCAL_FIELD: [r(6.4, 7.4, 7.2, 9.2, 1), l(6.4, 10.4, 13.6, 10.4),
                                     l(10, 10.4, 10, 16.6),
                                     l(16.8, 12.4, 16.8, 17.2), l(14.4, 14.8, 19.2, 14.8)],
    gamify.BADGE_CARTOGRAPHER: [p("M5.6 7.8 l4.2-1.6 v10.4 l-4.2 1.6z"),
                                p("M9.8 6.2 l4.4 1.6 v10.4 l-4.4-1.6z"),
                                p("M14.2 7.8 l4.2-1.6 v10.4 l-4.2 1.6z")],
}


def simge(badge: dict) -> str:
    """Tek bir rozetin SVG metni."""
    desen = HALKA_DESENI.get(badge["category"], "")
    halka = HALKA_RENGI.get(badge["rarity"], HALKA_RENGI[gamify.RARITY_COMMON])
    cerceve = (
        f'<circle cx="12" cy="12" r="11.1" fill="none" stroke="{halka}" '
        f'stroke-width="1.1"'
        + (f' stroke-dasharray="{desen}" stroke-linecap="round"' if desen else "")
        + "/>"
    )
    # Rutbe ailesi cift halkalidir: hiyerarsi uzaktan okunur.
    if badge["category"] == gamify.CATEGORY_RANK:
        cerceve += (
            f'<circle cx="12" cy="12" r="9.2" fill="none" stroke="{halka}" '
            f'stroke-width=".55" opacity=".65"/>'
        )
    govde = "".join(GOVDELER[badge["code"]])
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        f'width="24" height="24" role="img" aria-label="{badge["label"]}">'
        f"<title>{badge['label']}</title>"
        f"{cerceve}"
        f'<g fill="none" stroke="{CIZGI}" stroke-width="1.35" stroke-linecap="round" '
        f'stroke-linejoin="round">{govde}</g>'
        "</svg>\n"
    )


def uret(hedef: Path = HEDEF) -> list[Path]:
    hedef.mkdir(parents=True, exist_ok=True)
    eksik = [badge["code"] for badge in gamify.BADGES if badge["code"] not in GOVDELER]
    if eksik:
        raise SystemExit("Govdesi olmayan rozet: " + ", ".join(eksik))
    yazilan: list[Path] = []
    for badge in gamify.BADGES:
        yol = hedef / f"{badge['code']}.svg"
        yol.write_text(simge(badge), encoding="utf-8")
        yazilan.append(yol)
    return yazilan


if __name__ == "__main__":
    dosyalar = uret()
    print(f"{len(dosyalar)} rozet simgesi yazildi: {HEDEF}")
