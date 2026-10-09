#!/usr/bin/env python3
"""Benchmark local STT engines against a JSONL speech manifest.

The manifest has one JSON object per line with (at least) ``id``, ``audio`` and
``reference``. Optional ``language`` (``ru``/``es``/``en``/``mixed``) and
``duration_seconds`` fields improve per-language and RTF reporting.

Engines:

* ``tone`` (T-One CTC, ru)            - sherpa-onnx online CTC
* ``vosk`` (zipformer small ru, ru)   - sherpa-onnx online transducer
* ``kroko`` (zipformer es, es)        - sherpa-onnx online transducer
* ``nemotron320`` / ``nemotron560``   - Nemotron 3.5 streaming 0.6B int8
* ``pseudo-whisper``                  - sliding window over the batch Whisper
* ``whisper``                         - authoritative batch Whisper baseline

Everything runs locally; no audio or transcript leaves the machine. Per-clip
results and per-engine summaries (WER/CER, RTF, first-partial latency, partial
cadence, peak RSS) are written to ``--output``.

Peak RSS is measured with ``resource.getrusage(RUSAGE_SELF)`` and is only
meaningful for engines that run in-process (the sherpa-onnx engines). For the
Whisper engines the actual work happens in the local ``whisper-server``
process, so ``peak_rss_mb`` is recorded as ``null``.

For clean memory numbers run one provider per invocation.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import resource
import statistics
import sys
import tempfile
import time
import unicodedata
import wave
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np

from app.transcribe import LocalWhisperTranscriber

# Directory names inside ``--model-root`` for every sherpa-onnx engine.
STREAMING_DIRS = {
    "tone": "sherpa-onnx-streaming-t-one-russian-2025-09-08",
    "vosk": "sherpa-onnx-streaming-zipformer-small-ru-vosk-int8-2025-08-16",
    "kroko": "sherpa-onnx-streaming-zipformer-es-kroko-2025-08-06",
    "nemotron320": "sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-320ms-int8-2026-06-11",
    "nemotron560": "sherpa-onnx-nemotron-3.5-asr-streaming-0.6b-560ms-int8-2026-06-11",
}

STREAMING_ENGINES = tuple(STREAMING_DIRS)
ENGINE_LANGUAGES = {
    "tone": {"ru"},
    "vosk": {"ru"},
    "kroko": {"es"},
    "nemotron320": {"ru", "es", "en", "mixed"},
    "nemotron560": {"ru", "es", "en", "mixed"},
    "pseudo-whisper": {"ru", "es", "en", "mixed"},
    "whisper": {"ru", "es", "en", "mixed"},
}
HAS_LANGUAGE_OPTION = {"nemotron320", "nemotron560"}

ALL_PROVIDERS = ("tone", "vosk", "kroko", "nemotron320", "nemotron560", "pseudo-whisper", "whisper", "both")


def _normalise(text: str) -> str:
    # Compare on the same alphabet: case-fold, drop Latin accents/diacritics and
    # strip every Unicode punctuation mark (Spanish ¿¡, Cyrillic quotes, dashes
    # and ellipsis included). Cyrillic is kept intact except for ё -> е, so the
    # distinct Russian letter й is not flattened to и.
    text = text.lower().replace("ё", "е")
    chars: list[str] = []
    for char in text:
        if "\u0400" <= char <= "\u04ff":
            chars.append(char)
            continue
        decomposed = unicodedata.normalize("NFKD", char)
        chars.append("".join(part for part in decomposed if not unicodedata.combining(part)))
    stripped = "".join(chars)
    cleaned = "".join(" " if unicodedata.category(char).startswith("P") else char for char in stripped)
    return " ".join(cleaned.split())


def _distance(left: list[str], right: list[str]) -> int:
    previous = list(range(len(right) + 1))
    for left_index, left_item in enumerate(left, start=1):
        current = [left_index]
        for right_index, right_item in enumerate(right, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[right_index] + 1,
                    previous[right_index - 1] + (left_item != right_item),
                )
            )
        previous = current
    return previous[-1]


def _error_rate(reference: str, hypothesis: str, *, characters: bool = False) -> float:
    ref = _normalise(reference)
    hyp = _normalise(hypothesis)
    ref_items = list(ref.replace(" ", "")) if characters else ref.split()
    hyp_items = list(hyp.replace(" ", "")) if characters else hyp.split()
    if not ref_items:
        return 0.0 if not hyp_items else 1.0
    return _distance(ref_items, hyp_items) / len(ref_items)


def _summary(values: list[float]) -> dict[str, float] | None:
    cleaned = [value for value in values if value is not None]
    if not cleaned:
        return None
    ordered = sorted(cleaned)
    return {
        "mean": round(statistics.mean(cleaned), 4),
        "median": round(statistics.median(cleaned), 4),
        "p95_nearest_rank": round(ordered[max(0, round((len(ordered) - 1) * 0.95))], 4),
    }


def _peak_rss_mb() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS reports bytes, Linux reports kilobytes.
    if sys.platform == "darwin":
        return round(usage / (1024 * 1024), 1)
    return round(usage / 1024, 1)


def _read_audio(path: Path) -> tuple[int, "np.ndarray"]:
    with wave.open(str(path), "rb") as wav:
        sample_rate = wav.getframerate()
        channels = wav.getnchannels()
        width = wav.getsampwidth()
        payload = wav.readframes(wav.getnframes())
    if width != 2:
        raise ValueError(f"{path.name} must be 16-bit PCM WAV")
    audio = np.frombuffer(payload, dtype=np.int16).astype(np.float32) / 32768.0
    if channels == 2:
        audio = audio.reshape(-1, 2).mean(axis=1)
    return sample_rate, audio


def _write_wav(path: Path, sample_rate: int, audio: "np.ndarray") -> None:
    data = np.clip(audio, -1.0, 1.0)
    pcm = (data * 32767.0).astype(np.int16).tobytes()
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm)


class OnlineEngine:
    """Loads one sherpa-onnx recognizer once and creates a fresh stream per clip."""

    def __init__(self, kind: str, model_root: Path, num_threads: int) -> None:
        try:
            import sherpa_onnx
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise SystemExit("This engine requires the optional 'sherpa-onnx' package") from exc
        self.kind = kind
        self.num_threads = num_threads
        self.supports_language = kind in HAS_LANGUAGE_OPTION
        directory = model_root / STREAMING_DIRS[kind]
        if not directory.is_dir():
            raise SystemExit(f"Missing streaming model directory: {directory}")
        tokens = directory / "tokens.txt"
        started = time.perf_counter()
        if kind == "tone":
            self.recognizer = sherpa_onnx.OnlineRecognizer.from_t_one_ctc(
                model=str(directory / "model.onnx"),
                tokens=str(tokens),
                sample_rate=8000,
                provider="cpu",
                enable_endpoint_detection=True,
            )
        elif kind == "vosk":
            self.recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
                tokens=str(tokens),
                encoder=str(directory / "encoder.int8.onnx"),
                decoder=str(directory / "decoder.onnx"),
                joiner=str(directory / "joiner.int8.onnx"),
                num_threads=num_threads,
                sample_rate=16000,
                feature_dim=80,
                enable_endpoint_detection=True,
                decoding_method="greedy_search",
            )
        elif kind == "kroko":
            self.recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
                tokens=str(tokens),
                encoder=str(directory / "encoder.onnx"),
                decoder=str(directory / "decoder.onnx"),
                joiner=str(directory / "joiner.onnx"),
                num_threads=num_threads,
                sample_rate=16000,
                feature_dim=80,
                enable_endpoint_detection=True,
                decoding_method="greedy_search",
            )
        else:  # nemotron320 / nemotron560
            self.recognizer = sherpa_onnx.OnlineRecognizer.from_transducer(
                tokens=str(tokens),
                encoder=str(directory / "encoder.int8.onnx"),
                decoder=str(directory / "decoder.int8.onnx"),
                joiner=str(directory / "joiner.int8.onnx"),
                num_threads=num_threads,
                sample_rate=16000,
                feature_dim=80,
                enable_endpoint_detection=True,
                decoding_method="greedy_search",
            )
        self.load_seconds = round(time.perf_counter() - started, 3)

    def new_stream(self, language: str) -> tuple[Any, str]:
        stream = self.recognizer.create_stream()
        applied = "default"
        if self.supports_language:
            wanted = language if language in {"ru", "es", "en"} else "auto"
            try:
                stream.set_option("language", wanted)
                applied = wanted
            except Exception as exc:  # pragma: no cover - depends on sherpa build
                applied = f"failed: {exc}"
        return stream, applied

    def _drain(self, stream: Any) -> None:
        while self.recognizer.is_ready(stream):
            self.recognizer.decode_stream(stream)

    async def run(self, path: Path, language: str, chunk_ms: int) -> dict[str, Any]:
        sample_rate, audio = _read_audio(path)
        audio_seconds = len(audio) / float(sample_rate)
        stream, applied_language = self.new_stream(language)
        frames_per_chunk = max(1, int(sample_rate * chunk_ms / 1000))
        partials: list[str] = []
        partial_times: list[float] = []
        first_partial_audio_ms: float | None = None
        started = time.perf_counter()
        for offset in range(0, len(audio), frames_per_chunk):
            chunk = audio[offset : offset + frames_per_chunk]
            stream.accept_waveform(sample_rate, chunk)
            self._drain(stream)
            text = str(self.recognizer.get_result(stream)).strip()
            if text and (not partials or partials[-1] != text):
                partials.append(text)
                partial_times.append(time.perf_counter())
                if first_partial_audio_ms is None:
                    first_partial_audio_ms = round((offset + len(chunk)) / sample_rate * 1000, 2)
        if self.kind == "tone":
            # T-One expects trailing padding; like the app, pad at the same
            # input rate as the audio frames (one stream, one input rate).
            padding = np.zeros(int(0.66 * sample_rate), dtype=np.float32)
            stream.accept_waveform(sample_rate, padding)
            self._drain(stream)
        stream.input_finished()
        self._drain(stream)
        final = str(self.recognizer.get_result(stream)).strip()
        elapsed = time.perf_counter() - started
        decode_ms = [round((moment - started) * 1000, 2) for moment in partial_times]
        cadence = [decode_ms[index] - decode_ms[index - 1] for index in range(1, len(decode_ms))]
        return {
            "text": final,
            "audio_seconds": round(audio_seconds, 3),
            "elapsed_s": round(elapsed, 3),
            "rtf": round(elapsed / audio_seconds, 4) if audio_seconds else None,
            "first_partial_ms": decode_ms[0] if decode_ms else None,
            "first_partial_audio_ms": first_partial_audio_ms,
            "partial_count": len(partials),
            "partial_cadence_ms": _summary(cadence),
            "language_option": applied_language,
            "partials": partials,
        }


async def _whisper(path: Path, language: str) -> dict[str, Any]:
    sample_rate, audio = _read_audio(path)
    audio_seconds = len(audio) / float(sample_rate)
    transcriber = LocalWhisperTranscriber(language=language)
    started = time.perf_counter()
    try:
        text = await transcriber.transcribe(path, language=language if language in {"ru", "es", "en"} else "auto")
    finally:
        await transcriber.aclose()
    elapsed = time.perf_counter() - started
    return {
        "text": text,
        "audio_seconds": round(audio_seconds, 3),
        "elapsed_s": round(elapsed, 3),
        "rtf": round(elapsed / audio_seconds, 4) if audio_seconds else None,
        "first_partial_ms": None,
        "first_partial_audio_ms": None,
        "partial_count": 0,
        "partial_cadence_ms": None,
        "partials": [],
    }


async def _pseudo_whisper(
    path: Path, language: str, step_ms: int = 1000, window_ms: int = 6000
) -> dict[str, Any]:
    sample_rate, audio = _read_audio(path)
    audio_seconds = len(audio) / float(sample_rate)
    transcriber = LocalWhisperTranscriber(language=language)
    step = max(1, int(sample_rate * step_ms / 1000))
    window = max(step, int(sample_rate * window_ms / 1000))
    partials: list[str] = []
    partial_times: list[float] = []
    first_partial_audio_ms: float | None = None
    window_failures = 0
    started = time.perf_counter()
    try:
        with tempfile.TemporaryDirectory(prefix="stt-pseudo-whisper-") as temp_dir:
            clip_path = Path(temp_dir) / "window.wav"
            for end in range(step, len(audio) + 1, step):
                start = max(0, end - window)
                _write_wav(clip_path, sample_rate, audio[start:end])
                try:
                    text = (
                        await transcriber.transcribe(
                            clip_path, language=language if language in {"ru", "es", "en"} else "auto"
                        )
                    ).strip()
                except Exception:
                    window_failures += 1
                    continue
                if text and (not partials or partials[-1] != text):
                    partials.append(text)
                    partial_times.append(time.perf_counter())
                    if first_partial_audio_ms is None:
                        first_partial_audio_ms = round(end / sample_rate * 1000, 2)
            full_path = Path(temp_dir) / "full.wav"
            _write_wav(full_path, sample_rate, audio)
            final = (
                await transcriber.transcribe(
                    full_path, language=language if language in {"ru", "es", "en"} else "auto"
                )
            ).strip()
    finally:
        await transcriber.aclose()
    elapsed = time.perf_counter() - started
    decode_ms = [round((moment - started) * 1000, 2) for moment in partial_times]
    cadence = [decode_ms[index] - decode_ms[index - 1] for index in range(1, len(decode_ms))]
    return {
        "text": final,
        "audio_seconds": round(audio_seconds, 3),
        "elapsed_s": round(elapsed, 3),
        "rtf": round(elapsed / audio_seconds, 4) if audio_seconds else None,
        "first_partial_ms": decode_ms[0] if decode_ms else None,
        "first_partial_audio_ms": first_partial_audio_ms,
        "partial_count": len(partials),
        "partial_cadence_ms": _summary(cadence),
        "window_failures": window_failures,
        "partials": partials,
    }


def _build_sherpa_engines(providers: list[str], model_root: Path, num_threads: int) -> dict[str, OnlineEngine]:
    engines: dict[str, OnlineEngine] = {}
    for provider in providers:
        if provider in STREAMING_DIRS:
            print(f"Loading {provider} ...", flush=True)
            engines[provider] = OnlineEngine(provider, model_root, num_threads)
            print(f"  loaded in {engines[provider].load_seconds}s", flush=True)
    return engines


async def run(args: argparse.Namespace) -> int:
    manifest = args.manifest.expanduser().resolve()
    rows = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        raise SystemExit("manifest is empty")
    if args.provider == "both":
        providers = ["tone", "whisper"]
    elif args.provider == "all":
        providers = list(STREAMING_ENGINES) + ["pseudo-whisper", "whisper"]
    else:
        providers = [args.provider]

    model_root = args.model_root.expanduser().resolve()
    engines = _build_sherpa_engines(providers, model_root, args.num_threads)

    results: list[dict[str, Any]] = []
    for row in rows:
        audio = (manifest.parent / row["audio"]).resolve()
        language = str(row.get("language") or args.language)
        item: dict[str, Any] = {
            "id": row["id"],
            "audio": str(audio),
            "reference": row["reference"],
            "language": language,
            "kind": row.get("kind"),
            "synthetic": row.get("synthetic"),
        }
        for provider in providers:
            if language not in ENGINE_LANGUAGES[provider]:
                item[provider] = {"skipped": f"{provider} does not cover language '{language}'"}
                continue
            try:
                if provider in engines:
                    engine_result = await engines[provider].run(audio, language, args.chunk_ms)
                elif provider == "whisper":
                    engine_result = await _whisper(audio, language)
                elif provider == "pseudo-whisper":
                    engine_result = await _pseudo_whisper(audio, language, args.step_ms, args.window_ms)
                else:  # pragma: no cover - guarded by argparse
                    raise ValueError(provider)
                engine_result["wer"] = round(_error_rate(row["reference"], engine_result["text"]), 4)
                engine_result["cer"] = round(_error_rate(row["reference"], engine_result["text"], characters=True), 4)
                item[provider] = engine_result
            except Exception as exc:
                item[provider] = {"error": str(exc)}
        results.append(item)

    summary: dict[str, Any] = {
        "manifest": str(manifest),
        "providers": providers,
        "clips": len(results),
        "chunk_ms": args.chunk_ms,
        "pseudo_whisper": {"step_ms": args.step_ms, "window_ms": args.window_ms},
        "peak_rss_mb": _peak_rss_mb() if engines else None,
        "engines": {},
    }
    for provider in providers:
        entries = [item[provider] for item in results if provider in item and "error" not in item and "skipped" not in item]
        valid = [entry for entry in entries if entry.get("wer") is not None]
        if not valid:
            summary["engines"][provider] = {"error": "no valid clips"}
            continue
        summary["engines"][provider] = {
            "clips": len(valid),
            "languages": sorted({item["language"] for item in results if item.get(provider, {}).get("wer") is not None}),
            "wer": _summary([entry["wer"] for entry in valid]),
            "cer": _summary([entry["cer"] for entry in valid]),
            "rtf": _summary([entry["rtf"] for entry in valid if entry["rtf"] is not None]),
            "elapsed_s": _summary([entry["elapsed_s"] for entry in valid]),
            "first_partial_ms": _summary(
                [entry["first_partial_ms"] for entry in valid if entry["first_partial_ms"] is not None]
            ),
            "first_partial_audio_ms": _summary(
                [entry["first_partial_audio_ms"] for entry in valid if entry.get("first_partial_audio_ms") is not None]
            ),
            "partial_count_mean": round(statistics.mean([entry["partial_count"] for entry in valid]), 2),
            "model_load_s": engines[provider].load_seconds if provider in engines else None,
        }

    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    payload = {"summary": summary, "clips": results}
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Detailed results: {output}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument(
        "--provider",
        choices=(*ALL_PROVIDERS, "all"),
        default="both",
        help="Engine to run; 'all' runs every streaming engine plus Whisper.",
    )
    parser.add_argument(
        "--model-root",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "models" / "streaming-stt",
        help="Directory containing the extracted sherpa-onnx streaming models.",
    )
    parser.add_argument("--chunk-ms", type=int, default=100, help="audio feed granularity for streaming engines")
    parser.add_argument("--step-ms", type=int, default=1000, help="pseudo-whisper re-transcription step")
    parser.add_argument("--window-ms", type=int, default=6000, help="pseudo-whisper trailing window")
    parser.add_argument("--language", default="ru", help="fallback language when a manifest row omits it")
    parser.add_argument("--num-threads", type=int, default=min(os.cpu_count() or 4, 8))
    parser.add_argument("--output", type=Path, default=Path("benchmark-results/stt.json"))
    args = parser.parse_args()
    if args.chunk_ms < 20:
        parser.error("--chunk-ms must be at least 20")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
