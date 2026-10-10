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
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app import model_catalog

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


# --- Mixed-language segmentation and voice routing (issue #13) ---------------
#
# The router is a deliberate, conservative classifier, not general-purpose
# language identification. Script alone cannot separate Latin-script languages
# such as English and Spanish, so only strong single-token evidence switches the
# voice and every ambiguous or unknown token inherits its neighbour. The goal is
# deterministic, testable routing with no wrong-language churn; extending it to
# arbitrary language pairs needs a separate decision.

_CYRILLIC_LETTER_RE = re.compile(r"[\u0400-\u052f]")
_LATIN_LETTER_RE = re.compile(r"[A-Za-z\u00c0-\u024f]")
_SPANISH_ORTHOGRAPHY_RE = re.compile(r"[\u00e1\u00e9\u00ed\u00f3\u00fa\u00fc\u00f1\u00bf\u00a1]", re.IGNORECASE)

# Strong single-token evidence. Shared/short function words are intentionally
# excluded (see AMBIGUOUS_LATIN_TOKENS) so "no", "in", "la" cannot flip a voice.
ENGLISH_SPEECH_CUES = frozenset(
    {
        "the", "and", "you", "your", "this", "that", "these", "those",
        "with", "without", "from", "please", "can", "could", "would",
        "should", "what", "when", "where", "how", "why", "are",
        "was", "were", "been", "have", "has", "had", "does",
        "did", "not", "but", "then", "than", "there", "here",
        "its", "they", "she", "our", "their",
        # technology and product vocabulary that dominates real mixed turns
        "docker", "container", "kubernetes", "deploy", "deployment",
        "commit", "push", "pull", "merge", "rebase", "branch", "build",
        "release", "issue", "ticket", "request", "response", "server",
        "client", "backend", "frontend", "api", "json", "yaml", "config",
        "cache", "queue", "thread", "process", "file", "folder", "test",
        "tests", "log", "logs", "error", "warning", "debug", "python",
        "javascript", "typescript", "rust", "linux", "macos", "windows",
        "git", "github", "http", "https", "url", "token", "model",
        "prompt", "agent", "plugin", "user", "input", "output", "voice",
        "speech", "transcript", "refactor", "feature", "bug", "fix",
    }
)

SPANISH_SPEECH_CUES = frozenset(
    {
        "hola", "gracias", "est\u00e1s", "estoy", "est\u00e1", "est\u00e1n", "vamos",
        "practicar", "aprender", "espa\u00f1ol", "palabra", "palabras", "frase",
        "frases", "ejemplo", "entonces", "ahora", "quiero", "quieres", "puedo",
        "puedes", "necesito", "tengo", "tienes", "hacer", "muy", "bien",
        "pero", "porque", "tambi\u00e9n", "siempre", "nunca", "luego", "amigo",
        "amiga", "se\u00f1or", "se\u00f1ora", "buenos", "buenas", "d\u00edas", "noches",
        "tardes", "c\u00f3mo", "qu\u00e9", "d\u00f3nde", "qui\u00e9n", "cu\u00e1ndo",
        "cu\u00e1nto", "mucho", "poco", "todav\u00eda", "quiz\u00e1s", "verdad",
    }
)

# Tokens that exist in several languages or are too short to carry evidence.
AMBIGUOUS_LATIN_TOKENS = frozenset(
    {
        "no", "si", "s\u00ed", "ok", "okay", "la", "el", "los", "las", "un", "una",
        "unos", "unas", "de", "del", "en", "y", "o", "a", "al", "es", "son",
        "como", "para", "por", "con", "sin", "me", "te", "se", "mi", "tu", "su",
        "lo", "le", "les", "in", "on", "at", "of", "to", "it", "as", "or", "an",
        "is", "be", "by", "am", "us", "we", "he", "so", "do", "if", "my",
        "luna", "prime", "plus", "pro", "max", "mini", "beta", "alpha",
    }
)

ROLE_PRIMARY = "primary"
ROLE_EXPLANATION = "explanation"
ROLE_EMBEDDED = "embedded"


@dataclass(frozen=True)
class LanguageRun:
    """One contiguous same-language span of assistant text."""

    text: str
    language: str
    role: str


@dataclass(frozen=True)
class SpeechChunk:
    """A language run together with the voice that should speak it."""

    text: str
    language: str
    role: str
    voice: str


@dataclass(frozen=True)
class VoicePlan:
    """Generic primary/explanation/fallback plan for one assistant turn.

    The plan is deliberately domain-neutral: ``primary_locale`` is the locale the
    reply should mostly be in, ``explanation_locale`` is an optional second
    locale a plugin asked for, and every other language is resolved per run.
    """

    primary_locale: str
    primary_voice: str
    explanation_locale: str | None = None
    explanation_voice: str | None = None
    enabled: bool = True


def _classify_latin_token(word: str) -> str | None:
    lowered = word.casefold().strip(".,;:!?()[]{}\"'`\u00ab\u00bb\u2026")
    if not lowered:
        return None
    # Ambiguity wins over orthography: a shared short word such as "sí" must
    # not switch the voice on its own, or short tokens flap between languages.
    if lowered in AMBIGUOUS_LATIN_TOKENS:
        return None
    if _SPANISH_ORTHOGRAPHY_RE.search(lowered):
        return "es"
    if lowered in ENGLISH_SPEECH_CUES:
        return "en"
    if lowered in SPANISH_SPEECH_CUES:
        return "es"
    return None


def _classify_speech_token(word: str) -> str | None:
    """Return confident language evidence for one token, or None."""

    if _CYRILLIC_LETTER_RE.search(word):
        return "ru"
    if _LATIN_LETTER_RE.search(word):
        return _classify_latin_token(word)
    return None


# One whitespace-delimited token can still mix scripts (``Привет,Docker`` or
# ``build.``). Split it into maximal script spans so a punctuation-joined
# foreign word is not swallowed by the surrounding language.
_SCRIPT_SPAN_RE = re.compile(
    r"[\u0400-\u052f]+|[A-Za-z\u00c0-\u024f]+|[^\u0400-\u052fA-Za-z\u00c0-\u024f]+"
)


def _iter_speech_pieces(text: str):
    """Yield ``(raw_piece, language_or_None)`` in exact source order."""

    for match in re.finditer(r"\s+|\S+", text):
        raw = match.group(0)
        if raw.isspace():
            yield raw, None
            continue
        for span in _SCRIPT_SPAN_RE.finditer(raw):
            piece = span.group(0)
            yield piece, _classify_speech_token(piece)


def segment_language_runs(
    text: str,
    primary_locale: str,
    explanation_locale: str | None = None,
) -> list[LanguageRun]:
    """Split assistant text into deterministic per-language runs.

    Whitespace and punctuation are preserved exactly; a token with no confident
    language evidence inherits the preceding run (or the following/primary run at
    the start). The returned roles are ``primary``, ``explanation`` or
    ``embedded`` relative to the requested locales.
    """

    if not text or not text.strip():
        return []
    primary = model_catalog.normalize_language(primary_locale) or "ru"
    explanation = model_catalog.normalize_language(explanation_locale or "")
    if explanation == primary:
        explanation = ""

    runs: list[list[str]] = []
    carry = ""
    for raw, language in _iter_speech_pieces(text):
        if language is None:
            if runs:
                runs[-1][0] += raw
            else:
                carry += raw
            continue
        if runs and runs[-1][1] == language:
            runs[-1][0] += carry + raw
        else:
            runs.append([carry + raw, language])
        carry = ""
    if carry:
        if runs:
            runs[-1][0] += carry
        else:
            return [LanguageRun(text, primary, ROLE_PRIMARY)]

    result: list[LanguageRun] = []
    for run_text, language in runs:
        if language == primary:
            role = ROLE_PRIMARY
        elif explanation and language == explanation:
            role = ROLE_EXPLANATION
        else:
            role = ROLE_EMBEDDED
        result.append(LanguageRun(run_text, language, role))
    return result


def route_speech(
    text: str,
    plan: VoicePlan,
    resolve_voice: Callable[[str], str | None] | None = None,
) -> list[SpeechChunk]:
    """Assign a voice to every language run, merging equal-voice neighbours.

    ``resolve_voice`` is injected so the router stays pure and testable; it maps
    a language prefix to an installed voice (or ``None``). A missing voice never
    fails the turn: the run falls back to the primary voice, which is the same
    degradation the single-voice path already had.
    """

    if not text or not text.strip():
        return []
    if not plan.enabled:
        return [SpeechChunk(text.strip(), model_catalog.normalize_language(plan.primary_locale), ROLE_PRIMARY, plan.primary_voice)]

    runs = segment_language_runs(text, plan.primary_locale, plan.explanation_locale)
    chunks: list[SpeechChunk] = []
    for run in runs:
        if run.role == ROLE_PRIMARY:
            voice = plan.primary_voice
        elif run.role == ROLE_EXPLANATION:
            voice = plan.explanation_voice or plan.primary_voice
        else:
            voice = (resolve_voice(run.language) if resolve_voice else None) or plan.primary_voice
        if run.role == ROLE_EXPLANATION and voice != plan.primary_voice:
            role = ROLE_EXPLANATION
        elif run.role == ROLE_EMBEDDED and voice != plan.primary_voice:
            role = ROLE_EMBEDDED
        else:
            role = ROLE_PRIMARY
        if chunks and chunks[-1].voice == voice:
            previous = chunks[-1]
            merged_role = previous.role if previous.role == role else role
            chunks[-1] = SpeechChunk(previous.text + run.text, previous.language or run.language, merged_role, voice)
        else:
            chunks.append(SpeechChunk(run.text, run.language, role, voice))

    cleaned: list[SpeechChunk] = []
    for chunk in chunks:
        stripped = chunk.text.strip()
        if stripped:
            cleaned.append(SpeechChunk(stripped, chunk.language, chunk.role, chunk.voice))
    return cleaned


async def join_speech_clips(clips: list[Path], destination: Path) -> bool:
    """Concatenate locally synthesized speech clips into one normalized WAV.

    Used only by the single-clip HTTP/HTML endpoints, which must keep returning
    exactly one audio artifact while a turn may have been spoken by several
    voices. Each input is resampled before concatenation because Piper, Silero,
    macOS and Edge do not share a sample rate or container. Returns ``False``
    when ffmpeg is unavailable or the join fails; the caller must then surface a
    clear error instead of silently speaking the whole text with one voice.
    """

    if not clips:
        return False
    if shutil.which("ffmpeg") is None:
        return False
    descriptor, raw_destination = tempfile.mkstemp(prefix="voice-of-luna-join-", suffix=".wav")
    os.close(descriptor)
    staged = Path(raw_destination)
    command = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y"]
    for clip in clips:
        command += ["-i", str(clip)]
    labels = "".join(f"[{index}:a]aresample=24000[a{index}];" for index in range(len(clips)))
    concat_inputs = "".join(f"[a{index}]" for index in range(len(clips)))
    command += [
        "-filter_complex",
        f"{labels}{concat_inputs}concat=n={len(clips)}:v=0:a=1[out]",
        "-map",
        "[out]",
        "-ac",
        "1",
        str(staged),
    ]
    process = await asyncio.create_subprocess_exec(
        *command,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    try:
        returncode = await process.wait()
    except BaseException:
        process.kill()
        await process.wait()
        staged.unlink(missing_ok=True)
        raise
    if returncode != 0 or not staged.is_file() or staged.stat().st_size == 0:
        staged.unlink(missing_ok=True)
        return False
    staged.replace(destination)
    return True
