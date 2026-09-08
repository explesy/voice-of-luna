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
        (?<=[,;:—–])(?:\s+)
    )
    """
)

SOURCES_SPLIT_RE = re.compile(
    r"""(?xi)
    (?:^|\n)\s*(?:[#/*_~-]+\s*)?(?:источники|ссылки|источник|sources|references)\b\s*:?
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
    """Extract the first ready sentence/segment from the streaming buffer."""
    clean_buf = buffer.lstrip()
    if not clean_buf:
        return None, ""

    split_re = FIRST_CHUNK_SPLIT_RE if is_first_chunk else SENTENCE_SPLIT_RE

    for match in split_re.finditer(clean_buf):
        split_pos = match.end()
        candidate = clean_buf[: match.start()].strip()

        is_clause_boundary = bool(re.search(r"[,;:—–]$", clean_buf[: match.start()].rstrip()))

        if is_clause_boundary:
            words = candidate.split()
            if len(words) < 3 or len(candidate) < 12:
                continue
        else:
            if len(candidate) < 6:
                continue

        if re.search(r"\b\d+\.$", candidate):
            continue

        if candidate.count("(") > candidate.count(")") or candidate.count("[") > candidate.count("]"):
            continue

        last_word = candidate.split()[-1] if candidate.split() else ""
        if (
            last_word.lower().rstrip(".,:;!?") + "." in ABBREVIATIONS
            or last_word.lower() in ABBREVIATIONS
        ):
            continue

        sentence = clean_buf[:split_pos].strip()
        remainder = clean_buf[split_pos:].lstrip()
        return sentence, remainder

    return None, clean_buf
