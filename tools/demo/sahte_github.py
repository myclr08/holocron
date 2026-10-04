"""Demo icin sahte GitHub: yerel git depolari + kucuk REST sunucusu (stdlib).

Uc uydurma depo kurulur (`ornek-org/...`). Her birinin ciplak bir "origin"i,
kullanicinin klonu gibi davranan bir calisma klonu ve sahte sunucunun PR
birlestirmek icin kullandigi ayri bir "birlestirici" klonu vardir. Gecmis son
30 gune yayilmis `--no-ff` birlesme commit'leridir; birkac PR onceden ambara
alinmistir (ana dalda geri alma commit'i).

Sunucunun DURUMU git'in kendisidir: kapali PR listesi origin'in ana dalindaki
"Merge pull request #N from ..." commit'lerinden okunur. Bu yuzden demo yeniden
baslatildiginda depolar oldugu gibi kullanilir.

Karsilanan uclar (Ambar'in kullandiklari):

  GET  /repos/{o}/{r}
  GET  /repos/{o}/{r}/pulls?state=closed|open
  GET  /repos/{o}/{r}/pulls/{n}
  POST /repos/{o}/{r}/pulls          (PR acar; demo'da hemen "onaylanip" birlesir)

Butun veri uydurmadir: kisiler, depolar ve basliklar gercek degildir.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

SAHTE_TOKEN = "demo-github-token"
KURULUS = "ornek-org"
ANA_DAL = "master"
WEB = "https://github.example"

# Her depo: (ad, [(baslik, yazar, dosya), ...], ambardakiler, cakisma ciftleri)
# Cakisma: ayni satiri degistiren iki PR; eskisini tek basina ambara almak
# "Rota hesaplama hatasi" verir (demo'da gosterilsin diye).
DEPOLAR: tuple[dict[str, Any], ...] = (
    {
        "ad": "odeme-servisi",
        "prlar": [
            ("Fatura numarası biçimi düzeltildi", "ayse.kaya", "src/fatura/numara.py"),
            ("Taksit hesaplamasına yeni kural", "mehmet.yilmaz", "src/odeme/taksit.py"),
            ("İade akışında zaman aşımı", "zeynep.arslan", "src/iade/akis.py"),
            ("Günlük limit kontrolü", "ayse.kaya", "src/odeme/limit.py"),
            ("Kart doğrulama hata mesajları", "can.demir", "src/kart/mesajlar.py"),
            ("3D Secure yönlendirmesi", "mehmet.yilmaz", "src/kart/yonlendirme.py"),
            ("Ödeme özeti e-postası", "elif.tas", "src/bildirim/ozet.py"),
            ("Kur çevrimi önbelleği", "burak.sahin", "src/kur/onbellek.py"),
            ("Mutabakat raporu sütunları", "deniz.oz", "src/mutabakat/rapor.py"),
            ("Ödeme API sürüm başlığı", "can.demir", "src/api/surum.py"),
        ],
        "ambarda": [4, 6],
    },
    {
        "ad": "musteri-portali",
        "prlar": [
            ("Giriş ekranında SSO düğmesi", "burak.sahin", "web/giris/sso.js"),
            ("Sepet sayfası yeni tasarım", "elif.tas", "web/sepet/sayfa.js"),
            ("Profil fotoğrafı yükleme", "zeynep.arslan", "web/profil/foto.js"),
            ("Bildirim tercihleri ekranı", "ayse.kaya", "web/ayarlar/bildirim.js"),
            ("Adres defteri düzenleme", "can.demir", "web/adres/defter.js"),
            ("Çeviri düzeltmeleri", "elif.tas", "web/i18n/tr.json"),
            ("Erişilebilirlik: odak halkaları", "burak.sahin", "web/stil/odak.css"),
            ("Sipariş geçmişi sayfalama", "deniz.oz", "web/siparis/gecmis.js"),
            ("Kampanya bandı", "mehmet.yilmaz", "web/anasayfa/bant.js"),
            ("Çerez onayı metni", "zeynep.arslan", "web/yasal/cerez.js"),
        ],
        "ambarda": [8],
    },
    {
        "ad": "raporlama",
        "prlar": [
            ("Aylık özet raporu", "deniz.oz", "motor/ozet.py"),
            ("CSV dışa aktarım ayırıcısı", "can.demir", "motor/csv.py"),
            ("Bölge kırılımı", "ayse.kaya", "motor/bolge.py"),
            ("Özet raporunda para birimi", "deniz.oz", "motor/ozet.py"),
            ("Zamanlanmış rapor kuyruğu", "mehmet.yilmaz", "motor/kuyruk.py"),
            ("Grafik renk paleti", "elif.tas", "motor/grafik.py"),
            ("Rapor önbelleği süresi", "burak.sahin", "motor/onbellek.py"),
            ("Yıllık karşılaştırma", "zeynep.arslan", "motor/yillik.py"),
            ("Excel şablonu güncellemesi", "can.demir", "motor/sablon.py"),
            ("Rapor erişim günlüğü", "ayse.kaya", "motor/gunluk.py"),
        ],
        "ambarda": [],
    },
)


def _git(cwd: Path, *args: str, env: dict[str, str] | None = None) -> str:
    sonuc = subprocess.run(
        ["git", "-c", "commit.gpgsign=false", "-c", "tag.gpgsign=false", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
    )
    if sonuc.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {sonuc.stderr.strip()}")
    return sonuc.stdout.strip()


def _kimlik(ad: str, an: datetime) -> dict[str, str]:
    """Commit'i uydurma kisinin adina ve verilen ana yazdirir."""
    damga = an.strftime("%Y-%m-%dT%H:%M:%S%z")
    return {
        **os.environ,
        "GIT_AUTHOR_NAME": ad,
        "GIT_AUTHOR_EMAIL": f"{ad}@example.com",
        "GIT_COMMITTER_NAME": ad,
        "GIT_COMMITTER_EMAIL": f"{ad}@example.com",
        "GIT_AUTHOR_DATE": damga,
        "GIT_COMMITTER_DATE": damga,
    }


class Depo:
    def __init__(self, kok: Path, ad: str) -> None:
        self.ad = ad
        self.tam_ad = f"{KURULUS}/{ad}"
        self.klasor = kok / ad
        self.origin = self.klasor / "origin.git"
        self.calisma = self.klasor / "calisma"
        self.birlestirici = self.klasor / "birlestirici"
        self.kilit = threading.Lock()

    @property
    def hazir(self) -> bool:
        return (self.origin / "HEAD").exists() and (self.calisma / ".git").exists()


def depolari_kur(kok: Path, sifirla: bool = False, an: datetime | None = None) -> list[Depo]:
    """Uc uydurma depoyu kurar; varsa oldugu gibi kullanir (`sifirla` hepsini siler)."""
    if sifirla and kok.exists():
        shutil.rmtree(kok, ignore_errors=True)
    kok.mkdir(parents=True, exist_ok=True)
    simdi = an or datetime.now(timezone.utc)
    depolar: list[Depo] = []
    for sira, tanim in enumerate(DEPOLAR):
        depo = Depo(kok, tanim["ad"])
        if not depo.hazir:
            shutil.rmtree(depo.klasor, ignore_errors=True)
            _depo_tohumla(depo, tanim, simdi, sira)
        depolar.append(depo)
    return depolar


def _depo_tohumla(depo: Depo, tanim: dict[str, Any], simdi: datetime, sira: int) -> None:
    depo.klasor.mkdir(parents=True)
    _git(depo.klasor, "init", "--quiet", "--bare", "-b", ANA_DAL, str(depo.origin))
    _git(depo.klasor, "clone", "--quiet", str(depo.origin), str(depo.birlestirici))
    b = depo.birlestirici
    _git(b, "checkout", "--quiet", "-b", ANA_DAL)
    baslangic = simdi - timedelta(days=31)
    (b / "README.md").write_text(f"# {depo.tam_ad}\n\nDemo deposu (uydurma).\n", encoding="utf-8")
    _git(b, "add", ".")
    _git(b, "commit", "--quiet", "-m", "İlk commit", env=_kimlik("demo.kurulum", baslangic))

    prlar = tanim["prlar"]
    adim = timedelta(days=29) / max(len(prlar), 1)
    numara_tabani = 100 + sira * 100
    birlesenler: list[tuple[int, str, datetime]] = []
    for indis, (baslik, yazar, dosya) in enumerate(prlar):
        numara = numara_tabani + indis * 3 + 1
        an = baslangic + adim * (indis + 1) + timedelta(hours=sira)
        dal = f"ozellik/{numara}"
        _git(b, "checkout", "--quiet", "-b", dal, ANA_DAL)
        hedef = b / dosya
        hedef.parent.mkdir(parents=True, exist_ok=True)
        onceki = hedef.read_text(encoding="utf-8") if hedef.exists() else "# demo\n"
        hedef.write_text(onceki + f"# {baslik}\nDEGER = {numara}\n", encoding="utf-8")
        _git(b, "add", ".")
        _git(b, "commit", "--quiet", "-m", baslik, env=_kimlik(yazar, an - timedelta(hours=3)))
        _git(b, "checkout", "--quiet", ANA_DAL)
        _git(
            b, "merge", "--quiet", "--no-ff", dal,
            "-m", f"Merge pull request #{numara} from {KURULUS}/{dal}", "-m", baslik,
            env=_kimlik(yazar, an),
        )
        _git(b, "branch", "-D", dal)
        birlesenler.append((numara, _git(b, "rev-parse", "HEAD"), an))

    # Onceden ambara alinmis PR'lar: bir ambar PR'i acilmis ve birlesmis gibi.
    if tanim["ambarda"]:
        an = simdi - timedelta(days=1, hours=sira)
        dal = f"ambar/al-{an.strftime('%Y%m%d-%H%M')}"
        _git(b, "checkout", "--quiet", "-b", dal, ANA_DAL)
        secilen = sorted((birlesenler[i] for i in tanim["ambarda"]), key=lambda x: x[2], reverse=True)
        for _numara, sha, _an in secilen:
            _git(b, "revert", "--no-edit", "-m", "1", sha, env=_kimlik("demo.kullanici", an))
        _git(b, "checkout", "--quiet", ANA_DAL)
        numara = numara_tabani + 90
        etiketler = ", ".join(f"#{n}" for n, _s, _a in sorted(secilen))
        _git(
            b, "merge", "--quiet", "--no-ff", dal,
            "-m", f"Merge pull request #{numara} from {KURULUS}/{dal}",
            "-m", f"Ambara alındı: {etiketler}",
            env=_kimlik("demo.kullanici", an + timedelta(minutes=20)),
        )
        _git(b, "branch", "-D", dal)
    _git(b, "push", "--quiet", "-u", "origin", ANA_DAL)
    _git(depo.klasor, "clone", "--quiet", str(depo.origin), str(depo.calisma))


# --- PR'lari git'ten okumak ------------------------------------------------


def _birlesmis_prlar(depo: Depo) -> list[dict[str, Any]]:
    """origin/ana dalin birinci ebeveyn zincirindeki 'Merge pull request' commit'leri."""
    cikti = _git(
        depo.origin, "log", "--first-parent", ANA_DAL,
        "--format=%H%x1f%an%x1f%cI%x1f%s%x1f%b%x1e",
    )
    prlar: list[dict[str, Any]] = []
    for kayit in cikti.split("\x1e"):
        parcalar = kayit.strip("\n").split("\x1f")
        if len(parcalar) < 5:
            continue
        sha, yazar, an, konu, govde = parcalar[:5]
        sha = sha.strip()
        if not konu.startswith("Merge pull request #"):
            continue
        numara = int(konu.split("#", 1)[1].split()[0])
        dal = konu.split(" from ", 1)[1].split("/", 1)[-1] if " from " in konu else ""
        baslik = govde.strip().splitlines()[0] if govde.strip() else konu
        damga = datetime.fromisoformat(an).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        prlar.append(_pr(depo, numara, baslik, yazar, "closed", damga, sha, dal))
    return prlar


def _pr(
    depo: Depo, numara: int, baslik: str, yazar: str, durum: str, an: str, sha: str, dal: str,
) -> dict[str, Any]:
    return {
        "number": numara,
        "title": baslik,
        "user": {"login": yazar},
        "state": durum,
        "merged_at": an if durum == "closed" else None,
        "updated_at": an,
        "merge_commit_sha": sha or None,
        "html_url": f"{WEB}/{depo.tam_ad}/pull/{numara}",
        "head": {"ref": dal},
        "base": {"ref": ANA_DAL},
    }


class SahteGitHub:
    """Durum: depolar (git) + acik PR'lar (bellekte)."""

    def __init__(self, depolar: list[Depo], otomatik_birlestir: bool = True) -> None:
        self.depolar = {depo.tam_ad: depo for depo in depolar}
        self.acik: dict[str, list[dict[str, Any]]] = {ad: [] for ad in self.depolar}
        self.otomatik_birlestir = otomatik_birlestir
        self.kilit = threading.Lock()

    def yonlendir(self, yontem: str, yol: str, sorgu: dict[str, list[str]], kimlik: str,
                  govde: Any) -> tuple[int, Any]:
        if kimlik not in (f"token {SAHTE_TOKEN}", f"Bearer {SAHTE_TOKEN}"):
            return 401, {"message": "Bad credentials"}
        parcalar = [p for p in yol.split("/") if p]
        if len(parcalar) < 3 or parcalar[0] != "repos":
            return 404, {"message": "Not Found"}
        depo = self.depolar.get(f"{parcalar[1]}/{parcalar[2]}")
        if depo is None:
            return 404, {"message": "Not Found"}
        kalan = parcalar[3:]
        if not kalan and yontem == "GET":
            return 200, {"full_name": depo.tam_ad, "default_branch": ANA_DAL,
                         "permissions": {"push": True}}
        if kalan == ["pulls"] and yontem == "GET":
            durum = (sorgu.get("state") or ["open"])[0]
            ogeler = _birlesmis_prlar(depo) if durum == "closed" else list(self.acik[depo.tam_ad])
            ogeler.sort(key=lambda pr: pr["updated_at"], reverse=True)
            sayfa_boyu = int((sorgu.get("per_page") or ["30"])[0])
            sayfa = int((sorgu.get("page") or ["1"])[0])
            return 200, ogeler[(sayfa - 1) * sayfa_boyu: sayfa * sayfa_boyu]
        if len(kalan) == 2 and kalan[0] == "pulls" and yontem == "GET":
            for pr in self.acik[depo.tam_ad] + _birlesmis_prlar(depo):
                if str(pr["number"]) == kalan[1]:
                    return 200, pr
            return 404, {"message": "Not Found"}
        if kalan == ["pulls"] and yontem == "POST":
            return self._pr_ac(depo, govde or {})
        return 404, {"message": "Not Found"}

    def _pr_ac(self, depo: Depo, govde: dict[str, Any]) -> tuple[int, Any]:
        dal = str(govde.get("head") or "")
        try:
            _git(depo.origin, "rev-parse", "--verify", f"refs/heads/{dal}")
        except RuntimeError:
            return 422, {"message": "Validation Failed", "errors": [{"field": "head", "code": "invalid"}]}
        with self.kilit:
            mevcut = [pr["number"] for pr in _birlesmis_prlar(depo) + self.acik[depo.tam_ad]]
            numara = max(mevcut + [0]) + 1
            an = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            pr = _pr(depo, numara, str(govde.get("title") or ""), "demo.kullanici", "open", an, "", dal)
            if self.otomatik_birlestir:
                # Demo: ekip PR'i hemen onaylayip "Create a merge commit" ile birlestirmis gibi.
                self._birlestir(depo, numara, dal, pr["title"])
            else:
                self.acik[depo.tam_ad].append(pr)
        return 201, pr

    def _birlestir(self, depo: Depo, numara: int, dal: str, baslik: str) -> None:
        b = depo.birlestirici
        with depo.kilit:
            _git(b, "fetch", "--quiet", "origin")
            _git(b, "checkout", "--quiet", ANA_DAL)
            _git(b, "reset", "--quiet", "--hard", f"origin/{ANA_DAL}")
            _git(
                b, "merge", "--quiet", "--no-ff", f"origin/{dal}",
                "-m", f"Merge pull request #{numara} from {KURULUS}/{dal}", "-m", baslik,
                env=_kimlik("demo.onaylayan", datetime.now(timezone.utc)),
            )
            _git(b, "push", "--quiet", "origin", ANA_DAL)


def sunucu_kur(durum: SahteGitHub, port: int = 8091, host: str = "127.0.0.1") -> ThreadingHTTPServer:
    class Isleyici(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            return

        def _isle(self, yontem: str) -> None:
            ayrik = urlparse(self.path)
            uzunluk = int(self.headers.get("Content-Length") or 0)
            ham = self.rfile.read(uzunluk) if uzunluk else b""
            try:
                govde = json.loads(ham) if ham else None
                kod, yuk = durum.yonlendir(
                    yontem, ayrik.path, parse_qs(ayrik.query),
                    self.headers.get("Authorization", ""), govde,
                )
            except Exception as hata:  # noqa: BLE001 - demo sunucusu dusmesin
                kod, yuk = 500, {"message": str(hata)}
            veri = json.dumps(yuk, ensure_ascii=False).encode("utf-8")
            self.send_response(kod)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(veri)))
            self.end_headers()
            self.wfile.write(veri)

        def do_GET(self) -> None:  # noqa: N802
            self._isle("GET")

        def do_POST(self) -> None:  # noqa: N802
            self._isle("POST")

    return ThreadingHTTPServer((host, port), Isleyici)


def baslat(durum: SahteGitHub, port: int = 8091, host: str = "127.0.0.1"):
    sunucu = sunucu_kur(durum, port=port, host=host)
    is_parcacigi = threading.Thread(target=sunucu.serve_forever, name="sahte-github", daemon=True)
    is_parcacigi.start()
    return sunucu, is_parcacigi


def ayarlari_yaz(context: Any, depolar: list[Depo], port: int) -> None:
    """Ambar ayarlarini sahte GitHub'a ve demo klonlarina yonlendirir."""
    from app import ambar

    mevcut = {repo["name"]: repo["id"] for repo in ambar.repos(context.settings)}
    ambar.save_config(
        context.settings,
        {
            "api_url": f"http://127.0.0.1:{port}",
            "token": SAHTE_TOKEN,
            "git_path": "",
            "proxy": "",
            "repos": [
                {"id": mevcut.get(depo.tam_ad, ""), "name": depo.tam_ad, "path": str(depo.calisma)}
                for depo in depolar
            ],
        },
    )
