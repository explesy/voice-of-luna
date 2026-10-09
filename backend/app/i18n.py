"""UI localization dictionary and helper functions for Voice of Luna."""

from __future__ import annotations

UI_TEXT: dict[str, dict[str, str]] = {
    "ru": {
        "title": "Voice of Lúna — Local Codex Voice Terminal",
        "lang_chip_title": "Язык общения и синтеза",
        "lang_aria_label": "Выбор языка",
        "model_chip_title": "Выбор модели Codex",
        "model_aria_label": "Выбор модели",
        "effort_chip_title": "Уровень reasoning (размышлений модели)",
        "effort_aria_label": "Уровень reasoning",
        "voice_chip_title": "Выбор языка и голоса",
        "voice_aria_label": "Выбор голоса",
        "optgroup_edge": "Нейросеть (Edge Cloud · Бесплатно)",
        "optgroup_piper": "Локальная нейросеть (Piper ONNX · Offline)",
        "optgroup_silero": "Локальная нейросеть (Silero · Offline)",
        "optgroup_macos": "Локальные macOS (Offline)",
        "optgroup_russian": "Русский (Russian)",
        "optgroup_other": "Другие языки (Other)",
        "optgroup_deprecated": "Устаревшие (Deprecated)",
        "stt_chip_title": "Модель распознавания речи для завершённой записи",
        "stt_aria_label": "Выбор модели распознавания речи",
        "stt_auto": "AUTO",
        "legacy_toggle_title": "Показать/скрыть устаревшие голоса",
        "multilingual_filter_title": "Только голоса с нативной сменой языка",
        "multilingual_empty": "Нет голосов с нативной сменой языка",
        "collapse_other": "▲ Скрыть другие языки",
        "expand_other": "▶ Другие языки",
        "vad_chip_title": "Автоматическое определение конца речи [V]",
        "warmup_chip_title": "Технический прогрев Codex расходует одну короткую реплику квоты на новый диалог",
        "record_shortcut": "record",
        "stop_shortcut": "stop speech",
        "exec_shortcut": "exec",
    },
    "en": {
        "title": "Voice of Lúna — Local Codex Voice Terminal",
        "lang_chip_title": "Spoken and synthesis language",
        "lang_aria_label": "Language Selection",
        "model_chip_title": "Select Codex Model",
        "model_aria_label": "Model Selection",
        "effort_chip_title": "Reasoning effort level",
        "effort_aria_label": "Reasoning effort",
        "voice_chip_title": "Select voice and language",
        "voice_aria_label": "Voice Selection",
        "optgroup_edge": "Neural (Edge Cloud · Free)",
        "optgroup_piper": "Local Neural (Piper ONNX · Offline)",
        "optgroup_silero": "Local Neural (Silero · Offline)",
        "optgroup_macos": "Local macOS (Offline)",
        "optgroup_russian": "Russian (Русский)",
        "optgroup_other": "Other Languages",
        "optgroup_deprecated": "Deprecated",
        "stt_chip_title": "Speech recognition model for the completed recording",
        "stt_aria_label": "Speech recognition model selection",
        "stt_auto": "AUTO",
        "legacy_toggle_title": "Show or hide deprecated voices",
        "multilingual_filter_title": "Show only voices with native code-switching",
        "multilingual_empty": "No voices with native code-switching",
        "collapse_other": "▲ Hide other languages",
        "expand_other": "▶ Other languages",
        "vad_chip_title": "Voice Activity Detection (auto-send) [V]",
        "warmup_chip_title": "Codex technical warmup uses 1 short quota turn on new session",
        "record_shortcut": "record",
        "stop_shortcut": "stop speech",
        "exec_shortcut": "exec",
    },
}


def get_ui_text(locale: str = "ru-RU") -> dict[str, str]:
    """Return UI translation dictionary for the requested locale."""
    norm = (locale or "").lower().replace("_", "-")
    lang = "en" if norm.startswith("en") else "ru"
    return UI_TEXT[lang]
