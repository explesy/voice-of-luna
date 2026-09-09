"""Speech audio processing and pipeline utilities for Voice of Luna.

Handles:
- Temporary audio file management
- FFmpeg-based audio conversion (mono 16kHz WAV)
- Streaming sentence extraction with clause boundary splitting
- Audio format verification
"""

from __future__ import annotations

import asyncio
import os
import re
import shutil
import tempfile
import wave
from pathlib import Path

MAX_AUDIO_BYTES = 12 * 1024 * 1024
AUDIO_SUFFIXES = {
    "audio/webm": ".webm",
    "audio/ogg": ".ogg",
    "audio/mp4": ".m4a",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
}

ABBREVIATIONS = {
    "т.д.", "т.п.", "т.е.", "руб.", "коп.", "г.", "ул.", "стр.", "рис.",
    "e.g.", "i.e.", "vs.", "etc.", "bros.", "mr.", "mrs.", "dr.", "corp.", "inc.",
}

SENTENCE_SPLIT_RE = re.compile(
    r"""(?x)
    (?:
        (?<=[.!?…])(?:\s+|\n)
        |
        \n+
    )
    """
)

# For the very first chunk of a turn, also allow natural clause boundaries
# (comma, colon, semicolon, dash) if sufficient words have accumulated,
# dramatically reducing time-to-first-audio (TTFA).
FIRST_CHUNK_SPLIT_RE = re.compile(
    r"""(?x)
    (?:
        (?<=[.!?…])(?:\s+|\n)
        |
        \n+
        |
        (?<=[,;:—–])(?:[ \t]+)
    )
    """
)

SPEECH_MIN_WORDS = 6
SPEECH_HARD_MAX_WORDS = 20
SPEECH_FIRST_CLAUSE_MIN_WORDS = 3
SPEECH_LATER_COMMA_MIN_WORDS = 12

def detect_effective_turn_locale(text: str, fallback_locale: str = "ru-RU") -> str:
    """Detect turn locale based on text content (Cyrillic -> ru-RU, Spanish markers -> es-ES, else en-US)."""
    if not text:
        return fallback_locale if fallback_locale != "auto" else "ru-RU"
    if re.search(r"[\u0400-\u04FF]", text):
        return "ru-RU"
    if re.search(r"[¿¡áéíóúÁÉÍÓÚñÑ]", text):
        return "es-ES"
    if re.search(r"[a-zA-Z]", text):
        return "en-US"
    return fallback_locale if fallback_locale != "auto" else "ru-RU"


SOURCE_HEADER_WORDS = (
    "источники",
    "ссылки",
    "источник",
    "sources",
    "references",
    "source",
    "fuentes",
    "referencias",
    "fuente",
)

SOURCES_SPLIT_RE = re.compile(
    r"""(?xi)
    (?:^|\n)\s*(?:[#/*_~-]+\s*)?(?:источники|ссылки|источник|sources|references|source|fuentes|referencias|fuente)\b\s*:?
    """
)


class AudioConversionError(RuntimeError):
    """Raised when a browser recording cannot be decoded locally."""


def write_temporary_audio(recording: bytes, suffix: str) -> Path:
    descriptor, raw_path = tempfile.mkstemp(prefix="voice-of-luna-", suffix=suffix)
    with os.fdopen(descriptor, "wb") as destination:
        destination.write(recording)
    return Path(raw_path)


def remove_temporary_audio(path: Path) -> None:
    path.unlink(missing_ok=True)


def is_16k_mono_wav(path: Path) -> bool:
    try:
        with wave.open(str(path), "rb") as reader:
            return (
                reader.getnchannels() == 1
                and reader.getframerate() == 16000
                and reader.getsampwidth() == 2
                and reader.getcomptype() == "NONE"
            )
    except Exception:
        return False


async def convert_to_wav(source: Path) -> Path:
    if shutil.which("ffmpeg") is None:
        raise AudioConversionError("ffmpeg is required to decode this browser recording locally")
    descriptor, raw_destination = tempfile.mkstemp(prefix="voice-of-luna-", suffix=".wav")
    os.close(descriptor)
    destination = Path(raw_destination)
    process = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(source),
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(destination),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    if await process.wait() == 0 and destination.stat().st_size:
        return destination
    remove_temporary_audio(destination)
    raise AudioConversionError("The recording could not be decoded locally")


def extract_speech_sentence(buffer: str, is_first_chunk: bool = False) -> tuple[str | None, str]:
    """Extract one natural, speech-sized segment from a streaming buffer.

    Strong sentence punctuation is preferred. Clause punctuation is useful for
    the first audible chunk and for already substantial later chunks; a hard
    word cap prevents a model that omits punctuation from creating a long
    silence before TTS can start.
    """
    clean_buf = buffer.lstrip()
    if not clean_buf:
        return None, ""

    split_re = FIRST_CHUNK_SPLIT_RE

    def is_valid_candidate(candidate: str, boundary: str) -> bool:
        words = candidate.split()
        if boundary in {",", ":", ";", "—", "–"}:
            minimum = (
                SPEECH_FIRST_CLAUSE_MIN_WORDS
                if is_first_chunk
                else (SPEECH_LATER_COMMA_MIN_WORDS if boundary == "," else SPEECH_MIN_WORDS)
            )
            if len(words) < minimum or len(candidate) < 12:
                return False
        elif is_first_chunk or len(candidate) >= 6:
            if len(candidate) < 6:
                return False
        else:
            return False

        if re.search(r"\b\d+\.$", candidate):
            return False
        if candidate.count("(") > candidate.count(")") or candidate.count("[") > candidate.count("]"):
            return False

        last_word = words[-1] if words else ""
        if (
            last_word.lower().rstrip(".,:;!?") + "." in ABBREVIATIONS
            or last_word.lower() in ABBREVIATIONS
        ):
            return False
        return True

    for match in split_re.finditer(clean_buf):
        split_pos = match.end()
        candidate = clean_buf[: match.start()].strip()
        boundary_match = re.search(r"[,;:—–]$", candidate)
        boundary = boundary_match.group(0) if boundary_match else "."
        # A Markdown list item commonly uses an em dash or colon inside its
        # title. The line break is the useful boundary in that case.
        if boundary_match and (clean_buf.startswith(("- ", "* ")) or "\n- " in clean_buf[: match.start()]):
            continue
        if not is_valid_candidate(candidate, boundary):
            continue

        sentence = clean_buf[:split_pos].strip()
        remainder = clean_buf[split_pos:].lstrip()
        return sentence, remainder

    # Punctuation-free output should still reach TTS eventually. Cut only at
    # whitespace so words and prosody are not damaged by a character limit.
    words = list(re.finditer(r"\S+", clean_buf))
    if len(words) >= SPEECH_HARD_MAX_WORDS:
        cut_match = words[SPEECH_HARD_MAX_WORDS - 1]
        candidate = clean_buf[: cut_match.end()].strip()
        if is_valid_candidate(candidate, "."):
            return candidate, clean_buf[cut_match.end() :].lstrip()

    return None, clean_buf
