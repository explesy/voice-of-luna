# Latency & Performance Benchmarks

## What the application measures during a real voice turn

The latency HUD describes one completed browser-to-local-server turn. It is a
diagnostic breakdown, not an SLA and not a cross-machine benchmark.

| Metric | Boundary |
|---|---|
| `VAD` | Detected speech end to recorder stop. |
| `ENC` | Browser PCM/WAV preparation after recorder stop. |
| `PREP` | Server receipt of the complete audio frame to a usable 16 kHz mono WAV. |
| `STT` | Start of Whisper transcription to transcript result. |
| `LLM` | LLM request start to first streamed text delta. |
| `TTS` | First speech segment queued to a completed audio artifact on the server. |
| `e2e` | Detected speech end to browser audio-render start. |

`e2e` is the closest available proxy for responsiveness. It means the browser
scheduled audio rendering; it cannot prove when physical speakers produced a
non-silent sample, because source clips may contain leading silence.

## Local reproducible benchmarks

Run the local speech benchmark:

```bash
make matrix
```

It performs seven warm repetitions per phrase, reports median, p95, min and
max, and never calls Codex. Edge TTS is opt-in because it is network-backed:

```bash
cd backend
uv run python scripts/run_model_matrix.py --include-edge
```

The runner reports `FALLBACK` rather than attributing a macOS fallback clip to
Piper, Silero, or Edge. A requested engine is comparable only when its reported
`actual` engine is the same.

### Recorded local TTS run — 2026-09-08

Environment: macOS 26.6.2 (25G83), Apple M2 / arm64, Python 3.14.3, project
revision `9f7570e`. Each result is seven measured warm repetitions after an
excluded warm-up call. `p95` uses nearest-rank selection; with only seven
samples it is the maximum observation, so it is a spread indicator rather than
a stable tail-latency estimate.

| Voice | Actual engine | First chunk median / min–max | Full sentence median / min–max |
|---|---|---:|---:|
| Dmitri | Piper | 402.2 ms / 240.2–527.6 | 908.2 ms / 783.4–958.2 |
| Irina | Piper | 471.7 ms / 413.7–585.2 | 1213.6 ms / 1050.8–1280.2 |
| Ksenia | Silero | 154.1 ms / 105.0–1800.4 | 272.4 ms / 195.3–403.2 |
| Eugene | Silero | 80.2 ms / 67.3–136.9 | 237.1 ms / 225.2–255.6 |
| Milena | macOS say | 1356.7 ms / 1309.1–1414.3 | 1396.4 ms / 1319.1–1452.1 |
| Svetlana | Edge | 975.1 ms / 701.5–2994.2 | 889.7 ms / 763.2–3813.9 |

All requested engines were actually used; no row is a fallback result. The
single 1.8 s Ksenia outlier and Edge's multi-second tail call for a larger
sample (20–30 repetitions) before any default-selection decision.

### Recorded local TTS run — 2026-09-09

Environment: macOS 26.6.2, Apple Silicon arm64, Python 3.12.13, project
revision `c9c3305`. This run used seven warm repetitions per phrase, excluded
the warm-up call, and did not call Codex or network-backed Edge TTS. It is a
fresh point-in-time measurement, not a before/after comparison with the
previous table.

| Voice | Actual engine | First chunk median / p95 | Full sentence median / p95 |
|---|---|---:|---:|
| Dmitri | Piper | 219.7 / 250.4 ms | 565.1 / 752.9 ms |
| Irina | Piper | 197.7 / 308.4 ms | 678.7 / 986.6 ms |
| Ksenia | Silero | 49.0 / 1105.6 ms | 102.6 / 229.6 ms |
| Eugene | Silero | 44.2 / 75.1 ms | 109.6 / 125.1 ms |
| Milena | macOS say | 1012.4 / 1117.9 ms | 1024.3 / 1181.6 ms |

Ksenia shows a large first-chunk tail despite a low median (one 1.1-second
observation in seven runs). This is evidence for continued measurement, not a
basis for automatic voice selection. The local results keep the focus on
Codex TTFT and turn coordination rather than premature local TTS optimization.

### Current full local TTS matrix — 2026-09-09

This repeat used the current checkout, seven measured warm repetitions after
one excluded warm-up call, and explicitly opted in to Edge. It verified every
supported voice and recorded only timing and the requested/actual engine — no
generated audio is retained. `p95` is nearest-rank and therefore the maximum
of seven samples; it is a spread indicator, not a stable tail percentile.

| Voice | Requested / actual engine | First chunk median / p95 | Full sentence median / p95 | Status |
|---|---|---:|---:|---|
| Dmitri | Piper / Piper | 418.2 / 1378.5 ms | 1432.1 / 1610.3 ms | PASS |
| Irina | Piper / Piper | 730.0 / 825.3 ms | 1528.9 / 1881.5 ms | PASS |
| Ksenia | Silero / Silero | 331.5 / 3540.0 ms | 1157.8 / 1480.6 ms | PASS |
| Baya | Silero / Silero | 713.3 / 1102.0 ms | 1001.6 / 1219.8 ms | PASS |
| Aidar | Silero / Silero | 479.8 / 707.9 ms | 897.0 / 908.9 ms | PASS |
| Eugene | Silero / Silero | 833.6 / 1450.8 ms | 861.3 / 1062.5 ms | PASS |
| Milena | macOS say / macOS say | 2107.1 / 3383.0 ms | 2020.0 / 3608.7 ms | PASS |
| Svetlana | Edge / Edge | 770.1 / 1063.1 ms | 769.6 / 831.7 ms | PASS |

All eight requested voice configurations were actually used, so no fallback result is being
attributed to a different voice. Compared with the full 2026-09-08 snapshot,
the current medians are lower for every listed voice, but this is **not** a
causal performance claim: the runs use different Python/runtime revisions and
uncontrolled machine and network state. The local-only earlier run on the same
date also differs for several voices, which confirms that seven samples are
too few to rank defaults. Ksenia continues to have a roughly one-second
first-chunk tail despite a low median; Edge has lower observed tails than the
previous snapshot but remains network-dependent.

Baya and Aidar were added to this run after the earlier snapshot had omitted
them. Baya shows the same kind of single long first-chunk tail as Ksenia;
Aidar stayed within a narrower first-chunk spread in the focused check. Both
are valid Silero results, but the sample is too small to make a default-voice
decision. The focused Baya/Aidar run under lighter load measured Baya at
33.3/70.3 ms and Aidar at 29.7/74.2 ms (first chunk/full sentence medians);
the difference from this full-matrix run is runtime variance, not a change in
voice implementation.

STT has no valid built-in synthetic fixture. Supplying silence would measure
silence handling rather than recognition. Use a licensed, known-speech 16 kHz
mono WAV when running an operator experiment:

```bash
cd backend
uv run python scripts/run_model_matrix.py --stt-fixture /absolute/path/to/fixture.wav
```

Record the fixture's language, transcript, duration, hardware, OS, Python and
model/runtime versions alongside the result. Do not commit private recordings
or conversation audio.

### Recorded local STT run — 2026-09-08

A user-supplied private Russian MP3 was converted locally to temporary 16 kHz
mono PCM WAV and removed immediately after the run. The original file was not
copied into the repository, and neither its transcript nor its audio is stored
here. The input duration was 11,304 ms; local transcription completed in
**864 ms** and returned non-empty speech (102 characters). No reference
transcript was supplied, so this verifies pipeline latency and speech detection
only — not recognition accuracy, WER, or punctuation quality.

## Live Codex measurements

Remote-model TTFT, first spoken segment, and end-to-end latency depend on the
selected model, reasoning effort, account state, queueing, conversation warmup
and network conditions. They must be collected from explicit operator turns and
reported with their sample count and percentile distribution. The local matrix
does not estimate them from historical constants and does not consume quota.

### Recorded live Codex snapshot — 2026-09-08

The following deliberate quota-consuming check used `reasoning_effort="low"`
and one fixed Russian prompt requesting exactly one short sentence. For each
model, `cold` is the first turn on a newly started local ephemeral app-server
thread and `warm` is the immediately following turn on the same thread. It is
one observation per state, not a percentile benchmark or an SLA.

| Model | Cold TTFT / total | Warm TTFT / total |
|---|---:|---:|
| GPT-5.4-Mini | 6098 / 6391 ms | 4801 / 5097 ms |
| GPT-5.6-Sol | 6495 / 6835 ms | 3854 / 4187 ms |
| GPT-5.6-Terra | 5552 / 5706 ms | 2132 / 2549 ms |
| GPT-5.6-Luna | 6479 / 6708 ms | 3968 / 4726 ms |
| GPT-5.5 | 5404 / 5447 ms | 3660 / 3664 ms |
| GPT-6-Astra | 6463 / 6845 ms | 3786 / 4144 ms |

Every warm observation was faster in this snapshot. That supports measuring
thread warm-up separately, but it does not establish a cross-model ranking:
remote queue state, account load, generation length, and network conditions
remain uncontrolled.

### Current live Codex matrix — 2026-09-09

The local runtime was queried with `model/list` immediately before testing.
It returned five models: GPT-6-Astra, GPT-5.6-Sol, GPT-5.6-Terra,
GPT-5.6-Luna, and GPT-5.5. GPT-5.4 and GPT-5.4 Mini were absent, so they were
not retried or represented as failures. Each current model received one fixed
short Russian prompt at `reasoning_effort="low"`: `cold` is a new ephemeral
thread and `warm` is the immediately following turn on that same thread.
Each row is one observation per state and consumes two Codex turns.

| Model | Cold TTFT / total | Warm TTFT / total | Stream chunks | Status |
|---|---:|---:|---:|---|
| GPT-6-Astra | 6304.0 / 6603.1 ms | 4958.5 / 6392.1 ms | 6 / 6 | PASS |
| GPT-5.6-Sol | 6123.6 / 6341.8 ms | 2978.0 / 3553.9 ms | 6 / 6 | PASS |
| GPT-5.6-Terra | 5553.8 / 5755.9 ms | 2760.6 / 3048.7 ms | 6 / 6 | PASS |
| GPT-5.6-Luna | 4108.8 / 4291.6 ms | 2325.3 / 2525.2 ms | 6 / 6 | PASS |
| GPT-5.5 | 5396.0 / 6292.6 ms | 3647.2 / 3855.2 ms | 6 / 6 | PASS |

All current models streamed non-empty responses in both states, and every
warm observation was faster. Relative to 2026-09-08, the model set changed
only by removal of GPT-5.4 Mini. The most visible timing changes are not
uniform: Luna's cold/warm TTFT improved, Astra's warm TTFT increased, and
Sol/Terra/5.5 remain in the same broad range. This supports keeping warmup
optional and measured, not selecting a permanent winner from one sample.

## Historical numbers

GPT-5.4 Mini figures below are historical only. GPT-5.4 and GPT-5.4 Mini retired from Codex sessions authenticated with ChatGPT on 31 August 2026 and are not shown in the live model picker.

The tables that previously listed fixed LLM × TTS latency values were a mixture
of single local observations and formula-derived estimates. They are retired
from this document: they are neither a current measurement nor a basis for
choosing a default model. Earlier timing claims remain available in git history
as historical context only.
