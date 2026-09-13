#!/usr/bin/env python3
"""Record a small local STT benchmark corpus with ffmpeg.

The script records mono 16 kHz PCM WAV files and writes a JSONL manifest with
the reference transcript entered by the operator. Nothing is uploaded and no
audio is added to the repository automatically.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path


DEFAULT_PROMPTS = [
    "Привет, Луна. Расскажи, какая сегодня погода.",
    "Телескоп собирает и фокусирует свет далёких звёзд.",
    "Поставь таймер на пять минут, а потом напомни мне сделать перерыв.",
    "Это короткая фраза с паузой и именем Анна.",
    "Проверь распознавание чисел: двадцать три, сто восемь и две тысячи двадцать шестой год.",
]


def _defaults() -> tuple[str, str]:
    system = platform.system().lower()
    if system == "darwin":
        return "avfoundation", ":0"
    if system == "linux":
        return "pulse", "default"
    if system == "windows":
        return "dshow", "audio=default"
    return "", ""


def _record_command(args: argparse.Namespace, output: Path) -> list[str]:
    input_format = args.format
    input_device = args.input
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        input_format,
        "-i",
        input_device,
        "-t",
        str(args.duration),
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(output),
    ]
    return command


def _write_manifest(manifest: Path, entry: dict[str, object]) -> None:
    with manifest.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def main() -> int:
    default_format, default_input = _defaults()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("stt-corpus"), help="local corpus directory")
    parser.add_argument("--manifest", type=Path, help="JSONL manifest (default: OUT/manifest.jsonl)")
    parser.add_argument("--count", type=int, default=len(DEFAULT_PROMPTS))
    parser.add_argument("--duration", type=float, default=8.0, help="maximum recording duration in seconds")
    parser.add_argument("--format", default=default_format, help="ffmpeg input format (macOS: avfoundation)")
    parser.add_argument("--input", default=default_input, help="ffmpeg input device, e.g. :0 or default")
    parser.add_argument("--prompt", action="append", help="prompt; repeat to provide a custom prompt list")
    args = parser.parse_args()

    if not shutil.which("ffmpeg"):
        print("ffmpeg is required. Install it locally and retry.", file=sys.stderr)
        return 2
    if not args.format or not args.input:
        parser.error("could not infer an ffmpeg device; provide --format and --input")
    if args.count < 1 or args.duration <= 0:
        parser.error("--count must be positive and --duration must be greater than zero")

    out_dir = args.out.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest = (args.manifest or out_dir / "manifest.jsonl").expanduser().resolve()
    manifest.parent.mkdir(parents=True, exist_ok=True)
    prompts = args.prompt or DEFAULT_PROMPTS
    prompts = (prompts * ((args.count + len(prompts) - 1) // len(prompts)))[: args.count]

    print(f"Recording locally with ffmpeg {args.format!r} input {args.input!r}")
    print(f"Output: {out_dir}")
    print("Speak the displayed sentence naturally. The entered reference must match what was spoken.")
    for index, prompt in enumerate(prompts, start=1):
        clip_id = f"clip-{index:03d}"
        output = out_dir / f"{clip_id}.wav"
        print(f"\n[{index}/{len(prompts)}] Say: {prompt}")
        try:
            input("Press Enter to record, or Ctrl-C to stop... ")
        except KeyboardInterrupt:
            print("\nStopped; existing clips remain in place.")
            return 0
        result = subprocess.run(_record_command(args, output), check=False)
        if result.returncode != 0 or not output.is_file():
            print(f"Recording failed for {clip_id}; ffmpeg exit={result.returncode}", file=sys.stderr)
            output.unlink(missing_ok=True)
            continue
        reference = input("Reference transcript (Enter to discard this clip): ").strip()
        if not reference:
            output.unlink(missing_ok=True)
            print("Discarded.")
            continue
        _write_manifest(
            manifest,
            {"id": clip_id, "audio": output.name, "reference": reference, "sample_rate": 16000},
        )
        print(f"Saved {output.name}")

    print(f"\nManifest: {manifest}")
    print("Keep this directory outside Git; it contains private recordings and reference text.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
