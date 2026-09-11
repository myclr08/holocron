"""Grid satirlarinin kurulmasi.

Ekran ucu (`/api/groups/{id}/issues`) ve Excel disa aktarimi ayni satirlari
kullanir; bu yuzden satir kurma, suzme ve siralama tek yerde durur. Burada SQL
yok: veri `repository` uzerinden okunur, bicimlendirme `fields` icinde yapilir.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import fields as field_utils, repository


class LocalView:
    """Bir istek boyunca yerel alan/deger/gecmis okumalarini bir arada tutar."""

    def __init__(
        self,
        fields: dict[int, dict[str, Any]],
        values: dict[str, dict[int, dict[str, Any]]],
        stats: dict[str, dict[int, dict[str, Any]]],
    ) -> None:
        self.fields = fields
        self.values = values
        self.stats = stats

    def column(self, column: str) -> tuple[dict[str, Any], str] | None:
        """Sutun kimligi yerel bir alana isaret ediyorsa (alan, turetilmis-ek)."""
        parsed = repository.parse_local_column(column)
        if parsed is None:
            return None
        field = self.fields.get(parsed[0])
        return (field, parsed[1]) if field else None

    def stat(self, key: str, field_id: int) -> dict[str, Any]:
        return self.stats.get(key, {}).get(field_id, {})

    def value(self, key: str, field_id: int) -> str:
        return self.values.get(key, {}).get(field_id, {}).get("value", "")

    def raw_for(self, key: str, field: dict[str, Any], suffix: str) -> Any:
        if not suffix:
            return self.value(key, field["id"])
        stat = self.stat(key, field["id"])
        if suffix == field_utils.DERIVED_CHANGES:
            return int(stat.get("changes", 0))
        return stat.get("changed_at")

    def cell(self, key: str, column: str, field: dict[str, Any], suffix: str) -> dict[str, Any]:
        raw = self.raw_for(key, field, suffix)
        if suffix:
            return {
                "field": column,
                "text": field_utils.format_derived_value(suffix, raw),
                "raw": raw,
                "derived": suffix,
            }
        stat = self.stat(key, field["id"])
        cell: dict[str, Any] = {
            "field": column,
            "text": field_utils.format_local_value(field["type"], raw),
            "raw": raw,
            "editable": True,
            "changes": int(stat.get("changes", 0)),
            "changed_at": stat.get("changed_at"),
        }
        return cell

    def sort_key(self, key: str, field: dict[str, Any], suffix: str) -> tuple[int, float, str]:
        raw = self.raw_for(key, field, suffix)
        if suffix:
            return field_utils.derived_sort_key(suffix, raw)
        return field_utils.local_sort_key(field["type"], raw)

    def search_text(self, key: str) -> str:
        """Aramanin kapsadigi yerel metin: sutun secilmemis olsa da gorunur."""
        parts = []
        for field_id, entry in (self.values.get(key) or {}).items():
            field = self.fields.get(field_id)
            if field is None:
                continue
            parts.append(field_utils.format_local_value(field["type"], entry.get("value")))
        return " ".join(part for part in parts if part)


@dataclass
class GridData:
    """Bir grubun ekranda/Excel'de gorunen hali."""

    group: dict[str, Any]
    columns: list[str]
    heads: list[dict[str, Any]]
    schemas: dict[str, dict[str, Any]]
    local_view: LocalView
    rows: list[dict[str, Any]]
    total: int
    sort_field: str
    sort_dir: str
    base_url: str


def build_grid(
    context: Any,
    group_id: int,
    columns: list[str] | None = None,
    q: str = "",
    sort: str = "",
    direction: str = "",
) -> GridData:
    """Grubun satirlarini kurar: sutunlar, suzgec ve siralama uygulanmis halde.

    Metin kirpmasi (`GRID_TEXT_LIMIT`) burada YAPILMAZ; ekran ucu kendi kirpar,
    Excel tam metni yazar.
    """
    conn = context.connection()
    group = repository.require_group(conn, group_id)
    chosen = (
        [str(column) for column in columns]
        if columns is not None
        else repository.group_columns(conn, group)
    )
    schemas = repository.field_schemas(conn)

    items = repository.list_items(conn, group_id)
    keys = [item["key"] for item in items]
    stored = repository.get_issues(conn, keys)
    local_view = LocalView(
        repository.local_fields_by_id(conn),
        repository.local_values_for(conn, keys),
        repository.history_stats(conn, keys),
    )

    rows = [build_row(item, stored.get(item["key"]), chosen, schemas, local_view) for item in items]
    total = len(rows)

    needle = field_utils.fold(q.strip()) if q else ""
    if needle:
        rows = [row for row in rows if matches(row, needle)]

    sort_field, sort_dir = sort_choice(group, sort, direction)
    if sort_field:
        rows = sort_rows(rows, sort_field, sort_dir, schemas, local_view)

    base_url = (context.settings.get("jira.base_url", "") or "").rstrip("/")
    for row in rows:
        row["url"] = f"{base_url}/browse/{row['key']}" if base_url else ""

    return GridData(
        group=group,
        columns=chosen,
        heads=[column_head(column, schemas, local_view) for column in chosen],
        schemas=schemas,
        local_view=local_view,
        rows=rows,
        total=total,
        sort_field=sort_field,
        sort_dir=sort_dir,
        base_url=base_url,
    )


def column_head(
    column: str, schemas: dict[str, dict[str, Any]], local_view: LocalView
) -> dict[str, Any]:
    head: dict[str, Any] = {"id": column, "name": repository.field_name(schemas, column)}
    found = local_view.column(column)
    if found is None:
        return head
    field, suffix = found
    head["local"] = field
    head["derived"] = suffix
    head["editable"] = not suffix
    return head


def build_row(
    item: dict[str, Any],
    record: dict[str, Any] | None,
    columns: list[str],
    schemas: dict[str, dict[str, Any]],
    local_view: LocalView,
) -> dict[str, Any]:
    raw = record["raw"] if record else {"key": item["key"], "fields": {}}
    cells = []
    for column in columns:
        found = local_view.column(column)
        if found is not None:
            cells.append(local_view.cell(item["key"], column, found[0], found[1]))
            continue
        value = field_utils.issue_value(raw, column)
        cells.append(
            {
                "field": column,
                "text": field_utils.format_field_value(schemas.get(column), value),
                "raw": jsonable(value),
            }
        )
    return {
        "key": item["key"],
        "pinned": item["pinned"],
        "missing": record is None,
        "fetched_at": record["fetched_at"] if record else None,
        "cells": cells,
        "_raw": raw,
        "_local_text": local_view.search_text(item["key"]),
    }


def matches(row: dict[str, Any], needle: str) -> bool:
    if needle in field_utils.fold(row["key"]):
        return True
    if any(needle in field_utils.fold(cell["text"]) for cell in row["cells"]):
        return True
    # Yerel degerler sutun secili olmasa da aranir: kullanicinin kendi notu.
    return needle in field_utils.fold(row.get("_local_text", ""))


def sort_choice(group: dict[str, Any], sort: str, direction: str) -> tuple[str, str]:
    field_id = (sort or "").strip()
    chosen_dir = (direction or "").strip().lower()
    if not field_id:
        group_sort = group.get("sort") or {}
        field_id = str(group_sort.get("field") or "").strip()
        chosen_dir = chosen_dir or str(group_sort.get("dir") or "").lower()
    if chosen_dir not in repository.SORT_DIRECTIONS:
        chosen_dir = "asc"
    return field_id, chosen_dir


def sort_rows(
    rows: list[dict[str, Any]],
    field_id: str,
    direction: str,
    schemas: dict[str, dict[str, Any]],
    local_view: LocalView,
) -> list[dict[str, Any]]:
    schema = schemas.get(field_id)
    local = local_view.column(field_id)

    def key(row: dict[str, Any]) -> tuple[Any, ...]:
        if local is not None:
            base = local_view.sort_key(row["key"], local[0], local[1])
        else:
            value = field_utils.issue_value(row["_raw"], field_id)
            base = field_utils.sort_key(schema, value)
        return (*base, field_utils.fold(row["key"]))

    return sorted(rows, key=key, reverse=direction == "desc")


def jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool, list, dict)):
        return value
    return str(value)
