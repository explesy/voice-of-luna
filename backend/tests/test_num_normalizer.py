"""Unit tests for ultra-fast Russian number normalization for TTS."""

from __future__ import annotations

import time
import pytest

from app.num_normalizer import normalize_numbers_for_speech


def test_user_news_case() -> None:
    text = (
        "Сегодня, 8 сентября 2026 года, одна из главных новостей — "
        "ранены 73 человека, а на рынках это уже толкнуло вверх цены на нефть."
    )
    result = normalize_numbers_for_speech(text)
    assert "восьмого сентября две тысячи двадцать шестого года" in result
    assert "семьдесят три человека" in result
    assert "8" not in result
    assert "2026" not in result
    assert "73" not in result


def test_dates_and_years_inflection() -> None:
    assert "первого января" in normalize_numbers_for_speech("1 января")
    assert "тридцать первого декабря" in normalize_numbers_for_speech("31 декабря")
    assert "две тысячи двадцать шестого года" in normalize_numbers_for_speech("2026 года")
    assert "две тысячи двадцать шестом году" in normalize_numbers_for_speech("в 2026 году")
    assert "две тысячи двадцать шестой год" in normalize_numbers_for_speech("2026 год")
    assert "девяностых" in normalize_numbers_for_speech("в 90-х")


def test_full_date_format() -> None:
    res = normalize_numbers_for_speech("Дата события: 08.09.2026.")
    assert "восьмое сентября две тысячи двадцать шестого года" in res


def test_ordinals_hyphens() -> None:
    assert "первый" in normalize_numbers_for_speech("1-й выпуск")
    assert "вторая" in normalize_numbers_for_speech("2-я часть")
    assert "третье" in normalize_numbers_for_speech("3-е место")
    assert "пятого" in normalize_numbers_for_speech("до 5-го числа")


def test_time_formats() -> None:
    assert "четырнадцать тридцать" in normalize_numbers_for_speech("встреча в 14:30")
    assert "восемь ноль ноль" in normalize_numbers_for_speech("в 08:00 утра")


def test_percentages() -> None:
    assert "один процент" in normalize_numbers_for_speech("рост на 1%")
    assert "четыре процента" in normalize_numbers_for_speech("около 4%")
    assert "семьдесят три процента" in normalize_numbers_for_speech("составляет 73%")
    assert "сто процентов" in normalize_numbers_for_speech("все 100%")


def test_currencies() -> None:
    assert "сто долларов" in normalize_numbers_for_speech("цена $100")
    assert "пятьдесят евро" in normalize_numbers_for_speech("стоимость 50€")
    assert "тысяча пятьсот рублей" in normalize_numbers_for_speech("1500 руб")


def test_decimals() -> None:
    assert "три целых пять десятых" in normalize_numbers_for_speech("коэффициент 3.5")
    assert "ноль целых пятнадцать сотых" in normalize_numbers_for_speech("составил 0,15")


def test_roman_centuries() -> None:
    assert "двадцать первый век" in normalize_numbers_for_speech("наступил XXI век")
    assert "двадцатого века" in normalize_numbers_for_speech("события XX века")
    assert "девятнадцатом веке" in normalize_numbers_for_speech("жили в XIX веке")


def test_performance_regression_guard() -> None:
    sample = (
        "Сегодня, 8 сентября 2026 года, ранены 73 человека, инфляция 4%, "
        "в 14:30 цена была $100 или 3.5 тысячи рублей в XXI веке."
    )
    # Warmup
    _ = normalize_numbers_for_speech(sample)

    t0 = time.perf_counter()
    iterations = 1000
    for _ in range(iterations):
        _ = normalize_numbers_for_speech(sample)
    t1 = time.perf_counter()

    avg_ms = ((t1 - t0) / iterations) * 1000
    print(f"Performance: {avg_ms:.4f} ms per paragraph")
    # Keep a generous guard for shared CI runners; detailed benchmarks belong
    # in the benchmark suite rather than a correctness test.
    assert avg_ms < 5.0


def test_empty_and_no_digit_text() -> None:
    assert normalize_numbers_for_speech("") == ""
    text_plain = "Привет, мир! Никаких цифр здесь нет."
    assert normalize_numbers_for_speech(text_plain) == text_plain
