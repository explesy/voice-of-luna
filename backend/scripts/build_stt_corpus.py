#!/usr/bin/env python3
"""Build a synthetic local STT corpus from known reference text using macOS TTS.

This is the "compute/latency/rough-WER" phase of the streaming-STT spike
(issue #22). Every clip is rendered offline with the built-in macOS ``say``
voices, so the reference text is known exactly and nothing is uploaded. The
generated corpus stays outside Git (``backend/models/`` is ignored).

Mixed-language clips are produced by rendering each segment with a matching
voice and concatenating the WAVs with ffmpeg.

Example:

    cd backend
    uv run python scripts/build_stt_corpus.py \
        --out models/stt-corpus-synthetic

The output directory contains ``clip-*.wav`` plus ``manifest.jsonl`` with
``id``, ``audio``, ``reference``, ``language``, ``kind`` and ``voice`` fields.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import wave
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Clip:
    language: str
    kind: str
    reference: str
    segments: list[tuple[str, str]] = field(default_factory=list)  # (voice, text)


RU_VOICE = "Milena"
ES_VOICE = "Mónica"
EN_VOICE = "Samantha"

CLIPS: list[Clip] = [
    Clip("ru", "command", "Привет, Луна. Расскажи, какая сегодня погода.", [("ru", "Привет, Луна. Расскажи, какая сегодня погода.")]),
    Clip("ru", "command", "Поставь таймер на пять минут, а потом напомни мне сделать перерыв.", [("ru", "Поставь таймер на пять минут, а потом напомни мне сделать перерыв.")]),
    Clip("ru", "sentence", "Телескоп собирает и фокусирует свет далёких звёзд.", [("ru", "Телескоп собирает и фокусирует свет далёких звёзд.")]),
    Clip("ru", "numbers", "Проверь распознавание чисел: двадцать три, сто восемь и две тысячи двадцать шестой год.", [("ru", "Проверь распознавание чисел: двадцать три, сто восемь и две тысячи двадцать шестой год.")]),
    Clip("ru", "sentence", "Это короткая фраза с паузой и именем Анна.", [("ru", "Это короткая фраза с паузой и именем Анна.")]),
    Clip("ru", "sentence", "Сегодня ветрено, возьми зонт и не забудь про тёплую куртку.", [("ru", "Сегодня ветрено, возьми зонт и не забудь про тёплую куртку.")]),
    Clip("es", "command", "Hola, Luna. ¿Qué tiempo hace hoy en la ciudad?", [("es", "Hola, Luna. ¿Qué tiempo hace hoy en la ciudad?")]),
    Clip("es", "command", "Pon un temporizador de cinco minutos y recuérdame hacer una pausa.", [("es", "Pon un temporizador de cinco minutos y recuérdame hacer una pausa.")]),
    Clip("es", "sentence", "El telescopio recoge y enfoca la luz de las estrellas lejanas.", [("es", "El telescopio recoge y enfoca la luz de las estrellas lejanas.")]),
    Clip("es", "numbers", "Comprueba los números: veintitrés, ciento ocho y el año dos mil veintiséis.", [("es", "Comprueba los números: veintitrés, ciento ocho y el año dos mil veintiséis.")]),
    Clip("es", "sentence", "Esta es una frase corta con una pausa y el nombre de Ana.", [("es", "Esta es una frase corta con una pausa y el nombre de Ana.")]),
    Clip("es", "sentence", "Hoy hace viento, lleva un paraguas y no olvides el abrigo.", [("es", "Hoy hace viento, lleva un paraguas y no olvides el abrigo.")]),
    Clip("en", "command", "Hello, Luna. Tell me what the weather is like today.", [("en", "Hello, Luna. Tell me what the weather is like today.")]),
    Clip("en", "command", "Set a timer for five minutes and remind me to take a break.", [("en", "Set a timer for five minutes and remind me to take a break.")]),
    Clip("en", "sentence", "The telescope collects and focuses light from distant stars.", [("en", "The telescope collects and focuses light from distant stars.")]),
    Clip("en", "numbers", "Check the numbers: twenty three, one hundred eight, and the year twenty twenty six.", [("en", "Check the numbers: twenty three, one hundred eight, and the year twenty twenty six.")]),
    Clip("en", "sentence", "This is a short phrase with a pause and the name Anna.", [("en", "This is a short phrase with a pause and the name Anna.")]),
    Clip("en", "sentence", "It is windy today, take an umbrella and do not forget a warm coat.", [("en", "It is windy today, take an umbrella and do not forget a warm coat.")]),
    Clip(
        "mixed",
        "code-switch",
        "Открой файл report и покажи строку line forty two.",
        [("ru", "Открой файл report и покажи строку "), ("en", "line forty two.")],
    ),
    Clip(
        "mixed",
        "code-switch",
        "Call the function и передай параметр температура.",
        [("en", "Call the function "), ("ru", "и передай параметр температура.")],
    ),
]

VOICES = {"ru": RU_VOICE, "es": ES_VOICE, "en": EN_VOICE}


def _say_to_wav(voice: str, text: str, destination: Path) -> None:
    command = [
        "say",
        "-v",
        voice,
        "-o",
        str(destination),
        "--data-format=LEI16@16000",
        "--",
        text,
    ]
    result = subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    if result.returncode != 0:
        raise SystemExit(f"say failed for voice '{voice}': {result.stderr.decode(errors='replace').strip()}")
    _assert_pcm16_mono_16k(destination)


def _concat(wavs: list[Path], destination: Path) -> None:
    if len(wavs) == 1:
        shutil.copyfile(wavs[0], destination)
        return
    inputs: list[str] = []
    for wav in wavs:
        inputs += ["-i", str(wav)]
    filter_inputs = "".join(f"[{index}:a]" for index in range(len(wavs)))
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        *inputs,
        "-filter_complex",
        f"{filter_inputs}concat=n={len(wavs)}:v=0:a=1[out]",
        "-map",
        "[out]",
        "-ar",
        "16000",
        "-ac",
        "1",
        "-c:a",
        "pcm_s16le",
        str(destination),
    ]
    subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)


def _assert_pcm16_mono_16k(path: Path) -> None:
    with wave.open(str(path), "rb") as wav:
        if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (16000, 1, 2):
            raise SystemExit(
                f"{path.name} is not 16 kHz mono 16-bit PCM "
                f"(rate={wav.getframerate()}, channels={wav.getnchannels()}, width={wav.getsampwidth()})"
            )


def _duration_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as wav:
        return wav.getnframes() / float(wav.getframerate())


def build(out_dir: Path) -> list[dict[str, object]]:
    if shutil.which("say") is None or shutil.which("ffmpeg") is None:
        raise SystemExit("build_stt_corpus requires macOS 'say' and 'ffmpeg' on PATH")
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = out_dir / "manifest.jsonl"
    manifest_path.write_text("", encoding="utf-8")

    rows: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="stt-corpus-") as temp_dir:
        temp = Path(temp_dir)
        for index, clip in enumerate(CLIPS, start=1):
            clip_id = f"synth-{clip.language}-{index:03d}"
            parts: list[Path] = []
            for seg_index, (segment_language, text) in enumerate(clip.segments):
                part = temp / f"{clip_id}-{seg_index}.wav"
                _say_to_wav(VOICES[segment_language], text, part)
                parts.append(part)
            destination = out_dir / f"{clip_id}.wav"
            _concat(parts, destination)
            _assert_pcm16_mono_16k(destination)
            row = {
                "id": clip_id,
                "audio": destination.name,
                "reference": clip.reference,
                "language": clip.language,
                "kind": clip.kind,
                "voice": ",".join(VOICES[lang] for lang, _ in clip.segments),
                "duration_seconds": round(_duration_seconds(destination), 3),
                "synthetic": True,
            }
            rows.append(row)
            with manifest_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("models/stt-corpus-synthetic"))
    args = parser.parse_args()
    rows = build(args.out.expanduser().resolve())
    print(f"Wrote {len(rows)} synthetic clips to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
