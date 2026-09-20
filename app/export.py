"""Grup -> Excel (.xlsx) disa aktarimi.

Satirlar ekrandaki grid ile ayni yerden gelir (`app/grid.py`), boylece Excel
dosyasi ekranda gorunenin birebir karsiligidir. Tek fark: grid hucre metnini
kirpar, burada tam metin yazilir ve tarih/sayi hucreleri gercek Excel tipinde
olusur.
"""

from __future__ import annotations

import io
import re
from datetime import date, datetime, tzinfo
from decimal import Decimal, InvalidOperation
from typing import Any, Sequence
from urllib.parse import quote

from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from . import (
    __version__,
    fields as field_utils,
    grid,
    repository,
    tasks as task_utils,
    teamslink,
)

MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

EXCEL_DATE_FORMAT = "DD.MM.YYYY"
EXCEL_DATETIME_FORMAT = "DD.MM.YYYY HH:MM"

SHEET_NAME_LIMIT = 31
COLUMN_WIDTH_LIMIT = 60
COLUMN_WIDTH_MIN = 9

HISTORY_SHEET = "Geçmiş"
INFO_SHEET = "Bilgi"
FALLBACK_SHEET = "Kayıtlar"
NO_HISTORY_TEXT = "Geçmiş tutulan alan yok"

HISTORY_HEADERS = ("Anahtar", "Alan", "Tarih", "Eski değer", "Yeni değer")

KIND_LABELS = {repository.KIND_MANUAL: "Manuel", repository.KIND_FILTER: "JQL filtresi"}

# Excel sayfa adinda yasak karakterler; tek tirnak da basta/sonda duramaz.
_FORBIDDEN_SHEET = re.compile(r"[\\/*?:\[\]]")
# Dosya adinda sorun cikaran karakterler (Windows dahil).
_FORBIDDEN_FILE = re.compile(r"[\\/:*?\"<>|\x00-\x1f]")
_SPACES = re.compile(r"\s+")
_DASHES = re.compile(r"-{2,}")
_NON_ASCII_NAME = re.compile(r"[^A-Za-z0-9._-]")

# ASCII dosya adi yedegi icin: Turkce harfler karsiliklarina duser.
_ASCII_MAP = str.maketrans(
    {
        "ç": "c", "Ç": "C", "ğ": "g", "Ğ": "G", "ı": "i", "İ": "I",
        "ö": "o", "Ö": "O", "ş": "s", "Ş": "S", "ü": "u", "Ü": "U",
    }
)

# Hucre tipleri: metin disindakiler gercek Excel hucresi olur.
KIND_TEXT = "text"
KIND_DATE = "date"
KIND_DATETIME = "datetime"
KIND_NUMBER = "number"
KIND_DECIMAL = "decimal"


def build_workbook(
    context: Any,
    group_id: int,
    columns: list[str] | None = None,
    include_history: bool = False,
    q: str | None = None,
    sort: str | None = None,
    direction: str = "",
    tz: tzinfo | None = None,
    now: datetime | None = None,
    keys: Sequence[str] | None = None,
) -> bytes:
    """Grubu .xlsx olarak uretir ve bellekteki baytlari dondurur.

    `keys` verilirse yalnizca o anahtarlar yazilir: grid'de onay kutusuyla
    secilen satirlar. Suzgec/siralama once uygulanir, secim onun uzerine biner.
    """
    data = grid.build_grid(
        context,
        group_id,
        columns=columns,
        q=q or "",
        sort=sort or "",
        direction=direction,
    )
    if keys is not None:
        data.rows = pick_rows(data.rows, keys)

    book = Workbook()
    sheet = book.active
    sheet.title = sheet_title(data.group["name"])
    _write_rows(sheet, data, tz)

    if include_history:
        _write_history(book, context, data, tz)

    _write_info(book, data, tz, now)

    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


# --- sayfa 1: kayitlar --------------------------------------------------


def _write_rows(sheet: Worksheet, data: grid.GridData, tz: tzinfo | None) -> None:
    headers = [head["name"] for head in data.heads]
    kinds = [_cell_kind(head, data.schemas) for head in data.heads]
    widths = [len(text) for text in headers]

    sheet.append(headers)
    for cell in sheet[1]:
        cell.font = Font(bold=True)

    for line, row in enumerate(data.rows, start=2):
        for index, cell in enumerate(row["cells"]):
            target = sheet.cell(row=line, column=index + 1)
            width = _write_cell(target, cell, kinds[index], data.heads[index], row, tz)
            if width > widths[index]:
                widths[index] = width

    last_column = get_column_letter(max(len(headers), 1))
    last_line = len(data.rows) + 1
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:{last_column}{last_line}"
    for index, width in enumerate(widths):
        letter = get_column_letter(index + 1)
        sheet.column_dimensions[letter].width = min(
            max(width + 2, COLUMN_WIDTH_MIN), COLUMN_WIDTH_LIMIT
        )


def _cell_kind(head: dict[str, Any], schemas: dict[str, dict[str, Any]]) -> str:
    """Sutunun Excel'deki hucre tipi."""
    local = head.get("local")
    if local is not None:
        derived = head.get("derived") or ""
        if derived == field_utils.DERIVED_CHANGES:
            return KIND_NUMBER
        if derived == field_utils.DERIVED_CHANGED_AT:
            return KIND_DATETIME
        if local["type"] == field_utils.LOCAL_NUMBER:
            return KIND_DECIMAL
        if local["type"] == field_utils.LOCAL_DATE:
            return KIND_DATE
        return KIND_TEXT

    kind = field_utils.schema_type(schemas.get(head["id"]))
    if kind == "date":
        return KIND_DATE
    if kind == "datetime":
        return KIND_DATETIME
    if kind == "number":
        return KIND_NUMBER
    return KIND_TEXT


def _write_cell(
    target: Any,
    cell: dict[str, Any],
    kind: str,
    head: dict[str, Any],
    row: dict[str, Any],
    tz: tzinfo | None,
) -> int:
    """Hucreyi yazar ve sutun genisligi icin gorunen uzunlugu dondurur."""
    # Teams belirteci dokumde GORUNMEZ: yalnizca uygulama icinde anlamlidir.
    text = teamslink.temizle(cell.get("text") or "")
    raw = cell.get("raw")

    if kind == KIND_DATE:
        moment = field_utils.parse_moment(raw)
        if moment is not None:
            target.value = moment.date()
            target.number_format = EXCEL_DATE_FORMAT
            return len(EXCEL_DATE_FORMAT)
    elif kind == KIND_DATETIME:
        moment = field_utils.parse_moment(raw)
        if moment is not None:
            local = moment.astimezone(tz) if tz is not None else moment.astimezone()
            # Excel zaman dilimi tasimaz; yerel saate cevrilip saf yazilir.
            target.value = local.replace(tzinfo=None)
            target.number_format = EXCEL_DATETIME_FORMAT
            return len(EXCEL_DATETIME_FORMAT)
    elif kind == KIND_NUMBER:
        number = _as_int_or_float(raw)
        if number is not None:
            target.value = number
            return len(str(number))
    elif kind == KIND_DECIMAL:
        number = _as_decimal(raw)
        if number is not None:
            target.value = number
            return len(text or str(number))

    if not text:
        return 0
    target.value = text
    if head["id"] in ("issuekey", "key") and row.get("url"):
        target.hyperlink = row["url"]
        target.style = "Hyperlink"
    return _display_width(text)


def _display_width(text: str) -> int:
    """Cok satirli metinde en uzun satir sutun genisligini belirler."""
    return max((len(line) for line in text.splitlines()), default=0)


def _as_int_or_float(value: Any) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    number = field_utils.parse_number(value)
    if number is None:
        return None
    return int(number) if number.is_integer() else number


def _as_decimal(value: Any) -> Decimal | None:
    """Yerel sayi degeri metin olarak saklanir; kayipsiz Decimal'e cevrilir."""
    if value is None or value == "":
        return None
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


# --- sayfa 2: gecmis ----------------------------------------------------


def _write_history(
    book: Workbook, context: Any, data: grid.GridData, tz: tzinfo | None
) -> None:
    sheet = book.create_sheet(HISTORY_SHEET)
    conn = context.connection()
    tracked = [field for field in repository.list_local_fields(conn) if field["track_history"]]
    if not tracked:
        sheet["A1"] = NO_HISTORY_TEXT
        sheet.column_dimensions["A"].width = len(NO_HISTORY_TEXT) + 2
        return

    sheet.append(list(HISTORY_HEADERS))
    for cell in sheet[1]:
        cell.font = Font(bold=True)

    entries: list[tuple[str, str, str, str, str]] = []
    for row in data.rows:
        key = row["key"]
        for field in tracked:
            if not data.local_view.stat(key, field["id"]).get("changes"):
                continue
            for item in repository.list_local_history(conn, key, field["id"]):
                entries.append(
                    (
                        item["changed_at"] or "",
                        key,
                        field["name"],
                        teamslink.temizle(item["old_text"]),
                        teamslink.temizle(item["new_text"]),
                    )
                )
    # Yeniden eskiye: ayni anda dusen satirlarda kayit/alan sirasi korunur.
    entries.sort(key=lambda item: item[0], reverse=True)

    widths = [len(text) for text in HISTORY_HEADERS]
    for line, entry in enumerate(entries, start=2):
        changed_at, key, name, old_text, new_text = entry
        values = [key, name, None, old_text, new_text]
        for index, value in enumerate(values):
            target = sheet.cell(row=line, column=index + 1)
            if index == 2:
                moment = field_utils.parse_moment(changed_at)
                if moment is None:
                    target.value = changed_at
                    widths[2] = max(widths[2], len(changed_at))
                    continue
                local = moment.astimezone(tz) if tz is not None else moment.astimezone()
                target.value = local.replace(tzinfo=None)
                target.number_format = EXCEL_DATETIME_FORMAT
                widths[2] = max(widths[2], len(EXCEL_DATETIME_FORMAT))
                continue
            if value:
                target.value = value
                widths[index] = max(widths[index], _display_width(value))

    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:E{len(entries) + 1}"
    for index, width in enumerate(widths):
        letter = get_column_letter(index + 1)
        sheet.column_dimensions[letter].width = min(
            max(width + 2, COLUMN_WIDTH_MIN), COLUMN_WIDTH_LIMIT
        )


# --- sayfa 3: bilgi -----------------------------------------------------


def _write_info(
    book: Workbook, data: grid.GridData, tz: tzinfo | None, now: datetime | None
) -> None:
    sheet = book.create_sheet(INFO_SHEET)
    group = data.group
    moment = now or datetime.now(tz=tz)
    if moment.tzinfo is not None:
        moment = moment.astimezone(tz).replace(tzinfo=None) if tz else moment.replace(tzinfo=None)
    moment = moment.replace(microsecond=0)

    lines: list[tuple[str, Any]] = [
        ("Grup", group["name"]),
        ("Tür", KIND_LABELS.get(group["kind"], group["kind"])),
    ]
    if group["kind"] == repository.KIND_FILTER:
        lines.append(("JQL", group["jql"]))
    lines.append(("Dışa aktarma", moment))
    lines.append(("Kayıt sayısı", len(data.rows)))
    lines.append(("Sürüm", __version__))

    for line, (label, value) in enumerate(lines, start=1):
        name_cell = sheet.cell(row=line, column=1, value=label)
        name_cell.font = Font(bold=True)
        value_cell = sheet.cell(row=line, column=2, value=value)
        if isinstance(value, datetime):
            value_cell.number_format = EXCEL_DATETIME_FORMAT

    sheet.column_dimensions["A"].width = 16
    sheet.column_dimensions["B"].width = min(
        max((len(str(value)) for _, value in lines), default=20) + 2, COLUMN_WIDTH_LIMIT
    )


# --- gorevler: tek sayfalik pano dokumu ---------------------------------

TASKS_SHEET = "Görevlerim"
TASKS_NAME = "Görevlerim"

# Ekrandaki pencere sirasi: Ad, Aciklama, Son durum, Not.
TASK_HEADERS = (
    "Durum",
    "Ad",
    "Açıklama",
    "Son durum",
    "Not",
    "Son tarih",
    "Jira kaydı",
    "Jira özeti",
    "Jira durumu",
    "Oluşturma",
    "Tamamlanma",
)

# Basliklarin hucre tipleri; geri kalani metin.
TASK_KINDS = {5: KIND_DATE, 9: KIND_DATETIME, 10: KIND_DATETIME}

# Ikinci sayfa: son durum defteri.
TASK_HISTORY_SHEET = "Son durum geçmişi"
TASK_HISTORY_HEADERS = ("Görev", "Tarih", "Metin")
NO_TASK_HISTORY_TEXT = "Son durum yazılmış görev yok"


def build_tasks_workbook(
    context: Any,
    status: str = "all",
    q: str = "",
    tz: tzinfo | None = None,
    now: datetime | None = None,
) -> bytes:
    """Gorev panosunu tek sayfalik .xlsx olarak uretir.

    Disa aktarim eski biten gorevleri de kapsar: ekranda gizlenmeleri panoyu
    temiz tutmak icindir, dokumden dusmelerini gerektirmez.
    """
    board = task_utils.build_board(
        context, q=q, include_old_done=True, now=now, status=status or "all"
    )

    book = Workbook()
    sheet = book.active
    sheet.title = sheet_title(TASKS_SHEET)
    sheet.append(list(TASK_HEADERS))
    for cell in sheet[1]:
        cell.font = Font(bold=True)

    widths = [len(text) for text in TASK_HEADERS]
    for line, task in enumerate(_ordered_tasks(board), start=2):
        issue = task.get("issue") or {}
        values = [
            repository.TASK_STATUS_LABELS.get(task["status"], task["status"]),
            task["title"],
            task["description"],
            task["son_durum"],
            task["note"],
            task["due_date"],
            task["issue_key"],
            issue.get("summary", ""),
            issue.get("status_text", ""),
            task["created_at"],
            task["done_at"],
        ]
        for index, value in enumerate(values):
            target = sheet.cell(row=line, column=index + 1)
            width = _write_task_cell(target, value, TASK_KINDS.get(index, KIND_TEXT), tz)
            if index == 6 and value and issue.get("url"):
                target.hyperlink = issue["url"]
                target.style = "Hyperlink"
            if width > widths[index]:
                widths[index] = width

    last_line = sum(len(column["tasks"]) for column in board.columns) + 1
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(TASK_HEADERS))}{last_line}"
    for index, width in enumerate(widths):
        letter = get_column_letter(index + 1)
        sheet.column_dimensions[letter].width = min(
            max(width + 2, COLUMN_WIDTH_MIN), COLUMN_WIDTH_LIMIT
        )

    _write_task_history(book, context, _ordered_tasks(board), tz)

    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _write_task_history(
    book: Workbook, context: Any, ordered: list[dict[str, Any]], tz: tzinfo | None
) -> None:
    """Ikinci sayfa: "son durum" defteri (gorev, tarih, metin).

    Ilk sayfada yalnizca GUNCEL son durum vardir; degisimlerin tamami burada
    eskiden yeniye durur.
    """
    sheet = book.create_sheet(sheet_title(TASK_HISTORY_SHEET))
    conn = context.connection()
    entries: list[tuple[str, str, str]] = []
    for task in ordered:
        for item in repository.list_task_status_history(conn, task["id"]):
            entries.append(
                (
                    task["title"],
                    item["olusturma"] or "",
                    teamslink.temizle(item["metin"]) or repository.TASK_STATUS_EMPTY_TEXT,
                )
            )
    if not entries:
        sheet["A1"] = NO_TASK_HISTORY_TEXT
        sheet.column_dimensions["A"].width = len(NO_TASK_HISTORY_TEXT) + 2
        return

    sheet.append(list(TASK_HISTORY_HEADERS))
    for cell in sheet[1]:
        cell.font = Font(bold=True)

    widths = [len(text) for text in TASK_HISTORY_HEADERS]
    for line, (title, when, text) in enumerate(entries, start=2):
        sheet.cell(row=line, column=1).value = title
        widths[0] = max(widths[0], _display_width(title))
        target = sheet.cell(row=line, column=2)
        moment = field_utils.parse_moment(when)
        if moment is None:
            target.value = when
            widths[1] = max(widths[1], len(when))
        else:
            local = moment.astimezone(tz) if tz is not None else moment.astimezone()
            target.value = local.replace(tzinfo=None)
            target.number_format = EXCEL_DATETIME_FORMAT
            widths[1] = max(widths[1], len(EXCEL_DATETIME_FORMAT))
        sheet.cell(row=line, column=3).value = text
        widths[2] = max(widths[2], _display_width(text))

    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:C{len(entries) + 1}"
    for index, width in enumerate(widths):
        letter = get_column_letter(index + 1)
        sheet.column_dimensions[letter].width = min(
            max(width + 2, COLUMN_WIDTH_MIN), COLUMN_WIDTH_LIMIT
        )


def _ordered_tasks(board: Any) -> list[dict[str, Any]]:
    """Sutun sirasi (yapilacak -> yapiliyor -> yapildi), icinde kart sirasi."""
    ordered: list[dict[str, Any]] = []
    for column in board.columns:
        ordered.extend(column["tasks"])
    return ordered


def _write_task_cell(target: Any, value: Any, kind: str, tz: tzinfo | None) -> int:
    text = "" if value is None else teamslink.temizle(str(value))
    if kind == KIND_NUMBER:
        number = _as_int_or_float(value)
        if number is not None:
            target.value = number
            return len(str(number))
    elif kind == KIND_DECIMAL:
        number = _as_decimal(value)
        if number is not None:
            target.value = number
            return len(str(number))
    elif kind == KIND_DATE:
        moment = field_utils.parse_moment(text)
        if moment is not None:
            target.value = moment.date()
            target.number_format = EXCEL_DATE_FORMAT
            return len(EXCEL_DATE_FORMAT)
    elif kind == KIND_DATETIME:
        moment = field_utils.parse_moment(text)
        if moment is not None:
            local = moment.astimezone(tz) if tz is not None else moment.astimezone()
            target.value = local.replace(tzinfo=None)
            target.number_format = EXCEL_DATETIME_FORMAT
            return len(EXCEL_DATETIME_FORMAT)
    if not text:
        return 0
    target.value = text
    return _display_width(text)


# --- Sefer: XP defteri --------------------------------------------------

LEDGER_SHEET = "XP defteri"
LEDGER_NAME = "Sefer-XP-defteri"

LEDGER_HEADERS = (
    "Tarih",
    "Kaynak",
    "Tür",
    "Puan",
    "Açıklama",
    "Referans",
)

# Puan gercek sayi hucresi olur ki Excel'de toplanabilsin.
LEDGER_KINDS = {0: KIND_DATETIME, 3: KIND_NUMBER}


def build_ledger_workbook(
    context: Any,
    source: str = "",
    tz: tzinfo | None = None,
) -> bytes:
    """Seferin XP defterini tek sayfalik .xlsx olarak uretir.

    Ekrandaki liste son 50 satiri gosterir; dokum defterin TAMAMINI yazar:
    "ne icin puan aldim" sorusunun tam cevabi burada durur.
    """
    from . import gamify
    from . import gamify_repo as gamify_store

    events = gamify.ledger(context, source, limit=gamify_store.LEDGER_MAX)

    book = Workbook()
    sheet = book.active
    sheet.title = sheet_title(LEDGER_SHEET)
    sheet.append(list(LEDGER_HEADERS))
    for cell in sheet[1]:
        cell.font = Font(bold=True)

    widths = [len(text) for text in LEDGER_HEADERS]
    for line, event in enumerate(events, start=2):
        values = [
            event["at"],
            event.get("source_label") or event["source"],
            gamify.RULE_LABELS.get(event["kind"], event["kind"]),
            event["points"],
            event["title"],
            event["ref"],
        ]
        for index, value in enumerate(values):
            target = sheet.cell(row=line, column=index + 1)
            width = _write_task_cell(target, value, LEDGER_KINDS.get(index, KIND_TEXT), tz)
            if width > widths[index]:
                widths[index] = width

    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:{get_column_letter(len(LEDGER_HEADERS))}{len(events) + 1}"
    for index, width in enumerate(widths):
        letter = get_column_letter(index + 1)
        sheet.column_dimensions[letter].width = min(
            max(width + 2, COLUMN_WIDTH_MIN), COLUMN_WIDTH_LIMIT
        )

    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _fit_columns(sheet: Worksheet, widths: list[int]) -> None:
    for index, width in enumerate(widths):
        letter = get_column_letter(index + 1)
        sheet.column_dimensions[letter].width = min(
            max(width + 2, COLUMN_WIDTH_MIN), COLUMN_WIDTH_LIMIT
        )


# --- adlandirma ---------------------------------------------------------


def sheet_title(name: str) -> str:
    """Excel sayfa adi: yasak karakter yok, 31 karakteri gecmez, bos kalmaz."""
    text = _FORBIDDEN_SHEET.sub(" ", str(name or ""))
    text = _SPACES.sub(" ", text).strip().strip("'")
    text = text[:SHEET_NAME_LIMIT].strip()
    return text or FALLBACK_SHEET


def file_stem(name: str, when: date | None = None) -> str:
    """Dosya adinin govdesi: <grup-adi>-<YYYY-AA-GG>."""
    text = _FORBIDDEN_FILE.sub("", str(name or ""))
    text = _SPACES.sub("-", text.strip())
    text = _DASHES.sub("-", text).strip("-.")
    stamp = (when or date.today()).isoformat()
    return f"{text or 'grup'}-{stamp}"


def ascii_stem(stem: str) -> str:
    """Eski istemciler icin ASCII yedek ad."""
    text = _NON_ASCII_NAME.sub("-", stem.translate(_ASCII_MAP))
    text = _DASHES.sub("-", text).strip("-.")
    return text or "holocron"


def content_disposition(name: str, when: date | None = None) -> str:
    """RFC 5987: UTF-8 ad + ASCII yedek."""
    stem = file_stem(name, when)
    return (
        f'attachment; filename="{ascii_stem(stem)}.xlsx"; '
        f"filename*=UTF-8''{quote(stem + '.xlsx')}"
    )


def pick_rows(
    rows: list[dict[str, Any]], keys: Sequence[str] | None
) -> list[dict[str, Any]]:
    """Secilen anahtarlarla suzer; sira grid'den gelen siradir."""
    if keys is None:
        return rows
    wanted = {str(key).strip().upper() for key in keys if str(key).strip()}
    return [row for row in rows if str(row["key"]).upper() in wanted]


def parse_columns(text: str | None) -> list[str] | None:
    """'a,b,c' -> ['a','b','c']; bos ise None (grubun secili sutunlari)."""
    if not text:
        return None
    chosen = [part.strip() for part in str(text).split(",") if part.strip()]
    return chosen or None


def truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "evet", "yes", "on")
