"""Unit tests for the STT benchmark helpers (issue #22 tooling)."""

from scripts.run_stt_benchmark import _error_rate, _normalise, _summary


def test_normalise_strips_unicode_punctuation_and_folds_yo() -> None:
    assert _normalise("¿Qué tal, Luna? ¡Hola!") == "que tal luna hola"
    assert _normalise("Тёплый — день…") == "теплый день"


def test_error_rate_ignores_punctuation_and_yo() -> None:
    assert _error_rate("¿Qué tal?", "que tal") == 0.0
    assert _error_rate("Тёплый день", "теплый день") == 0.0
    assert _error_rate("два слова", "одно") > 0.0


def test_summary_reports_mean_median_and_p95() -> None:
    result = _summary([1.0, 2.0, 3.0, 4.0])
    assert result is not None
    assert result["mean"] == 2.5
    assert result["median"] == 2.5
    assert result["p95_nearest_rank"] == 4.0
    assert _summary([]) is None
