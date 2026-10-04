"""Ambar: yerel klondaki git isleri (alt surec, kabuk yok).

Holocron burada YALNIZCA su isleri yapar: `fetch`, `origin/<ana dal>`dan yeni
bir `ambar/...` dali acmak, secilen commit'leri `git revert` ile geri almak,
o dali `origin`e itmek ve kullanicinin klonunu aldigi hale geri dondurmek.
Ana dala (master/main) hicbir zaman yazilmaz, zorla itme (`--force`) yoktur,
birlestirme yapilmaz.

Komutlar her zaman arguman listesiyle calisir (`shell=False`); Windows'ta
konsol penceresi acilmasin diye `CREATE_NO_WINDOW` bayragi verilir (bkz.
`copilot.sessiz_calistir_ayarlari`). Kimlik istemi asili kalmasin diye
`GIT_TERMINAL_PROMPT=0` ayarlanir.

"Ambarda mi" bilgisi AYRI bir durumda tutulmaz, ana dalin kendisinden
turetilir: bir commit'in ana dalda `git revert` izi ("This reverts commit
<sha>") varsa ve o geri alma da geri alinmamissa, commit ambardadir.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

from .copilot import sessiz_calistir_ayarlari

log = logging.getLogger("holocron.ambar")

# Yerel komutlar kisa surer; ag isteyenler (fetch/push) vekil arkasinda uzar.
LOCAL_TIMEOUT = 120
NETWORK_TIMEOUT = 300

BRANCH_PREFIX = "ambar/"
# Ambarin actigi dallar bu iki on ekle baslar; baska dal adi itilmez.
HOLD_PREFIX = "ambar/al-"
RELEASE_PREFIX = "ambar/cikar-"

# git revert'in kendi yazdigi iz. Birlestirme commit'inde ", reversing changes
# made to <sha>." diye devam eder; ilk sha geri alinan commit'tir.
REVERT_RE = re.compile(r"This reverts commit ([0-9a-fA-F]{7,40})")

# GitHub'in mesaj kaliplari: "Merge pull request #142 from org/dal" ve
# squash'ta "Baslik (#142)".
MERGE_PR_RE = re.compile(r"^Merge pull request #(\d+) from \S+")
SQUASH_PR_RE = re.compile(r"\(#(\d+)\)\s*$")

_FIELD = "\x1f"
_RECORD = "\x1e"
LOG_FORMAT = "%H%x1f%P%x1f%an%x1f%cI%x1f%s%x1f%b%x1e"

GIT_CANDIDATES_WINDOWS: tuple[str, ...] = (
    r"%ProgramFiles%\Git\cmd\git.exe",
    r"%ProgramFiles(x86)%\Git\cmd\git.exe",
    r"%LOCALAPPDATA%\Programs\Git\cmd\git.exe",
    r"%ProgramW6432%\Git\cmd\git.exe",
)


class GitError(Exception):
    """git komutu basarisiz oldu. Mesaj Turkce, ayrinti stderr'den."""

    def __init__(self, code: str, message: str, detail: str = "") -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


@dataclass
class Conflict(Exception):
    """Geri alma cakisti; islem temizce geri sarildi."""

    sha: str
    subject: str
    files: list[str] = field(default_factory=list)
    detail: str = ""

    def __str__(self) -> str:  # pragma: no cover - yalnizca log icin
        return f"conflict at {self.sha[:10]}: {', '.join(self.files)}"


@dataclass
class Commit:
    sha: str
    parents: list[str]
    author: str
    date: str
    subject: str
    body: str = ""

    @property
    def is_merge(self) -> bool:
        return len(self.parents) > 1

    def message(self) -> str:
        return f"{self.subject}\n{self.body}"


# --- git'i bulmak --------------------------------------------------------


def find_git(configured: str = "", which: Any = shutil.which) -> str:
    """git.exe'nin yolu. Ayar doluysa o, degilse PATH, sonra bilinen klasorler.

    `holocron.bat` uygulamayi pythonw ile actigi icin surec terminaldeki PATH'i
    gormeyebilir (Copilot'ta yasandi); Windows'ta Git for Windows'un olagan
    klasorleri de denenir.
    """
    text = (configured or "").strip().strip('"')
    if text:
        path = Path(os.path.expandvars(os.path.expanduser(text)))
        if path.is_file():
            return str(path)
        raise GitError(
            "git_not_found",
            "Ayarlar'daki git yolu bulunamadı. Komut istemine `where git` yazıp çıkan "
            "tam yolu (git.exe) Ayarlar → Ambar → Git yolu alanına yazın.",
        )
    found = which("git")
    if found:
        return found
    if sys.platform.startswith("win"):
        for candidate in GIT_CANDIDATES_WINDOWS:
            path = Path(os.path.expandvars(candidate))
            if "%" not in str(path) and path.is_file():
                return str(path)
    raise GitError(
        "git_not_found",
        "git bulunamadı. Git for Windows kurulu değilse kurun; kuruluysa Ayarlar → Ambar → "
        "Git yolu alanına `where git` çıktısını yazın.",
    )


def git_env(proxy: str = "", base: dict[str, str] | None = None) -> dict[str, str]:
    """Alt surecin ortami: istem yok, Ingilizce cikti, istenirse vekil."""
    env = dict(os.environ if base is None else base)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["LC_ALL"] = "C"
    env["LANG"] = "C"
    # Bazi kurulumlar editor acmaya calisir; --no-edit zaten var, yine de kapat.
    env["GIT_EDITOR"] = "true"
    if proxy:
        env["HTTPS_PROXY"] = proxy
        env["HTTP_PROXY"] = proxy
        env["https_proxy"] = proxy
        env["http_proxy"] = proxy
    return env


# --- calistirici -----------------------------------------------------------


class Git:
    """Tek bir klonda git komutlari. Her cagri bir alt surec, kabuk yok."""

    def __init__(self, exe: str, path: str, env: dict[str, str] | None = None) -> None:
        self.exe = exe
        self.path = path
        self.env = env if env is not None else git_env()

    def run(
        self,
        *args: str,
        check: bool = True,
        timeout: float = LOCAL_TIMEOUT,
    ) -> subprocess.CompletedProcess[str]:
        command = [self.exe, "-C", self.path, "-c", "core.quotepath=off", *args]
        try:
            result = subprocess.run(  # noqa: S603 - arguman listesi, kabuk yok
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=self.env,
                timeout=timeout,
                **sessiz_calistir_ayarlari(),
            )
        except subprocess.TimeoutExpired as exc:
            raise GitError(
                "git_timeout",
                f"git {args[0] if args else ''} {int(timeout)} saniyede bitmedi. Ağ ya da "
                "vekil sunucu yavaş olabilir; birazdan yeniden deneyin.",
            ) from exc
        except OSError as exc:
            raise GitError("git_not_found", f"git çalıştırılamadı: {exc}") from exc
        if check and result.returncode != 0:
            raise GitError(
                "git_failed",
                f"git {args[0] if args else ''} başarısız oldu.",
                (result.stderr or result.stdout or "").strip()[-2000:],
            )
        return result

    def out(self, *args: str, timeout: float = LOCAL_TIMEOUT) -> str:
        return self.run(*args, timeout=timeout).stdout.strip()

    def ok(self, *args: str) -> bool:
        return self.run(*args, check=False).returncode == 0


# --- dogrulama ------------------------------------------------------------


def check_work_tree(git: Git) -> str:
    """Klasor bir git calisma agaci mi; ust klasoru dondurur."""
    path = Path(git.path)
    if not path.is_dir():
        raise GitError("path_missing", "Klon klasörü bulunamadı.")
    result = git.run("rev-parse", "--is-inside-work-tree", "--show-toplevel", check=False)
    lines = (result.stdout or "").strip().splitlines()
    if result.returncode != 0 or not lines or lines[0].strip() != "true":
        raise GitError("not_a_repo", "Klasör bir git klonu değil.")
    remote = git.run("remote", "get-url", "origin", check=False)
    if remote.returncode != 0 or not remote.stdout.strip():
        raise GitError("no_origin", "Klonda `origin` uzak deposu tanımlı değil.")
    return lines[-1].strip() if len(lines) > 1 else str(path)


def busy_state(git: Git) -> str:
    """Yarim kalmis bir revert/merge/rebase/cherry-pick varsa adini dondurur."""
    for name in ("REVERT_HEAD", "MERGE_HEAD", "CHERRY_PICK_HEAD", "rebase-merge", "rebase-apply"):
        resolved = git.out("rev-parse", "--git-path", name)
        target = Path(resolved)
        if not target.is_absolute():
            target = Path(git.path) / target
        if target.exists():
            return name
    return ""


def dirty_files(git: Git) -> list[str]:
    """Izlenen dosyalardaki kaydedilmemis degisiklikler (izlenmeyenler sayilmaz)."""
    text = git.run("status", "--porcelain", "--untracked-files=no").stdout
    return [line[3:] for line in text.splitlines() if line.strip()]


def ensure_clean(git: Git) -> None:
    busy = busy_state(git)
    if busy:
        raise GitError(
            "repo_busy",
            "Klonda yarım kalmış bir git işlemi var "
            f"({busy}). Önce onu tamamlayın ya da iptal edin.",
        )
    dirty = dirty_files(git)
    if dirty:
        listed = ", ".join(dirty[:5]) + (" ..." if len(dirty) > 5 else "")
        raise GitError(
            "dirty_tree",
            "Klonda kaydedilmemiş değişiklik var; Holocron başlamadı. Değişiklikleri commit "
            f"edin ya da stash'leyin: {listed}",
        )


# --- dallar ---------------------------------------------------------------


def current_position(git: Git) -> tuple[str, str]:
    """('branch', ad) ya da ('detached', sha)."""
    result = git.run("symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    name = result.stdout.strip()
    if result.returncode == 0 and name:
        return "branch", name
    return "detached", git.out("rev-parse", "HEAD")


def default_branch(git: Git) -> str:
    """origin'in ana dali: once origin/HEAD, sonra master, sonra main."""
    result = git.run("symbolic-ref", "--quiet", "refs/remotes/origin/HEAD", check=False)
    name = result.stdout.strip()
    if result.returncode == 0 and name.startswith("refs/remotes/origin/"):
        short = name[len("refs/remotes/origin/"):]
        if short and git.ok("rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{short}"):
            return short
    for candidate in ("master", "main"):
        if git.ok("rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{candidate}"):
            return candidate
    raise GitError(
        "no_default_branch",
        "origin/master ya da origin/main bulunamadı. Klonda bir kez `git fetch` çalıştırın.",
    )


def fetch(git: Git) -> None:
    git.run("fetch", "--quiet", "origin", timeout=NETWORK_TIMEOUT)


def unique_branch(git: Git, base_name: str) -> str:
    """Yerelde ve origin'de olmayan bir dal adi (gerekirse -2, -3 eklenir)."""
    for index in range(1, 50):
        name = base_name if index == 1 else f"{base_name}-{index}"
        if not name.startswith(BRANCH_PREFIX):
            raise GitError("bad_branch", "Ambar dalı 'ambar/' ile başlamalı.")
        if not git.ok("check-ref-format", "--branch", name):
            raise GitError("bad_branch", "Dal adı geçersiz.")
        local = git.ok("rev-parse", "--verify", "--quiet", f"refs/heads/{name}")
        remote = git.ok("rev-parse", "--verify", "--quiet", f"refs/remotes/origin/{name}")
        if not local and not remote:
            return name
    raise GitError("bad_branch", "Boş bir ambar dalı adı bulunamadı.")


# --- gecmisi okumak -----------------------------------------------------


def parse_log(text: str) -> list[Commit]:
    commits: list[Commit] = []
    for record in text.split(_RECORD):
        record = record.strip("\n")
        if not record.strip():
            continue
        parts = record.split(_FIELD)
        if len(parts) < 6:
            continue
        sha, parents, author, date, subject, body = parts[:6]
        commits.append(
            Commit(
                sha=sha.strip(),
                parents=parents.split(),
                author=author,
                date=date,
                subject=subject,
                body=body.strip(),
            )
        )
    return commits


def show_commits(
    git: Git, shas: Sequence[str], ignore_missing: bool = False
) -> list[Commit]:
    """Verilen commit'ler, topolojik sirayla (yeniden eskiye)."""
    if not shas:
        return []
    extra = ["--ignore-missing"] if ignore_missing else []
    # `out()` degil: str.strip() \x1f/\x1e ayiricilarini da bosluk sayip siler.
    text = git.run(
        "log", "--no-walk=sorted", *extra, f"--format={LOG_FORMAT}", *shas
    ).stdout
    commits = parse_log(text)
    return order_newest_first(git, commits)


def revert_commits(git: Git, ref: str) -> list[Commit]:
    """Ana dalda "This reverts commit" izi tasiyan butun commit'ler (yeniden eskiye)."""
    text = git.run("log", ref, "--grep=This reverts commit", f"--format={LOG_FORMAT}").stdout
    return parse_log(text)


def order_newest_first(git: Git, commits: list[Commit]) -> list[Commit]:
    """Topolojik sira, yeniden eskiye: bir commit hicbir atasindan sonra gelmez.

    Tarih sirasi yetmez: ayni saniyede birlesen iki PR'da `--no-walk=sorted`
    sirayi rastgele verebilir, oysa geri almada torun atadan once gelmeli.
    """
    if len(commits) < 2:
        return commits
    oldest = min(commit.date for commit in commits)
    since = _day_before(oldest)
    args = ["rev-list", "--topo-order"]
    if since:
        args.append(f"--since={since}")
    text = git.run(*args, *[commit.sha for commit in commits], check=False).stdout
    position = {line.strip(): index for index, line in enumerate(text.splitlines()) if line.strip()}
    fallback = len(position)
    indexed = list(enumerate(commits))
    indexed.sort(key=lambda pair: (position.get(pair[1].sha, fallback), pair[0]))
    return [commit for _index, commit in indexed]


def _day_before(iso: str) -> str:
    from datetime import datetime, timedelta

    try:
        moment = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return (moment - timedelta(days=1)).isoformat()


def commits_since(git: Git, ref: str, since_iso: str) -> dict[str, int]:
    """Ana daldaki commit'ler -> sira (0 = en yeni)."""
    text = git.out("rev-list", ref, f"--since={since_iso}")
    shas = [line.strip() for line in text.splitlines() if line.strip()]
    return {sha: index for index, sha in enumerate(shas)}


def changed_files(git: Git, shas: Sequence[str]) -> dict[str, list[str]]:
    """Her commit'in ilk ebeveynine gore degistirdigi dosyalar (tek alt surec)."""
    result: dict[str, list[str]] = {sha: [] for sha in shas}
    if not shas:
        return result
    marker = "@@AMBAR@@"
    proc = git.run(
        "log", "--no-walk=unsorted", "--diff-merges=first-parent", "--name-only",
        f"--format={marker}%H", *shas, check=False,
    )
    if proc.returncode != 0:
        # Eski git (--diff-merges yok): commit basina tek tek.
        for sha in shas:
            files = git.run("diff", "--name-only", f"{sha}^1", sha, check=False).stdout
            result[sha] = [line for line in files.splitlines() if line.strip()]
        return result
    current = ""
    for line in proc.stdout.splitlines():
        if line.startswith(marker):
            current = line[len(marker):].strip()
            result.setdefault(current, [])
        elif line.strip() and current:
            result[current].append(line.strip())
    return result


def pr_number_of(commit: Commit) -> int | None:
    match = MERGE_PR_RE.match(commit.subject)
    if match:
        return int(match.group(1))
    match = SQUASH_PR_RE.search(commit.subject)
    if match:
        return int(match.group(1))
    return None


def pr_title_of(commit: Commit) -> str:
    """Birlestirme commit'inde PR basligi govdenin ilk satiridir."""
    if MERGE_PR_RE.match(commit.subject):
        first = commit.body.strip().splitlines()[0] if commit.body.strip() else ""
        return first or commit.subject
    return SQUASH_PR_RE.sub("", commit.subject).strip() or commit.subject


# --- ambardakiler ---------------------------------------------------------


@dataclass
class Held:
    """Ambardaki bir commit ve onu ambarda tutan (geri alinmamis) revert."""

    commit: Commit
    revert: Commit

    @property
    def sha(self) -> str:
        return self.commit.sha


def held_commits(git: Git, ref: str) -> list[Held]:
    """Ana daldan turetilen ambar: geri alinmis ve geri alinmasi geri alinmamis commit'ler.

    Zincir: M'yi R1 geri alir (M ambarda); R1'i R2 geri alir (M cikti); M'yi
    yeniden R3 geri alir (M yine ambarda). Kural tek: bir commit'in, kendisi
    geri alinmamis bir revert'i varsa o commit "geri alinmis"tir.
    """
    reverts = revert_commits(git, ref)
    by_target: dict[str, list[Commit]] = {}
    for commit in reverts:
        for target in REVERT_RE.findall(commit.message()):
            bucket = by_target.setdefault(target.lower(), [])
            if commit not in bucket:
                bucket.append(commit)
    short_keys = [key for key in by_target if len(key) < 40]
    revert_shas = {commit.sha for commit in reverts}

    def reverts_of(sha: str) -> list[Commit]:
        found = list(by_target.get(sha.lower(), []))
        for key in short_keys:
            if sha.lower().startswith(key):
                found.extend(item for item in by_target[key] if item not in found)
        return found

    memo: dict[str, Commit | None] = {}

    def active(sha: str, depth: int = 0) -> Commit | None:
        if sha in memo:
            return memo[sha]
        memo[sha] = None  # dongu korumasi
        result: Commit | None = None
        if depth < 64:
            for revert in reverts_of(sha):  # yeniden eskiye
                if active(revert.sha, depth + 1) is None:
                    result = revert
                    break
        memo[sha] = result
        return result

    # Hedefler kisa sha olabilir; tam sha'ya cevirmek icin git'e sorulur.
    targets: dict[str, Commit] = {}
    full_targets: list[str] = []
    for key in by_target:
        if len(key) == 40:
            full_targets.append(key)
        else:
            resolved = git.run("rev-parse", "--verify", "--quiet", f"{key}^{{commit}}", check=False)
            if resolved.returncode == 0 and resolved.stdout.strip():
                full_targets.append(resolved.stdout.strip())
    existing = [sha for sha in dict.fromkeys(full_targets) if sha not in revert_shas]
    # Klonda olmayan hedef (ornegin baska bir dalin commit'i) sessizce atlanir.
    for commit in show_commits(git, existing, ignore_missing=True):
        targets[commit.sha] = commit

    held: list[Held] = []
    for sha, commit in targets.items():
        revert = active(sha)
        if revert is not None:
            held.append(Held(commit=commit, revert=revert))
    # Ambara en son alinan basta.
    order = {commit.sha: index for index, commit in enumerate(reverts)}
    held.sort(key=lambda item: order.get(item.revert.sha, 0))
    return held


# --- islem: dal ac, geri al, it, eski hale don ------------------------------


@dataclass
class Prepared:
    branch: str
    base: str
    commits: list[Commit]


def revert_onto_branch(
    git: Git,
    base: str,
    branch_base_name: str,
    shas: Sequence[str],
) -> Prepared:
    """origin/<base>'den dal acar, commit'leri yeniden eskiye geri alir, iter.

    Her durumda klon basladigi hale doner: ozgun dal geri gelir, gecici yerel
    dal silinir. Cakismada revert iptal edilir ve `Conflict` firlatilir; o an
    hicbir sey itilmemistir.
    """
    ensure_clean(git)
    kind, original = current_position(git)
    commits = show_commits(git, list(dict.fromkeys(shas)))  # yeniden eskiye
    if len(commits) != len(set(shas)):
        raise GitError("commit_missing", "Seçilen commit'lerden biri klonda bulunamadı.")
    branch = unique_branch(git, branch_base_name)
    if not branch.startswith(BRANCH_PREFIX) or branch in (base, f"origin/{base}"):
        raise GitError("bad_branch", "Ambar dalı ana dal olamaz.")

    created = False
    try:
        git.run("checkout", "--quiet", "--no-track", "-b", branch, f"refs/remotes/origin/{base}")
        created = True
        for commit in commits:
            args = ["revert", "--no-edit"]
            if commit.is_merge:
                args += ["-m", "1"]
            result = git.run(*args, commit.sha, check=False)
            if result.returncode != 0:
                files = [
                    line.strip()
                    for line in git.run(
                        "diff", "--name-only", "--diff-filter=U", check=False
                    ).stdout.splitlines()
                    if line.strip()
                ]
                git.run("revert", "--abort", check=False)
                detail = (result.stderr or result.stdout or "").strip()[-1500:]
                raise Conflict(sha=commit.sha, subject=commit.subject, files=files, detail=detail)
        # Yalnizca bu dal, acik refspec ile, zorlamasiz itilir.
        git.run(
            "push", "--porcelain", "origin", f"refs/heads/{branch}:refs/heads/{branch}",
            timeout=NETWORK_TIMEOUT,
        )
    finally:
        _restore(git, kind, original, branch if created else "")
    return Prepared(branch=branch, base=base, commits=commits)


def _restore(git: Git, kind: str, original: str, branch: str) -> None:
    """Klonu islemden onceki haline getirir; hata yutulur ama loglanir."""
    try:
        if busy_state(git) == "REVERT_HEAD":
            git.run("revert", "--abort", check=False)
        target = ["checkout", "--quiet", original] if kind == "branch" else [
            "checkout", "--quiet", "--detach", original
        ]
        if git.run(*target, check=False).returncode != 0:
            # Gecici dalda yalnizca bizim yazdigimiz degisiklik olabilir.
            git.run("checkout", "--quiet", "--force", *target[2:], check=False)
        if branch:
            git.run("branch", "-D", branch, check=False)
    except GitError:  # pragma: no cover - son care
        log.warning("Ambar: klon eski haline döndürülemedi", exc_info=True)


def is_on(git: Git, sha: str, ref: str) -> bool:
    return git.ok("merge-base", "--is-ancestor", sha, ref)


def distinct(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))
