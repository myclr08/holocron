"""Ambar: birlesmis PR'lari ana daldan PR yoluyla geri almak ve geri getirmek.

Sorun: ana dal salt okunur (degisiklik yalnizca PR ile), ve butun ortamlar
(UAT, prod) ana daldan derlenir. UAT'ta testi suren bir degisiklik prod
surumune sizmasin diye surumden once bazi PR'lar geri alinir ("ambara al"),
surumden sonra geri getirilir ("ambardan cikar"). Ikisi de bir PR acarak olur.

Ilkeler:

* Holocron ana dala yazmaz, PR birlestirmez, zorla itmez; yalnizca `ambar/...`
  dali acar ve PR acar.
* Jira anahtari kullanilmaz: eslesme commit/PR uzerindendir.
* Ambar durumu ayri tutulmaz, ana daldan turetilir (`ambar_git.held_commits`).
* Surum takibi yok: hatirlatma, tarih, etiket yok. Dugmeye kullanici basar.

Bu modul ayarlari, depo dogrulamasini ve islemlerin depo depo yurutulmesini
toplar; git isleri `ambar_git`, GitHub istekleri `ambar_github` icindedir.
"""

from __future__ import annotations

import json
import logging
import os
import re
import secrets as pysecrets
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from . import ambar_git as agit
from . import net
from .ambar_github import DEFAULT_API_URL, GitHub, GitHubError, host_of, normalize_api_url
from .repository import RepositoryError
from .settings_store import PROXY_DIRECT

log = logging.getLogger("holocron.ambar")

SETTING_API_URL = "ambar.api_url"
SETTING_REPOS = "ambar.repolar"
SETTING_TOKEN = "ambar.token"
SETTING_GIT = "ambar.git_yolu"
SETTING_PROXY = "ambar.proxy"

OP_HOLD = "al"
OP_RELEASE = "cikar"

DEFAULT_DAYS = 30
MAX_DAYS = 365
MAX_REPOS = 30
MAX_ITEMS = 200
NOTE_LIMIT = 500

CONFLICT_TITLE = "Rota hesaplama hatası"
ERROR_TITLE = "Ambar işlemi yapılamadı"

# GitHub kurulus adi: harf/rakam ve tire; depo adi: harf/rakam, nokta, alt cizgi, tire.
REPO_NAME_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})/[A-Za-z0-9._-]{1,100}$")
REPO_ID_RE = re.compile(r"^r[0-9a-f]{8}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_CREDENTIAL_RE = re.compile(r"(://)[^/@\s]+@")

_RUN_LOCK = threading.Lock()


def token_key(repo_id: str) -> str:
    return f"{SETTING_TOKEN}.{repo_id}"


# --- dogrulama ------------------------------------------------------------


def clean_repo_name(value: Any) -> str:
    text = str(value or "").strip()
    if text.lower().endswith(".git"):
        text = text[:-4]
    if not REPO_NAME_RE.match(text) or text.split("/", 1)[1] in (".", ".."):
        raise RepositoryError(
            "invalid_repo_name", "Depo adı 'kurulus/depo' biçiminde olmalı (ör. ornek-org/api)."
        )
    return text


def clean_repo_path(value: Any) -> str:
    text = str(value or "").strip().strip('"')
    if not text:
        raise RepositoryError("invalid_repo_path", "Klon klasörü boş olamaz.")
    if _CONTROL_RE.search(text):
        raise RepositoryError("invalid_repo_path", "Klon klasörü geçersiz karakter içeriyor.")
    expanded = os.path.expandvars(os.path.expanduser(text))
    if not os.path.isabs(expanded):
        raise RepositoryError(
            "invalid_repo_path", "Klon klasörü tam yol olmalı (ör. C:\\kod\\api)."
        )
    return os.path.normpath(expanded)


def clean_api_url(value: Any) -> str:
    text = normalize_api_url(str(value or ""))
    if not text.startswith(("http://", "https://")) or _CONTROL_RE.search(text):
        raise RepositoryError("invalid_api_url", "API adresi http:// veya https:// ile başlamalı.")
    return text


def clean_days(value: Any) -> int:
    try:
        days = int(value)
    except (TypeError, ValueError):
        return DEFAULT_DAYS
    return max(1, min(days, MAX_DAYS))


def redact(text: str, tokens: Iterable[str] = ()) -> str:
    """git ciktisinda gecebilecek kimlik bilgilerini temizler."""
    cleaned = _CREDENTIAL_RE.sub(r"\1***@", str(text or ""))
    for token in tokens:
        if token:
            cleaned = cleaned.replace(token, "***")
    return cleaned


# --- ayarlar ---------------------------------------------------------------


def _stored_repos(settings: Any) -> list[dict[str, str]]:
    raw = settings.get(SETTING_REPOS, "") or ""
    try:
        data = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        return []
    repos: list[dict[str, str]] = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        repo_id = str(item.get("id") or "")
        if not REPO_ID_RE.match(repo_id):
            continue
        repos.append(
            {
                "id": repo_id,
                "name": str(item.get("name") or ""),
                "path": str(item.get("path") or ""),
            }
        )
    return repos


def repos(settings: Any) -> list[dict[str, str]]:
    return _stored_repos(settings)


def config_view(settings: Any) -> dict[str, Any]:
    """Ayarlar ekrani: tokenlar yok, yalnizca ayarli/ayarsiz bayragi."""
    return {
        "api_url": normalize_api_url(settings.get(SETTING_API_URL, "") or ""),
        "default_api_url": DEFAULT_API_URL,
        "git_path": settings.get(SETTING_GIT, "") or "",
        "proxy": settings.get(SETTING_PROXY, "") or "",
        "token_set": settings.has_secret(SETTING_TOKEN),
        "repos": [
            {**repo, "token_set": settings.has_secret(token_key(repo["id"]))}
            for repo in _stored_repos(settings)
        ],
    }


def save_config(settings: Any, payload: dict[str, Any]) -> dict[str, Any]:
    """Ayarlari dogrular ve yazar. Bos token alani mevcut tokeni bozmaz."""
    if "api_url" in payload:
        settings.set(SETTING_API_URL, clean_api_url(payload.get("api_url")))
    if "git_path" in payload:
        text = str(payload.get("git_path") or "").strip()
        if _CONTROL_RE.search(text):
            raise RepositoryError("invalid_git_path", "Git yolu geçersiz karakter içeriyor.")
        settings.set(SETTING_GIT, text)
    if "proxy" in payload:
        text = str(payload.get("proxy") or "").strip()
        if text and not re.match(r"^https?://\S+$", text):
            raise RepositoryError(
                "invalid_proxy", "Vekil sunucu http://adres:kapı biçiminde olmalı."
            )
        settings.set(SETTING_PROXY, text)
    token = payload.get("token")
    if token:
        settings.set(SETTING_TOKEN, str(token).strip())
    if payload.get("clear_token"):
        settings.delete(SETTING_TOKEN)

    if "repos" in payload:
        items = payload.get("repos")
        if not isinstance(items, list):
            raise RepositoryError("invalid_repos", "repos bir liste olmalı.")
        if len(items) > MAX_REPOS:
            raise RepositoryError("invalid_repos", f"En fazla {MAX_REPOS} depo tanımlanabilir.")
        old = {repo["id"]: repo for repo in _stored_repos(settings)}
        fresh: list[dict[str, str]] = []
        names: set[str] = set()
        for item in items:
            if not isinstance(item, dict):
                continue
            repo_id = str(item.get("id") or "")
            if not REPO_ID_RE.match(repo_id) or repo_id not in old:
                repo_id = "r" + pysecrets.token_hex(4)
            name = clean_repo_name(item.get("name"))
            if name.lower() in names:
                raise RepositoryError("duplicate_repo", f"{name} iki kez tanımlanmış.")
            names.add(name.lower())
            path = clean_repo_path(item.get("path"))
            fresh.append({"id": repo_id, "name": name, "path": path})
            repo_token = item.get("token")
            if repo_token:
                settings.set(token_key(repo_id), str(repo_token).strip())
            if item.get("clear_token"):
                settings.delete(token_key(repo_id))
        for gone in set(old) - {repo["id"] for repo in fresh}:
            settings.delete(token_key(gone))
        settings.set(SETTING_REPOS, json.dumps(fresh, ensure_ascii=False))
    return config_view(settings)


# --- calisma ortami ------------------------------------------------------


def _token_for(settings: Any, repo: dict[str, str]) -> str:
    return (settings.get(token_key(repo["id"]), "") or settings.get(SETTING_TOKEN, "") or "").strip()


def _all_tokens(settings: Any) -> list[str]:
    found = [settings.get(SETTING_TOKEN, "") or ""]
    found += [settings.get(token_key(repo["id"]), "") or "" for repo in _stored_repos(settings)]
    return [token for token in found if token]


def git_for(settings: Any, repo: dict[str, str]) -> agit.Git:
    exe = agit.find_git(settings.get(SETTING_GIT, "") or "")
    proxy = (settings.get(SETTING_PROXY, "") or "").strip()
    return agit.Git(exe, repo["path"], agit.git_env(proxy))


def github_for(settings: Any, repo: dict[str, str]) -> GitHub:
    """Depo tokeni ya da genel token ile istemci; ag ayarlari Jira ile ayni."""
    api_url = normalize_api_url(settings.get(SETTING_API_URL, "") or "")
    config = settings.jira_config()
    own_proxy = (settings.get(SETTING_PROXY, "") or "").strip()
    if own_proxy:
        proxies: dict[str, str] | None = {"http": own_proxy, "https": own_proxy}
        trust_env = True
    else:
        mapped, _source = net.effective_proxies(
            host_of(api_url),
            mode=config.proxy_mode,
            proxy_http=config.proxy_http,
            proxy_https=config.proxy_https,
            no_proxy=config.no_proxy,
        )
        proxies = mapped or None
        trust_env = config.proxy_mode != PROXY_DIRECT
    verify: bool | str = False if not config.verify_ssl else (config.ca_file or True)
    session = net.build_session(ipv4_first=config.ipv4_first, trust_env=trust_env)
    return GitHub(api_url, _token_for(settings, repo), session=session, proxies=proxies, verify=verify)


def web_url(settings: Any, full_name: str) -> str:
    """Deponun tarayici adresi: github.com ya da Enterprise kok adresi."""
    api_url = normalize_api_url(settings.get(SETTING_API_URL, "") or "")
    if api_url == DEFAULT_API_URL:
        root = "https://github.com"
    else:
        root = re.sub(r"/api/v3$", "", api_url)
    return f"{root}/{full_name}"


def _short(sha: str) -> str:
    return (sha or "")[:10]


def _error(exc: Exception, settings: Any, title: str = ERROR_TITLE) -> dict[str, Any]:
    tokens = _all_tokens(settings)
    if isinstance(exc, agit.GitError):
        return {
            "code": exc.code,
            "title": title,
            "message": redact(exc.message, tokens),
            "detail": redact(exc.detail, tokens),
        }
    if isinstance(exc, GitHubError):
        return {"code": exc.code, "title": title, "message": redact(exc.message, tokens), "detail": ""}
    if isinstance(exc, RepositoryError):
        return {"code": exc.code, "title": title, "message": exc.message, "detail": ""}
    log.exception("Ambar: beklenmeyen hata")
    return {"code": "unexpected", "title": title, "message": "Beklenmeyen bir hata oluştu.", "detail": ""}


def _held_view(settings: Any, repo: dict[str, str], item: agit.Held) -> dict[str, Any]:
    number = agit.pr_number_of(item.commit)
    return {
        "sha": item.commit.sha,
        "short": _short(item.commit.sha),
        "number": number,
        "title": agit.pr_title_of(item.commit),
        "author": item.commit.author,
        "merged_at": item.commit.date,
        "held_at": item.revert.date,
        "revert_sha": item.revert.sha,
        "url": f"{web_url(settings, repo['name'])}/pull/{number}" if number else "",
    }


def _label(number: int | None, sha: str) -> str:
    return f"#{number}" if number else _short(sha)


# --- liste ------------------------------------------------------------------


def _find_repo(settings: Any, repo_id: str) -> dict[str, str]:
    for repo in _stored_repos(settings):
        if repo["id"] == repo_id:
            return repo
    raise RepositoryError("repo_not_found", "Depo bulunamadı.", status=404)


def overview(
    settings: Any,
    days: Any = DEFAULT_DAYS,
    repo_id: str = "",
    do_fetch: bool = True,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Sayfanin verisi: depo depo son birlesen PR'lar, ambardakiler, bekleyenler."""
    window = clean_days(days)
    moment = now or datetime.now(timezone.utc)
    since = moment - timedelta(days=window)
    chosen = [_find_repo(settings, repo_id)] if repo_id else _stored_repos(settings)
    result: list[dict[str, Any]] = []
    for repo in chosen:
        result.append(_repo_overview(settings, repo, since, do_fetch))
    return {
        "days": window,
        "repos": result,
        "count": sum(len(entry["held"]) for entry in result),
    }


def _repo_overview(
    settings: Any, repo: dict[str, str], since: datetime, do_fetch: bool
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": repo["id"],
        "name": repo["name"],
        "url": web_url(settings, repo["name"]),
        "base": "",
        "prs": [],
        "held": [],
        "pending": [],
        "error": None,
        "warning": "",
    }
    try:
        git = git_for(settings, repo)
        agit.check_work_tree(git)
        if do_fetch:
            try:
                agit.fetch(git)
            except agit.GitError as exc:
                entry["warning"] = (
                    "git fetch başarısız; son çekilen hâl gösteriliyor. "
                    + redact(exc.detail or exc.message, _all_tokens(settings))[:300]
                )
        base = agit.default_branch(git)
        ref = f"refs/remotes/origin/{base}"
        entry["base"] = base
        held = agit.held_commits(git, ref)
        entry["held"] = [_held_view(settings, repo, item) for item in held]
        held_shas = {item.sha for item in held}

        github = github_for(settings, repo)
        pulls = github.merged_pulls(repo["name"], base, since)
        on_base = agit.commits_since(git, ref, (since - timedelta(days=2)).isoformat())
        pulls = [
            pr for pr in pulls
            if pr.merge_sha in on_base and not pr.head_ref.startswith(agit.BRANCH_PREFIX)
        ]
        # Ana daldaki sira: ayni saniyede birlesenler de dogru dizilir.
        pulls.sort(key=lambda pr: on_base[pr.merge_sha])
        files = agit.changed_files(git, [pr.merge_sha for pr in pulls])
        by_number = {pr.number: pr for pr in pulls}
        for view in entry["held"]:
            pr = by_number.get(view["number"]) if view["number"] else None
            if pr is not None and pr.merge_sha == view["sha"]:
                view["title"] = pr.title
                view["author"] = pr.author
                view["url"] = pr.url or view["url"]
        entry["prs"] = [
            {
                **pr.as_dict(),
                "files": files.get(pr.merge_sha, []),
                "held": pr.merge_sha in held_shas,
            }
            for pr in pulls
        ]
        try:
            entry["pending"] = github.open_ambar_pulls(repo["name"], agit.BRANCH_PREFIX)
        except GitHubError:
            entry["pending"] = []
    except (agit.GitError, GitHubError, RepositoryError) as exc:
        entry["error"] = _error(exc, settings)
    return entry


def held_count(settings: Any) -> dict[str, Any]:
    """Kenar cubugu sayisi: aga cikmadan, klondaki son origin haliyle."""
    per_repo: dict[str, int] = {}
    for repo in _stored_repos(settings):
        try:
            git = git_for(settings, repo)
            if not os.path.isdir(repo["path"]):
                continue
            base = agit.default_branch(git)
            per_repo[repo["id"]] = len(agit.held_commits(git, f"refs/remotes/origin/{base}"))
        except (agit.GitError, RepositoryError, OSError):
            continue
    return {"count": sum(per_repo.values()), "repos": per_repo}


def check_repo(settings: Any, repo_id: str) -> dict[str, Any]:
    """Ayarlar'daki "Sına": git, klasor, origin, ana dal, API erisimi."""
    repo = _find_repo(settings, repo_id)
    steps: list[dict[str, Any]] = []

    def step(label: str, ok: bool, message: str) -> None:
        steps.append({"label": label, "ok": ok, "message": message})

    try:
        git = git_for(settings, repo)
        step("git", True, git.exe)
        top = agit.check_work_tree(git)
        step("Klon", True, top)
        origin = redact(git.out("remote", "get-url", "origin"), _all_tokens(settings))
        matches = repo["name"].lower() in origin.lower().replace(".git", "")
        step(
            "origin", True,
            origin + ("" if matches else "  (uyarı: adres depo adını içermiyor)"),
        )
        base = agit.default_branch(git)
        step("Ana dal", True, base)
    except (agit.GitError, RepositoryError) as exc:
        error = _error(exc, settings)
        step("Klon", False, (error["message"] + " " + error["detail"]).strip())
        return {"ok": False, "steps": steps}
    try:
        info = github_for(settings, repo).repository(repo["name"])
        step(
            "GitHub", True,
            f"{info['full_name']} erişilebilir; varsayılan dal {info['default_branch'] or '?'}"
            + ("" if info["can_push"] else " (uyarı: token'ın yazma yetkisi görünmüyor)"),
        )
    except GitHubError as exc:
        step("GitHub", False, exc.message)
        return {"ok": False, "steps": steps}
    return {"ok": True, "steps": steps}


# --- islemler ---------------------------------------------------------------


def _group(settings: Any, items: Any, key: str) -> list[tuple[dict[str, str], list[dict[str, Any]]]]:
    if not isinstance(items, list) or not items:
        raise RepositoryError("no_selection", "Hiçbir şey seçilmedi.")
    if len(items) > MAX_ITEMS:
        raise RepositoryError("too_many", f"Bir seferde en fazla {MAX_ITEMS} seçim yapılabilir.")
    known = {repo["id"]: repo for repo in _stored_repos(settings)}
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        repo_id = str(item.get("repo_id") or "")
        if repo_id not in known:
            raise RepositoryError("repo_not_found", "Seçimdeki depo artık tanımlı değil.", 404)
        value = item.get(key)
        if key == "number":
            try:
                value = int(value)
            except (TypeError, ValueError) as exc:
                raise RepositoryError("invalid_number", "PR numarası sayı olmalı.") from exc
            if value <= 0:
                raise RepositoryError("invalid_number", "PR numarası sayı olmalı.")
        else:
            value = str(value or "").strip().lower()
            if not re.fullmatch(r"[0-9a-f]{40}", value):
                raise RepositoryError("invalid_sha", "Commit kimliği geçersiz.")
        note = _CONTROL_RE.sub(" ", str(item.get("note") or "")).strip()[:NOTE_LIMIT]
        bucket = grouped.setdefault(repo_id, [])
        if all(existing[key] != value for existing in bucket):
            bucket.append({key: value, "note": note})
    order = [repo["id"] for repo in _stored_repos(settings)]
    return [(known[repo_id], grouped[repo_id]) for repo_id in order if repo_id in grouped]


def _stamp(now: datetime | None) -> str:
    moment = now.astimezone() if now and now.tzinfo else (now or datetime.now())
    return moment.strftime("%Y%m%d-%H%M")


def run(context: Any, op: str, items: Any, now: datetime | None = None) -> dict[str, Any]:
    """Secilenleri depo depo isler; her depo kendi PR'ini ya da hatasini alir."""
    if op not in (OP_HOLD, OP_RELEASE):
        raise RepositoryError("invalid_op", "Bilinmeyen ambar işlemi.")
    settings = context.settings
    groups = _group(settings, items, "number" if op == OP_HOLD else "sha")
    if not _RUN_LOCK.acquire(blocking=False):
        raise RepositoryError("ambar_busy", "Başka bir ambar işlemi sürüyor; bitmesini bekleyin.", 409)
    try:
        stamp = _stamp(now)
        # Calisma kimligi: ayni dakikada basilan iki dugme ayri islemdir.
        run_id = f"{stamp}-{pysecrets.token_hex(3)}"
        results = [
            (_hold_repo if op == OP_HOLD else _release_repo)(settings, repo, chosen, stamp)
            for repo, chosen in groups
        ]
    finally:
        _RUN_LOCK.release()

    from . import gamify  # dongusel ice aktarmayi onlemek icin burada

    reward: dict[str, Any] = {"points": 0, "events": [], "badges": []}
    try:
        with context.db_lock:
            reward = gamify.on_ambar(context, op, results, run_id, now)
    except Exception:  # pragma: no cover - puan ikincil, islem birincil
        log.warning("Ambar: Sefer puanı yazılamadı", exc_info=True)
    return {
        "op": op,
        "results": results,
        "ok": all(item["ok"] for item in results),
        "reward": reward,
    }


def _result(repo: dict[str, str]) -> dict[str, Any]:
    return {
        "repo_id": repo["id"],
        "name": repo["name"],
        "ok": False,
        "items": [],
        "pr_number": 0,
        "pr_url": "",
        "branch": "",
        "error": None,
    }


def _hold_repo(
    settings: Any, repo: dict[str, str], chosen: list[dict[str, Any]], stamp: str
) -> dict[str, Any]:
    result = _result(repo)
    result["items"] = [{"number": item["number"], "label": f"#{item['number']}"} for item in chosen]
    notes = {item["number"]: item["note"] for item in chosen}
    pushed = ""
    try:
        github = github_for(settings, repo)
        git = git_for(settings, repo)
        agit.check_work_tree(git)
        agit.ensure_clean(git)
        agit.fetch(git)
        base = agit.default_branch(git)
        ref = f"refs/remotes/origin/{base}"
        held = {item.sha for item in agit.held_commits(git, ref)}
        pulls = []
        for item in chosen:
            pr = github.pull(repo["name"], item["number"])
            if not pr.merged_at or not pr.merge_sha:
                raise RepositoryError("not_merged", f"#{item['number']} birleşmemiş bir PR.")
            if pr.base_ref and pr.base_ref != base:
                raise RepositoryError(
                    "wrong_base", f"#{item['number']} {base} dalına değil {pr.base_ref} dalına birleşmiş."
                )
            if not agit.is_on(git, pr.merge_sha, ref):
                raise RepositoryError(
                    "not_on_base", f"#{item['number']} birleşme commit'i origin/{base} üzerinde değil."
                )
            if pr.merge_sha in held:
                raise RepositoryError("already_held", f"#{item['number']} zaten ambarda.")
            pulls.append(pr)
        by_sha = {pr.merge_sha: pr for pr in pulls}
        try:
            prepared = agit.revert_onto_branch(
                git, base, agit.HOLD_PREFIX + stamp, [pr.merge_sha for pr in pulls]
            )
        except agit.Conflict as conflict:
            pr = by_sha.get(conflict.sha)
            result["error"] = {
                "code": "conflict",
                "title": CONFLICT_TITLE,
                "message": (
                    f"{_label(pr.number if pr else None, conflict.sha)} geri alınırken çakışma "
                    "oldu; işlem geri sarıldı, klon olduğu gibi bırakıldı. Bu PR'ı seçimden "
                    "çıkarıp yeniden deneyebilirsiniz."
                ),
                "detail": redact(conflict.detail, _all_tokens(settings)),
                "files": conflict.files,
                "pr": {"number": pr.number if pr else None, "title": pr.title if pr else conflict.subject},
            }
            return result
        pushed = prepared.branch
        ordered = [by_sha[commit.sha] for commit in prepared.commits]
        numbers = sorted(pr.number for pr in ordered)
        lines = [
            "Bu PR **Holocron Ambar** ile hazırlandı. Aşağıdaki PR'ların değişiklikleri "
            "`git revert` ile geri alınır (yeniden eskiye). Ana dala doğrudan yazılmadı; "
            "birleştirmek ekibin onayına bağlı.",
            "",
            "Ambara alınan PR'lar:",
            "",
        ]
        for pr in ordered:
            line = f"- #{pr.number} {pr.title} (`{_short(pr.merge_sha)}`, {pr.author})"
            if notes.get(pr.number):
                line += f"\n  - Not: {notes[pr.number]}"
            lines.append(line)
        lines += ["", "Geri getirmek için Holocron → Ambar → **Ambardan çıkar**."]
        created = github.create_pull(
            repo["name"],
            title="Ambara alındı: " + ", ".join(f"#{n}" for n in numbers),
            head=prepared.branch,
            base=base,
            body="\n".join(lines),
        )
        result.update(
            ok=True,
            pr_number=created["number"],
            pr_url=created["url"],
            branch=prepared.branch,
            items=[{"number": pr.number, "label": f"#{pr.number}"} for pr in ordered],
        )
    except (agit.GitError, GitHubError, RepositoryError) as exc:
        result["error"] = _error(exc, settings)
        if pushed:
            result["branch"] = pushed
            result["error"]["message"] = (
                f"Dal itildi ({pushed}) ama PR açılamadı: " + result["error"]["message"]
                + " PR'ı GitHub'dan elle açabilirsiniz."
            )
    return result


def _release_repo(
    settings: Any, repo: dict[str, str], chosen: list[dict[str, Any]], stamp: str
) -> dict[str, Any]:
    result = _result(repo)
    result["items"] = [{"sha": item["sha"], "label": _short(item["sha"])} for item in chosen]
    pushed = ""
    try:
        github = github_for(settings, repo)
        git = git_for(settings, repo)
        agit.check_work_tree(git)
        agit.ensure_clean(git)
        agit.fetch(git)
        base = agit.default_branch(git)
        ref = f"refs/remotes/origin/{base}"
        held = {item.sha: item for item in agit.held_commits(git, ref)}
        selected: list[agit.Held] = []
        for item in chosen:
            found = held.get(item["sha"])
            if found is None:
                raise RepositoryError(
                    "not_held", f"{_short(item['sha'])} artık ambarda değil; listeyi yenileyin."
                )
            selected.append(found)
        by_revert: dict[str, list[agit.Held]] = {}
        for item in selected:
            by_revert.setdefault(item.revert.sha, []).append(item)
        labels = {
            item.sha: _label(agit.pr_number_of(item.commit), item.sha) for item in selected
        }
        result["items"] = [
            {"sha": item.sha, "label": labels[item.sha], "number": agit.pr_number_of(item.commit)}
            for item in selected
        ]
        try:
            prepared = agit.revert_onto_branch(
                git, base, agit.RELEASE_PREFIX + stamp, list(by_revert)
            )
        except agit.Conflict as conflict:
            owners = by_revert.get(conflict.sha) or []
            first = owners[0] if owners else None
            result["error"] = {
                "code": "conflict",
                "title": CONFLICT_TITLE,
                "message": (
                    f"{labels.get(first.sha, '') if first else _short(conflict.sha)} geri getirilirken "
                    "çakışma oldu; işlem geri sarıldı, klon olduğu gibi bırakıldı."
                ),
                "detail": redact(conflict.detail, _all_tokens(settings)),
                "files": conflict.files,
                "pr": {
                    "number": agit.pr_number_of(first.commit) if first else None,
                    "title": agit.pr_title_of(first.commit) if first else conflict.subject,
                },
            }
            return result
        pushed = prepared.branch
        ordered: list[agit.Held] = []
        for commit in prepared.commits:
            ordered.extend(by_revert.get(commit.sha, []))
        numbers = sorted(
            (agit.pr_number_of(item.commit) or 0, item.sha) for item in ordered
        )
        title_labels = [f"#{n}" if n else _short(sha) for n, sha in numbers]
        lines = [
            "Bu PR **Holocron Ambar** ile hazırlandı. Daha önce ambara alınan PR'ları geri "
            "getirir: ambara alırken yapılan geri almalar `git revert` ile geri alınır "
            "(yeniden eskiye). Ana dala doğrudan yazılmadı; birleştirmek ekibin onayına bağlı.",
            "",
            "Ambardan çıkarılan PR'lar:",
            "",
        ]
        for item in ordered:
            lines.append(
                f"- {labels[item.sha]} {agit.pr_title_of(item.commit)} "
                f"(geri alma `{_short(item.revert.sha)}`)"
            )
        created = github.create_pull(
            repo["name"],
            title="Ambardan çıkarıldı: " + ", ".join(title_labels),
            head=prepared.branch,
            base=base,
            body="\n".join(lines),
        )
        result.update(
            ok=True,
            pr_number=created["number"],
            pr_url=created["url"],
            branch=prepared.branch,
            items=[
                {"sha": item.sha, "label": labels[item.sha], "number": agit.pr_number_of(item.commit)}
                for item in ordered
            ],
        )
    except (agit.GitError, GitHubError, RepositoryError) as exc:
        result["error"] = _error(exc, settings)
        if pushed:
            result["branch"] = pushed
            result["error"]["message"] = (
                f"Dal itildi ({pushed}) ama PR açılamadı: " + result["error"]["message"]
                + " PR'ı GitHub'dan elle açabilirsiniz."
            )
    return result
