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

## Historical numbers

The tables that previously listed fixed LLM × TTS latency values were a mixture
of single local observations and formula-derived estimates. They are retired
from this document: they are neither a current measurement nor a basis for
choosing a default model. Earlier timing claims remain available in git history
as historical context only.
