#!/usr/bin/env python3
"""Benchmark local T-One and/or Whisper against a JSONL speech manifest.

Manifest rows contain: {"id": "clip-001", "audio": "clip-001.wav",
"reference": "эталонный текст"}. Audio paths are resolved relative to the
manifest directory. Results are written locally and never sent to a service.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import string
import sys
import time
import wave
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.transcribe import LocalWhisperTranscriber, ToneStreamingSession


def _normalise(text: str) -> str:
    punctuation = string.punctuation + "«»—–…„“”"
    text = text.lower().translate(str.maketrans({char: " " for char in punctuation}))
    return " ".join(text.split())


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


async def _tone(path: Path, model: Path, tokens: Path, chunk_ms: int) -> dict[str, Any]:
    started = time.perf_counter()
    partials: list[str] = []
    first_partial_ms: float | None = None
    with wave.open(str(path), "rb") as wav:
        sample_rate = wav.getframerate()
        if wav.getnchannels() != 1 or wav.getsampwidth() != 2:
            raise ValueError(f"{path.name} must be mono 16-bit PCM WAV")
        session = ToneStreamingSession(str(model), str(tokens), sample_rate)
        frames_per_chunk = max(1, int(sample_rate * chunk_ms / 1000))
        while True:
            payload = wav.readframes(frames_per_chunk)
            if not payload:
                break
            text = await session.push_pcm(payload, sample_rate)
            if text:
                partials.append(text)
                if first_partial_ms is None:
                    first_partial_ms = (time.perf_counter() - started) * 1000
        final = await session.finalize()
    return {
        "text": final,
        "first_partial_ms": round(first_partial_ms, 2) if first_partial_ms is not None else None,
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
        "partial_count": len(partials),
        "partials": partials,
    }


async def _whisper(path: Path, language: str) -> dict[str, Any]:
    started = time.perf_counter()
    transcriber = LocalWhisperTranscriber(language=language)
    try:
        text = await transcriber.transcribe(path, language=language)
    finally:
        await transcriber.aclose()
    return {"text": text, "elapsed_ms": round((time.perf_counter() - started) * 1000, 2)}


def _summary(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    ordered = sorted(values)
    return {
        "mean": round(statistics.mean(values), 2),
        "median": round(statistics.median(values), 2),
        "p95_nearest_rank": round(ordered[max(0, round((len(ordered) - 1) * 0.95))], 2),
    }


async def run(args: argparse.Namespace) -> int:
    manifest = args.manifest.expanduser().resolve()
    rows = [json.loads(line) for line in manifest.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        raise SystemExit("manifest is empty")
    model = args.tone_model or Path(os.environ.get("VOICE_OF_LUNA_TONE_MODEL", ""))
    tokens = args.tone_tokens or Path(os.environ.get("VOICE_OF_LUNA_TONE_TOKENS", ""))
    if args.provider in {"tone", "both"} and (not model.is_file() or not tokens.is_file()):
        raise SystemExit("T-One requires --tone-model/--tone-tokens or the matching environment variables")

    results: list[dict[str, Any]] = []
    for row in rows:
        audio = (manifest.parent / row["audio"]).resolve()
        item: dict[str, Any] = {"id": row["id"], "audio": str(audio), "reference": row["reference"]}
        if args.provider in {"tone", "both"}:
            try:
                tone = await _tone(audio, model, tokens, args.chunk_ms)
                tone["wer"] = round(_error_rate(row["reference"], tone["text"]), 4)
                tone["cer"] = round(_error_rate(row["reference"], tone["text"], characters=True), 4)
                item["tone"] = tone
            except Exception as exc:
                item["tone_error"] = str(exc)
        if args.provider in {"whisper", "both"}:
            try:
                whisper = await _whisper(audio, args.language)
                whisper["wer"] = round(_error_rate(row["reference"], whisper["text"]), 4)
                whisper["cer"] = round(_error_rate(row["reference"], whisper["text"], characters=True), 4)
                item["whisper"] = whisper
            except Exception as exc:
                item["whisper_error"] = str(exc)
        results.append(item)

    summary: dict[str, Any] = {"clips": len(results), "provider": args.provider}
    for provider in ("tone", "whisper"):
        entries = [item[provider] for item in results if provider in item]
        if entries:
            summary[provider] = {
                "wer": _summary([entry["wer"] for entry in entries]),
                "cer": _summary([entry["cer"] for entry in entries]),
                "elapsed_ms": _summary([entry["elapsed_ms"] for entry in entries]),
            }
            if provider == "tone":
                summary[provider]["first_partial_ms"] = _summary(
                    [entry["first_partial_ms"] for entry in entries if entry["first_partial_ms"] is not None]
                )

    payload = {"manifest": str(manifest), "summary": summary, "clips": results}
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"Detailed results: {output}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--provider", choices=("tone", "whisper", "both"), default="both")
    parser.add_argument("--tone-model", type=Path)
    parser.add_argument("--tone-tokens", type=Path)
    parser.add_argument("--chunk-ms", type=int, default=200)
    parser.add_argument("--language", default="ru")
    parser.add_argument("--output", type=Path, default=Path("benchmark-results/stt.json"))
    args = parser.parse_args()
    if args.chunk_ms < 20:
        parser.error("--chunk-ms must be at least 20")
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
