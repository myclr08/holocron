"""Yerel alan degerleri: tip dogrulama, normalizasyon, bicimlendirme, siralama.

Saf katman (app.fields): veritabani yok, saat yok.
"""

from __future__ import annotations

import pytest

from app import fields as field_utils
from app.fields import (
    LOCAL_TYPES,
    LocalValueError,
    derived_sort_key,
    format_derived_value,
    format_local_value,
    local_sort_key,
    normalize_local_value,
    normalize_options,
)

SECENEKLER = ["bekliyor", "musteriye soruldu", "bitti"]


def norm(kind, value, options=None):
    return normalize_local_value(kind, value, options)


# --- ortak davranis -----------------------------------------------------


def test_every_type_treats_none_and_blank_as_delete():
    for kind in LOCAL_TYPES:
        assert norm(kind, None, SECENEKLER) == ""
        assert norm(kind, "", SECENEKLER) == ""
        assert norm(kind, "   ", SECENEKLER) == ""


def test_unknown_type_is_refused():
    with pytest.raises(LocalValueError):
        norm("sihirli", "deger")


def test_empty_stored_value_formats_as_empty():
    for kind in LOCAL_TYPES:
        assert format_local_value(kind, "") == ""
        assert format_local_value(kind, None) == ""


# --- metin --------------------------------------------------------------


def test_text_keeps_content_and_trims_edges():
    assert norm("text", "  musteriye soruldu  ") == "musteriye soruldu"
    assert norm("text", "ilk satir\nikinci satir") == "ilk satir\nikinci satir"
    assert format_local_value("text", "İstanbul") == "İstanbul"


# --- sayi ---------------------------------------------------------------


@pytest.mark.parametrize(
    "girdi, beklenen",
    [
        ("5", "5"),
        ("5.0", "5"),
        ("5,5", "5.5"),       # Turkce klavye ondalik virgulu
        ("3.50", "3.5"),
        ("-2.25", "-2.25"),
        ("100", "100"),       # normalize() 1E+2 uretmemeli
        ("0", "0"),
        ("0.0", "0"),
        (" 12 ", "12"),
        (7, "7"),
        (7.5, "7.5"),
    ],
)
def test_number_is_stored_as_plain_dot_decimal(girdi, beklenen):
    assert norm("number", girdi) == beklenen


@pytest.mark.parametrize("girdi", ["salatalik", "1.2.3", "5 kg", "--3", "nan", "inf"])
def test_number_rejects_nonsense(girdi):
    with pytest.raises(LocalValueError) as excinfo:
        norm("number", girdi)
    assert "Sayı bekleniyor" in str(excinfo.value)


def test_number_sorts_numerically_not_as_text():
    order = sorted(["13", "5", "100"], key=lambda value: local_sort_key("number", value))
    assert order == ["5", "13", "100"]


# --- tarih --------------------------------------------------------------


@pytest.mark.parametrize(
    "girdi, beklenen",
    [
        ("2026-09-11", "2026-09-11"),
        ("11.09.2026", "2026-09-11"),
        ("1.9.2026", "2026-09-01"),
        ("11/09/2026", "2026-09-11"),
    ],
)
def test_date_is_stored_as_iso(girdi, beklenen):
    assert norm("date", girdi) == beklenen


@pytest.mark.parametrize("girdi", ["yarin", "2026-13-01", "32.09.2026", "11.09.26", "2026/09/11"])
def test_date_rejects_nonsense(girdi):
    with pytest.raises(LocalValueError):
        norm("date", girdi)


def test_date_is_shown_in_turkish_order():
    assert format_local_value("date", "2026-09-11") == "11.09.2026"


def test_date_sorts_chronologically():
    order = sorted(
        ["2026-12-01", "2026-01-05", "2026-09-11"],
        key=lambda value: local_sort_key("date", value),
    )
    assert order == ["2026-01-05", "2026-09-11", "2026-12-01"]


# --- evet/hayir ---------------------------------------------------------


@pytest.mark.parametrize("girdi", [True, 1, "1", "evet", "EVET", "true", "Yes", "on"])
def test_bool_true_forms(girdi):
    assert norm("bool", girdi) == "1"


@pytest.mark.parametrize("girdi", [False, 0, "0", "hayir", "HAYIR", "false", "No", "off"])
def test_bool_false_forms(girdi):
    assert norm("bool", girdi) == "0"


def test_bool_rejects_other_words():
    with pytest.raises(LocalValueError) as excinfo:
        norm("bool", "belki")
    assert "Evet/Hayır" in str(excinfo.value)


def test_bool_is_shown_in_turkish():
    assert format_local_value("bool", "1") == "Evet"
    assert format_local_value("bool", "0") == "Hayır"


def test_bool_sorts_false_before_true():
    order = sorted(["1", "0", "1"], key=lambda value: local_sort_key("bool", value))
    assert order == ["0", "1", "1"]


# --- liste --------------------------------------------------------------


def test_select_accepts_only_known_options():
    assert norm("select", "bitti", SECENEKLER) == "bitti"
    with pytest.raises(LocalValueError) as excinfo:
        norm("select", "salatalik", SECENEKLER)
    assert "seçenekler arasında yok" in str(excinfo.value)


def test_select_matches_case_insensitively_and_returns_canonical_text():
    assert norm("select", "BEKLIYOR", SECENEKLER) == "bekliyor"
    assert norm("select", "Bitti", SECENEKLER) == "bitti"


def test_select_without_options_is_refused():
    with pytest.raises(LocalValueError):
        norm("select", "bitti", [])


def test_options_are_cleaned_and_deduplicated():
    assert normalize_options("select", [" bekliyor ", "bekliyor", "", "bitti"]) == [
        "bekliyor",
        "bitti",
    ]
    assert normalize_options("select", "bir\n\niki\n") == ["bir", "iki"]


def test_options_are_ignored_for_other_types():
    assert normalize_options("text", ["a", "b"]) == []


def test_select_needs_at_least_one_option():
    with pytest.raises(LocalValueError):
        normalize_options("select", [])


# --- bos deger siralamada sona duser ------------------------------------


def test_blank_values_sort_last():
    order = sorted(["", "abc", ""], key=lambda value: local_sort_key("text", value))
    assert order == ["abc", "", ""]


# --- turetilmis sutunlar ------------------------------------------------


def test_change_count_is_rendered_as_number():
    assert format_derived_value(field_utils.DERIVED_CHANGES, 3) == "3"
    assert format_derived_value(field_utils.DERIVED_CHANGES, 0) == "0"
    assert format_derived_value(field_utils.DERIVED_CHANGES, None) == "0"


def test_last_change_is_rendered_as_datetime_or_blank():
    text = format_derived_value(
        field_utils.DERIVED_CHANGED_AT, "2026-09-11T09:05:00+00:00", tz=field_utils.timezone.utc
    )
    assert text == "11.09.2026 09:05"
    assert format_derived_value(field_utils.DERIVED_CHANGED_AT, None) == ""


def test_derived_sorting():
    counts = sorted([3, 10, 1], key=lambda value: derived_sort_key("changes", value))
    assert counts == [1, 3, 10]
    moments = sorted(
        ["2026-09-11T09:05:00+00:00", None, "2026-01-01T00:00:00+00:00"],
        key=lambda value: derived_sort_key("changed_at", value),
    )
    assert moments == ["2026-01-01T00:00:00+00:00", "2026-09-11T09:05:00+00:00", None]
