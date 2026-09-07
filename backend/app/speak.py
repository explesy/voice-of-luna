"""Local macOS speech synthesis for replies written in Cyrillic."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

logger = logging.getLogger("voice_of_luna.speak")


class LocalSpeechError(RuntimeError):
    """Raised when a local response cannot be rendered to audio."""


@dataclass
class VoiceInfo:
    name: str
    locale: str
    sample: str
    is_russian: bool = False
    is_enhanced: bool = False
    engine: str = "macos"

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


EDGE_VOICES: dict[str, str] = {
    "Svetlana (Neural · Edge)": "ru-RU-SvetlanaNeural",
    "Dmitry (Neural · Edge)": "ru-RU-DmitryNeural",
}

SILERO_VOICES: dict[str, str] = {
    "Ksenia (Silero Neural · Offline)": "xenia",
    "Baya (Silero Neural · Offline)": "baya",
    "Aidar (Silero Neural · Offline)": "aidar",
}


def is_edge_voice(voice_name: str) -> bool:
    """Check if the given voice name corresponds to an Edge TTS neural voice."""
    if not voice_name:
        return False
    return (
        voice_name in EDGE_VOICES
        or "edge" in voice_name.lower()
        or ("neural" in voice_name.lower() and "silero" not in voice_name.lower())
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
    return "ru-RU-SvetlanaNeural"


def is_silero_voice(voice_name: str) -> bool:
    """Check if the given voice name corresponds to a Silero offline neural voice."""
    if not voice_name:
        return False
    return (
        voice_name in SILERO_VOICES
        or "silero" in voice_name.lower()
        or any(k.lower() in voice_name.lower() for k in ("ksenia", "baya", "aidar"))
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
    if not re.search(r"[A-Za-z]", text):
        return text
    return re.sub(r"[A-Za-z]+", lambda m: transliterate_latin_word(m.group(0)), text)



_silero_model = None


def _get_silero_model():
    """Lazily load and cache the local Silero TTS PyTorch model."""
    global _silero_model
    if _silero_model is not None:
        return _silero_model

    import torch

    candidates = [
        Path(__file__).resolve().parents[1] / "models" / "silero_v4_ru.pt",
        Path("models/silero_v4_ru.pt"),
        Path.home() / ".cache" / "voice-of-luna" / "models" / "silero_v4_ru.pt",
    ]
    model_path = next((p for p in candidates if p.is_file()), None)
    if model_path is None:
        model_path = Path(__file__).resolve().parents[1] / "models" / "silero_v4_ru.pt"
        model_path.parent.mkdir(parents=True, exist_ok=True)
        import urllib.request

        url = "https://models.silero.ai/models/tts/ru/v4_ru.pt"
        req = urllib.request.Request(url, headers={"User-Agent": "Voice-Of-Luna"})
        with urllib.request.urlopen(req, timeout=60) as resp, open(model_path, "wb") as f:
            while chunk := resp.read(1024 * 1024):
                f.write(chunk)

    device = torch.device("cpu")
    torch.set_num_threads(4)
    _silero_model = torch.package.PackageImporter(str(model_path)).load_pickle(
        "tts_models", "model"
    )
    _silero_model.to(device)
    return _silero_model


async def prewarm_voice(voice_name: str | None) -> None:
    """Load the selected local neural voice before its first spoken reply.

    This does not synthesize audio or download a model. A missing optional
    Silero model remains a normal per-turn fallback rather than making opening
    a conversation fail or unexpectedly causing network traffic.
    """
    if not voice_name or not is_silero_voice(voice_name):
        return
    candidates = (
        Path(__file__).resolve().parents[1] / "models" / "silero_v4_ru.pt",
        Path("models/silero_v4_ru.pt"),
        Path.home() / ".cache" / "voice-of-luna" / "models" / "silero_v4_ru.pt",
    )
    if not any(path.is_file() for path in candidates):
        return
    await asyncio.to_thread(_get_silero_model)


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

    if not voices:
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

    silero_ru_voices = [
        VoiceInfo(
            name="Ksenia (Silero Neural · Offline)",
            locale="ru_RU",
            sample="Здравствуйте! Меня зовут Ксения.",
            is_russian=True,
            is_enhanced=True,
            engine="silero",
        ),
        VoiceInfo(
            name="Baya (Silero Neural · Offline)",
            locale="ru_RU",
            sample="Здравствуйте! Меня зовут Бая.",
            is_russian=True,
            is_enhanced=True,
            engine="silero",
        ),
        VoiceInfo(
            name="Aidar (Silero Neural · Offline)",
            locale="ru_RU",
            sample="Здравствуйте! Меня зовут Айдар.",
            is_russian=True,
            is_enhanced=True,
            engine="silero",
        ),
    ]

    _cached_installed_voices = edge_ru_voices + silero_ru_voices + ru_voices + other_voices
    return _cached_installed_voices


def get_default_voice() -> str:
    """Determine the default voice: environment variable -> Milena (Enhanced) if present -> Milena."""
    env_voice = os.environ.get("VOICE_OF_LUNA_RUSSIAN_VOICE")
    if env_voice:
        return env_voice
    voices = get_installed_voices()
    for v in voices:
        if v.name == "Milena (Enhanced)":
            return "Milena (Enhanced)"
    return "Milena"


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


def get_voice_for_locale(locale_prefix: str) -> str | None:
    """Find the best installed voice for a locale prefix (e.g. 'es' -> 'Mónica')."""
    prefix = locale_prefix.lower()
    voices = get_installed_voices()
    for v in voices:
        if v.locale.lower().startswith(prefix):
            if v.is_enhanced or any(preferred in v.name.lower() for preferred in ("mónica", "monica", "paulina")):
                return v.name
    for v in voices:
        if v.locale.lower().startswith(prefix):
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
        (?:\n|\A)\s*(?:[#/*_~-]+\s*)?(?:источники|ссылки|источник|sources|references)\b\s*:?[\s\S]*$
        |
        # Case 2: Trailing block of markdown links / citations at the end of text
        (?<=[.!?…\n])\s*
        (?:
            (?:[#/*_~-]+\s*)?(?:источники|ссылки|источник|sources|references)\b\s*:?\s*
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
        r"(?i)(?:\n|\A)\s*(?:[#/*_~-]+\s*)?(?:источники|ссылки|источник|sources|references)\b\s*:?",
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

    async def _synthesize_macos(self, clean_text: str, active_voice: str) -> Path | None:
        is_russian = "ru" in active_voice.lower() or "milena" in active_voice.lower()
        if is_russian and not re.search(r"[\u0400-\u052f]", clean_text):
            spanish_voice = get_voice_for_locale("es")
            if spanish_voice and (
                re.search(r"[áéíóúüñ¿¡]", clean_text, re.IGNORECASE)
                or any(w in clean_text.lower().split() for w in ("hola", "el", "la", "de", "que", "y", "en", "un", "por", "para", "con", "no", "es", "estás", "estoy", "amigo", "luna"))
            ):
                active_voice = spanish_voice
            else:
                return None

        if shutil.which("say") is None:
            raise LocalSpeechError("macOS say is required for local Russian speech")

        descriptor, raw_wav = tempfile.mkstemp(prefix="voice-of-luna-speech-", suffix=".wav")
        os.close(descriptor)
        destination = Path(raw_wav)
        destination.unlink(missing_ok=True)
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
                _, stderr_bytes = await render.communicate()
                returncode = render.returncode
            else:
                returncode = await render.wait()
                stderr_bytes = b""
            if returncode != 0 or not destination.is_file():
                err_msg = stderr_bytes.decode(errors="replace").strip() if stderr_bytes else ""
                raise LocalSpeechError(
                    f"The local macOS voice '{active_voice}' could not speak this reply: {err_msg or 'unknown error'}"
                )
            return destination
        except BaseException:
            destination.unlink(missing_ok=True)
            raise

    async def _synthesize_silero(self, clean_text: str, voice_name: str) -> Path:
        import wave
        import torch

        speaker_id = resolve_silero_speaker(voice_name)
        silero_text = transliterate_latin_for_speech(clean_text)
        descriptor, raw_wav = tempfile.mkstemp(prefix="voice-of-luna-speech-", suffix=".wav")
        os.close(descriptor)
        destination = Path(raw_wav)
        destination.unlink(missing_ok=True)

        def _generate():
            model = _get_silero_model()
            audio = model.apply_tts(text=silero_text, speaker=speaker_id, sample_rate=48000)
            int16_data = (audio.clamp(-1.0, 1.0) * 32767).to(torch.int16).numpy().tobytes()
            with wave.open(str(destination), "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(48000)
                wav_file.writeframes(int16_data)

        try:
            await asyncio.to_thread(_generate)
            if not destination.is_file() or destination.stat().st_size == 0:
                raise LocalSpeechError(f"Silero TTS produced an empty audio clip for '{voice_name}'")
            return destination
        except BaseException:
            destination.unlink(missing_ok=True)
            raise

    async def synthesize(self, text: str, voice: str | None = None) -> Path | None:
        clean_text = sanitize_for_speech(text)
        if not clean_text:
            return None

        active_voice = voice or self.voice

        if is_edge_voice(active_voice):
            try:
                return await self._synthesize_edge(clean_text, active_voice)
            except Exception as exc:
                logger.warning(
                    "Edge TTS failed for voice '%s' (%s), falling back to local macOS say",
                    active_voice,
                    exc,
                )
                active_voice = get_default_voice()

        if is_silero_voice(active_voice):
            try:
                return await self._synthesize_silero(clean_text, active_voice)
            except Exception as exc:
                logger.warning(
                    "Silero TTS failed for voice '%s' (%s), falling back to local macOS say",
                    active_voice,
                    exc,
                )
                active_voice = get_default_voice()

        return await self._synthesize_macos(clean_text, active_voice)
