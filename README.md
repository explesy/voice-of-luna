<p align="center">
  <img src="backend/app/static/tarot-card-la-lune.png" alt="Voice of Lúna Logo" width="160" style="border-radius: 14px; box-shadow: 0 8px 32px rgba(0, 240, 255, 0.25);" />
</p>

<h1 align="center">Voice of Lúna</h1>

<p align="center">
  <strong>Personal, local-first voice bridge to OpenAI Codex via local stdio JSON-RPC</strong><br>
  <em>Sub-second streaming TTFA, 100% local speech-to-text, multi-tier neural TTS, and instant barge-in.</em>
</p>

<p align="center">
  <a href="https://github.com/explesy/voice-of-luna/actions/workflows/ci.yml"><img src="https://github.com/explesy/voice-of-luna/actions/workflows/ci.yml/badge.svg" alt="CI Status" /></a>
  <img src="https://img.shields.io/badge/python-3.12%2B-blue.svg?logo=python&logoColor=white" alt="Python 3.12+" />
  <img src="https://img.shields.io/badge/FastAPI-0.115%2B-009688.svg?logo=fastapi&logoColor=white" alt="FastAPI" />
  <img src="https://img.shields.io/badge/HTMX-2.0-3366cc.svg?logo=htmx&logoColor=white" alt="HTMX 2.0" />
  <img src="https://img.shields.io/badge/managed%20by-uv-DE5FE9.svg" alt="Managed by uv" />
  <img src="https://img.shields.io/badge/privacy-100%25%20local--first-00f0ff.svg" alt="100% Local-First" />
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green.svg" alt="MIT License" /></a>
</p>

---

## 🌒 Overview

**Voice of Lúna** is a personal, open-source, local-first voice companion. It bridges your browser microphone directly to top-tier OpenAI models through your existing, already-authenticated local **Codex CLI** (`codex app-server`), keeping the intelligence in the high-reasoning text domain while delivering an ultra-responsive, natural voice conversation loop.

Unlike traditional cloud voice assistants that upload unencrypted voice recordings, bill you for expensive audio tokens, or force rigid robotic turn-taking, Voice of Lúna runs its audio capture and speech recognition locally on your own machine.

### Why Voice of Lúna?

- 🔒 **Zero Audio Leakage & Total Privacy**: Speech recognition runs entirely offline with local Whisper. Your raw voice recordings never leave your machine.
- 🔑 **No Extra API Keys or Costs**: Communicates directly with your authenticated local Codex session (`codex login`) via stdio JSON-RPC. No pay-per-token API billing or additional third-party subscriptions.
- ⚡ **Streaming Sentence-Level TTS (<2s TTFA)**: Speech synthesis starts the millisecond the first punctuation mark arrives from the model token stream — you hear the answer before the model finishes generating.
- 🛑 **True Natural Barge-In**: Interrupt Lúna at any moment simply by speaking or pressing `[Space]`. Audio playback stops instantly, queues are flushed, and a new turn begins.
- 🎙️ **Multi-Tier Voice Engine**: High-fidelity free cloud neural voices (Microsoft Edge TTS), ultra-fast offline neural voices (Silero TTS v4), and native macOS speech with automatic graceful fallback.
- 🌙 **Cyber-Tarot Dark Aesthetic**: Server-rendered HTMX interface with interactive Radar HUD, dynamic pulse core, millisecond telemetry readouts, and collapsible transcripts.

---

## 🏛️ Architecture

```mermaid
flowchart LR
    subgraph Browser ["Client Browser (Desktop / Mobile)"]
        Mic["🎙️ Microphone<br/>(AudioWorklet + VAD)"]
        UI["📡 Radar HUD & Chat<br/>(HTMX + Vanilla JS)"]
        Speaker["🔊 Audio Player<br/>(Instant Barge-In)"]
    end

    subgraph Backend ["Voice of Lúna Backend (FastAPI on localhost)"]
        WS["⚡ WebSocket Pipeline<br/>(Turn & Audio Stream)"]
        VAD["⏱️ Client VAD<br/>(450ms silence endpointing)"]
        STT["🎧 Local Whisper STT<br/>(whisper.cpp / whisper-server)"]
        Chunker["✂️ Streaming Chunker<br/>(Early sentence extraction)"]
        
        subgraph TTS ["Multi-Tier TTS Engine"]
            Edge["☁️ Edge TTS (Neural Cloud)"]
            Piper["🚀 Piper ONNX (Offline Neural)"]
            Silero["⚡ Silero v4 (Offline Neural)"]
            Mac["🍏 macOS say (Native Offline)"]
        end
    end

    subgraph LocalCodex ["Local Codex Companion"]
        AppServer["🤖 codex app-server<br/>(stdio JSON-RPC)"]
        Models["🧠 GPT-5.6-Sol / Mini / Astra<br/>(Streaming Tokens)"]
    end

    Mic -->|Raw PCM| WS
    WS --> VAD --> STT
    STT -->|Transcribed Text| AppServer
    AppServer --> Models
    Models -->|Token Stream| Chunker
    Chunker -->|Sentence Chunks| TTS
    TTS -->|MP3 / WAV Audio| WS
    WS --> Speaker
    UI -.->|Barge-In Interrupt| WS
```

---

## ✨ Features

### 1. Ultra-Low Latency Voice Loop (TTFA)
In conversational interfaces, **Time To First Audio (TTFA)** is what creates the feeling of a genuine dialogue. Voice of Lúna features an early-extraction streaming pipeline:
- As soon as the model outputs an opening clause (3+ words ending in `,`, `:`, `—`, `.`), it is immediately dispatched to the TTS synthesis queue.
- You hear the assistant start speaking while subsequent sentences are generated and synthesized in parallel.
- Benchmarked at **~1.99s TTFA** with lightweight models and **~2.15s TTFA** with everyday workhorse models.

### 2. Multi-Tier Speech Synthesis (TTS)
Switch voices on the fly with automatic multi-tier fallback:

| Engine | Tier | Latency (1st Chunk) | Description |
|:---|:---:|:---:|:---|
| **Piper TTS ONNX** | 🚀 Offline Neural | **~165 – 225 ms** | Ultra-responsive offline ONNX neural voices (`Dmitri`, `Irina`). Low memory footprint, no PyTorch warmup needed. |
| **Silero TTS v4** | ⚡ Offline Neural | **50 – 120 ms** | Blazing fast PyTorch neural model (`Eugene`, `Ksenia`, `Baya`). 100% offline with Latin phonetic transliteration. |
| **Microsoft Edge TTS** | ☁️ Cloud Neural | ~1.5 – 3.5 s | Studio-quality cloud neural voices (`Svetlana`, `Dmitry`, `Jenny`). Free, natural prosody, no API keys required. |
| **macOS say** | 🍏 Native Offline | ~1.0 s | Zero-dependency macOS native voice (`Milena`, `Samantha`). System-level offline fallback. |

### 3. Client-Side VAD & Instant Barge-In
- **Continuous Voice Activity Detection (VAD)** automatically detects when you stop speaking (450ms silence endpointing) and triggers processing without manual clicks.
- **Barge-in**: Speak or click the Radar HUD while the assistant is talking, and playback cuts off within milliseconds, cancelling remaining synthesis tasks.

### 4. Live Codex Discovery & Warmup
- **Dynamic Model Discovery**: Queries `/api/models` directly from your local Codex runtime (`gpt-5.6-sol`, `gpt-5.4-mini`, `gpt-5.6-terra`, `gpt-6-astra`).
- **Adjustable Reasoning**: Configure reasoning effort (`low`, `medium`, `high`) per session.
- **Hidden Warmup (`WARM: ON/OFF`)**: Executes an invisible 1-turn background ping when opening a session to eliminate cold-start thread preparation latency.

### 5. Capability-Isolated Plugins
Extend Lúna's capabilities without granting plugins access to credentials or raw audio:
- **`Lúna (Core)`**: Neutral, empathetic conversation core.
- **`Focus Sprint`**: Timed Pomodoro-style sprints with progress check-ins.
- **`Spanish Buddy`**: Conversational Spanish language tutor.

---

## 📊 Performance Benchmarks

Measured on Apple Silicon with local `codex app-server` (stdio JSON-RPC) and `reasoning_effort="low"`:

### Time To First Audio (TTFA)
*Measured from prompt dispatch to first audible frame delivery in browser:*

| Model \ TTS Engine | Silero v4 ⚡ | Piper ONNX 🚀 | macOS say 🍏 | Edge TTS ☁️ |
|:---|:---:|:---:|:---:|:---:|
| **GPT-5.4-Mini** *(Lightweight)* | **1.71 – 1.99 s** 🏆 | **1.88 s** ⚡ | 2.97 – 3.00 s | 4.93 – 12.29 s |
| **GPT-5.6-Sol** *(Everyday Workhorse)* | **2.15 – 2.38 s** 🚀 | **2.52 s** ✨ | 3.16 – 3.64 s | 3.63 – 12.93 s |
| **GPT-5.6-Terra** *(Balanced Coding)* | **2.47 – 2.85 s** | **2.98 s** | 3.66 – 4.10 s | 3.87 – 13.39 s |
| **GPT-5.6-Luna** *(Voice Companion)* | **4.42 – 4.95 s** | **5.10 s** | 5.49 – 6.22 s | 6.52 – 15.51 s |
| **GPT-6-Astra** *(Flagship Reasoning)* | **8.38 – 8.70 s** | **8.86 s** | 9.50 – 9.98 s | 10.37 – 19.27 s |

> 📖 **Full benchmarks**: See [`docs/05 — Latency & Performance Benchmarks.md`](docs/05%20%E2%80%94%20Latency%20&%20Performance%20Benchmarks.md) for TTFT breakdowns, throughput graphs, and detailed audio chunk profiling.

---

## 🚀 Getting Started

### Prerequisites

1. **macOS** or **Linux** with **Python 3.12+**.
2. [**uv**](https://docs.astral.sh/uv/) (modern, fast Python package manager).
3. **ffmpeg** and **whisper.cpp** for local speech recognition.
4. **OpenAI Codex CLI** signed in (`codex login`).

### Step 1: Install Audio Dependencies

On macOS with Homebrew:
```bash
brew install ffmpeg whisper-cpp
```

### Step 2: Download Local Whisper Model

Download the recommended multilingual Whisper model (GGML small, ~465 MB):
```bash
mkdir -p data/models
curl -L --retry 3 https://huggingface.co/ggerganov/whisper.cpp/resolve/main/ggml-small.bin \
  -o data/models/ggml-small.bin
```
*(The `data/` directory is git-ignored).*

### Step 3: Clone & Install

```bash
git clone https://github.com/explesy/voice-of-luna.git
cd voice-of-luna
make setup
```

Or using `uv` directly:
```bash
cd backend
uv sync --group dev
```

### Step 4: Launch Voice of Lúna

```bash
make run
```
*(Or `cd backend && uv run uvicorn app.main:app --reload`)*

Open your browser at **[http://127.0.0.1:8000](http://127.0.0.1:8000)**.

---

## 🎮 Interface & Controls

<p align="center">
  <img src="backend/app/static/favicon.svg" width="64" alt="Lúna Icon" /><br>
  <strong>Cyber-Tarot Radar Console</strong>
</p>

| Input / Control | Action |
|:---|:---|
| **Click Radar HUD** | Toggle recording on / off |
| **[Space]** | Push-to-talk / toggle microphone |
| **Speak during playback** | Instant barge-in: interrupts assistant speech immediately |
| **[Escape]** | Stop playback / reset audio state |
| **Voice Selector** | Choose between Edge Neural, Silero Offline Neural, or macOS system voices |
| **Model & Reasoning** | Select Codex model and toggle reasoning effort (`low`, `med`, `high`) |
| **WARM: ON/OFF** | Enable pre-warming of Codex threads for zero-latency first turn |
| **Prompt Input Dock** | Fallback text input dock for hybrid typing & voice interaction |

---

## ⚙️ Configuration

Voice of Lúna works out of the box with zero configuration, but can be customized via environment variables:

| Variable | Default | Description |
|:---|:---|:---|
| `VOICE_OF_LUNA_WHISPER_MODEL` | `data/models/ggml-small.bin` | Absolute path to the Whisper GGML model file |
| `VOICE_OF_LUNA_WHISPER_LANGUAGE` | `ru` | Language code for transcription (`ru`, `en`, `auto`) |
| `VOICE_OF_LUNA_WHISPER_SERVER` | `true` | Enable persistent background `whisper-server` process |
| `VOICE_OF_LUNA_WHISPER_HOST` | `127.0.0.1` | Local Whisper server host |
| `VOICE_OF_LUNA_WHISPER_PORT` | `8089` | Local Whisper server port |
| `VOICE_OF_LUNA_RUSSIAN_VOICE` | `Milena (Enhanced)` | Default macOS speech voice |
| `VOICE_OF_LUNA_CONVERSATION_IDLE_TTL_SECONDS` | `900` | Inactivity timeout before reclaiming idle Codex threads (15 min) |

---

## 🧪 Testing & Verification

The test suite runs completely offline with 100% mocked model calls — running tests will **never consume your Codex quota**:
 
```bash
make test
```
Or:
```bash
cd backend
uv run pytest -q
```

To run the live hardware benchmark and test matrix:
```bash
make matrix
```

To verify version synchronization (SemVer single source of truth):
```bash
cd backend
uv run pytest tests/test_version.py
```

---

## 🔒 Security & Privacy Model

- **Localhost Boundary**: Voice of Lúna binds exclusively to `127.0.0.1`. It is designed as a personal companion, never to be exposed directly to the public internet without an authentication layer.
- **No Token Storage**: The application never touches, reads, or caches your OAuth credentials. All LLM calls pass through the local `codex app-server` binary already authorized on your machine.
- **Ephemeral Audio Lifecycle**: Recorded audio chunks, temporary WAV conversions, and synthesized reply audio are wiped immediately after turn delivery.
- **Isolated Plugins**: Plugins run in restricted turn contexts and have no access to file systems, credentials, or raw audio data.

---

## 📚 Documentation

For in-depth architectural and product specifications, explore the [`docs/`](docs/) directory:

- [**00 — Project Overview**](docs/00%20%E2%80%94%20PROJECT%20README.md): High-level mission and product decisions.
- [**01 — Product & UX Spec**](docs/01%20%E2%80%94%20Product%20&%20UX%20Spec.md): User experience design, telemetry, and states.
- [**02 — Technical Architecture**](docs/02%20%E2%80%94%20Technical%20Architecture.md): Audio pipeline, WebSocket protocol, and process lifecycles.
- [**03 — Conversation Protocol**](docs/03%20%E2%80%94%20Conversation%20Protocol.md): Turn-taking rules, silence handling, and barge-in guarantees.
- [**04 — Implementation Plan**](docs/04%20%E2%80%94%20MVP%20&%20Implementation%20Plan.md): Milestone roadmap and quality gates.
- [**05 — Latency & Performance Benchmarks**](docs/05%20%E2%80%94%20Latency%20&%20Performance%20Benchmarks.md): Extensive TTFT, TTFA, and TTS engine benchmarks.

---

## 🤝 Contributing

Contributions are welcome! Please read [CONTRIBUTING.md](CONTRIBUTING.md) and [AGENTS.md](AGENTS.md) before opening an issue or submitting a pull request.

For security vulnerabilities, please refer to [SECURITY.md](SECURITY.md).

---

## 📄 License

Voice of Lúna is licensed under the [MIT License](LICENSE).
