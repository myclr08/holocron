"""Alan formatlayici: her tip icin deterministik cikti."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.fields import (
    GRID_TEXT_LIMIT,
    changed_field_ids,
    fold,
    format_field_value,
    issue_value,
    sort_key,
    truncate,
)

ISTANBUL = timezone(timedelta(hours=3))


def fmt(kind, value, **kwargs):
    return format_field_value({"type": kind}, value, **kwargs)


def test_null_is_empty_for_every_type():
    for kind in ("string", "user", "status", "array", "date", "datetime", "number", "sprint"):
        assert fmt(kind, None) == ""


def test_user_uses_display_name():
    assert fmt("user", {"displayName": "Demo Kullanici", "name": "demo"}) == "Demo Kullanici"
    # displayName yoksa elde ne varsa.
    assert fmt("user", {"name": "demo"}) == "demo"


@pytest.mark.parametrize(
    "kind",
    ["status", "priority", "issuetype", "resolution", "project", "version", "component", "option"],
)
def test_named_objects_use_name(kind):
    assert fmt(kind, {"name": "Acik", "id": "1"}) == "Acik"


def test_option_falls_back_to_value():
    assert fmt("option", {"value": "Birinci secenek"}) == "Birinci secenek"


def test_array_joins_items_with_comma():
    schema = {"type": "array", "items": "component"}
    value = [{"name": "API"}, {"name": "Arayuz"}]
    assert format_field_value(schema, value) == "API, Arayuz"


def test_array_of_strings():
    assert format_field_value({"type": "array", "items": "string"}, ["a", "b"]) == "a, b"


def test_date_is_turkish_order():
    assert fmt("date", "2026-09-11") == "11.09.2026"


def test_datetime_converts_to_given_timezone():
    assert fmt("datetime", "2026-09-11T10:30:00.000+0000", tz=ISTANBUL) == "11.09.2026 13:30"
    # Z sonekli bicim de ayni sonucu verir.
    assert fmt("datetime", "2026-09-11T10:30:00Z", tz=ISTANBUL) == "11.09.2026 13:30"
    # Kaynak zaten +03:00 ise ceviri kimlik olur.
    assert fmt("datetime", "2026-09-11T13:30:00.000+0300", tz=ISTANBUL) == "11.09.2026 13:30"


def test_datetime_object_is_accepted():
    moment = datetime(2026, 9, 11, 10, 30, tzinfo=timezone.utc)
    assert fmt("datetime", moment, tz=ISTANBUL) == "11.09.2026 13:30"


def test_number_has_no_thousand_separator():
    assert fmt("number", 1234567) == "1234567"
    assert fmt("number", 1234.5) == "1234.5"
    assert fmt("number", 12.0) == "12"
    assert fmt("number", "8") == "8"


def test_string_passes_through_and_strips_wiki_noise():
    assert fmt("string", "  Duz metin  ") == "Duz metin"
    assert fmt("string", "{color:red}Kirmizi{color}") == "Kirmizi"


def test_adf_document_becomes_plain_text():
    document = {
        "type": "doc",
        "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "Birinci satir"}]},
            {"type": "paragraph", "content": [{"type": "text", "text": "Ikinci satir"}]},
        ],
    }
    assert fmt("string", document) == "Birinci satir\nIkinci satir"


def test_sprint_like_custom_field_uses_name():
    assert fmt("any", {"name": "Sprint 7", "state": "active"}) == "Sprint 7"


def test_unknown_object_without_name_falls_back_to_short_json():
    text = format_field_value(None, {"beklenmeyen": 1})
    assert text.startswith("{") and "beklenmeyen" in text


def test_truncate_keeps_limit():
    long_text = "x" * (GRID_TEXT_LIMIT + 50)
    cut = truncate(long_text)
    assert len(cut) == GRID_TEXT_LIMIT
    assert cut.endswith("…")
    assert truncate("kisa") == "kisa"


def test_issue_value_reads_root_and_fields():
    record = {"key": "DEMO-1", "id": "10", "fields": {"summary": "Ozet"}}
    assert issue_value(record, "issuekey") == "DEMO-1"
    assert issue_value(record, "id") == "10"
    assert issue_value(record, "summary") == "Ozet"
    assert issue_value(record, "yok") is None


def test_sort_key_orders_numbers_numerically():
    values = [10, 2, 1]
    assert sorted(values, key=lambda v: sort_key({"type": "number"}, v)) == [1, 2, 10]


def test_sort_key_puts_empty_last():
    schema = {"type": "string"}
    values = ["b", None, "a"]
    assert sorted(values, key=lambda v: sort_key(schema, v)) == ["a", "b", None]


def test_sort_key_orders_dates_chronologically():
    schema = {"type": "datetime"}
    values = ["2026-09-11T10:00:00+0000", "2026-01-02T10:00:00+0000"]
    assert sorted(values, key=lambda v: sort_key(schema, v))[0].startswith("2026-01")


def test_fold_handles_turkish_dotted_i():
    assert fold("İSTANBUL") == fold("istanbul") == fold("Istanbul") == "istanbul"
    assert fold("IŞIK") == fold("ışık")
    # Nokta disindaki harfler korunur: "ş" ile "s" ayni sayilmaz.
    assert fold("ışık") != fold("isik")


def test_changed_field_ids_lists_only_differences():
    old = {"fields": {"summary": "Eski", "status": {"name": "Acik"}}}
    new = {"fields": {"summary": "Yeni", "status": {"name": "Acik"}}}
    assert changed_field_ids(old, new) == ["summary"]
    assert changed_field_ids(None, new) == []


def test_changed_field_ids_ignores_key_order_in_objects():
    old = {"fields": {"status": {"id": "1", "name": "Acik"}}}
    new = {"fields": {"status": {"name": "Acik", "id": "1"}}}
    assert changed_field_ids(old, new) == []
