#!/usr/bin/env python3
"""Run reproducible local speech benchmarks; do not estimate LLM latency.

This command intentionally makes no Codex call. It reports local TTS results
only when the requested engine was actually used, and reports STT only against
a supplied real-speech WAV fixture. Live LLM measurements belong to an
explicit, quota-consuming operator run and are not derived from constants.
"""

from __future__ import annotations

import argparse
import asyncio
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.speak import LocalMacOsSpeaker, SpeechSynthesisResult
from app.transcribe import LocalWhisperTranscriber

PHRASE_SHORT = "Привет! Телескоп собирает и фокусирует свет."
PHRASE_LONG = (
    "Телескоп собирает и фокусирует свет с помощью системы линз или зеркал, "
    "создавая четкое изображение далёких звезд."
)


@dataclass(frozen=True)
class Distribution:
    samples_ms: list[float]

    @property
    def median_ms(self) -> float:
        return statistics.median(self.samples_ms)

    @property
    def p95_ms(self) -> float:
        values = sorted(self.samples_ms)
        return values[round((len(values) - 1) * 0.95)]


@dataclass(frozen=True)
class TTSBenchmarkResult:
    requested_engine: str
    actual_engine: str | None
    voice: str
    first_chunk: Distribution | None
    full_sentence: Distribution | None
    status: str
    note: str = ""


async def _measure_synthesis(
    speaker: LocalMacOsSpeaker, text: str, requested_engine: str, runs: int
) -> tuple[Distribution | None, str | None, str, str]:
    samples: list[float] = []
    actual_engine: str | None = None
    for _ in range(runs):
        started = time.perf_counter()
        result: SpeechSynthesisResult | None = await speaker.synthesize_with_metadata(text)
        elapsed_ms = (time.perf_counter() - started) * 1000
        if result is None:
            return None, None, "SKIP", "empty synthesis result"
        try:
            if result.actual_engine != requested_engine:
                return None, result.actual_engine, "FALLBACK", result.fallback_reason or "engine changed"
            actual_engine = result.actual_engine
            samples.append(elapsed_ms)
        finally:
            result.path.unlink(missing_ok=True)
    return Distribution(samples), actual_engine, "PASS", ""


async def run_tts_benchmarks(runs: int, include_edge: bool) -> list[TTSBenchmarkResult]:
    voices = [
        ("piper", "Dmitri (Piper Neural · Offline)"),
        ("piper", "Irina (Piper Neural · Offline)"),
        ("silero", "Ksenia (Silero Neural · Offline)"),
        ("silero", "Eugene (Silero Neural · Offline)"),
        ("macos", "Milena"),
    ]
    if include_edge:
        voices.append(("edge", "Svetlana (Neural · Edge)"))

    results: list[TTSBenchmarkResult] = []
    for engine, voice in voices:
        speaker = LocalMacOsSpeaker(voice=voice)
        _, actual_engine, warmup_status, warmup_note = await _measure_synthesis(
            speaker, "Привет.", engine, runs=1
        )
        if warmup_status != "PASS":
            results.append(TTSBenchmarkResult(engine, actual_engine, voice, None, None, warmup_status, warmup_note))
            continue
        first, actual_engine, status, note = await _measure_synthesis(speaker, PHRASE_SHORT, engine, runs)
        if status != "PASS":
            results.append(TTSBenchmarkResult(engine, actual_engine, voice, None, None, status, note))
            continue
        full, actual_engine, status, note = await _measure_synthesis(speaker, PHRASE_LONG, engine, runs)
        results.append(TTSBenchmarkResult(engine, actual_engine, voice, first, full, status, note))
    return results


async def run_stt_benchmark(fixture: Path | None) -> str:
    if fixture is None:
        return "STT: SKIP — pass --stt-fixture with a licensed real-speech 16 kHz mono WAV; silence is not a benchmark."
    if not fixture.is_file():
        return f"STT: SKIP — fixture not found: {fixture}"
    started = time.perf_counter()
    try:
        transcript = await LocalWhisperTranscriber().transcribe(fixture, language="ru")
    except Exception as exc:
        return f"STT: FAIL — {exc}"
    elapsed_ms = (time.perf_counter() - started) * 1000
    return f"STT: PASS — {elapsed_ms:.1f} ms, transcript={transcript!r}"


def _format_distribution(value: Distribution | None) -> str:
    if value is None:
        return "—"
    return f"median {value.median_ms:.1f}; p95 {value.p95_ms:.1f}; min {min(value.samples_ms):.1f}; max {max(value.samples_ms):.1f} ms"


async def run(args: argparse.Namespace) -> int:
    print("VOICE OF LUNA — LOCAL SPEECH BENCHMARK")
    print(f"warm runs per phrase: {args.runs}; Edge enabled: {args.include_edge}")
    print("LLM/TTFA matrix: SKIP — this command never estimates remote Codex latency.")
    print(await run_stt_benchmark(args.stt_fixture))
    print()
    for result in await run_tts_benchmarks(args.runs, args.include_edge):
        actual = result.actual_engine or "—"
        print(f"{result.voice}: {result.status} (requested={result.requested_engine}, actual={actual})")
        if result.status == "PASS":
            print(f"  first chunk: {_format_distribution(result.first_chunk)}")
            print(f"  full sentence: {_format_distribution(result.full_sentence)}")
        elif result.note:
            print(f"  reason: {result.note}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=int, default=7, help="warm measured runs per phrase (default: 7)")
    parser.add_argument("--stt-fixture", type=Path, help="licensed real-speech WAV fixture")
    parser.add_argument("--include-edge", action="store_true", help="allow network-backed Edge TTS")
    args = parser.parse_args()
    if args.runs < 2:
        parser.error("--runs must be at least 2 to report a distribution")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
