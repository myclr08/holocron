"""Ambar testleri icin sahte dunya: yerel git depolari + sahte GitHub API.

* `GitWorld`: ciplak (bare) bir "uzak" depo, gelistiricinin klonu (`dev`,
  PR birlestirmeyi o taklit eder) ve kullanicinin klonu (`work`, Holocron'un
  ayarlarina yazilan klasor). Birlestirme `--no-ff` (GitHub'in "Create a merge
  commit" dugmesi) ya da squash ile yapilir.
* `FakeGitHub`: 127.0.0.1'de kucuk bir HTTP sunucusu. Yalnizca Ambar'in
  kullandigi uclari bilir; gelen her istegi (yol + Authorization) kaydeder.

Hicbir test gercek GitHub'a ya da aga cikmaz.
"""

from __future__ import annotations

import json
import subprocess
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


def git(cwd: Path | str, *args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout.strip()


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class FakeGitHub:
    def __init__(self, prefix: str = "", token: str = "ghp_test_token_123") -> None:
        self.prefix = prefix.rstrip("/")
        self.token = token
        self.repos: dict[str, dict[str, Any]] = {}
        self.requests: list[dict[str, Any]] = []
        self.next_number = 1000
        self.fail_create: dict[str, int] = {}
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:  # sessiz
                return

            def _send(self, status: int, payload: Any) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _handle(self, method: str) -> None:
                parsed = urlparse(self.path)
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length) if length else b""
                owner.requests.append(
                    {
                        "method": method,
                        "path": parsed.path,
                        "query": parse_qs(parsed.query),
                        "auth": self.headers.get("Authorization", ""),
                        "body": json.loads(raw) if raw else None,
                    }
                )
                status, payload = owner.route(
                    method, parsed.path, parse_qs(parsed.query),
                    self.headers.get("Authorization", ""), json.loads(raw) if raw else None,
                )
                self._send(status, payload)

            def do_GET(self) -> None:  # noqa: N802
                self._handle("GET")

            def do_POST(self) -> None:  # noqa: N802
                self._handle("POST")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def api_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}{self.prefix}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    # --- veri ---------------------------------------------------------

    def add_repo(self, full_name: str, world: "GitWorld | None" = None) -> None:
        self.repos[full_name] = {"pulls": [], "world": world}

    def add_pull(self, full_name: str, **fields: Any) -> dict[str, Any]:
        stamp = fields.pop("at", None) or now_iso()
        item = {
            "number": fields["number"],
            "title": fields.get("title", f"PR {fields['number']}"),
            "user": {"login": fields.get("author", "ayse")},
            "state": fields.get("state", "closed"),
            "merged_at": fields.get("merged_at", stamp),
            "updated_at": fields.get("updated_at", stamp),
            "merge_commit_sha": fields.get("sha", ""),
            "html_url": f"https://github.example/{full_name}/pull/{fields['number']}",
            "head": {"ref": fields.get("head", f"feature-{fields['number']}")},
            "base": {"ref": fields.get("base", "master")},
        }
        self.repos[full_name]["pulls"].append(item)
        return item

    def created(self, full_name: str | None = None) -> list[dict[str, Any]]:
        found = [
            req for req in self.requests
            if req["method"] == "POST" and req["path"].endswith("/pulls")
        ]
        if full_name:
            found = [req for req in found if f"/repos/{full_name}/" in req["path"]]
        return found

    # --- yonlendirme --------------------------------------------------

    def route(
        self, method: str, path: str, query: dict[str, list[str]], auth: str, body: Any
    ) -> tuple[int, Any]:
        if self.prefix:
            if not path.startswith(self.prefix + "/"):
                return 404, {"message": "Not Found"}
            path = path[len(self.prefix):]
        if auth != f"token {self.token}":
            return 401, {"message": "Bad credentials"}
        parts = [part for part in path.split("/") if part]
        if len(parts) < 3 or parts[0] != "repos":
            return 404, {"message": "Not Found"}
        full_name = f"{parts[1]}/{parts[2]}"
        repo = self.repos.get(full_name)
        if repo is None:
            return 404, {"message": "Not Found"}
        rest = parts[3:]
        if not rest and method == "GET":
            return 200, {
                "full_name": full_name,
                "default_branch": "master",
                "permissions": {"push": True},
            }
        if rest == ["pulls"] and method == "GET":
            state = (query.get("state") or ["open"])[0]
            items = [pr for pr in repo["pulls"] if pr["state"] == state]
            items.sort(key=lambda pr: pr["updated_at"], reverse=True)
            per_page = int((query.get("per_page") or ["30"])[0])
            page = int((query.get("page") or ["1"])[0])
            return 200, items[(page - 1) * per_page: page * per_page]
        if rest == ["pulls"] and method == "POST":
            if full_name in self.fail_create:
                return self.fail_create[full_name], {"message": "Validation Failed"}
            world = repo.get("world")
            if world is not None:
                # Dal gercekten uzak depoya itilmis olmali.
                try:
                    world.remote_sha(body["head"])
                except AssertionError:
                    return 422, {"message": "Validation Failed", "errors": [{"field": "head"}]}
            self.next_number += 1
            item = {
                "number": self.next_number,
                "title": body["title"],
                "body": body["body"],
                "user": {"login": "holocron"},
                "state": "open",
                "merged_at": None,
                "updated_at": now_iso(),
                "merge_commit_sha": None,
                "html_url": f"https://github.example/{full_name}/pull/{self.next_number}",
                "head": {"ref": body["head"]},
                "base": {"ref": body["base"]},
            }
            repo["pulls"].append(item)
            return 201, item
        if len(rest) == 2 and rest[0] == "pulls" and method == "GET":
            for pr in repo["pulls"]:
                if str(pr["number"]) == rest[1]:
                    return 200, pr
            return 404, {"message": "Not Found"}
        return 404, {"message": "Not Found"}


class GitWorld:
    """Uzak depo + gelistirici klonu + kullanicinin klonu."""

    def __init__(self, root: Path, name: str, github: FakeGitHub) -> None:
        self.root = root
        self.name = name
        self.github = github
        self.remote = root / "remote.git"
        self.dev = root / "dev"
        self.work = root / "work"
        root.mkdir(parents=True, exist_ok=True)
        git(root, "init", "--quiet", "--bare", "-b", "master", str(self.remote))
        git(root, "clone", "--quiet", str(self.remote), str(self.dev))
        git(self.dev, "checkout", "--quiet", "-b", "master")
        (self.dev / "README.md").write_text("ornek\n", encoding="utf-8")
        (self.dev / "app.txt").write_text("satir 1\nsatir 2\nsatir 3\n", encoding="utf-8")
        git(self.dev, "add", ".")
        git(self.dev, "commit", "--quiet", "-m", "ilk commit")
        git(self.dev, "push", "--quiet", "-u", "origin", "master")
        git(root, "clone", "--quiet", str(self.remote), str(self.work))
        github.add_repo(name, self)

    # --- PR birlestirme taklidi ----------------------------------------

    def merge_pr(
        self,
        number: int,
        files: dict[str, str],
        title: str = "",
        squash: bool = False,
        author: str = "ayse",
        register: bool = True,
    ) -> str:
        title = title or f"Degisiklik {number}"
        branch = f"feature-{number}"
        git(self.dev, "fetch", "--quiet", "origin")
        git(self.dev, "checkout", "--quiet", "master")
        git(self.dev, "reset", "--quiet", "--hard", "origin/master")
        git(self.dev, "checkout", "--quiet", "-b", branch)
        for path, text in files.items():
            target = self.dev / path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
        git(self.dev, "add", ".")
        git(self.dev, "commit", "--quiet", "-m", f"{title} calismasi")
        git(self.dev, "checkout", "--quiet", "master")
        if squash:
            git(self.dev, "merge", "--quiet", "--squash", branch)
            git(self.dev, "commit", "--quiet", "-m", f"{title} (#{number})")
        else:
            git(
                self.dev, "merge", "--quiet", "--no-ff", branch,
                "-m", f"Merge pull request #{number} from org/{branch}", "-m", title,
            )
        git(self.dev, "push", "--quiet", "origin", "master")
        sha = git(self.dev, "rev-parse", "HEAD")
        if register:
            self.github.add_pull(self.name, number=number, title=title, sha=sha, author=author)
        return sha

    def merge_branch(self, branch: str, number: int) -> str:
        """Ambar'in actigi PR'i GitHub'in "Create a merge commit" dugmesi gibi birlestirir."""
        git(self.dev, "fetch", "--quiet", "origin")
        git(self.dev, "checkout", "--quiet", "master")
        git(self.dev, "reset", "--quiet", "--hard", "origin/master")
        git(
            self.dev, "merge", "--quiet", "--no-ff", f"origin/{branch}",
            "-m", f"Merge pull request #{number} from org/{branch}",
        )
        git(self.dev, "push", "--quiet", "origin", "master")
        for pr in self.github.repos[self.name]["pulls"]:
            if pr["number"] == number:
                pr["state"] = "closed"
                pr["merged_at"] = now_iso()
                pr["merge_commit_sha"] = git(self.dev, "rev-parse", "HEAD")
        return git(self.dev, "rev-parse", "HEAD")

    def remote_sha(self, branch: str) -> str:
        return git(self.remote, "rev-parse", f"refs/heads/{branch}")

    def remote_branches(self) -> list[str]:
        text = git(self.remote, "for-each-ref", "--format=%(refname:short)", "refs/heads")
        return [line for line in text.splitlines() if line]

    def file(self, path: str, ref: str = "origin/master") -> str:
        git(self.dev, "fetch", "--quiet", "origin")
        return git(self.dev, "show", f"{ref}:{path}")

    def work_state(self) -> dict[str, Any]:
        return {
            "head": git(self.work, "rev-parse", "HEAD"),
            "branch": git(self.work, "rev-parse", "--abbrev-ref", "HEAD"),
            "status": git(self.work, "status", "--porcelain"),
            "branches": git(self.work, "for-each-ref", "--format=%(refname:short)", "refs/heads"),
        }
