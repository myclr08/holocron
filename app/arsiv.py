"""Arsiv: goreve ve Jira kaydina iliştirilen belgeler ("veri kartlari").

Holocron bir Jedi bilgi arsividir; bu modul onun belge rafi. Uc kural:

1. **Dosya KOPYALANIR.** Kullanicinin surukledigi dosya yerinde kalir, bir
   kopyasi `<Belgeler>/holocron/holocron-belgeler/` altina yazilir. Veritabani
   yalnizca ust veriyi ve baglari tutar.
2. **Ayni icerik iki kez kopyalanmaz.** Icerik SHA-256 ozetiyle tanınır; ayni
   dosya ikinci kez gelirse var olan belge kullanilir, yalnizca bag eklenir.
3. **Bag Jira'ya gitmez.** Gorev ve kayit baglari yalnizca bu makinededir;
   bagi kaldirmak dosyayi silmez. Silme ayri ve onayli bir istir
   ("Arsivden sil").

Klasore elle birakilan dosyalar yeniden taramada "baglanmamis" belge olarak
gorunur. Modul HTTP bilmez; dosya acma/klasorde gosterme isletim sistemine
verilir ve testlerde yerine sahte bir acici konur.
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import subprocess
import sys
import unicodedata
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

from . import fields as field_utils, paths, repository
from .repository import RepositoryError

log = logging.getLogger("holocron.arsiv")

# Tek belge icin ust sinir. Kurum ici bir belge (PDF, Excel, ekran goruntusu)
# bunun cok altindadir; sinir yanlislikla surulenen kurulum ISO'sunu durdurur.
MAX_SIZE = 100 * 1024 * 1024

CHUNK = 1024 * 1024

TARGET_TASK = "task"
TARGET_ISSUE = "issue"
TARGETS = (TARGET_TASK, TARGET_ISSUE)

SOURCE_UPLOAD = "upload"
SOURCE_SCAN = "scan"

KIND_PDF = "pdf"
KIND_DOC = "doc"
KIND_XLS = "xls"
KIND_IMG = "img"
KIND_OTHER = "other"
KINDS = (KIND_PDF, KIND_DOC, KIND_XLS, KIND_IMG, KIND_OTHER)

KIND_LABELS = {
    KIND_PDF: "PDF",
    KIND_DOC: "Belge",
    KIND_XLS: "Tablo",
    KIND_IMG: "Görsel",
    KIND_OTHER: "Diğer",
}

_EXTENSIONS = {
    KIND_PDF: {".pdf"},
    KIND_DOC: {".doc", ".docx", ".odt", ".rtf", ".txt", ".md", ".ppt", ".pptx", ".odp"},
    KIND_XLS: {".xls", ".xlsx", ".xlsm", ".csv", ".ods", ".tsv"},
    KIND_IMG: {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".tif", ".tiff", ".heic"},
}

STATE_LINKED = "linked"
STATE_UNLINKED = "unlinked"

# Yukleme sirasinda yazilan gecici dosyanin uzantisi; tarama bunu atlar.
PART_SUFFIX = ".holocron-part"

# Windows'ta dosya adi olamayan isimler (uzantisiyla da olamaz: "con.txt").
_RESERVED = {"CON", "PRN", "AUX", "NUL"} | {f"COM{i}" for i in range(1, 10)} | {
    f"LPT{i}" for i in range(1, 10)
}
_BAD_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
MAX_NAME = 120

Launcher = Callable[[list[str]], None]


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# --- ad ve tur -------------------------------------------------------------


def sanitize_name(name: Any) -> str:
    """Kullanicidan gelen dosya adini guvenli bir tek parca ada cevirir.

    Yol parcalari atilir (`..\\..\\x.pdf` -> `x.pdf`), Windows'un kabul
    etmedigi karakterler `_` olur, sondaki nokta/bosluk kirpilir, ayrilmis
    adlar (`CON`, `NUL`...) one `_` alir, ad uzantisi korunarak kisaltilir.
    """
    text = unicodedata.normalize("NFC", str(name or ""))
    text = text.replace("\\", "/").split("/")[-1]
    text = _BAD_CHARS.sub("_", text).strip().rstrip(". ")
    text = re.sub(r"\s+", " ", text)
    if not text or set(text) <= {".", "_", " "}:
        text = "belge"
    stem, ext = os.path.splitext(text)
    if not stem:  # ".bashrc" gibi: uzanti sanilan sey aslinda ad
        stem, ext = ext, ""
    if stem.split(".")[0].upper() in _RESERVED:
        stem = "_" + stem
    ext = ext[:16]
    if len(stem) + len(ext) > MAX_NAME:
        stem = stem[: MAX_NAME - len(ext)].rstrip(". ") or "belge"
    return stem + ext


def kind_of(name: str) -> str:
    ext = os.path.splitext(str(name or ""))[1].lower()
    for kind, extensions in _EXTENSIONS.items():
        if ext in extensions:
            return kind
    return KIND_OTHER


def unique_name(folder: Path, name: str) -> str:
    """Klasorde bos bir ad: `rapor.pdf`, `rapor (2).pdf`, `rapor (3).pdf`...

    Windows dosya adlarinda buyuk/kucuk harf ayirmaz; karsilastirma o yuzden
    harf duyarsizdir, Linux'ta da ayni davranir.
    """
    taken = {entry.name.casefold() for entry in folder.iterdir()} if folder.exists() else set()
    if name.casefold() not in taken:
        return name
    stem, ext = os.path.splitext(name)
    for number in range(2, 10_000):
        candidate = f"{stem} ({number}){ext}"
        if candidate.casefold() not in taken:
            return candidate
    return f"{stem} ({uuid.uuid4().hex[:8]}){ext}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(CHUNK), b""):
            digest.update(block)
    return digest.hexdigest()


# --- satir -> sozluk -------------------------------------------------------


def _row(row: Any) -> dict[str, Any]:
    return {
        "id": int(row["id"]),
        "name": row["name"],
        "file_name": row["file_name"],
        "sha256": row["sha256"] or "",
        "size": int(row["size"] or 0),
        "kind": row["kind"],
        "kind_label": KIND_LABELS.get(row["kind"], KIND_LABELS[KIND_OTHER]),
        "source": row["source"],
        "created_at": row["created_at"],
    }


def get_document(conn: Any, document_id: Any) -> dict[str, Any] | None:
    try:
        number = int(document_id)
    except (TypeError, ValueError):
        return None
    row = conn.execute("SELECT * FROM documents WHERE id = ?", (number,)).fetchone()
    return _row(row) if row else None


def require_document(conn: Any, document_id: Any) -> dict[str, Any]:
    found = get_document(conn, document_id)
    if found is None:
        raise RepositoryError("not_found", "Belge Arşiv'de bulunamadı.", 404)
    return found


def _by_hash(conn: Any, digest: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM documents WHERE sha256 = ? ORDER BY id LIMIT 1", (digest,)
    ).fetchone()
    return _row(row) if row else None


def _insert(conn: Any, *, name: str, file_name: str, digest: str, size: int, source: str) -> dict[str, Any]:
    with conn:
        cursor = conn.execute(
            "INSERT INTO documents (name, file_name, sha256, size, kind, source, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (name, file_name, digest, int(size), kind_of(file_name), source, now_iso()),
        )
    return require_document(conn, cursor.lastrowid)


# --- yukleme ----------------------------------------------------------------


class PartWriter:
    """Gelen baytlari klasorde gecici bir dosyaya yazar, ozeti ayni anda hesaplar.

    Sinir asilinca `too_large` hatasi verir; gecici dosyayi `discard` siler.
    HTTP ucu (async akis) ve `store_chunks` (senkron) ayni yazari kullanir.
    """

    def __init__(self, folder: Path, max_size: int = MAX_SIZE) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        self.folder = folder
        self.max_size = max_size
        self.path = folder / f".{uuid.uuid4().hex}{PART_SUFFIX}"
        self.size = 0
        self._digest = hashlib.sha256()
        self._handle = open(self.path, "wb")

    def write(self, chunk: bytes) -> None:
        if not chunk:
            return
        self.size += len(chunk)
        if self.size > self.max_size:
            raise RepositoryError(
                "too_large",
                f"Belge çok büyük: sınır {self.max_size // (1024 * 1024)} MB.",
                413,
            )
        self._digest.update(chunk)
        self._handle.write(chunk)

    def close(self) -> None:
        if not self._handle.closed:
            self._handle.close()

    @property
    def hexdigest(self) -> str:
        return self._digest.hexdigest()

    def discard(self) -> None:
        self.close()
        try:
            self.path.unlink()
        except OSError:
            pass


def adopt(conn: Any, part: PartWriter, original_name: Any) -> tuple[dict[str, Any], bool]:
    """Yazilmis gecici dosyayi Arsiv'e alir; `(belge, zaten_vardi)` dondurur.

    Ayni icerik zaten Arsiv'deyse gecici dosya atilir, var olan belge doner.
    Gecici dosya her durumda temizlenir.
    """
    try:
        part.close()
        if part.size == 0:
            raise RepositoryError("empty_file", "Boş dosya Arşiv'e alınmaz.")
        folder = part.folder
        digest = part.hexdigest
        existing = _by_hash(conn, digest)
        if existing is not None and (folder / existing["file_name"]).exists():
            return existing, True
        final = unique_name(folder, sanitize_name(original_name))
        os.replace(part.path, folder / final)
        if existing is not None:
            # Kayit vardi ama dosyasi elle silinmisti: ayni satir yeni dosyayla canlanir.
            with conn:
                conn.execute(
                    "UPDATE documents SET file_name = ?, size = ?, kind = ? WHERE id = ?",
                    (final, part.size, kind_of(final), existing["id"]),
                )
            return require_document(conn, existing["id"]), True
        return (
            _insert(
                conn, name=sanitize_name(original_name), file_name=final, digest=digest,
                size=part.size, source=SOURCE_UPLOAD,
            ),
            False,
        )
    finally:
        part.discard()


def store_chunks(
    conn: Any,
    chunks: Iterable[bytes],
    original_name: Any,
    folder: Path | None = None,
    max_size: int = MAX_SIZE,
) -> tuple[dict[str, Any], bool]:
    """Senkron yukleme: baytlar gecici dosyaya, oradan Arsiv'e."""
    part = PartWriter(folder or paths.archive_dir(), max_size)
    try:
        for chunk in chunks:
            part.write(chunk)
    except BaseException:
        part.discard()
        raise
    return adopt(conn, part, original_name)


def store_file(conn: Any, source: Path, folder: Path | None = None) -> tuple[dict[str, Any], bool]:
    """Diskteki bir dosyayi Arsiv'e kopyalar (demo ve testler icin)."""

    def chunks() -> Iterable[bytes]:
        with open(source, "rb") as handle:
            yield from iter(lambda: handle.read(CHUNK), b"")

    return store_chunks(conn, chunks(), source.name, folder)


# --- tarama -----------------------------------------------------------------


def _visible_files(folder: Path) -> list[Path]:
    if not folder.exists():
        return []
    found: list[Path] = []
    for entry in folder.iterdir():
        if entry.name.startswith(".") or entry.name.endswith(PART_SUFFIX):
            continue
        # Office'in kilit dosyasi ("~$rapor.docx") belge degildir.
        if entry.name.startswith("~$") or entry.name.lower() in ("desktop.ini", "thumbs.db"):
            continue
        try:
            if entry.is_file():
                found.append(entry)
        except OSError:
            continue
    return found


def rescan(conn: Any, folder: Path | None = None) -> dict[str, int]:
    """Klasore elle birakilmis dosyalari "baglanmamis" belge olarak kaydeder.

    Kayitli dosyaya dokunulmaz. Elle silinmis dosyanin satiri da durur:
    listede "kayıp" gorunur, kullanici baglariyla birlikte silebilir.
    """
    target = folder or paths.archive_dir()
    known = {
        str(row["file_name"]).casefold()
        for row in conn.execute("SELECT file_name FROM documents").fetchall()
    }
    added = 0
    for entry in _visible_files(target):
        if entry.name.casefold() in known:
            continue
        try:
            size = entry.stat().st_size
            digest = sha256_file(entry)
        except OSError:
            log.warning("arsiv: okunamayan dosya atlandi: %s", entry.name)
            continue
        _insert(conn, name=entry.name, file_name=entry.name, digest=digest, size=size, source=SOURCE_SCAN)
        added += 1
    if added:
        log.info("arsiv: taramada %d yeni belge", added)
    return {"added": added}


# --- baglar -----------------------------------------------------------------


def clean_target(conn: Any, target_type: Any, target_id: Any) -> tuple[str, str]:
    """`(tur, kimlik)` dogrular; gorev var olmali, kayit anahtari gecerli olmali."""
    kind = str(target_type or "").strip().lower()
    if kind not in TARGETS:
        raise RepositoryError("invalid_target", "Bağ türü 'task' ya da 'issue' olmalı.")
    if kind == TARGET_TASK:
        try:
            number = int(str(target_id).strip())
        except (TypeError, ValueError) as exc:
            raise RepositoryError("invalid_target", "Görev kimliği geçersiz.") from exc
        repository.require_task(conn, number)
        return kind, str(number)
    keys = repository.parse_issue_keys(str(target_id or ""))
    if len(keys) != 1:
        raise RepositoryError("invalid_target", "Geçerli bir Jira anahtarı verin (ör. DEMO-1).")
    return kind, keys[0]


def link(conn: Any, document_id: Any, target_type: Any, target_id: Any) -> bool:
    """Bag ekler; zaten varsa `False`."""
    document = require_document(conn, document_id)
    kind, ident = clean_target(conn, target_type, target_id)
    with conn:
        cursor = conn.execute(
            "INSERT OR IGNORE INTO document_links (document_id, target_type, target_id, created_at) "
            "VALUES (?, ?, ?, ?)",
            (document["id"], kind, ident, now_iso()),
        )
    return cursor.rowcount > 0


def unlink(conn: Any, document_id: Any, target_type: Any, target_id: Any) -> bool:
    """Bagi kaldirir; dosyaya ve diger baglara dokunmaz."""
    document = require_document(conn, document_id)
    kind = str(target_type or "").strip().lower()
    ident = str(target_id or "").strip()
    if kind == TARGET_ISSUE:
        ident = ident.upper()
    with conn:
        cursor = conn.execute(
            "DELETE FROM document_links WHERE document_id = ? AND target_type = ? AND target_id = ?",
            (document["id"], kind, ident),
        )
    return cursor.rowcount > 0


def task_document_count(conn: Any, task_id: Any) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS c FROM document_links WHERE target_type = 'task' AND target_id = ?",
        (str(task_id),),
    ).fetchone()
    return int(row["c"]) if row else 0


def _links_by_document(conn: Any) -> dict[int, list[dict[str, Any]]]:
    rows = conn.execute(
        "SELECT document_id, target_type, target_id, created_at FROM document_links "
        "ORDER BY created_at, target_type, target_id"
    ).fetchall()
    task_ids = [int(row["target_id"]) for row in rows if row["target_type"] == TARGET_TASK]
    titles: dict[str, str] = {}
    if task_ids:
        unique = sorted(set(task_ids))
        for start in range(0, len(unique), 400):
            chunk = unique[start : start + 400]
            marks = ",".join("?" for _ in chunk)
            for row in conn.execute(
                f"SELECT id, title FROM tasks WHERE id IN ({marks})", chunk
            ).fetchall():
                titles[str(row["id"])] = row["title"]
    issue_keys = [row["target_id"] for row in rows if row["target_type"] == TARGET_ISSUE]
    records = repository.get_issues(conn, issue_keys) if issue_keys else {}
    result: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        kind, ident = row["target_type"], row["target_id"]
        if kind == TARGET_TASK:
            if ident not in titles:
                continue  # silinmis gorevin artigi
            label = titles[ident]
        else:
            raw = (records.get(ident) or {}).get("raw") or {}
            label = field_utils.plain_text((raw.get("fields") or {}).get("summary"))
        result.setdefault(int(row["document_id"]), []).append(
            {"type": kind, "target": ident, "label": label or ""}
        )
    return result


def _view(document: dict[str, Any], links: list[dict[str, Any]], folder: Path) -> dict[str, Any]:
    view = dict(document)
    view["links"] = links
    view["missing"] = not (folder / document["file_name"]).exists()
    return view


def list_documents(
    conn: Any,
    q: str = "",
    kind: str = "",
    state: str = "",
    folder: Path | None = None,
) -> list[dict[str, Any]]:
    """Arsiv listesi, yeniden eskiye. Arama ad + bag etiketleri uzerinde."""
    target = folder or paths.archive_dir()
    links = _links_by_document(conn)
    needle = field_utils.fold(str(q or "").strip())
    wanted_kind = str(kind or "").strip().lower()
    wanted_state = str(state or "").strip().lower()
    result: list[dict[str, Any]] = []
    for row in conn.execute("SELECT * FROM documents ORDER BY created_at DESC, id DESC").fetchall():
        document = _row(row)
        own = links.get(document["id"], [])
        if wanted_kind in KINDS and document["kind"] != wanted_kind:
            continue
        if wanted_state == STATE_LINKED and not own:
            continue
        if wanted_state == STATE_UNLINKED and own:
            continue
        if needle:
            haystack = " ".join(
                [document["name"]] + [f"{item['target']} {item['label']}" for item in own]
            )
            if needle not in field_utils.fold(haystack):
                continue
        result.append(_view(document, own, target))
    return result


def documents_for(
    conn: Any, target_type: Any, target_id: Any, folder: Path | None = None
) -> list[dict[str, Any]]:
    """Bir goreve ya da kayda bagli belgeler (detay ekranindaki veri kartlari)."""
    kind = str(target_type or "").strip().lower()
    ident = str(target_id or "").strip()
    if kind == TARGET_ISSUE:
        ident = ident.upper()
    target = folder or paths.archive_dir()
    rows = conn.execute(
        "SELECT d.* FROM documents d JOIN document_links l ON l.document_id = d.id "
        "WHERE l.target_type = ? AND l.target_id = ? ORDER BY l.created_at, d.id",
        (kind, ident),
    ).fetchall()
    links = _links_by_document(conn)
    return [_view(_row(row), links.get(int(row["id"]), []), target) for row in rows]


def counts(conn: Any) -> dict[str, int]:
    total = conn.execute("SELECT COUNT(*) AS c FROM documents").fetchone()["c"]
    linked = conn.execute(
        "SELECT COUNT(DISTINCT document_id) AS c FROM document_links"
    ).fetchone()["c"]
    return {"total": int(total), "linked": int(linked), "unlinked": int(total) - int(linked)}


# --- silme ------------------------------------------------------------------


def delete_document(conn: Any, document_id: Any, folder: Path | None = None) -> dict[str, Any]:
    """Dosyayi ve butun baglarini siler. Dosya zaten yoksa satir yine gider."""
    document = require_document(conn, document_id)
    target = folder or paths.archive_dir()
    path = target / document["file_name"]
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise RepositoryError(
            "file_busy",
            "Dosya silinemedi: başka bir uygulamada açık olabilir. Kapatıp yeniden deneyin.",
            409,
        ) from exc
    with conn:
        conn.execute("DELETE FROM document_links WHERE document_id = ?", (document["id"],))
        conn.execute("DELETE FROM documents WHERE id = ?", (document["id"],))
    return document


# --- isletim sistemine verme --------------------------------------------


def _no_window() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform.startswith("win") else 0


def _spawn(command: list[str]) -> None:
    subprocess.Popen(  # noqa: S603 - sabit komut, kullanici girdisi yol olarak gecer
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=_no_window(),
    )


def _existing_path(conn: Any, document_id: Any, folder: Path | None) -> Path:
    document = require_document(conn, document_id)
    path = (folder or paths.archive_dir()) / document["file_name"]
    if not path.exists():
        raise RepositoryError(
            "file_missing",
            "Dosya Arşiv klasöründe yok: elle silinmiş ya da taşınmış olabilir.",
            404,
        )
    return path


def open_document(
    conn: Any, document_id: Any, folder: Path | None = None, launcher: Launcher | None = None
) -> Path:
    """Dosyayi varsayilan uygulamasiyla acar."""
    path = _existing_path(conn, document_id, folder)
    if launcher is not None:
        launcher(["open", str(path)])
        return path
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # type: ignore[attr-defined]  # noqa: S606
        elif sys.platform == "darwin":
            _spawn(["open", str(path)])
        else:
            _spawn(["xdg-open", str(path)])
    except OSError as exc:
        raise RepositoryError("open_failed", "Dosya açılamadı: varsayılan uygulama yok.", 500) from exc
    return path


def reveal_document(
    conn: Any, document_id: Any, folder: Path | None = None, launcher: Launcher | None = None
) -> Path:
    """Dosyanin klasorunu acar, mumkunse dosyayi secili gosterir."""
    path = _existing_path(conn, document_id, folder)
    if launcher is not None:
        launcher(["reveal", str(path)])
        return path
    try:
        if sys.platform.startswith("win"):
            # explorer kendi penceresini acar; konsol penceresi cikmasin.
            _spawn(["explorer", f"/select,{path}"])
        elif sys.platform == "darwin":
            _spawn(["open", "-R", str(path)])
        else:
            _spawn(["xdg-open", str(path.parent)])
    except OSError as exc:
        raise RepositoryError("open_failed", "Klasör açılamadı.", 500) from exc
    return path


def reveal_folder(folder: Path | None = None, launcher: Launcher | None = None) -> Path:
    target = folder or paths.archive_dir()
    if launcher is not None:
        launcher(["folder", str(target)])
        return target
    try:
        if sys.platform.startswith("win"):
            os.startfile(str(target))  # type: ignore[attr-defined]  # noqa: S606
        elif sys.platform == "darwin":
            _spawn(["open", str(target)])
        else:
            _spawn(["xdg-open", str(target)])
    except OSError as exc:
        raise RepositoryError("open_failed", "Klasör açılamadı.", 500) from exc
    return target
