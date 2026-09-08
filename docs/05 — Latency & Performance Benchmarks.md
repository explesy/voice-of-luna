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

## Historical numbers

The tables that previously listed fixed LLM × TTS latency values were a mixture
of single local observations and formula-derived estimates. They are retired
from this document: they are neither a current measurement nor a basis for
choosing a default model. Earlier timing claims remain available in git history
as historical context only.
