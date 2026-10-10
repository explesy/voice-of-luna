"""Local macOS speech synthesis for replies written in Cyrillic."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
import tempfile
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

from app import model_catalog
from app.num_normalizer import normalize_numbers_for_speech

logger = logging.getLogger("voice_of_luna.speak")


class LocalSpeechError(RuntimeError):
    """Raised when a local response cannot be rendered to audio."""


def _terminate_process(process: object) -> None:
    """Best-effort kill for a subprocess abandoned by cancellation.

    ``kill`` is absent on test doubles, so it is looked up defensively; a
    cancelled ``say`` render must not keep writing its output file after the
    caller has cleaned it up.
    """

    kill = getattr(process, "kill", None)
    if callable(kill):
        try:
            kill()
        except Exception:
            pass


@dataclass(frozen=True)
class SpeechSynthesisResult:
    """Audio artifact together with the engine that actually produced it."""

    path: Path
    requested_engine: str
    actual_engine: str
    fallback_reason: str | None = None
    actual_voice: str | None = None


@dataclass
class VoiceInfo:
    name: str
    locale: str
    sample: str
    is_russian: bool = False
    is_enhanced: bool = False
    engine: str = "macos"
    is_downloaded: bool = True
    model_id: str | None = None
    size_mb: float = 0.0
    languages: tuple[str, ...] = ()
    multilingual: bool = False
    code_switching: str = model_catalog.CODE_SWITCHING_NONE
    catalog_status: str = model_catalog.CATALOG_STATUS_RECOMMENDED
    deprecation_reason: str = ""
    user_defined: bool = False

    @property
    def is_deprecated(self) -> bool:
        return model_catalog.is_deprecated(self.catalog_status)

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


EDGE_VOICES: dict[str, str] = {
    "Svetlana (Neural · Edge)": "ru-RU-SvetlanaNeural",
    "Dmitry (Neural · Edge)": "ru-RU-DmitryNeural",
    "Jenny (Neural · Edge)": "en-US-JennyNeural",
    "Guy (Neural · Edge)": "en-US-GuyNeural",
    "Aria (Neural · Edge)": "en-US-AriaNeural",
    "Elvira (Neural · Edge)": "es-ES-ElviraNeural",
    "Alvaro (Neural · Edge)": "es-ES-AlvaroNeural",
}

SILERO_VOICES: dict[str, str] = {
    "Ksenia (Silero Neural · Offline)": "xenia",
    "Baya (Silero Neural · Offline)": "baya",
    "Aidar (Silero Neural · Offline)": "aidar",
    "Eugene (Silero Neural · Offline)": "eugene",
    "Raya (Silero Neural · Offline)": "raya",
    "Ksenia v5 (Silero Neural · Offline)": "xenia",
    "Baya v5 (Silero Neural · Offline)": "baya",
    "Aidar v5 (Silero Neural · Offline)": "aidar",
    "Eugene v5 (Silero Neural · Offline)": "eugene",
    "Raya v5 (Silero Neural · Offline)": "raya",
}

PIPER_VOICES: dict[str, str] = {
    "Dmitri (Piper Neural · Offline)": "ru_RU-dmitri-medium",
    "Irina (Piper Neural · Offline)": "ru_RU-irina-medium",
    "Denis (Piper Neural · Offline)": "ru_RU-denis-medium",
    "Ruslan (Piper Neural · Offline)": "ru_RU-ruslan-medium",
    "Lessac (Piper Neural · Offline)": "en_US-lessac-medium",
}


def is_silero_available() -> bool:
    """Check if PyTorch (torch) and SciPy (scipy) are installed for Silero offline neural voices."""
    import importlib.util
    return (
        importlib.util.find_spec("torch") is not None
        and importlib.util.find_spec("scipy") is not None
    )


def registered_voice_model(voice_name: str):
    """Return the catalog model that owns an exact voice name, if any.

    Registry lookup takes precedence over heuristic name matching so a custom
    voice whose name contains e.g. "Neural" is not misclassified as Edge.
    """

    if not voice_name:
        return None
    from app.tts_manager import MODEL_CATALOG

    for model in MODEL_CATALOG.values():
        if voice_name in model.voices:
            return model
    folded = voice_name.casefold()
    for model in MODEL_CATALOG.values():
        if any(voice.casefold() == folded for voice in model.voices):
            return model
    return None


def is_edge_voice(voice_name: str) -> bool:
    """Check if the given voice name corresponds to an Edge TTS neural voice."""
    if not voice_name:
        return False
    model = registered_voice_model(voice_name)
    if model is not None:
        return model.engine == "edge"
    return (
        voice_name in EDGE_VOICES
        or "edge" in voice_name.lower()
        or ("neural" in voice_name.lower() and "silero" not in voice_name.lower() and "piper" not in voice_name.lower())
    )


def resolve_edge_voice(voice_name: str) -> str:
    """Resolve human-readable voice name to Microsoft Edge TTS voice ID."""
    if voice_name in EDGE_VOICES:
        return EDGE_VOICES[voice_name]
    for k, v in EDGE_VOICES.items():
        if v.lower() == voice_name.lower():
            return v
        if k.lower() in voice_name.lower() or voice_name.lower() in k.lower():
            return v
    if any(n in voice_name.lower() for n in ("jenny", "guy", "aria", "samantha", "alex")):
        return "en-US-JennyNeural"
    return "ru-RU-SvetlanaNeural"


def is_silero_voice(voice_name: str) -> bool:
    """Check if the given voice name corresponds to a Silero offline neural voice."""
    if not voice_name:
        return False
    model = registered_voice_model(voice_name)
    if model is not None:
        return model.engine == "silero"
    return (
        voice_name in SILERO_VOICES
        or "silero" in voice_name.lower()
        or any(k.lower() in voice_name.lower() for k in ("ksenia", "baya", "aidar", "eugene", "raya"))
    )


def resolve_silero_speaker(voice_name: str) -> str:
    """Resolve human-readable voice name to Silero speaker ID."""
    if voice_name in SILERO_VOICES:
        return SILERO_VOICES[voice_name]
    for k, v in SILERO_VOICES.items():
        if v.lower() == voice_name.lower():
            return v
        if k.lower() in voice_name.lower() or voice_name.lower() in k.lower():
            return v
    return "xenia"


def is_piper_voice(voice_name: str) -> bool:
    """Check if the given voice name corresponds to a Piper offline neural voice."""
    if not voice_name:
        return False
    model = registered_voice_model(voice_name)
    if model is not None:
        return model.engine == "piper"
    return (
        voice_name in PIPER_VOICES
        or "piper" in voice_name.lower()
        or any(k.lower() in voice_name.lower() for k in ("dmitri", "irina", "denis", "ruslan", "lessac"))
    )


def _engine_for_voice(voice_name: str) -> str:
    model = registered_voice_model(voice_name)
    if model is not None:
        return model.engine
    if is_edge_voice(voice_name):
        return "edge"
    if is_piper_voice(voice_name):
        return "piper"
    if is_silero_voice(voice_name):
        return "silero"
    return "macos"


def resolve_piper_model(voice_name: str) -> str | None:
    """Resolve a voice name to its Piper model filename stem.

    Returns ``None`` for an unknown voice instead of silently substituting a
    different built-in model, so an unavailable custom voice fails clearly
    rather than being attributed to Dmitri.
    """

    model = registered_voice_model(voice_name)
    if model is not None and model.engine == "piper":
        key = model.voice_model_keys.get(voice_name)
        if key:
            return key
        onnx = next((spec.filename for spec in model.files if spec.filename.endswith(".onnx")), None)
        if onnx:
            return onnx[: -len(".onnx")]
    if voice_name in PIPER_VOICES:
        return PIPER_VOICES[voice_name]
    for k, v in PIPER_VOICES.items():
        if v.lower() == voice_name.lower() or voice_name.lower() in k.lower():
            return v
    lower = voice_name.lower()
    if "denis" in lower:
        return "ru_RU-denis-medium"
    if "ruslan" in lower:
        return "ru_RU-ruslan-medium"
    if "irina" in lower:
        return "ru_RU-irina-medium"
    if "lessac" in lower:
        return "en_US-lessac-medium"
    return None


def _piper_model_languages(model_key: str) -> tuple[str, ...]:
    """Languages supported by a Piper model key, from the catalog when known."""

    from app.tts_manager import MODEL_CATALOG

    target = f"{model_key}.onnx"
    for model in MODEL_CATALOG.values():
        if model.engine != "piper":
            continue
        if model_key in model.voice_model_keys.values() or any(spec.filename == target for spec in model.files):
            languages = model.resolved_languages()
            if languages:
                return languages
    if model_key.startswith("en_"):
        return ("en",)
    if model_key.startswith("ru_"):
        return ("ru",)
    return ()


# ---------------------------------------------------------------------------
# Latin-to-Cyrillic phonetic transliteration for Russian-only TTS models (Silero)
# ---------------------------------------------------------------------------

COMMON_ENG_WORDS: dict[str, str] = {
    "the": "зе",
    "of": "оф",
    "and": "энд",
    "in": "ин",
    "to": "ту",
    "a": "э",
    "an": "эн",
    "for": "фор",
    "on": "он",
    "with": "уиз",
    "at": "эт",
    "by": "бай",
    "from": "фром",
    "is": "из",
    "are": "ар",
    "was": "уоз",
    "were": "уёр",
    "be": "би",
    "it": "ит",
    "its": "итс",
    "as": "эз",
    "or": "ор",
    "that": "дэт",
    "this": "дис",
    "switch": "свитч",
    "nintendo": "нинтендо",
    "legend": "ледженд",
    "zelda": "зельда",
    "mario": "марио",
    "super": "супер",
    "odyssey": "одисси",
    "breath": "бреф",
    "wild": "вайлд",
    "tears": "тирс",
    "kingdom": "кингдом",
    "playstation": "плейстейшн",
    "xbox": "эксбокс",
    "game": "гейм",
    "games": "геймс",
    "pro": "про",
    "plus": "плюс",
    "ultra": "ультра",
    "mini": "мини",
    "max": "макс",
    "lite": "лайт",
    "apple": "эппл",
    "google": "гугл",
    "microsoft": "майкрософт",
    "windows": "виндовс",
    "linux": "линукс",
    "steam": "стим",
    "deck": "дек",
    "valve": "вэлв",
    "sony": "сони",
    "chat": "чат",
    "gpt": "джипити",
    "ai": "эйай",
    "codex": "кодекс",
    "voice": "войс",
    "open": "оупен",
    "source": "сорс",
    "fastapi": "фастапи",
    "python": "пайтон",
    "docker": "докер",
    "github": "гитхаб",
    "git": "гит",
    "web": "веб",
    "app": "апп",
    "apps": "аппс",
    "mac": "мак",
    "macos": "макос",
    "ios": "айос",
    "android": "андроид",
}

MULTI_CHAR_PHONETICS: list[tuple[str, str]] = [
    ("sch", "ш"),
    ("tion", "шн"),
    ("sion", "шн"),
    ("ough", "о"),
    ("ight", "айт"),
    ("tch", "ч"),
    ("th", "т"),
    ("sh", "ш"),
    ("ch", "ч"),
    ("ph", "ф"),
    ("ck", "к"),
    ("ee", "и"),
    ("ea", "и"),
    ("oo", "у"),
    ("ou", "ау"),
    ("ow", "оу"),
    ("qu", "кв"),
    ("wh", "в"),
    ("ai", "эй"),
    ("ay", "эй"),
    ("ei", "ей"),
    ("ey", "ей"),
    ("oi", "ой"),
    ("oy", "ой"),
    ("kn", "н"),
    ("wr", "р"),
]

SINGLE_CHAR_PHONETICS: dict[str, str] = {
    "a": "а",
    "b": "б",
    "c": "к",
    "d": "д",
    "e": "е",
    "f": "ф",
    "g": "г",
    "h": "х",
    "i": "и",
    "j": "дж",
    "k": "к",
    "l": "л",
    "m": "м",
    "n": "н",
    "o": "о",
    "p": "п",
    "q": "к",
    "r": "р",
    "s": "с",
    "t": "т",
    "u": "у",
    "v": "в",
    "w": "в",
    "x": "кс",
    "y": "и",
    "z": "з",
}


def transliterate_latin_word(word: str) -> str:
    """Phonetically transliterate a single English/Latin word to Cyrillic."""
    lower_word = word.lower()
    if lower_word in COMMON_ENG_WORDS:
        cyr = COMMON_ENG_WORDS[lower_word]
    else:
        cyr = lower_word
        for pattern, repl in MULTI_CHAR_PHONETICS:
            cyr = cyr.replace(pattern, repl)
        cyr = "".join(SINGLE_CHAR_PHONETICS.get(ch, ch) for ch in cyr)

    if word.isupper() and len(word) > 1:
        return cyr.upper()
    if word[0].isupper():
        return cyr.capitalize()
    return cyr


def transliterate_latin_for_speech(text: str) -> str:
    """Convert Latin script words into Cyrillic phonetic equivalents for Russian TTS models."""
    if not re.search(r"[\u0400-\u052f]", text):
        return text
    if not re.search(r"[A-Za-z]", text):
        return text
    return re.sub(r"[A-Za-z]+", lambda m: transliterate_latin_word(m.group(0)), text)



_silero_models: dict[str, object] = {}


def _get_silero_model(model_filename: str = "silero_v4_ru.pt"):
    """Lazily load and cache the local Silero TTS PyTorch model (v4 or v5)."""
    global _silero_models
    if model_filename in _silero_models:
        return _silero_models[model_filename]

    if not is_silero_available():
        raise LocalSpeechError(
            "PyTorch (torch) is not installed. Silero voices require the optional 'silero' extra: "
            "install it with `make setup-silero` or `pip install '.[silero]'`"
        )

    import torch
    from app.tts_manager import find_model_file, tts_model_manager

    target_file = model_filename
    model_path = find_model_file(target_file)
    model_id = "silero_v5_ru" if "v5" in target_file else "silero_v4_ru"

    # No silent cross-version substitution: if the requested Silero version is
    # not installed, fail so the normal fallback machinery can pick a different
    # voice and report the voice that actually produced the audio.
    if model_path is None or not tts_model_manager.is_ready(model_id):
        raise LocalSpeechError(
            f"Silero model '{target_file}' is missing or failed checksum verification. "
            "Download it from the TTS model manager before using this voice."
        )

    device = torch.device("cpu")
    torch.set_num_threads(4)
    model = torch.package.PackageImporter(str(model_path)).load_pickle(
        "tts_models", "model"
    )
    model.to(device)
    _silero_models[model_filename] = model
    return model


_piper_cache: dict[str, object] = {}


def _get_piper_voice(model_key: str):
    """Lazily load and cache a PiperVoice ONNX instance."""
    global _piper_cache
    if model_key in _piper_cache:
        return _piper_cache[model_key]

    from app.tts_manager import MODEL_CATALOG, find_model_file, tts_model_manager
    model = next(
        (definition for definition in MODEL_CATALOG.values()
         if any(spec.filename == f"{model_key}.onnx" for spec in definition.files)),
        None,
    )
    if model is None or not tts_model_manager.is_ready(model.id):
        raise LocalSpeechError(f"Piper voice model '{model_key}' is missing or failed checksum verification")
    onnx_file = find_model_file(f"{model_key}.onnx")
    if not onnx_file:
        raise LocalSpeechError(f"Piper voice model '{model_key}' is not downloaded yet")

    from piper import PiperVoice
    config_file = find_model_file(f"{model_key}.onnx.json")
    voice = PiperVoice.load(str(onnx_file), config_path=str(config_file) if config_file else None)
    _piper_cache[model_key] = voice
    return voice


async def prewarm_voice(voice_name: str | None) -> None:
    """Load the selected local neural voice before its first spoken reply.

    This does not synthesize audio or download a model. A missing optional
    Silero/Piper model remains a normal per-turn fallback rather than making
    opening a conversation fail or unexpectedly causing network traffic.
    """
    if not voice_name:
        return

    from app.tts_manager import find_model_file, tts_model_manager

    if is_silero_voice(voice_name):
        if is_silero_available():
            silero_file = "silero_v5_ru.pt" if "v5" in voice_name.lower() else "silero_v4_ru.pt"
            model_id = "silero_v5_ru" if "v5" in voice_name.lower() else "silero_v4_ru"
            if find_model_file(silero_file) and tts_model_manager.is_ready(model_id):
                if "v5" in voice_name.lower():
                    await asyncio.to_thread(_get_silero_model, silero_file)
                else:
                    await asyncio.to_thread(_get_silero_model)
    elif is_piper_voice(voice_name):
        model_key = resolve_piper_model(voice_name)
        model = tts_model_manager.get_model_for_voice(voice_name)
        if model_key and model and tts_model_manager.is_ready(model.id):
            await asyncio.to_thread(_get_piper_voice, model_key)


_cached_installed_voices: list[VoiceInfo] | None = None
_active_voice: str | None = None


def get_installed_voices(force_refresh: bool = False) -> list[VoiceInfo]:
    """Return available TTS voices on the system, with Edge Neural and Russian voices first."""
    global _cached_installed_voices
    if _cached_installed_voices is not None and not force_refresh:
        return _cached_installed_voices

    voices: list[VoiceInfo] = []
    if shutil.which("say") is not None:
        try:
            result = subprocess.run(
                ["say", "-v", "?"],
                capture_output=True,
                text=True,
                check=True,
                timeout=5,
            )
            for line in result.stdout.splitlines():
                match = re.match(r"^(.*?)\s+([a-z]{2}_[A-Z0-9]+)\s+#\s*(.*)$", line)
                if match:
                    name, locale, sample = match.groups()
                    name = name.strip()
                    locale = locale.strip()
                    sample = sample.strip()
                    is_ru = locale.lower().startswith("ru") or "milena" in name.lower()
                    is_enh = "(enhanced)" in name.lower() or "premium" in name.lower()
                    voices.append(
                        VoiceInfo(
                            name=name,
                            locale=locale,
                            sample=sample,
                            is_russian=is_ru,
                            is_enhanced=is_enh,
                            engine="macos",
                        )
                    )
        except Exception:
            pass

    if not voices and shutil.which("say") is not None:
        voices = [
            VoiceInfo(
                name="Milena (Enhanced)",
                locale="ru_RU",
                sample="Здравствуйте! Меня зовут Милена.",
                is_russian=True,
                is_enhanced=True,
                engine="macos",
            ),
            VoiceInfo(
                name="Milena",
                locale="ru_RU",
                sample="Здравствуйте! Меня зовут Милена.",
                is_russian=True,
                is_enhanced=False,
                engine="macos",
            ),
            VoiceInfo(
                name="Sara",
                locale="da_DK",
                sample="Goddag! Mit navn er Sara.",
                is_russian=False,
                is_enhanced=False,
                engine="macos",
            ),
            VoiceInfo(
                name="Samantha",
                locale="en_US",
                sample="Hello! My name is Samantha.",
                is_russian=False,
                is_enhanced=False,
                engine="macos",
            ),
            VoiceInfo(
                name="Mónica",
                locale="es_ES",
                sample="¡Hola! Me llamo Mónica.",
                is_russian=False,
                is_enhanced=False,
                engine="macos",
            ),
        ]

    ru_voices = [v for v in voices if v.is_russian]
    ru_voices.sort(key=lambda v: (not v.is_enhanced, v.name))
    other_voices = [v for v in voices if not v.is_russian]
    other_voices.sort(key=lambda v: (v.locale, v.name))

    edge_ru_voices = [
        VoiceInfo(
            name="Svetlana (Neural · Edge)",
            locale="ru_RU",
            sample="Здравствуйте! Меня зовут Светлана.",
            is_russian=True,
            is_enhanced=True,
            engine="edge",
        ),
        VoiceInfo(
            name="Dmitry (Neural · Edge)",
            locale="ru_RU",
            sample="Здравствуйте! Меня зовут Дмитрий.",
            is_russian=True,
            is_enhanced=True,
            engine="edge",
        ),
    ]

    from app.tts_manager import tts_model_manager

    silero_installed = is_silero_available() and tts_model_manager.is_ready("silero_v4_ru")
    silero_v5_installed = is_silero_available() and tts_model_manager.is_ready("silero_v5_ru")
    silero_ru_voices = [
        VoiceInfo(
            name="Ksenia (Silero Neural · Offline)",
            locale="ru_RU",
            sample="Здравствуйте! Меня зовут Ксения.",
            is_russian=True,
            is_enhanced=True,
            engine="silero",
            is_downloaded=silero_installed,
            model_id="silero_v4_ru",
            size_mb=40.0,
        ),
        VoiceInfo(
            name="Baya (Silero Neural · Offline)",
            locale="ru_RU",
            sample="Здравствуйте! Меня зовут Бая.",
            is_russian=True,
            is_enhanced=True,
            engine="silero",
            is_downloaded=silero_installed,
            model_id="silero_v4_ru",
            size_mb=40.0,
        ),
        VoiceInfo(
            name="Aidar (Silero Neural · Offline)",
            locale="ru_RU",
            sample="Здравствуйте! Меня зовут Айдар.",
            is_russian=True,
            is_enhanced=True,
            engine="silero",
            is_downloaded=silero_installed,
            model_id="silero_v4_ru",
            size_mb=40.0,
        ),
        VoiceInfo(
            name="Eugene (Silero Neural · Offline)",
            locale="ru_RU",
            sample="Здравствуйте! Меня зовут Евгений.",
            is_russian=True,
            is_enhanced=True,
            engine="silero",
            is_downloaded=silero_installed,
            model_id="silero_v4_ru",
            size_mb=40.0,
        ),
        VoiceInfo(
            name="Raya (Silero Neural · Offline)",
            locale="ru_RU",
            sample="Здравствуйте! Меня зовут Райя.",
            is_russian=True,
            is_enhanced=True,
            engine="silero",
            is_downloaded=silero_installed,
            model_id="silero_v4_ru",
            size_mb=40.0,
        ),
        VoiceInfo(
            name="Ksenia v5 (Silero Neural · Offline)",
            locale="ru_RU",
            sample="Здравствуйте! Меня зовут Ксения.",
            is_russian=True,
            is_enhanced=True,
            engine="silero",
            is_downloaded=silero_v5_installed,
            model_id="silero_v5_ru",
            size_mb=139.0,
        ),
        VoiceInfo(
            name="Baya v5 (Silero Neural · Offline)",
            locale="ru_RU",
            sample="Здравствуйте! Меня зовут Бая.",
            is_russian=True,
            is_enhanced=True,
            engine="silero",
            is_downloaded=silero_v5_installed,
            model_id="silero_v5_ru",
            size_mb=139.0,
        ),
        VoiceInfo(
            name="Aidar v5 (Silero Neural · Offline)",
            locale="ru_RU",
            sample="Здравствуйте! Меня зовут Айдар.",
            is_russian=True,
            is_enhanced=True,
            engine="silero",
            is_downloaded=silero_v5_installed,
            model_id="silero_v5_ru",
            size_mb=139.0,
        ),
        VoiceInfo(
            name="Eugene v5 (Silero Neural · Offline)",
            locale="ru_RU",
            sample="Здравствуйте! Меня зовут Евгений.",
            is_russian=True,
            is_enhanced=True,
            engine="silero",
            is_downloaded=silero_v5_installed,
            model_id="silero_v5_ru",
            size_mb=139.0,
        ),
        VoiceInfo(
            name="Raya v5 (Silero Neural · Offline)",
            locale="ru_RU",
            sample="Здравствуйте! Меня зовут Райя.",
            is_russian=True,
            is_enhanced=True,
            engine="silero",
            is_downloaded=silero_v5_installed,
            model_id="silero_v5_ru",
            size_mb=139.0,
        ),
    ]

    piper_dmitri_installed = tts_model_manager.is_ready("piper_ru_dmitri")
    piper_irina_installed = tts_model_manager.is_ready("piper_ru_irina")
    piper_denis_installed = tts_model_manager.is_ready("piper_ru_denis")
    piper_ruslan_installed = tts_model_manager.is_ready("piper_ru_ruslan")
    piper_lessac_installed = tts_model_manager.is_ready("piper_en_lessac")
    piper_ru_voices = [
        VoiceInfo(
            name="Dmitri (Piper Neural · Offline)",
            locale="ru_RU",
            sample="Здравствуйте! Меня зовут Дмитрий.",
            is_russian=True,
            is_enhanced=True,
            engine="piper",
            is_downloaded=piper_dmitri_installed,
            model_id="piper_ru_dmitri",
            size_mb=60.0,
        ),
        VoiceInfo(
            name="Irina (Piper Neural · Offline)",
            locale="ru_RU",
            sample="Здравствуйте! Меня зовут Ирина.",
            is_russian=True,
            is_enhanced=True,
            engine="piper",
            is_downloaded=piper_irina_installed,
            model_id="piper_ru_irina",
            size_mb=60.0,
        ),
        VoiceInfo(
            name="Denis (Piper Neural · Offline)",
            locale="ru_RU",
            sample="Здравствуйте! Меня зовут Денис.",
            is_russian=True,
            is_enhanced=True,
            engine="piper",
            is_downloaded=piper_denis_installed,
            model_id="piper_ru_denis",
            size_mb=60.0,
        ),
        VoiceInfo(
            name="Ruslan (Piper Neural · Offline)",
            locale="ru_RU",
            sample="Здравствуйте! Меня зовут Руслан.",
            is_russian=True,
            is_enhanced=True,
            engine="piper",
            is_downloaded=piper_ruslan_installed,
            model_id="piper_ru_ruslan",
            size_mb=60.0,
        ),
    ]

    piper_en_voices = [
        VoiceInfo(
            name="Lessac (Piper Neural · Offline)",
            locale="en_US",
            sample="First off, I'd like to say that I'm a big fan of your work.",
            is_russian=False,
            is_enhanced=True,
            engine="piper",
            is_downloaded=piper_lessac_installed,
            model_id="piper_en_lessac",
            size_mb=63.0,
        ),
    ]

    edge_en_voices = [
        VoiceInfo(
            name="Jenny (Neural · Edge)",
            locale="en_US",
            sample="Hello! My name is Jenny.",
            is_russian=False,
            is_enhanced=True,
            engine="edge",
            is_downloaded=True,
            model_id="edge_tts_cloud",
        ),
        VoiceInfo(
            name="Guy (Neural · Edge)",
            locale="en_US",
            sample="Hello! My name is Guy.",
            is_russian=False,
            is_enhanced=True,
            engine="edge",
            is_downloaded=True,
            model_id="edge_tts_cloud",
        ),
        VoiceInfo(
            name="Aria (Neural · Edge)",
            locale="en_US",
            sample="Hello! My name is Aria.",
            is_russian=False,
            is_enhanced=True,
            engine="edge",
            is_downloaded=True,
            model_id="edge_tts_cloud",
        ),
    ]

    edge_es_voices = [
        VoiceInfo(
            name="Elvira (Neural · Edge)",
            locale="es_ES",
            sample="¡Hola! Me llamo Elvira.",
            is_russian=False,
            is_enhanced=True,
            engine="edge",
            is_downloaded=True,
            model_id="edge_tts_cloud",
        ),
        VoiceInfo(
            name="Alvaro (Neural · Edge)",
            locale="es_ES",
            sample="¡Hola! Me llamo Alvaro.",
            is_russian=False,
            is_enhanced=True,
            engine="edge",
            is_downloaded=True,
            model_id="edge_tts_cloud",
        ),
    ]

    _cached_installed_voices = _apply_voice_capabilities(
        edge_ru_voices
        + piper_ru_voices
        + silero_ru_voices
        + ru_voices
        + edge_en_voices
        + piper_en_voices
        + edge_es_voices
        + other_voices
        + _user_defined_piper_voices()
    )
    return _cached_installed_voices


def _apply_voice_capabilities(voices: list[VoiceInfo]) -> list[VoiceInfo]:
    """Fill language/status metadata for voices whose model did not declare it."""

    for voice in voices:
        if voice.languages:
            # User-defined voices already declare their capability.
            continue
        capability = model_catalog.voice_capability(voice.name, voice.locale)
        voice.languages = capability.languages
        voice.code_switching = capability.code_switching
        voice.multilingual = capability.multilingual
        voice.catalog_status = capability.status
        voice.deprecation_reason = capability.deprecation_reason
    return voices


def _user_defined_piper_voices() -> list[VoiceInfo]:
    """Build VoiceInfo entries for user-defined Piper models (issue #14)."""

    from app.tts_manager import MODEL_CATALOG, tts_model_manager

    voices: list[VoiceInfo] = []
    for model in MODEL_CATALOG.values():
        if not (model.user_defined and model.engine == "piper"):
            continue
        for voice_name in model.voices:
            capability = model.voice_capability_overrides.get(voice_name) or model_catalog.voice_capability(
                voice_name,
                model.locale,
                status=model.catalog_status,
                deprecation_reason=model.deprecation_reason,
            )
            voices.append(
                VoiceInfo(
                    name=voice_name,
                    locale=model.locale,
                    sample=f"User-defined voice: {voice_name}",
                    is_russian="ru" in capability.languages,
                    is_enhanced=False,
                    engine="piper",
                    is_downloaded=tts_model_manager.is_ready(model.id),
                    model_id=model.id,
                    size_mb=model.size_mb,
                    languages=capability.languages,
                    multilingual=capability.multilingual,
                    code_switching=capability.code_switching,
                    catalog_status=capability.status,
                    deprecation_reason=capability.deprecation_reason,
                    user_defined=True,
                )
            )
    return voices


def get_default_voice() -> str:
    """Determine the default voice: environment variable -> Milena (Enhanced) if present -> Milena -> Dmitri -> Svetlana."""
    env_voice = os.environ.get("VOICE_OF_LUNA_RUSSIAN_VOICE")
    if env_voice:
        return env_voice
    voices = get_installed_voices()
    for candidate in (
        "Milena (Enhanced)",
        "Milena",
        "Dmitri (Piper Neural · Offline)",
        "Svetlana (Neural · Edge)",
    ):
        for v in voices:
            if (
                v.name == candidate
                and not v.is_deprecated
                and (getattr(v, "is_downloaded", True) or v.engine in ("macos", "edge"))
            ):
                return v.name
    for v in voices:
        if v.is_russian and not v.is_deprecated:
            return v.name
    return "Milena"


def get_default_voice_for_locale(locale: str) -> str:
    """Find the best default voice for a given locale (e.g. 'en-US' -> 'Jenny (Neural · Edge)', 'es-ES' -> 'Elvira').

    Deprecated voices are never chosen automatically. A multilingual voice that
    covers the requested language is preferred, then a matching installed voice.
    """
    norm = (locale or "").lower().replace("_", "-")
    prefix = norm.split("-")[0]
    voices = [v for v in get_installed_voices() if not v.is_deprecated]
    if prefix:
        for v in voices:
            if v.multilingual and prefix in (v.languages or ()):
                return v.name
    if norm.startswith("en"):
        for v in voices:
            if v.name == "Jenny (Neural · Edge)":
                return v.name
        for v in voices:
            if v.name == "Lessac (Piper Neural · Offline)" and getattr(v, "is_downloaded", False):
                return v.name
        for v in voices:
            if v.locale.lower().startswith("en") and "samantha" in v.name.lower():
                return v.name
        for v in voices:
            if v.locale.lower().startswith("en"):
                return v.name
        return "Jenny (Neural · Edge)"
    elif norm.startswith("es"):
        for v in voices:
            if v.name == "Elvira (Neural · Edge)":
                return v.name
        for v in voices:
            if v.locale.lower().startswith("es") and "mónica" in v.name.lower():
                return v.name
        for v in voices:
            if v.locale.lower().startswith("es"):
                return v.name
        return "Elvira (Neural · Edge)"
    return get_default_voice()


def voice_matches_locale(voice_name: str, locale: str) -> bool:
    """Check if the given voice matches the target locale prefix (e.g. 'es', 'en', 'ru')."""
    if not voice_name or not locale:
        return False
    norm_loc = locale.lower().replace("_", "-")
    prefix = norm_loc.split("-")[0]
    voices = get_installed_voices()
    for v in voices:
        if v.name.lower() == voice_name.lower():
            if v.languages:
                return prefix in v.languages or (v.multilingual and prefix in v.languages)
            v_loc = v.locale.lower().replace("_", "-")
            return v_loc.startswith(prefix)
    name_lower = voice_name.lower()
    if prefix == "ru" and any(r in name_lower for r in ("milena", "svetlana", "dmitry", "dmitri", "irina", "ksenia", "baya", "aidar", "eugene", "raya")):
        return True
    if prefix == "en" and any(e in name_lower for e in ("jenny", "guy", "aria", "samantha", "lessac")):
        return True
    if prefix == "es" and any(s in name_lower for s in ("mónica", "monica", "elvira", "alvaro")):
        return True
    return False


def get_active_voice() -> str:
    """Return currently active voice."""
    global _active_voice
    if _active_voice:
        return _active_voice
    return get_default_voice()


def set_active_voice(voice: str) -> None:
    """Set the currently active voice."""
    global _active_voice
    _active_voice = voice


def _voice_is_ready(voice: VoiceInfo) -> bool:
    """A voice can be used right now: local models must be downloaded/verified."""

    return voice.engine in ("macos", "edge") or bool(getattr(voice, "is_downloaded", True))


def get_ready_voice_for_locale(locale_prefix: str, exclude_voice: str | None = None) -> str | None:
    """Best *ready* installed voice for a language, local-first.

    Used by mixed-language routing for embedded and explanation runs. The
    existing locale resolver can return a catalog entry whose model file is not
    installed, so this wrapper filters readiness and prefers the same
    privacy-preserving order as the synthesis fallback (Piper, Silero, macOS,
    then Edge). Returns ``None`` when nothing suitable is installed.
    """

    prefix = model_catalog.normalize_language(locale_prefix)
    if not prefix:
        return None
    excluded = exclude_voice.casefold() if exclude_voice else None
    voices = [voice for voice in get_installed_voices() if not voice.is_deprecated and _voice_is_ready(voice)]

    def matches(voice: VoiceInfo) -> bool:
        languages = voice.languages or model_catalog.languages_for_locale(voice.locale)
        return prefix in languages or voice.locale.lower().replace("_", "-").startswith(prefix)

    for engine in ("piper", "silero", "macos", "edge"):
        for voice in voices:
            if voice.engine != engine or not matches(voice):
                continue
            if excluded and voice.name.casefold() == excluded:
                continue
            return voice.name
    return None


def get_voice_for_locale(
    locale_prefix: str,
    allowed_engines: set[str] | None = None,
    excluded_engines: set[str] | None = None,
) -> str | None:
    """Find the best installed voice for a locale prefix (e.g. 'es' -> 'Mónica').

    Can optionally filter by allowed_engines or excluded_engines.
    """
    prefix = locale_prefix.lower()
    voices = [v for v in get_installed_voices() if not v.is_deprecated]
    if allowed_engines is not None:
        voices = [v for v in voices if v.engine in allowed_engines]
    if excluded_engines is not None:
        voices = [v for v in voices if v.engine not in excluded_engines]

    for v in voices:
        languages = v.languages or model_catalog.languages_for_locale(v.locale)
        if prefix in languages:
            if v.is_enhanced or v.multilingual or any(preferred in v.name.lower() for preferred in ("mónica", "monica", "paulina", "samantha")):
                return v.name
    for v in voices:
        languages = v.languages or model_catalog.languages_for_locale(v.locale)
        if prefix in languages or v.locale.lower().startswith(prefix):
            return v.name
    return None


def reset_active_voice() -> None:
    """Reset the active voice override (useful for tests)."""
    global _active_voice
    _active_voice = None


TRAILING_SOURCES_RE = re.compile(
    r"""(?xi)
    (?:
        # Case 1: Explicit header
        (?:\n|\A)\s*(?:[#/*_~-]+\s*)?(?:источники|ссылки|источник|sources|references|source|fuentes|referencias|fuente)\b\s*:?[\s\S]*$
        |
        # Case 2: Trailing block of markdown links / citations at the end of text
        (?<=[.!?…\n])\s*
        (?:
            (?:[#/*_~-]+\s*)?(?:источники|ссылки|источник|sources|references|source|fuentes|referencias|fuente)\b\s*:?\s*
        )?
        (?:
            (?:[-*•·]|\d+\.)?\s*
            [\(\[]?\s*
            (?:
                \[[^\]]+\]\((?:https?://)[^)\s]+\)
                |
                https?://\S+
            )
            [\)\]]?
            \s*[,;•·–—\-/\n\s]*
        )+
        $
    )
    """
)


def is_pure_citation(text: str) -> bool:
    """Return True if text contains only citation links, URLs, bullets, numbers, and punctuation."""
    if not text:
        return True
    stripped = re.sub(r"\[[^\]]+\]\([^)]+\)", "", text)
    stripped = re.sub(r"https?://\S+", "", stripped)
    stripped = re.sub(r"[-*•·\d.,;:/\\|()\[\]\s–—\"\'«»“”„!?>#~`]", "", stripped)
    return len(stripped.strip()) == 0


def sanitize_for_speech(text: str) -> str:
    """Sanitize and clean text for natural spoken TTS synthesis.

    1. If string consists purely of citations/links, discard it completely.
    2. Cut off any trailing sources / references block (explicit or trailing link list).
    3. Strip parenthetical citation blocks containing URLs or markdown links.
    4. Convert inline markdown links [Title](url) -> Title.
    5. Strip bare URLs (http://, https://).
    6. Strip backslashes and escaped markdown sequences.
    7. Normalize quotes that macOS voices (e.g. Milena) speak as "backslash" («, », “, ”, „ -> ").
    8. Clean basic markdown formatting (*, _, `, #, ~, >).
    9. Normalize whitespace and punctuation.
    """
    if not text:
        return ""

    if is_pure_citation(text):
        return ""

    # 1. Cut off explicit sources/references section at the end
    text = re.split(
        r"(?i)(?:\n|\A)\s*(?:[#/*_~-]+\s*)?(?:источники|ссылки|источник|sources|references|source|fuentes|referencias|fuente)\b\s*:?",
        text,
    )[0]

    # 2. Cut off trailing citations block after sentence end
    trailing_match = TRAILING_SOURCES_RE.search(text)
    if trailing_match:
        text = text[: trailing_match.start()]

    # 3. Remove parenthetical citations that contain URLs or markdown links.
    def strip_citation_parens(s: str) -> str:
        result: list[str] = []
        i = 0
        n = len(s)
        while i < n:
            if s[i] == "(" and (i == 0 or s[i - 1] != "]"):
                depth = 1
                j = i + 1
                while j < n and depth > 0:
                    if s[j] == "(":
                        depth += 1
                    elif s[j] == ")":
                        depth -= 1
                    j += 1
                paren_content = s[i:j]
                lower_content = paren_content.lower()
                if (
                    "http://" in paren_content
                    or "https://" in paren_content
                    or "источник" in lower_content
                    or "ссылк" in lower_content
                ):
                    while result and result[-1] in " \t":
                        result.pop()
                    i = j
                    continue
                else:
                    result.append(paren_content)
                    i = j
                    continue
            result.append(s[i])
            i += 1
        return "".join(result)

    text = strip_citation_parens(text)

    # 4. Replace inline markdown links [Title](url) -> Title
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)

    # 5. Remove any remaining bare URLs
    text = re.sub(r"https?://\S+", "", text)

    # 6. Remove backslashes and unescape markdown sequences (e.g. \*\* -> **)
    text = re.sub(r"\\+", "", text)

    # 7. Normalize quotes that macOS voices (especially Milena) mispronounce as "backslash"
    text = re.sub(r"[«»“”„]", '"', text)

    # 8. Clean basic markdown syntax (*, _, `, #, ~, >)
    text = re.sub(r"[*_`#~>]", "", text)

    # 9. Strip bullet list markers at the start of lines/text (- , * , • , · )
    text = re.sub(r"(?m)^\s*[-*•·]\s+", "", text)

    # 10. Normalize punctuation and whitespace
    text = re.sub(r"\s+([.,;:!?])", r"\1", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


class LocalMacOsSpeaker:
    """Render text through a local macOS voice or free Edge TTS with local fallback."""

    def __init__(self, voice: str | None = None) -> None:
        self.voice = voice or get_active_voice()

    async def _synthesize_edge(self, clean_text: str, voice_name: str) -> Path:
        import edge_tts

        edge_voice_id = resolve_edge_voice(voice_name)
        descriptor, raw_mp3 = tempfile.mkstemp(prefix="voice-of-luna-speech-", suffix=".mp3")
        os.close(descriptor)
        destination = Path(raw_mp3)
        destination.unlink(missing_ok=True)
        try:
            communicate = edge_tts.Communicate(clean_text, edge_voice_id, rate="+5%")
            await communicate.save(str(destination))
            if not destination.is_file() or destination.stat().st_size == 0:
                raise LocalSpeechError(f"Edge TTS returned an empty audio clip for '{voice_name}'")
            return destination
        except BaseException:
            destination.unlink(missing_ok=True)
            raise

    def _macos_effective_voice(self, clean_text: str, active_voice: str) -> str | None:
        """Return the voice ``say`` will actually use, or None if it cannot speak.

        Extracted so callers can report the true voice rather than claiming the
        requested one when the Russian-voice/Spanish-text heuristic switches.
        """

        is_russian = "ru" in active_voice.lower() or "milena" in active_voice.lower()
        if is_russian and not re.search(r"[\u0400-\u052f]", clean_text):
            spanish_voice = get_voice_for_locale("es")
            if spanish_voice and (
                re.search(r"[áéíóúüñ¿¡]", clean_text, re.IGNORECASE)
                or any(w in clean_text.lower().split() for w in ("hola", "el", "la", "de", "que", "y", "en", "un", "por", "para", "con", "no", "es", "estás", "estoy", "amigo", "luna"))
            ):
                return spanish_voice
            return None
        return active_voice

    async def _synthesize_macos(self, clean_text: str, active_voice: str) -> Path | None:
        effective_voice = self._macos_effective_voice(clean_text, active_voice)
        if effective_voice is None:
            return None
        active_voice = effective_voice

        if shutil.which("say") is None:
            raise LocalSpeechError("macOS say is required for local speech")

        descriptor, raw_wav = tempfile.mkstemp(prefix="voice-of-luna-speech-", suffix=".wav")
        os.close(descriptor)
        destination = Path(raw_wav)
        destination.unlink(missing_ok=True)
        render = None
        try:
            render = await asyncio.create_subprocess_exec(
                "say",
                "-v",
                active_voice,
                "-o",
                str(destination),
                "--data-format=LEI16@22050",
                "--",
                clean_text,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            if hasattr(render, "communicate"):
                try:
                    _, stderr_bytes = await render.communicate()
                except asyncio.CancelledError:
                    _terminate_process(render)
                    raise
                returncode = render.returncode
            else:
                try:
                    returncode = await render.wait()
                except asyncio.CancelledError:
                    _terminate_process(render)
                    raise
                stderr_bytes = b""
            if returncode != 0 or not destination.is_file():
                err_msg = stderr_bytes.decode(errors="replace").strip() if stderr_bytes else ""
                raise LocalSpeechError(
                    f"The local macOS voice '{active_voice}' could not speak this reply: {err_msg or 'unknown error'}"
                )
            return destination
        except BaseException:
            _terminate_process(render)
            destination.unlink(missing_ok=True)
            raise

    async def _synthesize_silero(self, clean_text: str, voice_name: str) -> Path:
        if not re.search(r"[\u0400-\u052f]", clean_text):
            raise LocalSpeechError(f"Silero TTS only supports Cyrillic/Russian text: '{clean_text[:40]}'")

        if not is_silero_available():
            raise LocalSpeechError(
                "PyTorch (torch) is not installed. Silero voices require the optional 'silero' extra: "
                "install it with `make setup-silero` or `pip install '.[silero]'`"
            )
        import wave
        import torch

        speaker_id = resolve_silero_speaker(voice_name)
        silero_text = normalize_numbers_for_speech(clean_text)
        silero_text = transliterate_latin_for_speech(silero_text)
        descriptor, raw_wav = tempfile.mkstemp(prefix="voice-of-luna-speech-", suffix=".wav")
        os.close(descriptor)
        destination = Path(raw_wav)
        destination.unlink(missing_ok=True)
        cancelled = threading.Event()

        def _generate():
            if "v5" in voice_name.lower():
                model = _get_silero_model("silero_v5_ru.pt")
            else:
                model = _get_silero_model()
            audio = model.apply_tts(text=silero_text, speaker=speaker_id, sample_rate=48000)
            int16_data = (audio.clamp(-1.0, 1.0) * 32767).to(torch.int16).numpy().tobytes()
            with wave.open(str(destination), "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(48000)
                wav_file.writeframes(int16_data)
            if cancelled.is_set():
                destination.unlink(missing_ok=True)

        try:
            await asyncio.to_thread(_generate)
            if not destination.is_file() or destination.stat().st_size == 0:
                raise LocalSpeechError(f"Silero TTS produced an empty audio clip for '{voice_name}'")
            return destination
        except asyncio.CancelledError:
            cancelled.set()
            destination.unlink(missing_ok=True)
            raise
        except BaseException:
            destination.unlink(missing_ok=True)
            raise

    async def _synthesize_piper(self, clean_text: str, voice_name: str) -> Path:
        import wave

        model_key = resolve_piper_model(voice_name)
        if not model_key:
            raise LocalSpeechError(f"Piper voice '{voice_name}' is not registered or installed")
        languages = _piper_model_languages(model_key)
        russian_only = languages == ("ru",)
        if russian_only and not re.search(r"[\u0400-\u052f]", clean_text):
            raise LocalSpeechError(f"Piper Russian TTS only supports Cyrillic text: '{clean_text[:40]}'")
        if russian_only:
            piper_text = transliterate_latin_for_speech(normalize_numbers_for_speech(clean_text))
        else:
            piper_text = clean_text
        descriptor, raw_wav = tempfile.mkstemp(prefix="voice-of-luna-speech-", suffix=".wav")
        os.close(descriptor)
        destination = Path(raw_wav)
        destination.unlink(missing_ok=True)
        cancelled = threading.Event()

        def _generate():
            voice = _get_piper_voice(model_key)
            with wave.open(str(destination), "wb") as wav_file:
                voice.synthesize_wav(piper_text, wav_file)
            if cancelled.is_set():
                destination.unlink(missing_ok=True)

        try:
            await asyncio.to_thread(_generate)
            if not destination.is_file() or destination.stat().st_size == 0:
                raise LocalSpeechError(f"Piper TTS produced an empty audio clip for '{voice_name}'")
            return destination
        except asyncio.CancelledError:
            cancelled.set()
            destination.unlink(missing_ok=True)
            raise
        except BaseException:
            destination.unlink(missing_ok=True)
            raise

    def _fallback_candidates(self, requested_voice: str, clean_text: str) -> list[str]:
        """Return installed same-locale voices in privacy-preserving fallback order."""
        requested_lower = requested_voice.lower()
        locale = "ru" if re.search(r"[\u0400-\u052f]", clean_text) else "en"
        if any(token in requested_lower for token in ("elvira", "alvaro", "mónica", "monica", "paulina")):
            locale = "es"
        elif any(token in requested_lower for token in ("dmitri", "irina", "ksenia", "baya", "aidar", "eugene", "raya", "milena")):
            locale = "ru"
        candidates: list[str] = []
        for engine in ("piper", "silero", "macos", "edge"):
            candidate = get_voice_for_locale(locale, allowed_engines={engine})
            if engine == "macos" and candidate is None:
                # Keep a deterministic local fallback candidate even on Linux;
                # the synthesizer will report that ``say`` is unavailable.
                candidate = "Milena" if locale == "ru" else "Samantha"
            if candidate and candidate.lower() not in {requested_lower, *(v.lower() for v in candidates)}:
                candidates.append(candidate)
        return candidates

    async def _synthesize_candidate(self, clean_text: str, voice_name: str) -> tuple[Path | None, str]:
        """Synthesize one candidate, returning the path and the voice actually used."""

        engine = _engine_for_voice(voice_name)
        if engine == "edge":
            return await self._synthesize_edge(clean_text, voice_name), voice_name
        if engine == "piper":
            return await self._synthesize_piper(clean_text, voice_name), voice_name
        if engine == "silero":
            return await self._synthesize_silero(clean_text, voice_name), voice_name
        effective = self._macos_effective_voice(clean_text, voice_name)
        path = await self._synthesize_macos(clean_text, voice_name)
        return path, (effective or voice_name)

    async def synthesize_with_metadata(
        self, text: str, voice: str | None = None
    ) -> SpeechSynthesisResult | None:
        """Synthesize speech and retain an auditable record of any fallback.

        The UI can continue to use :meth:`synthesize`, while diagnostics and
        benchmarks can report the engine that produced the clip rather than the
        engine that was merely requested.
        """
        clean_text = sanitize_for_speech(text)
        if not clean_text:
            return None

        requested_voice = voice or self.voice
        requested_engine = _engine_for_voice(requested_voice)
        candidates = [requested_voice, *self._fallback_candidates(requested_voice, clean_text)]
        failures: list[str] = []
        for candidate in candidates:
            try:
                path, actual_voice = await self._synthesize_candidate(clean_text, candidate)
                if path is None:
                    raise LocalSpeechError(f"voice '{candidate}' produced no audio")
                return SpeechSynthesisResult(
                    path=path,
                    requested_engine=requested_engine,
                    actual_engine=_engine_for_voice(actual_voice),
                    fallback_reason="; ".join(failures) if failures else None,
                    actual_voice=actual_voice,
                )
            except Exception as exc:
                failures.append(f"{candidate}: {exc}")
                logger.warning("TTS candidate failed for voice '%s': %s", candidate, exc)
        raise LocalSpeechError("All TTS fallback candidates failed: " + "; ".join(failures))

    async def synthesize(self, text: str, voice: str | None = None) -> Path | None:
        """Backward-compatible synthesis API returning only the generated path."""
        result = await self.synthesize_with_metadata(text, voice)
        return result.path if result else None
